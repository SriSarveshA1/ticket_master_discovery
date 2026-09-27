# 08 — Error Handling

**Files:** `backend/services/exceptions.py`, `backend/tools/_helpers.py`, `backend/sessions/manager.py` (errors), `backend/main.py` (`_register_error_handlers`)

## The principle

Errors happen at four different layers, and each layer has a *different audience*:

| Where it fails | Who needs to know | How we deliver it |
|---|---|---|
| External API (timeout, 5xx, 404, bad JSON) | the **LLM**, so it can explain to the user | tool returns `{"error": "..."}` text |
| Tool logic bug | developer (log) + LLM (friendly text) | `error_payload()` logs traceback, returns generic text |
| LLM provider (bad key, quota, outage) | the **frontend** | HTTP 503 `"The AI service is temporarily unavailable."` |
| Client mistake (empty message, stale action_id, unknown session) | the **frontend** | 422 / 409 / 404 with a one-line `detail` |
| Anything unexpected | developer (log) + frontend (generic) | HTTP 500 `"Something went wrong on our side."` |

Never a raw Python traceback to the user. Ever.

## Layer 1 — Service: translate transport errors into domain exceptions

```python
# services/exceptions.py
class ExternalAPIError(Exception):       user_message = "The external service returned an unexpected error."
class ExternalAPIUnavailable(ExternalAPIError): user_message = "The book catalogue service is temporarily unavailable..."
class ResourceNotFound(ExternalAPIError):       user_message = "I couldn't find anything matching that id."
class InvalidQuery(ExternalAPIError):           user_message = "That search request was invalid."
```

`_get_json()` in `openlibrary.py` is the *only* place `httpx` exceptions and status codes
are inspected. Everything above it deals in these four exceptions with ready-made
`user_message`s.

## Layer 2 — Tools: never raise, return text

```python
try:
    results = await openlibrary.search_books(...)
except Exception as exc:
    return error_payload(exc, context="search_books")
if not results:
    return no_results("books")
```

`error_payload()`:
* for our `ExternalAPIError` subclasses → uses `user_message`,
* for anything else → **logs the traceback** (developer sees it) and returns a generic
  message (user does not see internals).

The system prompt then tells the model: *"If a tool returns an error or no results, explain
it plainly and suggest what to try next. Never show raw JSON or stack traces."*

Result: "The book catalogue is temporarily unavailable — want me to try again in a moment?"
instead of a 500. The assignment's examples — *No events found*, *Ticketmaster API
temporarily unavailable*, *Invalid city provided*, *No pricing information* — are all this
pattern.

## Layer 3 — Session manager: LLM failures become one exception

```python
try:
    return await self._agent.ainvoke(payload, config=...)
except Exception as exc:
    logger.exception(...)
    raise AgentUnavailable("The AI service is temporarily unavailable.") from exc
```

Tools already caught their own errors, so what reaches here is the model provider failing
(401 bad key, 429 rate limit, network). One exception type, one HTTP mapping.

Also defined here: `SessionNotFound` (404), `NoPendingAction` (409), `ActionMismatch` (409).

## Layer 4 — FastAPI: exception → status code, in one place

```python
@app.exception_handler(NoPendingAction)
async def _(request, exc): return JSONResponse(409, {"detail": str(exc)})

@app.exception_handler(AgentUnavailable)
async def _(request, exc): return JSONResponse(503, {"detail": str(exc)})

@app.exception_handler(RequestValidationError)   # friendlier 422
@app.exception_handler(Exception)                # last line of defence -> 500 + log
```

Every error the frontend can receive has the shape `{"detail": "<one friendly sentence>"}`.
The Streamlit app relies on that: `st.error(r.json()["detail"])`.

## Try it

With the server running (`uvicorn backend.main:app`):

```bash
curl -X POST localhost:8000/chat -H 'content-type: application/json' -d '{"message":""}'
# 422 {"detail":"Invalid message: String should have at least 1 character"}

curl -X POST localhost:8000/approve -H 'content-type: application/json' -d '{"session_id":"x","action_id":"y"}'
# 409 {"detail":"There is no action awaiting approval in this session."}

curl localhost:8000/sessions/nope
# 404 {"detail":"Unknown session 'nope'."}

OPENAI_API_KEY=sk-wrong uvicorn backend.main:app   # then POST /chat
# 503 {"detail":"The AI service is temporarily unavailable."}

curl localhost:8000/books/OL0000000W
# 404 {"detail":"I couldn't find anything matching that id."}
```

## Exercises

1. Set `OPENLIBRARY_BASE_URL=https://localhost:1` in `.env`. Chat "find dune". Read what the
   agent tells the user. Check the backend log for the real reason.
2. Add a `RateLimited` exception for HTTP 429 in `_get_json` with its own `user_message`.
   Ticketmaster rate-limits at 5 req/s — you will need this.
3. Raise `ValueError("boom")` at the top of `search_books`'s service function. Confirm the
   user sees the generic message and the log shows the traceback.
