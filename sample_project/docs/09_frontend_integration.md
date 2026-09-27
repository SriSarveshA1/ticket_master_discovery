# 09 — Connecting a Frontend to the AI Backend

**File:** `frontend/streamlit_app.py`

## The contract

The frontend imports `requests` and `streamlit`. It does **not** import LangChain, does not
know what a tool is, and never sees an API key. Its whole world is:

```
POST /chat     {message, session_id}            -> ChatResponse
POST /approve  {session_id, action_id}          -> ChatResponse
POST /reject   {session_id, action_id}          -> ChatResponse
GET  /health
DELETE /sessions/{id}
```

This is the assignment's "Frontend → HTTP → FastAPI → Agent" rule. If you were writing React
instead, the same five calls are all you need.

## Three responsibilities

### 1. Keep a stable `session_id`

```python
if "session_id" not in st.session_state:
    st.session_state.session_id = f"ui-{uuid.uuid4().hex[:10]}"
```

Generated once per browser session, sent with every request. This is the agent's memory
key (doc 06). "New conversation" = `DELETE /sessions/{id}` + generate a new id.

### 2. Render by `status`

```python
data = _post("/chat", {"message": prompt, "session_id": st.session_state.session_id})
st.session_state.messages.append({"role": "assistant", "content": data["response"]})
st.session_state.pending_action = data["pending_action"] if data["status"] == "awaiting_approval" else None
```

`completed` → just show `response`.
`awaiting_approval` → show `response` (the "You selected:" card text) **and** remember
`pending_action` so the buttons can be drawn.

### 3. The approval card

```python
if pending := st.session_state.pending_action:
    st.text(pending["description"])
    payload = {"session_id": ..., "action_id": pending["action_id"]}
    if st.button("✅ Approve"): data = _post("/approve", payload); ...
    if st.button("❌ Reject"):  data = _post("/reject", payload); ...
```

After either call the backend returns a normal `ChatResponse`; we append it and clear
`pending_action`. If `handoff_url` is present we render it as a link. **The frontend never
constructs the URL itself** — it only displays what the backend released after approval.

## Error display

Every backend error is `{"detail": "..."}` (doc 08), so one helper covers all of them:

```python
if r.status_code >= 400:
    st.error(f"{r.status_code}: {r.json()['detail']}")
```

Plus a `requests.RequestException` catch for "backend not running".

## Streamlit-specific gotchas

* Streamlit re-runs the whole script on every click. Anything that must persist goes in
  `st.session_state`. That is why `messages` and `pending_action` live there.
* After mutating state from a button handler, call `st.rerun()` so the page redraws with
  the new messages.
* Long agent calls: use `with st.spinner(...)` and a generous `timeout=` on `requests.post`
  (an agent doing 3 tool calls can take 10–20 s).

## Same thing in React (sketch)

```ts
const [sessionId] = useState(() => crypto.randomUUID());
const [pending, setPending] = useState<PendingAction | null>(null);

async function send(message: string) {
  const r = await fetch(`${API}/chat`, {method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({message, session_id: sessionId})});
  const data = await r.json();
  push({role: "assistant", content: data.response});
  setPending(data.status === "awaiting_approval" ? data.pending_action : null);
}
async function decide(path: "/approve" | "/reject") {
  const r = await fetch(`${API}${path}`, {..., body: JSON.stringify({session_id: sessionId, action_id: pending!.action_id})});
  ...
}
```

Identical shape. CORS in `main.py` must list your React dev origin (`http://localhost:3000`).

## Exercises

1. Show `cover_url` from the pending action's arguments… wait, it is not there. Add
   `cover_url` as an argument to `proceed_to_borrow`, update `describe_action`, and render
   the image on the card with `st.image`. (Bonus 1 analogue.)
2. Add a sidebar toggle that calls `GET /sessions/{id}` and shows `has_pending_action`.
3. Break the contract on purpose: rename `pending_action` to `pending` in `schemas.py` only.
   What breaks, and how quickly did you find it? This is why the schema file is the contract.
