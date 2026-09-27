# 02 — The FastAPI Layer

**Files:** `backend/main.py`, `backend/api/routes.py`, `backend/models/schemas.py`, `backend/config.py`

## What FastAPI's job is (and is not)

FastAPI's job: accept HTTP, validate input, call *one* function, return JSON, translate
errors to status codes. It should contain **no agent logic**. If you find yourself writing
`agent.invoke(...)` inside a route, stop and move it into a service/manager class.

## 1. The API contract — `models/schemas.py`

Pydantic models define exactly what goes over the wire. Read `ChatRequest` and
`ChatResponse` first; the frontend is written against them.

```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(default_factory=lambda: f"session-{uuid4().hex[:12]}")
```

* `min_length=1` → an empty message is rejected with **422 before your code runs**.
* `default_factory` → the frontend *may* omit `session_id` on the first call; we mint one and
  return it. The frontend must then reuse it (that is what makes memory work).

```python
class ChatResponse(BaseModel):
    session_id: str
    status: Literal["completed", "awaiting_approval"]
    response: str
    pending_action: PendingAction | None = None
    handoff_url: str | None = None
```

`status` is the key design decision: **one response shape for both normal answers and HITL
pauses**. The frontend just switches on it. Your assignment example response only had
`response` + `session_id`; adding `status` and `pending_action` is how you extend it for HITL.

## 2. App factory + lifespan — `main.py`

```python
def create_app(model=None, checkpointer=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        agent = build_agent(model=model, checkpointer=checkpointer)   # once, at startup
        app.state.session_manager = SessionManager(agent)
        yield
    app = FastAPI(lifespan=lifespan)
    ...
    return app

app = create_app()   # what uvicorn imports
```

Why a factory instead of a bare `app = FastAPI()`?

* Tests call `create_app(model=FakeLLM())` and get a fully wired app with **no OpenAI key
  and no network**. See `tests/conftest.py`.
* Startup work (building the agent, opening a SQLite connection) belongs in `lifespan`, not
  at import time. Import-time side effects make testing and tooling painful.

`app.state` is FastAPI's sanctioned place for "singletons the routes need".

## 3. Dependency injection — `api/routes.py`

```python
def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager

@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, manager: SessionManager = Depends(get_session_manager)):
    turn = await manager.chat(body.session_id, body.message)
    return _to_response(turn)
```

Three lines. Everything interesting is in `SessionManager`. This is what a "thin route"
looks like.

## 4. `async` all the way down

FastAPI routes are `async def`. Our agent call is `await agent.ainvoke(...)`, our tools are
`async def`, our HTTP client is `httpx.AsyncClient`. Keep the chain async and one slow
Open Library call does not block other users' requests.

(If you use the synchronous `requests` library inside a tool, LangGraph runs it in a
thread pool — it works, but `httpx` async is cleaner.)

## 5. CORS

```python
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, ...)
```

A browser frontend on `localhost:3000` (React) or `localhost:8501` (Streamlit) calling
`localhost:8000` is a *cross-origin* request. Without this middleware the browser blocks it.
Origins come from `.env` so production differs from dev without code changes.

## 6. Configuration — `config.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    llm_model: str = "openai:gpt-4o-mini"
    openai_api_key: str | None = None
    ...
settings = Settings()
```

`pydantic-settings` reads real env vars, then `.env`, then defaults. Typed. One object,
imported everywhere. Never `os.getenv()` scattered across files, never a key in code.

## Endpoint summary

| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| POST | `/chat` | `ChatRequest` | `ChatResponse` | status = `completed` or `awaiting_approval` |
| POST | `/chat/stream` | `ChatRequest` | SSE stream | Bonus 6 — `status`/`token` frames then one `done` frame (doc 13) |
| POST | `/approve` | `{session_id, action_id}` | `ChatResponse` | resumes the agent; `handoff_url` set |
| POST | `/reject` | `{session_id, action_id}` | `ChatResponse` | resumes; agent acknowledges |
| GET | `/sessions/{id}` | — | history + `has_pending_action` | 404 if unknown |
| DELETE | `/sessions/{id}` | — | 204 | wipes checkpointer thread |
| GET | `/books/{work_key}` | — | `BookDetails` | direct service call (no LLM) |
| GET | `/health` | — | model + backend | frontend shows a status badge |

Open `http://localhost:8000/docs` — FastAPI generates this table interactively from the
schemas. Use it to poke the API before writing any frontend.

## Exercises

1. Add `GET /tools` that returns the name and description of every tool in `ALL_TOOLS`.
   (Hint: `tool.name`, `tool.description`.) Notice you did not touch the agent.
2. Change `max_length` on `ChatRequest.message` to 20 and send a long message. Read the 422
   body. Find where that friendly message is produced in `main.py`.
