"""
SessionManager -- the glue between FastAPI routes and the LangGraph agent.

WHY THIS LAYER EXISTS
---------------------
Routes should be thin: parse request -> call one method -> return response.
All the "agent plumbing" (thread configs, reading interrupts, resuming, pulling
the final message out of the state) lives here, so routes.py stays readable
and this logic is testable without HTTP.

KEY IDEA:  session_id  ==  LangGraph thread_id
           The checkpointer stores one conversation per thread_id. Reusing the
           id on every request is what gives the agent memory.

HITL LIFECYCLE (see docs/07):
    chat()    -> agent.ainvoke(new HumanMessage)
                 ├─ result has "__interrupt__"  -> return status="awaiting_approval"
                 └─ otherwise                   -> return status="completed"
    approve() -> agent.ainvoke(Command(resume={"decisions":[{"type":"approve"}]}))
    reject()  -> agent.ainvoke(Command(resume={"decisions":[{"type":"reject", ...}]}))
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt

from backend.config import settings
from backend.models.schemas import ChatMessage, PendingAction
from backend.tools import TOOLS_REQUIRING_APPROVAL

logger = logging.getLogger(__name__)


# ----- Errors the API layer translates into HTTP status codes --------------


class SessionError(Exception):
    """Base class for session-level problems."""


class SessionNotFound(SessionError):
    """No conversation exists for this session_id."""


class NoPendingAction(SessionError):
    """approve/reject called but the agent is not waiting for a decision."""


class ActionMismatch(SessionError):
    """approve/reject carried an action_id that is not the current pending one."""


class AgentUnavailable(SessionError):
    """The LLM call failed (bad key, rate limit, provider outage, ...)."""


# ----- Result object returned to routes -------------------------------------


@dataclass
class AgentTurn:
    session_id: str
    response: str
    pending_action: PendingAction | None = None
    handoff_url: str | None = None

    @property
    def status(self) -> str:
        return "awaiting_approval" if self.pending_action else "completed"

    def as_dict(self) -> dict[str, Any]:
        """Same fields as the ChatResponse schema -- used by the streaming `done` event."""
        return {
            "session_id": self.session_id,
            "status": self.status,
            "response": self.response,
            "pending_action": self.pending_action.model_dump() if self.pending_action else None,
            "handoff_url": self.handoff_url,
        }


# Friendly progress text shown while a tool runs (Bonus 6 - streaming).
TOOL_STATUS_LABELS = {
    "search_books": "Searching the catalogue…",
    "search_books_by_subject": "Browsing by subject…",
    "get_book_details": "Fetching book details…",
    "search_authors": "Looking up authors…",
    "get_author_details": "Fetching author details…",
    "proceed_to_borrow": "Preparing hand-off…",
}


# ----- The manager -----------------------------------------------------------


class SessionManager:
    def __init__(self, agent: CompiledStateGraph):
        self._agent = agent

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _config(session_id: str) -> dict:
        # `thread_id` is how LangGraph's checkpointer namespaces state.
        # `recursion_limit` caps model<->tools loops per invocation.
        return {
            "configurable": {"thread_id": session_id},
            "recursion_limit": settings.max_agent_steps * 2 + 1,
        }

    async def _pending_interrupt(self, session_id: str) -> Interrupt | None:
        """Return the live interrupt for this thread, if the graph is paused."""
        state = await self._agent.aget_state(self._config(session_id))
        return state.interrupts[0] if state.interrupts else None

    @staticmethod
    def _to_pending_action(interrupt: Interrupt) -> PendingAction:
        # Shape produced by HumanInTheLoopMiddleware:
        #   interrupt.value == {"action_requests": [{"name", "args", "description"}],
        #                       "review_configs":  [{"action_name", "allowed_decisions"}]}
        request = interrupt.value["action_requests"][0]
        return PendingAction(
            action_id=interrupt.id,
            tool_name=request["name"],
            arguments=request.get("args", {}),
            description=request.get("description", "Approval required."),
        )

    async def _run(self, session_id: str, payload) -> dict:
        """Invoke the graph, converting LLM/provider failures into AgentUnavailable."""
        try:
            return await self._agent.ainvoke(payload, config=self._config(session_id))
        except Exception as exc:  # noqa: BLE001
            # Tool errors never re(.venv) srisarveshr@IABLR-LT1351 sample_project % uvicorn backend.main:app --reload --port 8000
            #
            # INFO:     Will watch for changes in these directories: ['/Users/srisarveshr/prac-projects/ticket_master_discovery_agent/sample_project']
            # /Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/site-packages/langchain_core/utils/pydantic.py:41: UserWarning: Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.
            #   from pydantic.v1 import BaseModel as BaseModelV1
            # 2026-09-26 22:06:50,233 INFO     backend.main: Using operating-system certificate store for TLS
            # INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
            # INFO:     Started reloader process [55582] using WatchFiles
            # /Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/site-packages/langchain_core/utils/pydantic.py:41: UserWarning: Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.
            #   from pydantic.v1 import BaseModel as BaseModelV1
            # 2026-09-26 22:06:54,800 INFO     backend.main: Using operating-system certificate store for TLS
            # INFO:     Started server process [55617]
            # INFO:     Waiting for application startup.
            # 2026-09-26 22:06:55,765 INFO     backend.agent: Agent built with 6 tools (1 need approval): proceed_to_borrow
            # 2026-09-26 22:06:55,765 INFO     backend.main: Startup complete (checkpointer=memory)
            # ERROR:    Traceback (most recent call last):
            #   File "/Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/site-packages/starlette/routing.py", line 638, in lifespan
            #     async with self.lifespan_context(app) as maybe_state:
            #                ~~~~~~~~~~~~~~~~~~~~~^^^^^
            #   File "/Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/contextlib.py", line 214, in __aenter__
            #     return await anext(self.gen)
            #            ^^^^^^^^^^^^^^^^^^^^^
            #   File "/Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/site-packages/fastapi/routing.py", line 216, in merged_lifespan
            #     async with original_context(app) as maybe_original_state:
            #                ~~~~~~~~~~~~~~~~^^^^^
            #   File "/Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/contextlib.py", line 214, in __aenter__
            #     return await anext(self.gen)
            #            ^^^^^^^^^^^^^^^^^^^^^
            #   File "/Users/srisarveshr/prac-projects/ticket_master_discovery_agent/sample_project/backend/main.py", line 84, in lifespan
            #     print(agent.get_graph().draw_ascii())
            #           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
            #   File "/Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/site-packages/langchain_core/runnables/graph.py", line 516, in draw_ascii
            #     return draw_ascii(
            #         {node.id: node.name for node in self.nodes.values()},
            #         self.edges,
            #     )
            #   File "/Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/site-packages/langchain_core/runnables/graph_ascii.py", line 299, in draw_ascii
            #     sug = _build_sugiyama_layout(vertices, edges)
            #   File "/Users/srisarveshr/.pyenv/versions/3.14.5/lib/python3.14/site-packages/langchain_core/runnables/graph_ascii.py", line 206, in _build_sugiyama_layout
            #     raise ImportError(msg)
            # ImportError: Install grandalf to draw graphs: `pip install grandalf`.
            #
            # ERROR:    Application startup failed. Exiting.
            # ^CINFO:     Stopping reloader process [55582]ach here (tools return text). What does reach
            # here is the model provider failing: auth, quota, network, timeouts.
            logger.exception("Agent invocation failed for session %s", session_id)
            raise AgentUnavailable("The AI service is temporarily unavailable.") from exc

    def _build_turn(self, session_id: str, result: dict) -> AgentTurn:
        """Translate a raw graph result into what the API returns."""
        if "__interrupt__" in result and result["__interrupt__"]:
            pending = self._to_pending_action(result["__interrupt__"][0])
            # No AI text exists yet (the model asked for a tool and we paused),
            # so the human-readable description *is* the response.
            return AgentTurn(session_id=session_id, response=pending.description, pending_action=pending)

        messages = result.get("messages", [])
        final_text = next(
            (m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content),
            "I'm not sure how to respond to that.",
        )
        return AgentTurn(
            session_id=session_id,
            response=final_text if isinstance(final_text, str) else str(final_text),
            handoff_url=self._extract_handoff_url(messages),
        )

    @staticmethod
    def _extract_handoff_url(messages: list) -> str | None:
        """After an approved action, pull the URL out of the tool's ToolMessage."""
        for m in reversed(messages):
            if isinstance(m, ToolMessage) and m.name in TOOLS_REQUIRING_APPROVAL:
                try:
                    data = json.loads(m.content)
                except (TypeError, ValueError):
                    return None
                return data.get("url") if data.get("status") == "approved" else None
            if isinstance(m, HumanMessage):
                break  # only look at the current turn
        return None

    # -- public API ---------------------------------------------------------

    async def _settle_pending_before_new_message(self, session_id: str) -> None:
        # Edge case: the user typed a new message while an approval card was
        # showing. We treat that as "not approved", let the graph finish that
        # branch, then process the new message. (Alternative design: return
        # HTTP 409 and force the frontend to resolve the card first.)
        if await self._pending_interrupt(session_id):
            logger.info("Session %s had a pending action; auto-rejecting before new message", session_id)
            await self._run(
                session_id,
                Command(
                    resume={
                        "decisions": [
                            {"type": "reject", "message": "The user did not approve and continued the conversation."}
                        ]
                    }
                ),
            )

    async def chat(self, session_id: str, message: str) -> AgentTurn:
        await self._settle_pending_before_new_message(session_id)
        result = await self._run(session_id, {"messages": [HumanMessage(content=message)]})
        return self._build_turn(session_id, result)

    async def stream_chat(self, session_id: str, message: str) -> AsyncIterator[dict[str, Any]]:
        """
        Bonus 6 -- streaming. Same job as `chat()`, but yields progress events
        while the agent works instead of one answer at the end.

        Event types (each is a plain dict the route turns into an SSE frame):
            {"type": "status", "message": "Searching the catalogue…"}   a tool is about to run
            {"type": "token",  "content": "Here"}                        one piece of the answer
            {"type": "done",   ...ChatResponse fields...}                final, authoritative result
            {"type": "error",  "detail": "..."}                          something failed mid-stream

        HOW IT WORKS
        `agent.astream(..., stream_mode=["messages", "updates"])` yields two kinds of chunk:
          * "messages" -> (message_chunk, metadata). AIMessageChunk.content is a token of the
            model's text; AIMessageChunk.tool_call_chunks tells us a tool is being called;
            ToolMessage means a tool finished.
          * "updates"  -> {node_name: state_delta}. When the HITL middleware pauses the graph
            the delta is {"__interrupt__": (Interrupt,)} -- that is how we detect the pause.
        """
        await self._settle_pending_before_new_message(session_id)

        interrupt: Interrupt | None = None
        try:
            async for mode, chunk in self._agent.astream(
                {"messages": [HumanMessage(content=message)]},
                config=self._config(session_id),
                stream_mode=["messages", "updates"],
            ):
                if mode == "updates":
                    if "__interrupt__" in chunk:
                        interrupt = chunk["__interrupt__"][0]
                    continue

                msg, _meta = chunk
                if isinstance(msg, AIMessageChunk):
                    for tc in getattr(msg, "tool_call_chunks", None) or []:
                        if tc.get("name"):
                            yield {"type": "status", "message": TOOL_STATUS_LABELS.get(tc["name"], f"Running {tc['name']}…")}
                    if msg.content:
                        text = msg.content if isinstance(msg.content, str) else str(msg.content)
                        yield {"type": "token", "content": text}
        except Exception:  # noqa: BLE001
            logger.exception("Streaming failed for session %s", session_id)
            yield {"type": "error", "detail": "The AI service is temporarily unavailable."}
            return

        # The tokens were a live preview. Build the authoritative result from the
        # saved state so `done` carries exactly what /chat would have returned
        # (including pending_action and handoff_url).
        state = await self._agent.aget_state(self._config(session_id))
        result: dict[str, Any] = {"messages": state.values.get("messages", [])}
        if interrupt is not None:
            result["__interrupt__"] = [interrupt]
        turn = self._build_turn(session_id, result)
        yield {"type": "done", **turn.as_dict()}

    async def approve(self, session_id: str, action_id: str) -> AgentTurn:
        await self._assert_pending(session_id, action_id)
        result = await self._run(session_id, Command(resume={"decisions": [{"type": "approve"}]}))
        return self._build_turn(session_id, result)

    async def reject(self, session_id: str, action_id: str) -> AgentTurn:
        await self._assert_pending(session_id, action_id)
        result = await self._run(
            session_id,
            Command(
                resume={
                    "decisions": [
                        {
                            "type": "reject",
                            # This text becomes the ToolMessage the LLM reads,
                            # so phrase it as information for the model.
                            "message": "The user REJECTED this action. Do not proceed. Ask what they would like instead.",
                        }
                    ]
                }
            ),
        )
        return self._build_turn(session_id, result)

    async def _assert_pending(self, session_id: str, action_id: str) -> None:
        interrupt = await self._pending_interrupt(session_id)
        if interrupt is None:
            raise NoPendingAction("There is no action awaiting approval in this session.")
        if interrupt.id != action_id:
            raise ActionMismatch("That action is no longer pending. Please refresh the conversation.")

    async def history(self, session_id: str) -> tuple[list[ChatMessage], bool]:
        state = await self._agent.aget_state(self._config(session_id))
        if not state.values:
            raise SessionNotFound(f"Unknown session '{session_id}'.")
        chat: list[ChatMessage] = []
        for m in state.values.get("messages", []):
            if isinstance(m, HumanMessage):
                chat.append(ChatMessage(role="user", content=str(m.content)))
            elif isinstance(m, AIMessage) and m.content:
                # Skip AIMessages that only contain tool_calls -- internal chatter.
                chat.append(ChatMessage(role="assistant", content=str(m.content)))
        return chat, bool(state.interrupts)

    async def delete(self, session_id: str) -> None:
        checkpointer = self._agent.checkpointer
        if checkpointer is None:
            return
        state = await self._agent.aget_state(self._config(session_id))
        if not state.values:
            raise SessionNotFound(f"Unknown session '{session_id}'.")
        await checkpointer.adelete_thread(session_id)
