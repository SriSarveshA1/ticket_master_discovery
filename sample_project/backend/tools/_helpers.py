"""
Shared helpers for tool modules.

CONCEPT: A tool's return value goes straight back into the LLM's context as a
`ToolMessage`. Two rules follow:

  1. Return TEXT (JSON string is ideal: compact, unambiguous, easy for the model
     to read fields from).
  2. NEVER let an exception escape a tool if you can help it. If the tool
     raises, the whole agent run fails and the user sees a 500. If instead the
     tool returns `{"error": "..."}`, the LLM reads the error and explains it
     to the user in natural language ("No events found for that city").
     This is how "graceful error handling" reaches the conversation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from backend.services.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)


def to_json(data: Any) -> str:
    """Serialise pydantic models / lists / dicts into a compact JSON string."""
    if isinstance(data, BaseModel):
        data = data.model_dump(exclude_none=True)
    elif isinstance(data, Sequence) and not isinstance(data, str):
        data = [d.model_dump(exclude_none=True) if isinstance(d, BaseModel) else d for d in data]
    return json.dumps(data, ensure_ascii=False)


def error_payload(exc: Exception, *, context: str = "") -> str:
    """Turn an exception into an LLM-readable error message (never a traceback)."""
    if isinstance(exc, ExternalAPIError):
        # The user/LLM only sees user_message. The cause (TLS, timeout, HTTP
        # status) stays in the server log so "catalogue unavailable" is debuggable.
        logger.warning("Tool %s failed: %s (cause: %s)", context, exc, exc.__cause__)
        message = exc.user_message
    else:
        # Log the real thing for developers; hide it from the model/user.
        logger.exception("Unexpected error in tool %s", context)
        message = "Something went wrong while fetching data. Please try again."
    return json.dumps({"error": message, "context": context})


def no_results(what: str) -> str:
    return json.dumps({"results": [], "message": f"No {what} found. Try different keywords or filters."})
