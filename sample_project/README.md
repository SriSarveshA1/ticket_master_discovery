# BookBuddy — FastAPI + LangChain Agent Sample (with Human-in-the-Loop)

A complete, tested reference implementation of a **full-stack AI agent**: natural-language
search over a real external API (Open Library), multiple custom LangChain tools, LLM-driven
tool selection, conversational memory, Human-in-the-Loop approval before an external action,
graceful error handling, streaming responses and persistent sessions.

It is the same *shape* as the "AI Event Discovery & Booking Assistant" (Ticketmaster)
assignment, on a different problem, so you learn the pattern instead of copying the answer.
**Start with [`docs/00_START_HERE.md`](docs/00_START_HERE.md).**

```
Streamlit ──HTTP──► FastAPI ──► SessionManager ──► LangChain agent ──► tools ──► Open Library API
                                       │                 │
                                       │                 └─ HumanInTheLoopMiddleware pauses on proceed_to_borrow
                                       └─ session_id == LangGraph thread_id (memory / sqlite)
```

## Features ↔ assignment requirements

| Requirement | Where |
|---|---|
| FastAPI backend, `POST /chat` `{message, session_id}` | `backend/api/routes.py`, `backend/models/schemas.py` |
| Multiple hand-written `@tool`s, real HTTP to a real API | `backend/tools/`, `backend/services/openlibrary.py` |
| Agent chooses tools (no if/else routing) | `backend/agent.py`, `backend/prompts/system_prompt.py` |
| Conversation history / follow-ups ("number 2", "what about…") | checkpointer in `backend/agent.py`; `thread_id` in `backend/sessions/manager.py` |
| Human-in-the-Loop: pause → Approve/Reject → resume → URL | `HumanInTheLoopMiddleware` + `POST /approve`, `POST /reject` |
| Never claims the action is "done"; URL only after approval | system prompt + `handoff_url` logic in `manager.py` |
| Friendly errors, correct HTTP codes, no tracebacks | `services/exceptions.py`, `tools/_helpers.py`, `main.py` |
| `.env` for secrets, `.gitignore`d | `backend/config.py`, `.env.example` |
| Bonus 5 — persistent sessions (SQLite) | `CHECKPOINT_BACKEND=sqlite` → `main.py` lifespan |
| Bonus 6 — streaming (Server-Sent Events) | `POST /chat/stream`, `manager.stream_chat()`, ⚡ toggle in UI |
| Offline tests of the whole flow with a fake LLM | `tests/` |

## Quick start

```bash
cd sample_project
python -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -r requirements.txt
cp .env.example .env                                  # add your OPENAI_API_KEY

uvicorn backend.main:app --reload --port 8000         # terminal 1 → http://localhost:8000/docs
streamlit run frontend/streamlit_app.py               # terminal 2 → http://localhost:8501
pytest -q                                             # no key / network needed
```

Try in the UI: *"Find sci-fi classics"* → *"only ones before 1970"* → *"tell me more about
number 2"* → *"I want to read it"* → **Approve**.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | one message → `{status, response, pending_action?, handoff_url?}` |
| POST | `/chat/stream` | same, as SSE frames: `status*`, `token*`, `done` |
| POST | `/approve` / `/reject` | `{session_id, action_id}` → resumes the paused agent |
| GET / DELETE | `/sessions/{id}` | history / wipe |
| GET | `/books/{work_key}` | direct lookup (no LLM) |
| GET | `/health` | model + checkpoint backend |

Import `postman/BookBuddy.postman_collection.json` into Postman. Each request's description explains the body, the success body, and the error codes. Collection variables `baseUrl`, `session_id`, and `action_id` chain the chat → approve flow.

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_MODEL` | `openai:gpt-4o-mini` | any `init_chat_model` string with a tool-calling model |
| `OPENAI_API_KEY` | — | required for the default model |
| `CHECKPOINT_BACKEND` | `memory` | `sqlite` → conversations survive restarts (Bonus 5) |
| `SQLITE_PATH` | `./sessions.db` | used when backend is `sqlite` |
| `CORS_ORIGINS` | `http://localhost:8501,http://localhost:3000` | your frontend origins |
| `USE_SYSTEM_CERTS` | `false` | set `true` behind corporate proxies (see below) |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows every external request |

## Troubleshooting

* **`CERTIFICATE_VERIFY_FAILED` on every external call** (common on corporate laptops with
  Zscaler etc.): set `USE_SYSTEM_CERTS=true` in `.env`. This makes Python trust the OS
  keychain via `truststore`. Affects the LLM provider too.
* **Startup fails with "Could not initialise LLM"**: `OPENAI_API_KEY` missing or `LLM_MODEL`
  names a provider whose package is not installed (`pip install langchain-anthropic`, …).
* **`503 The AI service is temporarily unavailable`** on `/chat`: the LLM call failed — bad
  key, quota, network. Check the backend log for the real cause.
* **Streaming arrives all at once**: a proxy buffered it; the two headers in `/chat/stream`
  handle nginx. With curl use `-N`.

## Layout

```
backend/   main.py · config.py · agent.py · api/ · tools/ · services/ · models/ · prompts/ · sessions/
frontend/  streamlit_app.py
tests/     fake_llm.py · conftest.py · test_api_flow.py · test_bonus_features.py · test_service_parsing.py
docs/      00_START_HERE.md … 14_other_bonus_ideas.md
```
