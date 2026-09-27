# 15 — Sessions: What They Are and Why This App Needs Them

**Files:** `backend/models/schemas.py` (`session_id`), `backend/sessions/manager.py`, `backend/agent.py` (checkpointer), `backend/api/routes.py` (`/sessions/{id}`), `frontend/streamlit_app.py`

Read this after [06 — Conversation memory](06_conversation_memory.md). Doc 06 explains *how* history is stored. This doc explains *what a session is*, why HTTP forces us to have one, and what breaks if you get it wrong.

---

## The one-sentence version

A **session** is one conversation. Its name is a string called `session_id`. Every request that belongs to that conversation sends the same string, and the backend  guses it as the key to load and save that conversation's memory.

There is **one agent** for the whole app. There are **as many sessions as there are conversations**. The session id is how the agent knows which conversation you are continuing.

---



## Why a session exists at all

Three facts collide:

1. **The model has no memory of its own.** Each call to the LLM starts blank. "Tell me more about number 2" only makes sense if we send the earlier turns along with it.
2. **HTTP has no memory either.** `POST /chat` is a fresh request every time. FastAPI does not remember the previous request unless *we* store something and look it up.
3. **Many people use the app at once.** User A searching for sci-fi and user B searching for cookbooks must not see each other's books, tool results, or pending approvals.

A session is the box that holds one conversation so all three are solved:

```
request arrives with session_id = "ui-3f9a"
        │
        ▼
look up the box labelled "ui-3f9a"
        │
        ├── previous messages          (so "number 2" means something)
        ├── tool results from before   (so the model still has the work_keys)
        └── a paused approval, if any  (so Approve resumes THIS conversation)
        │
        ▼
run the agent, then put the updated box back under "ui-3f9a"
```

A different `session_id` is a different box. Same agent, separate boxes.

---



## Two names for the same string

You will see both words in the code. They are the same value, used by different libraries.


| Name         | Who uses it              | Where                                         |
| ------------ | ------------------------ | --------------------------------------------- |
| `session_id` | our API and frontend     | JSON body, URL `/sessions/{session_id}`       |
| `thread_id`  | LangGraph's checkpointer | `{"configurable": {"thread_id": session_id}}` |


The translation is one function in `backend/sessions/manager.py`:

```python
def _config(session_id: str) -> dict:
    return {
        "configurable": {"thread_id": session_id},
        "recursion_limit": ...,
    }
```

Every `ainvoke`, `astream`, and `aget_state` call passes this config. The checkpointer then loads and saves state **only for that thread**. That is the whole mechanism. There is no sessions database table of our own in the memory backend — the checkpointer *is* the session store. With `CHECKPOINT_BACKEND=sqlite` the same threads are rows in `sessions.db` (see [doc 12](12_bonus_5_persistent_conversation.md)).

---



## What lives inside one session

After a few turns, `agent.aget_state(config)` for that id returns roughly:


| Piece                          | Example                                                     | Why it matters                           |
| ------------------------------ | ----------------------------------------------------------- | ---------------------------------------- |
| Message list                   | Human, AI tool-call, ToolMessage JSON, AI text, next Human… | This *is* the conversational context     |
| Tool outputs still in the list | `[{"work_key":"OL893414W","title":"Dune"}, …]`              | "Number 2" can be turned back into an id |
| `next`                         | `()` if idle, or the HITL node if paused                    | Tells us whether the graph is waiting    |
| `interrupts`                   | the pending `proceed_to_borrow` call, with its `action_id`  | Approve/Reject resumes this exact pause  |


`GET /sessions/{session_id}` does **not** return all of that. `SessionManager.history()` keeps only user and assistant text, plus a boolean `has_pending_action`. Tool calls stay internal. The UI does not need them; the model does, and it still has them in the checkpoint.

---



## The life of a session

```
created          first POST /chat that uses this id
                 (frontend usually invents the id before the first message)
reused           every later /chat, /chat/stream, /approve, /reject with the same id
inspected        GET  /sessions/{id}     -> messages + has_pending_action
wiped            DELETE /sessions/{id}   -> checkpointer.adelete_thread(id)
unknown          GET or DELETE of an id that was never used -> 404
```

Who invents the id?

- **The Streamlit app** generates `ui-<10 hex chars>` once and stores it in `st.session_state.session_id`. It sends that on every call. "New conversation" deletes the backend thread and drops the local id so a new one is generated.
- **The API** will invent one if the client omits it: `ChatRequest.session_id` defaults to `session-<12 hex chars>`. The response always echoes `session_id`. If the client threw that away, the next message starts a **new empty** conversation. The client must keep the id it got back.

For the assignment, generate the id in the frontend and reuse it. That is the reliable pattern.

---



## Why this matters for each feature



### Follow-up questions (Core Requirement 4)

"Only show me ones under £100" and "tell me more about the second one" work because the **same session** still holds the previous list. A new `session_id` makes the agent answer as if it had never spoken to you. The model is not "remembering the user". It is being shown the messages stored under that id.

### Human-in-the-Loop (Core Requirement 7)

The pause is not a flag in the frontend. The graph is frozen **inside that session's checkpoint**, mid-run, holding the tool call it wants to make. `POST /approve` must send:

- the same `session_id` (which frozen graph), and
- the `action_id` from `pending_action` (which interrupt — so a stale button cannot approve the wrong thing).

Approve on a different session id returns 409: *"There is no action awaiting approval in this session."*

### Many users, one process

`main.py` builds **one** agent and **one** `SessionManager` at startup and shares them. That is correct. Isolation does not come from one agent per user (that would be slow and wasteful). It comes from a different `thread_id` per conversation. Two browser tabs with two ids never read each other's checkpoints.

### Restarting the backend


| `CHECKPOINT_BACKEND` | What happens to sessions                                                                                                     |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `memory` (default)   | The boxes live in a Python dict. Stop uvicorn and every session is gone.                                                     |
| `sqlite`             | The boxes live in `sessions.db`. Restart and `GET /sessions/{id}` still works, and a pending approval can still be approved. |


The session *concept* is identical. Only the shelf the boxes sit on changes.

---



## Do not confuse these three "sessions"

This is the mistake that costs the most time.


| Thing                              | Where it lives                                         | Lost when                                                   | What it is for                                                         |
| ---------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------- | ---------------------------------------------------------------------- |
| `session_id` **/** `thread_id`     | backend checkpointer                                   | process restart, unless sqlite                              | the agent's real memory                                                |
| `st.session_state`                 | the Streamlit process, in the browser tab's script run | you refresh the Streamlit page, or click "New conversation" | remembering the id, the messages drawn on screen, and the Approve card |
| **An HTTP cookie / login session** | not used here                                          | —                                                           | we have no accounts and no cookies                                     |


The chat bubbles you see are a **copy** kept in `st.session_state.messages` so Streamlit can redraw them. The source of truth is the backend checkpoint. That is why `GET /sessions/{id}` can rebuild the transcript even if the UI forgot it.

Practical consequence: refresh the Streamlit page and you get a **new** `ui-…` id, so the agent looks like it forgot you — even if the old thread is still in memory or sqlite. The old conversation is still there under the old id. The sidebar shows the current id; `curl localhost:8000/sessions/<old-id>` still returns the old history until you delete it or restart a memory backend.

---



## What a session is not

- **Not a user account.** There is no login. Anyone who knows the id can continue that conversation and approve a pending action. The ids are long random hex so they are hard to guess. Do not use `user-1`, `user-2`. For a class project this is enough; a real product would tie the id to an authenticated user.
- **Not the chat UI.** The frontend can be thrown away and rebuilt. `curl` with the same `session_id` continues the same conversation.
- **Not stored inside the LLM.** Switching `LLM_MODEL` does not move or delete sessions. The next reply is just produced by a different model, still reading the same history.
- **Not one session per tool call.** A search, a follow-up, and an approval are three HTTP requests and one session.

---



## A concrete trace

```
POST /chat
{ "message": "Find sci-fi classics", "session_id": "ui-abc" }
→ checkpoint "ui-abc" was empty, so this is turn 1
→ response lists 1. Dune  2. Foundation, and echoes "session_id": "ui-abc"

POST /chat
{ "message": "Tell me more about number 2", "session_id": "ui-abc" }
→ checkpoint "ui-abc" is loaded first
→ model sees its own numbered list + the ToolMessage that contained OL46125W
→ it calls get_book_details("OL46125W")

POST /chat
{ "message": "Tell me more about number 2", "session_id": "ui-OTHER" }
→ different box, empty history
→ model has no "number 2" and will ask what you mean, or search again
```

Same words, different session, different behaviour. The session id is the difference.

---



## How this maps to the assignment

The assignment's example request already has the field:

```json
{ "message": "Find concerts in London this weekend", "session_id": "user-123" }
```

Use a random id, not a guessable one, but the idea is identical. For Ticketmaster:

- one `session_id` per browser conversation,
- send it on `/chat`, `/approve`, and `/reject`,
- that is what makes "what about Manchester?" and "book the second one" work,
- `GET /sessions/{id}` and `DELETE /sessions/{id}` are the optional endpoints in the suggested API design.

You do not need a `Session` SQL table for the core assignment. `InMemorySaver` plus `thread_id` is a complete session system. SQLite (Bonus 5) is the same system with a file underneath.

---



## Try it

Backend running on port 8000:

```bash
# conversation A
curl -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message":"Find books about dune","session_id":"learn-a"}' | jq .response

# conversation B does not know about A
curl -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message":"What was the first book you found?","session_id":"learn-b"}' | jq .response

# A still does
curl -s localhost:8000/sessions/learn-a | jq .messages

# wipe A
curl -s -X DELETE localhost:8000/sessions/learn-a -o /dev/null -w "%{http_code}\n"
curl -s localhost:8000/sessions/learn-a -w "\n%{http_code}\n"
```

The last call is 404. The box is gone.

---



## Exercises

1. In two browser tabs, confirm the sidebar shows two different `ui-…` ids. Search in one. Ask "what about the first one?" in the other. Explain the answer using the table in "Do not confuse these three sessions".
2. Copy a session id from the sidebar, restart Streamlit only (not FastAPI), and `curl` `GET /sessions/<that-id>`. Why is the history still there when the page looks empty?
3. Remove `session_id` from the Streamlit payload so the backend mints a new one every message. Try a follow-up. Put the field back.
4. While an Approve card is showing, call `POST /approve` with the right `action_id` but a **different** `session_id`. Read the 409. Then call it with the original id.

