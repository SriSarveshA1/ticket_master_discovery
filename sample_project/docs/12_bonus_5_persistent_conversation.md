# 12 — Bonus 5: Persistent Conversation (survives a backend restart)

**Files:** `backend/main.py` (lifespan), `backend/agent.py` (`build_agent(checkpointer=...)`), `backend/config.py`, `tests/test_bonus_features.py`
**Enable:** `CHECKPOINT_BACKEND=sqlite` in `.env`

## The problem, in plain words

In doc 06 you learned that the agent's memory is a *checkpointer* — an object that saves the
conversation after every step, keyed by `thread_id` (= your `session_id`).

The default checkpointer is `InMemorySaver`. "In memory" means: a Python dictionary inside
the running `uvicorn` process. The moment you stop the backend (Ctrl-C, crash, redeploy)
that dictionary is gone, and with it every conversation. The user comes back, sends
"tell me more about number 2", and the agent has no idea what list they mean.

Bonus 5 asks: **store the history somewhere that outlives the process.**

## The solution, in one sentence

Swap the dictionary-backed checkpointer for a file-backed one. Nothing else changes.

```python
# before (memory)
from langgraph.checkpoint.memory import InMemorySaver
checkpointer = InMemorySaver()

# after (sqlite)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
async with AsyncSqliteSaver.from_conn_string("./sessions.db") as checkpointer:
    ...
```

Both objects implement the same interface (`BaseCheckpointSaver`). The agent, the session
manager, the routes, the frontend — none of them know or care which one is in use. That is
the payoff of passing the checkpointer *into* `build_agent()` instead of hard-coding it.

## Why it lives in `lifespan`

Look at `backend/main.py`:

```python
@asynccontextmanager
async def lifespan(app):
    if checkpointer is not None or settings.checkpoint_backend == "memory":
        agent = build_agent(model=model, checkpointer=checkpointer)
        app.state.session_manager = SessionManager(agent)
        yield
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        async with AsyncSqliteSaver.from_conn_string(settings.sqlite_path) as saver:
            agent = build_agent(model=model, checkpointer=saver)
            app.state.session_manager = SessionManager(agent)
            yield          # <- app serves requests here; DB connection stays open
        # <- connection closed cleanly on shutdown
```

`AsyncSqliteSaver` owns a database connection. A connection must be opened once at startup
and closed once at shutdown — which is precisely the shape of FastAPI's `lifespan`
(code before `yield` = startup, after = shutdown). Opening it per request would be slow and
wrong; opening it at import time would leak it.

## What is actually in the file?

```bash
sqlite3 sessions.db ".tables"
# checkpoints  writes
sqlite3 sessions.db "select thread_id, count(*) from checkpoints group by thread_id;"
# ui-3f9a1c2b   7
```

One row per *step* of the graph (model called, tool ran, …), per thread. LangGraph stores
the whole message list as a serialized blob, plus which node runs next. That last part is
why the strongest demo below works.

## Try it (5 minutes)

```bash
pip install langgraph-checkpoint-sqlite       # already in requirements.txt
echo "CHECKPOINT_BACKEND=sqlite" >> .env
uvicorn backend.main:app --port 8000
```

1. In Streamlit, search for something and note your session id in the sidebar.
2. `curl localhost:8000/sessions/<id>` — you see the history.
3. **Stop uvicorn. Start it again.**
4. `curl localhost:8000/sessions/<id>` — history still there. Type a follow-up in the same
   Streamlit tab — the agent still knows the context.

### The strongest demo: a paused approval survives a restart

Because the checkpointer also stores *where the graph is paused*, this works:

1. "I want to read number 2" → Approve/Reject card appears.
2. Restart the backend.
3. Click **Approve**. `/approve` finds the pending interrupt in SQLite, resumes it, and
   returns the URL.

`tests/test_bonus_features.py::test_pending_approval_survives_backend_restart` does exactly
this with two `TestClient`s sharing one temp DB file. Read that test — it is the whole
feature in 20 lines.

## Things beginners trip on

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: langgraph.checkpoint.sqlite` | package not installed | `pip install langgraph-checkpoint-sqlite` |
| Works, but history vanishes anyway | `CHECKPOINT_BACKEND` still `memory`, or `.env` not loaded | `curl /health` shows `checkpoint_backend` — check it says `sqlite` |
| `sessions.db` appears in `git status` | forgot to ignore it | `*.db` is in `.gitignore` here — keep it |
| Two uvicorn workers, weird behaviour | SQLite is single-writer | one worker for dev; use Postgres saver for real multi-worker deployments |

## Going further (not required)

* **Postgres / Redis**: `langgraph-checkpoint-postgres` has the same `from_conn_string`
  pattern. Swap the import and the connection string — nothing else.
* **Cleanup**: old threads accumulate. A nightly job calling `checkpointer.adelete_thread`
  for sessions older than N days is the usual answer.
* **Your assignment**: Ticketmaster conversations are identical from the checkpointer's
  point of view. Same code, same toggle.

## Exercise

Delete the `else` branch in `lifespan`, set `CHECKPOINT_BACKEND=sqlite`, and start the
server. Read the error. Now you know what the config validation in `config.py`
(`Literal["memory", "sqlite"]`) protects you from. Restore the branch.
