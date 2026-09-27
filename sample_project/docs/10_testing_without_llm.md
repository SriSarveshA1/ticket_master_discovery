# 10 — Testing Without an LLM (or a Network)

**Files:** `tests/fake_llm.py`, `tests/conftest.py`, `tests/test_api_flow.py`, `tests/test_service_parsing.py`, `tests/test_bonus_features.py`

## Why bother

Every manual test of the full flow costs tokens, takes 20 seconds, and gives a slightly
different answer each time. You cannot iterate on HITL plumbing like that. The trick is to
**replace the two non-deterministic, external things** — the LLM and the external API —
with scripted stand-ins, and test *your* code, which is everything else.

```
Frontend ─► FastAPI ─► SessionManager ─► Agent ─► Tools ─► Service ─► External API
            └────────── all real ──────────────────────┘    └ patched ┘
                                          ▲
                                     LLM: scripted
```

## Piece 1 — a scripted LLM (`tests/fake_llm.py`)

```python
class ScriptedLLM(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs): return self
    def _stream(...): ...   # emits tool-call chunks or word-by-word tokens
```

`GenericFakeChatModel` replays a list of `AIMessage`s: each time the agent calls the model,
it pops the next one. We add two things: `bind_tools` (the agent calls it; we ignore the
schemas because our replies are pre-written) and `_stream` (so `/chat/stream` tests work).

Helpers make scripts readable:

```python
ai_tool_call("search_books", {"query": "sci-fi"}, "c1")   # "model decides to call this tool"
ai_text("I found Dune and Foundation.")                     # "model answers in text"
```

**The tools still run for real.** If the script says "call `search_books`", the agent
really executes `search_books`, which calls the service — which is why we also patch that.

## Piece 2 — a patched service (`tests/conftest.py`)

```python
@pytest.fixture
def patched_openlibrary(monkeypatch):
    async def fake_search(query, **kwargs): return FAKE_BOOKS
    monkeypatch.setattr(openlibrary, "search_books", fake_search)
    monkeypatch.setattr(openlibrary, "get_book_details", fake_details)
```

Tools call `openlibrary.search_books(...)` through the *module*, so replacing the module
attribute is enough. No `httpx`, no network, deterministic data.

## Piece 3 — an app factory fixture

```python
@pytest.fixture
def make_client(patched_openlibrary):
    def _make(*ai_messages):
        app = create_app(model=replies(ai_messages), checkpointer=InMemorySaver())
        client = TestClient(app); client.__enter__()   # runs lifespan
        return client
    ...
```

This is why `create_app()` takes `model` and `checkpointer` parameters (doc 02). Each test
gets a fresh agent with its own script and its own memory.

## Reading a test as a story

`test_search_then_followup_then_approve` *is* the assignment's Mandatory Test Scenario:

```python
client = make_client(
    ai_tool_call("search_books", {...}, "c1"),        # step 4: agent calls tool
    ai_text("1. Dune ... 2. Foundation ..."),          # step 6: multiple events shown
    ai_tool_call("get_book_details", {...}, "c2"),    # step 7-8: follow-up understood
    ai_text("Foundation: a galactic empire..."),
    ai_tool_call("proceed_to_borrow", {...}, "c3"),   # step 9-10: user chooses, agent pauses
    ai_text("You approved Foundation. Continue..."),  # step 14-15: resume, URL
)
r = client.post("/chat", ...)          # completed
r = client.post("/chat", ...)          # completed, context used
r = client.post("/chat", ...)          # awaiting_approval, handoff_url is None  <-- assignment rule
r = client.post("/approve", ...)       # completed, handoff_url set
```

Note the script has **six** model replies for **three** user messages: each tool call
needs a model reply to *request* it and another to *answer after* it.

## What we test where

| File | What | Needs |
|---|---|---|
| `test_service_parsing.py` | JSON → `BookSummary`; input validation | nothing |
| `test_api_flow.py` | routes, session manager, HITL approve/reject, 4xx codes | fake LLM |
| `test_bonus_features.py` | SSE frames, SQLite persistence across "restarts" | fake LLM (+ sqlite pkg) |
| (manual) | does the *real* model pick sensible tools for real questions | real LLM key |

The last row is the only thing you cannot automate cheaply — and it is mostly prompt
tuning, which you do by hand anyway.

## Exercises

1. Write `test_two_sessions_are_isolated`: two `session_id`s, one search each, assert
   `GET /sessions/{id}` shows only its own messages.
2. Write a test where the patched service raises `ExternalAPIUnavailable`. Script the model
   to reply `ai_text("Sorry, the catalogue is down.")`. Assert the HTTP status is **200**
   (not 503) — the error was handled *inside the conversation*.
3. Break `_extract_handoff_url` (return `None` always) and run the suite. Which test fails?
   That is your safety net for the assignment's most important rule.
