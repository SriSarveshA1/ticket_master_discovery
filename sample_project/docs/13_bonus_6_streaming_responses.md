# 13 — Bonus 6: Streaming Responses (Server-Sent Events)

**Files:** `backend/sessions/manager.py` (`stream_chat`), `backend/api/routes.py` (`/chat/stream`, `_sse`), `frontend/streamlit_app.py` (`_post_stream`), `tests/test_bonus_features.py`

## The problem, in plain words

`POST /chat` does this: receive message → agent thinks → calls tools → writes answer →
**then** send the whole answer. For a 3-tool question that is 10–20 seconds of a spinner.
ChatGPT-style products instead show words as the model produces them, and show "Searching…"
while tools run. That is streaming.

## Two pieces of new knowledge

### 1. What Server-Sent Events (SSE) are

SSE is *not* websockets and needs no library. It is an ordinary HTTP response that:

* has `Content-Type: text/event-stream`,
* never "finishes" until the server is done — the server keeps writing to the body,
* is plain text in a fixed shape: lines of `event:` and `data:` separated by a blank line.

```
event: status
data: {"type": "status", "message": "Searching the catalogue…"}

event: token
data: {"type": "token", "content": "I"}

event: token
data: {"type": "token", "content": " found"}

event: done
data: {"type": "done", "status": "completed", "response": "I found ...", "pending_action": null, "handoff_url": null}

```

Any client that can read an HTTP body line by line can consume it: browsers (`EventSource`
or `fetch` + reader), `requests` with `stream=True`, `curl -N`.

### 2. How to get tokens out of the agent

`agent.ainvoke()` returns the final state. `agent.astream()` yields **events while the graph
runs**. Which events depends on `stream_mode`:

| `stream_mode` | You get | We use it for |
|---|---|---|
| `"messages"` | `(message_chunk, metadata)` for every token / tool message | tokens, "tool is running" |
| `"updates"` | `{node_name: what_changed}` after each node | detecting the HITL pause (`"__interrupt__"`) |
| `"values"` | the whole state after each node | (not needed here) |

Pass a list to get several at once: `stream_mode=["messages", "updates"]` → each item is
`(mode, chunk)`.

## The backend, step by step — `SessionManager.stream_chat()`

```python
async def stream_chat(self, session_id, message):
    await self._settle_pending_before_new_message(session_id)   # same edge case as chat()

    interrupt = None
    async for mode, chunk in self._agent.astream(
        {"messages": [HumanMessage(content=message)]},
        config=self._config(session_id),
        stream_mode=["messages", "updates"],
    ):
        if mode == "updates":
            if "__interrupt__" in chunk:            # HITL middleware paused the graph
                interrupt = chunk["__interrupt__"][0]
            continue

        msg, _meta = chunk
        if isinstance(msg, AIMessageChunk):
            for tc in msg.tool_call_chunks or []:   # model decided to call a tool
                if tc.get("name"):
                    yield {"type": "status", "message": TOOL_STATUS_LABELS[tc["name"]]}
            if msg.content:                          # a piece of the answer text
                yield {"type": "token", "content": msg.content}

    # Tokens were a live preview. Build the authoritative result from saved state.
    state = await self._agent.aget_state(self._config(session_id))
    result = {"messages": state.values["messages"]}
    if interrupt: result["__interrupt__"] = [interrupt]
    turn = self._build_turn(session_id, result)     # SAME function /chat uses
    yield {"type": "done", **turn.as_dict()}
```

Three design decisions worth understanding:

1. **It is an `async` generator** (`yield` inside `async def`). Each `yield` hands one event
   to the route, which writes it to the HTTP body immediately. Nothing is buffered.
2. **`done` reuses `_build_turn`.** The final frame carries the exact JSON `/chat` returns —
   `status`, `response`, `pending_action`, `handoff_url`. The frontend therefore handles the
   HITL card identically whether it streamed or not. Tokens are cosmetic; `done` is truth.
3. **Errors become a frame, not a status code.** Once the first byte is sent the HTTP
   status is already `200`; you cannot change it. So mid-stream failures are yielded as
   `{"type": "error", "detail": "..."}` and the client shows that.

## The route — `POST /chat/stream`

```python
def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, manager = Depends(get_session_manager)):
    async def event_source():
        async for event in manager.stream_chat(body.session_id, body.message):
            yield _sse(event)
    return StreamingResponse(event_source(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

`StreamingResponse` takes any (async) iterator of strings/bytes and writes each item as it
is produced. The two headers stop proxies (and nginx specifically) from buffering the
stream into one big chunk, which would silently turn your streaming back into non-streaming
in production.

## The frontend — `_post_stream()` in Streamlit

```python
with requests.post(f"{API_URL}/chat/stream", json=payload, stream=True) as r:
    for line in r.iter_lines(decode_unicode=True):
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: "):])
        if event["type"] == "status": status_box.caption(f"⏳ {event['message']}")
        elif event["type"] == "token": text += event["content"]; placeholder.markdown(text + "▌")
        elif event["type"] == "error": st.error(event["detail"]); return None
        elif event["type"] == "done": placeholder.markdown(event["response"]); return event
```

* `stream=True` → `requests` gives you lines as they arrive instead of waiting for the end.
* `st.empty()` placeholders are Streamlit's way to redraw one spot repeatedly.
* The `done` event is passed to the *same* `_handle_agent_reply()` used by the non-streaming
  path, so Approve/Reject works unchanged.

The sidebar toggle **⚡ Stream responses** switches between `/chat` and `/chat/stream` so you
can feel the difference on the same question.

In a browser (React), the equivalent is:

```ts
const res = await fetch(`${API}/chat/stream`, {method: "POST", body: JSON.stringify(payload), headers: {...}});
const reader = res.body!.getReader(); const dec = new TextDecoder(); let buf = "";
while (true) {
  const {value, done} = await reader.read(); if (done) break;
  buf += dec.decode(value, {stream: true});
  let i; while ((i = buf.indexOf("\n\n")) >= 0) {      // one SSE frame
    const frame = buf.slice(0, i); buf = buf.slice(i + 2);
    const data = frame.split("\n").find(l => l.startsWith("data: "));
    if (data) handle(JSON.parse(data.slice(6)));
  }
}
```

(`EventSource` is simpler but only supports GET; we need POST for the body.)

## See it raw

```bash
curl -N -X POST localhost:8000/chat/stream \
     -H 'content-type: application/json' \
     -d '{"message":"find sci-fi classics","session_id":"demo"}'
```

`-N` disables curl's buffering. You will watch `status`, then `token` frames, then `done`
scroll past. That is the whole protocol.

## Streaming + Human-in-the-Loop

When the model calls `proceed_to_borrow`, the stream emits one `status` frame
("Preparing hand-off…"), the graph pauses, and the `done` frame arrives with
`status: "awaiting_approval"` and the `pending_action`. The frontend shows the card. Approve
is still a normal `POST /approve` — you do not stream the resume (though you could:
`agent.astream(Command(resume=...))` works the same way).
`test_stream_pauses_for_approval_and_can_be_approved` covers this path.

## Things beginners trip on

| Symptom | Cause | Fix |
|---|---|---|
| Everything arrives at once at the end | a proxy or `curl` without `-N` buffered it | add the two headers; use `curl -N`; in Streamlit use `stream=True` |
| Tokens for *both* "Let me search…" and the final answer get concatenated | some models emit text before a tool call | we take `response` from saved state in `done`, not from concatenated tokens — so the stored answer is right even if the preview looked odd |
| Status code is 200 but the body says error | by design — see decision 3 above | handle the `error` frame |
| Fake LLM raises "No generations found in stream" | fake model could not stream tool calls | see `ScriptedLLM._stream` in `tests/fake_llm.py` |

## Exercises

1. Add a `tool_result` event when a `ToolMessage` arrives in `"messages"` mode
   (`isinstance(msg, ToolMessage)` → `msg.name`). Show "✓ Searched the catalogue" in the UI.
2. Stream the *approve* step: add `POST /approve/stream` that runs
   `agent.astream(Command(resume=...))` through the same event loop.
3. Time both paths on the same 2-tool question with your phone's stopwatch: total time is
   identical, but when does the *first* word appear? That difference is why streaming exists.
