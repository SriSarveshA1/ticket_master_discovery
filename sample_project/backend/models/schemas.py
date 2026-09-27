"""
Request / response schemas.

CONCEPT: FastAPI + Pydantic give you validation, docs and serialisation from a
single class definition. A request body that fails validation is rejected with
HTTP 422 *before* your code runs -- one whole category of errors handled for
free. Everything the frontend needs to know about the API is in this file.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# /chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, examples=["Find me some sci-fi novels"])
    # If the frontend doesn't supply one we mint a new session.
    session_id: str = Field(default_factory=lambda: f"session-{uuid.uuid4().hex[:12]}", max_length=128)


class PendingAction(BaseModel):
    """Describes a tool call the agent wants to run but is waiting for approval on."""

    action_id: str
    tool_name: str
    arguments: dict
    description: str  # human-readable "You selected: ..." text


class ChatResponse(BaseModel):
    session_id: str
    # "completed"         -> `response` is the agent's final answer
    # "awaiting_approval" -> agent paused; frontend must show Approve / Reject
    status: Literal["completed", "awaiting_approval"]
    response: str
    pending_action: PendingAction | None = None
    # Populated only after an approved hand-off (the assignment's "booking URL").
    handoff_url: str | None = None


# ---------------------------------------------------------------------------
# /approve  and  /reject
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    session_id: str = Field(..., max_length=128)
    # Echo back the id we sent in PendingAction. Protects against approving a
    # stale action after the conversation moved on.
    action_id: str = Field(..., max_length=128)


# ---------------------------------------------------------------------------
# /sessions/{id}
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    has_pending_action: bool


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"]
    llm_model: str
    checkpoint_backend: str


class ErrorResponse(BaseModel):
    detail: str
