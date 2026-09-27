"""
Streamlit frontend.

Run:  streamlit run frontend/streamlit_app.py
(Backend must be running on http://localhost:8000)

CONCEPT: The frontend knows NOTHING about LangChain. It only speaks HTTP to
FastAPI, exactly like a React app would. The AI agent never lives in the UI.

Three things the UI has to do:
  1. keep a stable `session_id` for the whole conversation (= agent memory),
  2. render `response` for status == "completed",
  3. render Approve / Reject buttons for status == "awaiting_approval" and
     call /approve or /reject with the `action_id`.
"""

from __future__ import annotations

import json
import os
import uuid

import requests
import streamlit as st

API_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="BookBuddy", page_icon="📚", layout="centered")
st.title("📚 BookBuddy — AI Book Discovery")


# ---------------------------------------------------------------------------
# Session state (Streamlit re-runs this file on every interaction, so anything
# that must persist across clicks goes into st.session_state).
# ---------------------------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = f"ui-{uuid.uuid4().hex[:10]}"
if "messages" not in st.session_state:
    st.session_state.messages = []  # list[{"role": "user"|"assistant", "content": str}]
if "pending_action" not in st.session_state:
    st.session_state.pending_action = None  # dict from the backend or None


# ---------------------------------------------------------------------------
# HTTP helpers -- all backend communication goes through these two functions
# ---------------------------------------------------------------------------
def _post(path: str, payload: dict) -> dict | None:
    try:
        r = requests.post(f"{API_URL}{path}", json=payload, timeout=90)
    except requests.RequestException:
        st.error("Cannot reach the backend. Is FastAPI running on port 8000?")
        return None
    if r.status_code >= 400:
        # Backend always returns {"detail": "<friendly message>"} on errors.
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        st.error(f"{r.status_code}: {detail}")
        return None
    return r.json()


def _handle_agent_reply(data: dict) -> None:
    st.session_state.messages.append({"role": "assistant", "content": data["response"]})
    st.session_state.pending_action = data.get("pending_action") if data["status"] == "awaiting_approval" else None
    if data.get("handoff_url"):
        st.session_state.messages.append(
            {"role": "assistant", "content": f"🔗 Continue on Open Library: {data['handoff_url']}"}
        )


def _post_stream(payload: dict) -> dict | None:
    """
    Bonus 6 -- consume the Server-Sent Events stream from POST /chat/stream.

    `stream=True` makes `requests` hand us the body line by line as it arrives
    instead of waiting for the whole response. Each SSE frame is:

        event: token
        data: {"type": "token", "content": "Hello"}
        <blank line>

    We only need the `data:` lines. Tokens are drawn into a placeholder as they
    arrive; the final `done` frame carries the same JSON that /chat returns and
    is what we actually store.
    """
    placeholder = st.empty()
    status_box = st.empty()
    text = ""
    try:
        with requests.post(f"{API_URL}/chat/stream", json=payload, stream=True, timeout=120) as r:
            if r.status_code >= 400:
                st.error(f"{r.status_code}: {r.json().get('detail', r.text)}")
                return None
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue  # skip `event:` lines and blank separators
                event = json.loads(line[len("data: "):])
                if event["type"] == "status":
                    status_box.caption(f"⏳ {event['message']}")
                elif event["type"] == "token":
                    text += event["content"]
                    placeholder.markdown(text + "▌")  # cursor effect
                elif event["type"] == "error":
                    st.error(event["detail"])
                    return None
                elif event["type"] == "done":
                    status_box.empty()
                    placeholder.markdown(event["response"])
                    return event
    except requests.RequestException:
        st.error("Cannot reach the backend. Is FastAPI running on port 8000?")
    return None


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.caption(f"Session: `{st.session_state.session_id}`")
    # Bonus 6: same conversation, two transport styles. Flip it and compare.
    use_streaming = st.toggle("⚡ Stream responses", value=True, help="Uses POST /chat/stream (Server-Sent Events)")
    if st.button("🆕 New conversation"):
        requests.delete(f"{API_URL}/sessions/{st.session_state.session_id}", timeout=10)
        for key in ("session_id", "messages", "pending_action"):
            del st.session_state[key]
        st.rerun()
    try:
        health = requests.get(f"{API_URL}/health", timeout=5).json()
        st.success(f"Backend OK · {health['llm_model']}")
    except requests.RequestException:
        st.error("Backend offline")
    st.markdown("**Try:**")
    st.markdown(
        "- Find sci-fi classics\n"
        "- Only ones published before 1970\n"
        "- Tell me more about number 2\n"
        "- Who is the author?\n"
        "- I want to read the first one"
    )

# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# Human-in-the-Loop card
# ---------------------------------------------------------------------------
pending = st.session_state.pending_action
if pending:
    with st.container(border=True):
        st.warning("**Approval required**")
        st.text(pending["description"])
        col_a, col_r = st.columns(2)
        payload = {"session_id": st.session_state.session_id, "action_id": pending["action_id"]}
        if col_a.button("✅ Approve", use_container_width=True):
            data = _post("/approve", payload)
            if data:
                st.session_state.messages.append({"role": "user", "content": "✅ Approved"})
                _handle_agent_reply(data)
                st.rerun()
        if col_r.button("❌ Reject", use_container_width=True):
            data = _post("/reject", payload)
            if data:
                st.session_state.messages.append({"role": "user", "content": "❌ Rejected"})
                _handle_agent_reply(data)
                st.rerun()

# ---------------------------------------------------------------------------
# Input box
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Ask about books or authors..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    payload = {"message": prompt, "session_id": st.session_state.session_id}
    with st.chat_message("assistant"):
        if use_streaming:
            data = _post_stream(payload)  # tokens appear live
        else:
            with st.spinner("Thinking..."):
                data = _post("/chat", payload)  # one answer at the end
    if data:
        _handle_agent_reply(data)
    st.rerun()
