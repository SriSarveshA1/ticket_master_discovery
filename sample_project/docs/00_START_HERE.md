# Start Here — How to Learn From This Sample Project

This folder is a **complete, working reference implementation** of the same *kind* of
application your assignment asks for, built on a different problem so you cannot copy-paste
it — you have to understand it and translate it.

| Your assignment | This sample |
|---|---|
| AI Event Discovery & Booking Assistant | AI Book Discovery & Reading Assistant |
| Ticketmaster Discovery API (needs API key) | Open Library API (no key needed) |
| `search_events`, `get_event_details`, `search_events_by_date/location` | `search_books`, `get_book_details`, `search_books_by_subject` |
| `search_venues`, `get_venue_details` | `search_authors`, `get_author_details` |
| `proceed_to_booking` (HITL) → Ticketmaster purchase URL | `proceed_to_borrow` (HITL) → Open Library page URL |
| "Never claim the ticket is booked" | "Never claim the book is borrowed" |

Every concept in the assignment's *Objective* section is implemented here and has a doc:

| Objective | Doc | Code |
|---|---|---|
| How agents interact with external APIs | [03](03_service_layer_external_api.md) | `backend/services/openlibrary.py` |
| How to create custom LangChain tools manually | [04](04_custom_tools.md) | `backend/tools/*.py` |
| How an LLM decides which tool to invoke | [05](05_agent_and_tool_selection.md) | `backend/agent.py`, `backend/prompts/system_prompt.py` |
| How to maintain conversational context | [06](06_conversation_memory.md) | `backend/agent.py` (checkpointer), `backend/sessions/manager.py` |
| What a session is, and why it exists | [15](15_sessions.md) | `backend/sessions/manager.py`, `frontend/streamlit_app.py` |
| How to expose an AI agent through FastAPI | [02](02_fastapi_layer.md) | `backend/main.py`, `backend/api/routes.py`, `backend/models/schemas.py` |
| How to connect a frontend to an AI backend | [09](09_frontend_integration.md) | `frontend/streamlit_app.py` |
| How to implement Human-in-the-Loop | [07](07_human_in_the_loop.md) | `backend/agent.py`, `backend/sessions/manager.py`, `backend/tools/action_tools.py` |
| How to structure a real-world full-stack AI app | [01](01_architecture_and_folder_structure.md) | the whole tree |
| Error handling | [08](08_error_handling.md) | `backend/services/exceptions.py`, `backend/tools/_helpers.py`, `backend/main.py` |
| Testing without spending on an LLM | [10](10_testing_without_llm.md) | `tests/` |
| **Translating all of this to Ticketmaster** | [11](11_translate_to_your_assignment.md) | — |
| Bonus 5 — Persistent conversation (SQLite) | [12](12_bonus_5_persistent_conversation.md) | `backend/main.py` (lifespan), `tests/test_bonus_features.py` |
| Bonus 6 — Streaming responses (SSE) | [13](13_bonus_6_streaming_responses.md) | `backend/sessions/manager.py` (`stream_chat`), `backend/api/routes.py` (`/chat/stream`), `frontend/streamlit_app.py` |
| Bonus 1–4 — how you would do them | [14](14_other_bonus_ideas.md) | — |

---

## Recommended learning path (≈ 3–4 hours)

1. **Run it first** (15 min). Follow the README, open `http://localhost:8000/docs`, try
   the Streamlit UI. Do the full flow: search → follow-up → "I want to read #2" → Approve.
   Seeing it work makes every doc below concrete.
2. **Read docs 01 → 09 in order**, each with the referenced code file open beside it.
   The code is heavily commented; the docs explain *why*, the comments explain *what*.
   Read [15 — Sessions](15_sessions.md) together with doc 06.
3. **Trace one request by hand** (doc 01 has a sequence diagram). Add `print()`s or set
   `LOG_LEVEL=DEBUG` and watch the tool calls happen.
4. **Do the exercises** at the end of each doc. They are small modifications that prove you
   understood the concept.
5. **Read doc 11** and start your own project from an empty folder, recreating the structure
   file by file. Do not copy this project — rebuild it for Ticketmaster.
6. Once the core works, read docs 12–13 for the two implemented bonuses (flip
   `CHECKPOINT_BACKEND=sqlite` and the ⚡ toggle in the UI to see them), then 14 for the rest.

## Running

```bash
cd sample_project
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then put your OPENAI_API_KEY in .env

# terminal 1 - backend
uvicorn backend.main:app --reload --port 8000

# terminal 2 - frontend
streamlit run frontend/streamlit_app.py

# tests (no API key or network needed)
pytest -q
```

If you see `CERTIFICATE_VERIFY_FAILED`, you are behind a corporate proxy — set
`USE_SYSTEM_CERTS=true` in `.env` (see README troubleshooting).

## Reading order for the code (if you prefer code-first)

```
backend/config.py            -> settings from .env
backend/services/openlibrary.py -> talking to the external API
backend/tools/book_tools.py  -> wrapping the service as LLM tools
backend/tools/action_tools.py-> the tool that needs approval
backend/prompts/system_prompt.py
backend/agent.py             -> create_agent + memory + HITL middleware
backend/sessions/manager.py  -> chat / approve / reject orchestration (+ stream_chat)
backend/api/routes.py        -> HTTP endpoints (+ /chat/stream SSE)
backend/main.py              -> app factory, lifespan, error handlers
frontend/streamlit_app.py
tests/test_api_flow.py       -> the mandatory scenario as a test
```
