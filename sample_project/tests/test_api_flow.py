"""
End-to-end tests of the "Mandatory Test Scenario" from the assignment, adapted
to books, running fully offline.

    1. user query -> 3. FastAPI -> 4. agent calls tool -> 6. multiple results
    7. follow-up -> 8. context understood
    9. user chooses -> 10. agent pauses (HITL) -> 12/13. approve -> 14. resume -> 15. URL
"""

from __future__ import annotations

from tests.fake_llm import ai_text, ai_tool_call

SESSION = {"session_id": "test-session-1"}


def test_search_then_followup_then_approve(make_client):
    client = make_client(
        # Turn 1: model decides to call search_books, then summarises.
        ai_tool_call("search_books", {"query": "sci-fi classics"}, "c1"),
        ai_text("I found:\n1. **Dune** by Frank Herbert (1965)\n2. **Foundation** by Isaac Asimov (1951)"),
        # Turn 2: follow-up "tell me about number 2" -> get_book_details, then answer.
        ai_tool_call("get_book_details", {"work_key": "OL46125W"}, "c2"),
        ai_text("Foundation: a galactic empire is falling..."),
        # Turn 3: "I'll take it" -> proceed_to_borrow (interrupts), then final message after approval.
        ai_tool_call(
            "proceed_to_borrow",
            {"work_key": "OL46125W", "title": "Foundation", "author": "Isaac Asimov", "year": 1951},
            "c3",
        ),
        ai_text("You approved Foundation. Continue on Open Library: https://openlibrary.org/works/OL46125W"),
    )

    # --- turn 1: search --------------------------------------------------
    r = client.post("/chat", json={"message": "Find sci-fi classics", **SESSION})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert "Dune" in body["response"] and "Foundation" in body["response"]

    # --- turn 2: conversational follow-up -------------------------------
    r = client.post("/chat", json={"message": "Tell me more about number 2", **SESSION})
    assert r.status_code == 200
    assert "Foundation" in r.json()["response"]

    # --- turn 3: user selects -> HITL pause ------------------------------
    r = client.post("/chat", json={"message": "I want to read it", **SESSION})
    body = r.json()
    assert body["status"] == "awaiting_approval"
    assert body["pending_action"]["tool_name"] == "proceed_to_borrow"
    assert "Foundation" in body["response"]  # confirmation card shows the selection
    assert body["handoff_url"] is None  # URL must NOT be revealed before approval
    action_id = body["pending_action"]["action_id"]

    # Session history reflects the pause.
    h = client.get(f"/sessions/{SESSION['session_id']}").json()
    assert h["has_pending_action"] is True
    assert h["messages"][0] == {"role": "user", "content": "Find sci-fi classics"}

    # --- approve -> agent resumes -> URL ----------------------------------
    r = client.post("/approve", json={**SESSION, "action_id": action_id})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["handoff_url"] == "https://openlibrary.org/works/OL46125W"

    # Nothing pending any more.
    r = client.post("/approve", json={**SESSION, "action_id": action_id})
    assert r.status_code == 409


def test_reject_does_not_reveal_url(make_client):
    client = make_client(
        ai_tool_call("proceed_to_borrow", {"work_key": "OL893414W", "title": "Dune", "author": "Frank Herbert"}, "c1"),
        ai_text("No problem, I won't proceed. Want to keep browsing?"),
    )
    r = client.post("/chat", json={"message": "Get me Dune", **SESSION})
    body = r.json()
    assert body["status"] == "awaiting_approval"

    r = client.post("/reject", json={**SESSION, "action_id": body["pending_action"]["action_id"]})
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "completed"
    assert body["handoff_url"] is None
    assert "won't proceed" in body["response"]


def test_wrong_action_id_is_rejected(make_client):
    client = make_client(
        ai_tool_call("proceed_to_borrow", {"work_key": "OL893414W", "title": "Dune", "author": "Frank Herbert"}, "c1"),
        ai_text("unused"),
    )
    client.post("/chat", json={"message": "Get me Dune", **SESSION})
    r = client.post("/approve", json={**SESSION, "action_id": "stale-id"})
    assert r.status_code == 409


def test_new_message_while_pending_auto_rejects(make_client):
    client = make_client(
        ai_tool_call("proceed_to_borrow", {"work_key": "OL893414W", "title": "Dune", "author": "Frank Herbert"}, "c1"),
        ai_text("Okay, cancelled."),  # reply to the auto-reject
        ai_text("Sure, here are some fantasy books..."),  # reply to the new message
    )
    client.post("/chat", json={"message": "Get me Dune", **SESSION})
    r = client.post("/chat", json={"message": "Actually, show me fantasy instead", **SESSION})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert "fantasy" in r.json()["response"]


def test_validation_errors_are_friendly(make_client):
    client = make_client()
    r = client.post("/chat", json={"message": "", "session_id": "x"})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Invalid message")


def test_unknown_session_404(make_client):
    client = make_client()
    assert client.get("/sessions/nope").status_code == 404
    assert client.delete("/sessions/nope").status_code == 404


def test_health(make_client):
    client = make_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
