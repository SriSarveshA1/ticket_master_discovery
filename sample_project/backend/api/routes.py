"""
HTTP endpoints.

Assignment mapping:
    POST /chat                    (required)
    POST /chat/stream             (Bonus 6 -- Server-Sent Events)
    POST /approve, POST /reject   (recommended -- HITL)
    GET  /health                  (recommended)
    GET/DELETE /sessions/{id}     (optional)
    GET  /books/{work_key}        (optional; analogue of GET /events/{event_id})

CONCEPT: Dependency injection.  Routes ask for a `SessionManager` via
`Depends(get_session_manager)`. FastAPI fetches it from `app.state`, where the
lifespan in main.py stored it. Tests can build the app with a fake LLM and the
routes never know the difference.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend.config import settings
from backend.models.schemas import (
    ApprovalRequest,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthResponse,
    SessionHistoryResponse,
)
from backend.services import openlibrary
from backend.services.exceptions import ExternalAPIUnavailable, InvalidQuery, ResourceNotFound
from backend.sessions.manager import AgentTurn, SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()


def get_session_manager(request: Request) -> SessionManager:
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:  # lifespan did not run -> misconfiguration
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Agent is not initialised.")
    return manager


def _to_response(turn: AgentTurn) -> ChatResponse:
    return ChatResponse(
        session_id=turn.session_id,
        status=turn.status,  # type: ignore[arg-type]
        response=turn.response,
        pending_action=turn.pending_action,
        handoff_url=turn.handoff_url,
    )


# ---------------------------------------------------------------------------
# Core conversation
# ---------------------------------------------------------------------------


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={503: {"model": ErrorResponse}},
    summary="Send a message to the agent",
)
async def chat(body: ChatRequest, manager: SessionManager = Depends(get_session_manager)) -> ChatResponse:
    """
    Send one user message. Response `status`:
    * `completed` -> show `response`
    * `awaiting_approval` -> show `response` plus Approve / Reject buttons that call
      `/approve` or `/reject` with `pending_action.action_id`.
    """
    turn = await manager.chat(body.session_id, body.message)
    return _to_response(turn)


def _sse(event: dict) -> str:
    """
    Format one Server-Sent Events frame.

    SSE is just a long-lived HTTP response whose body is plain text in this shape:

        event: token
        data: {"content": "Hello"}
        <blank line>

    Browsers (`EventSource`) and any HTTP client that can read lines understand it.
    No websockets, no extra libraries.
    """
    event_type = event.get("type", "message")
    return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post(
    "/chat/stream",
    summary="Send a message and stream the answer back (Server-Sent Events)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream of status/token/done events"}},
)
async def chat_stream(body: ChatRequest, manager: SessionManager = Depends(get_session_manager)) -> StreamingResponse:
    """
    Bonus 6 -- streaming version of `/chat`.

    Frames arrive in this order:
    1. zero or more `status` frames  -> "Searching the catalogue…" (a tool is running)
    2. zero or more `token` frames   -> pieces of the answer, show them as they arrive
    3. exactly one `done` frame      -> the same JSON `/chat` would have returned
       (`status`, `response`, `pending_action`, `handoff_url`). Trust this one.
    or an `error` frame if something failed mid-way.
    """

    async def event_source() -> AsyncIterator[str]:
        async for event in manager.stream_chat(body.session_id, body.message):
            yield _sse(event)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",  # proxies must not buffer or cache the stream
            "X-Accel-Buffering": "no",  # tells nginx specifically not to buffer
        },
    )


@router.post(
    "/approve",
    response_model=ChatResponse,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Approve the pending action and resume the agent",
)
async def approve(body: ApprovalRequest, manager: SessionManager = Depends(get_session_manager)) -> ChatResponse:
    turn = await manager.approve(body.session_id, body.action_id)
    return _to_response(turn)


@router.post(
    "/reject",
    response_model=ChatResponse,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Reject the pending action and resume the agent",
)
async def reject(body: ApprovalRequest, manager: SessionManager = Depends(get_session_manager)) -> ChatResponse:
    turn = await manager.reject(body.session_id, body.action_id)
    return _to_response(turn)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}", response_model=SessionHistoryResponse, responses={404: {"model": ErrorResponse}})
async def get_session(session_id: str, manager: SessionManager = Depends(get_session_manager)) -> SessionHistoryResponse:
    messages, pending = await manager.history(session_id)
    return SessionHistoryResponse(session_id=session_id, messages=messages, has_pending_action=pending)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: {"model": ErrorResponse}})
async def delete_session(session_id: str, manager: SessionManager = Depends(get_session_manager)) -> None:
    await manager.delete(session_id)


# ---------------------------------------------------------------------------
# Direct data access (bypasses the agent) -- handy for the frontend to render
# rich cards, and a good place to see the service layer used without an LLM.
# ---------------------------------------------------------------------------


@router.get("/books/{work_key}", responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
async def get_book(work_key: str):
    try:
        return await openlibrary.get_book_details(work_key)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.user_message) from exc
    except InvalidQuery as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.user_message) from exc
    except ExternalAPIUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, exc.user_message) from exc


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", llm_model=settings.llm_model, checkpoint_backend=settings.checkpoint_backend)
