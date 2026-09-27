"""
Tests for Bonus 5 (persistent conversation) and Bonus 6 (streaming responses).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import create_app
from tests.fake_llm import ai_text, ai_tool_call, replies

SESSION = {"session_id": "bonus-session"}


# ---------------------------------------------------------------------------
# Bonus 6 -- streaming (Server-Sent Events)
# ---------------------------------------------------------------------------


def _read_sse(response) -> list[dict]:
    """Parse an SSE body into a list of event dicts (what a browser's EventSource does)."""
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_stream_emits_status_tokens_then_done(make_client):
    client = make_client(
        ai_tool_call("search_books", {"query": "sci-fi"}, "c1"),
        ai_text("I found Dune and Foundation."),
    )
    with client.stream("POST", "/chat/stream", json={"message": "find sci-fi", **SESSION}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _read_sse(r)

    types = [e["type"] for e in events]
    assert types[0] == "status"  # tool about to run
    assert events[0]["message"] == "Searching the catalogue…"
    assert types.count("token") > 1  # answer arrived in pieces
    assert types[-1] == "done"  # exactly one final frame

    streamed_text = "".join(e["content"] for e in events if e["type"] == "token")
    done = events[-1]
    assert streamed_text == done["response"] == "I found Dune and Foundation."
    assert done["status"] == "completed"
    assert done["session_id"] == SESSION["session_id"]

    # The streamed turn is in memory like any other: a normal /chat follow-up sees it.
    assert client.get(f"/sessions/{SESSION['session_id']}").json()["messages"][-1]["content"] == done["response"]


def test_stream_pauses_for_approval_and_can_be_approved(make_client):
    client = make_client(
        ai_tool_call("proceed_to_borrow", {"work_key": "OL893414W", "title": "Dune", "author": "Frank Herbert"}, "c1"),
        ai_text("You approved Dune. Continue here: https://openlibrary.org/works/OL893414W"),
    )
    with client.stream("POST", "/chat/stream", json={"message": "get me Dune", **SESSION}) as r:
        events = _read_sse(r)

    done = events[-1]
    assert done["type"] == "done"
    assert done["status"] == "awaiting_approval"
    assert done["pending_action"]["tool_name"] == "proceed_to_borrow"
    assert done["handoff_url"] is None  # still hidden until approved

    # HITL works identically after a streamed turn.
    r = client.post("/approve", json={**SESSION, "action_id": done["pending_action"]["action_id"]})
    assert r.status_code == 200
    assert r.json()["handoff_url"] == "https://openlibrary.org/works/OL893414W"


# ---------------------------------------------------------------------------
# Bonus 5 -- persistent conversation (SQLite checkpointer)
# ---------------------------------------------------------------------------


def test_history_survives_backend_restart(tmp_path, monkeypatch, patched_openlibrary):
    pytest.importorskip("langgraph.checkpoint.sqlite", reason="pip install langgraph-checkpoint-sqlite")

    db_file = tmp_path / "sessions.db"
    monkeypatch.setattr(settings, "checkpoint_backend", "sqlite")
    monkeypatch.setattr(settings, "sqlite_path", str(db_file))

    # --- "process 1": chat, then shut the app down (TestClient exit == uvicorn stop)
    with TestClient(create_app(model=replies([ai_text("Hello! Ask me about books.")]))) as client:
        r = client.post("/chat", json={"message": "hi", "session_id": "durable"})
        assert r.json()["response"] == "Hello! Ask me about books."
    assert db_file.exists()

    # --- "process 2": brand-new app, same DB file -> history is still there
    with TestClient(create_app(model=replies([ai_text("Still here.")]))) as client:
        history = client.get("/sessions/durable").json()
        assert [m["content"] for m in history["messages"]] == ["hi", "Hello! Ask me about books."]

        # And the conversation simply continues.
        r = client.post("/chat", json={"message": "are you still there?", "session_id": "durable"})
        assert r.json()["response"] == "Still here."
        assert len(client.get("/sessions/durable").json()["messages"]) == 4

        # Delete works on the sqlite backend too.
        assert client.delete("/sessions/durable").status_code == 204
        assert client.get("/sessions/durable").status_code == 404


def test_pending_approval_survives_backend_restart(tmp_path, monkeypatch, patched_openlibrary):
    """The strongest demo of persistence: pause for approval, restart, then approve."""
    pytest.importorskip("langgraph.checkpoint.sqlite")

    monkeypatch.setattr(settings, "checkpoint_backend", "sqlite")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions.db"))
    action = {"work_key": "OL893414W", "title": "Dune", "author": "Frank Herbert"}

    with TestClient(create_app(model=replies([ai_tool_call("proceed_to_borrow", action, "c1")]))) as client:
        body = client.post("/chat", json={"message": "get Dune", "session_id": "durable"}).json()
        assert body["status"] == "awaiting_approval"
        action_id = body["pending_action"]["action_id"]

    # restart
    with TestClient(create_app(model=replies([ai_text("Approved! https://openlibrary.org/works/OL893414W")]))) as client:
        assert client.get("/sessions/durable").json()["has_pending_action"] is True
        r = client.post("/approve", json={"session_id": "durable", "action_id": action_id})
        assert r.status_code == 200
        assert r.json()["handoff_url"] == "https://openlibrary.org/works/OL893414W"
