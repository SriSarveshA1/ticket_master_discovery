"""
FastAPI application entry point.

Run locally:
    uvicorn backend.main:app --reload --port 8000
Docs:
    http://localhost:8000/docs

WHAT HAPPENS AT STARTUP (lifespan)
----------------------------------
1. Create the checkpointer (memory or sqlite).
2. Build the agent once -- it is expensive-ish and stateless, so share it.
3. Wrap it in a SessionManager and park it on `app.state` for the routes.

WHY `create_app()` RETURNS A NEW APP
------------------------------------
Tests call `create_app(model=FakeLLM())` to get an isolated app with no
network/LLM dependency. The module-level `app = create_app()` is what uvicorn
imports.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from backend.agent import build_agent
from backend.api.routes import router
from backend.config import settings
from backend.services.exceptions import ExternalAPIError, ExternalAPIUnavailable
from backend.sessions.manager import (
    ActionMismatch,
    AgentUnavailable,
    NoPendingAction,
    SessionManager,
    SessionNotFound,
)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if settings.use_system_certs:
    # Must run before any HTTPS client is created. See config.py for why.
    import truststore

    truststore.inject_into_ssl()
    logger.info("Using operating-system certificate store for TLS")


def create_app(
    model: BaseChatModel | str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # ----- Bonus 5: choose where conversations are stored -----------------
        #
        #   memory (default)  InMemorySaver: a Python dict. Fast, zero setup,
        #                     but every conversation is lost when uvicorn stops.
        #   sqlite            AsyncSqliteSaver: same data written to a .db file.
        #                     Restart the backend and GET /sessions/{id} still
        #                     returns the history; a paused HITL approval can
        #                     even be approved after a restart.
        #
        # The SQLite saver owns a database connection, so it is an async context
        # manager that must stay open for the app's whole lifetime -- which is
        # exactly what `lifespan` is for. Tests bypass this by passing their own
        # `checkpointer`.
        if checkpointer is not None or settings.checkpoint_backend == "memory":
            agent = build_agent(model=model, checkpointer=checkpointer)
            app.state.session_manager = SessionManager(agent)
            logger.info("Startup complete (checkpointer=%s)", "injected" if checkpointer else "memory")
            _print_agent_graph(agent)
            yield
        else:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # pip install langgraph-checkpoint-sqlite

            async with AsyncSqliteSaver.from_conn_string(settings.sqlite_path) as saver:
                agent = build_agent(model=model, checkpointer=saver)
                app.state.session_manager = SessionManager(agent)
                logger.info("Startup complete (checkpointer=sqlite, file=%s)", settings.sqlite_path)
                _print_agent_graph(agent)
                yield
        logger.info("Shutdown complete")

    app = FastAPI(
        title="Book Discovery Agent API",
        description="FastAPI + LangChain agent sample (analogue of the Ticketmaster assignment).",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Browser frontends (Streamlit/React on another port) need CORS enabled.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_error_handlers(app)
    app.include_router(router)
    return app


def _print_agent_graph(agent) -> None:
    """Best-effort ASCII dump of the LangGraph. Needs optional `grandalf`."""
    try:
        print(agent.get_graph().draw_ascii())
    except ImportError as exc:
        logger.warning(
            "Skipping graph ASCII dump (%s). Install with: pip install grandalf "
            "(into the same Python that runs uvicorn: %s)",
            exc,
            __import__("sys").executable,
        )


def _register_error_handlers(app: FastAPI) -> None:
    """
    CONCEPT: "Do not display raw Python exceptions to frontend users."

    Every exception type our layers raise is mapped here to a status code and a
    safe message. The generic handler at the bottom is the last line of defence.
    """

    def _json(code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=code, content={"detail": detail})

    @app.exception_handler(SessionNotFound)
    async def _not_found(_: Request, exc: SessionNotFound):
        return _json(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(NoPendingAction)
    async def _no_pending(_: Request, exc: NoPendingAction):
        return _json(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(ActionMismatch)
    async def _mismatch(_: Request, exc: ActionMismatch):
        return _json(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(AgentUnavailable)
    async def _agent_down(_: Request, exc: AgentUnavailable):
        return _json(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    @app.exception_handler(ExternalAPIUnavailable)
    async def _upstream_down(_: Request, exc: ExternalAPIUnavailable):
        return _json(status.HTTP_503_SERVICE_UNAVAILABLE, exc.user_message)

    @app.exception_handler(ExternalAPIError)
    async def _upstream_error(_: Request, exc: ExternalAPIError):
        return _json(status.HTTP_502_BAD_GATEWAY, exc.user_message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Make 422s friendlier than FastAPI's default nested structure.
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p != "body") or "request"
        return _json(422, f"Invalid {field}: {first.get('msg', 'invalid value')}")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error: %s", exc)
        return _json(status.HTTP_500_INTERNAL_SERVER_ERROR, "Something went wrong on our side. Please try again.")


app = create_app()
