# 03 — Service Layer: How the Agent Talks to an External API

**Files:** `backend/services/openlibrary.py`, `backend/services/exceptions.py`

## Mental model

The agent never touches HTTP. Tools never touch HTTP. Only the **service module** does.

```
tool (LLM-facing)  ──calls──►  service function (Python-facing)  ──HTTP──►  external API
```

The service is ordinary Python you could use in a CLI script with no LLM involved at all.
That is a good test of whether you have separated concerns properly.

## The four responsibilities of a service function

Look at `search_books()` in `openlibrary.py`:

### 1. Build the request

```python
parts = [query]
if author: parts.append(f'author:"{author}"')
if subject: parts.append(f'subject:"{subject}"')
if year_from or year_to: parts.append(f"first_publish_year:[{year_from or '*'} TO {year_to or '*'}]")
params = {"q": " ".join(parts), "fields": _SEARCH_FIELDS, "limit": min(limit, 10)}
```

Notice `fields=` — we ask the API for **only the fields we need**. Open Library's full
search doc is several KB per book; we want ~200 bytes. For Ticketmaster the equivalent is
choosing `size=` sensibly and then *not* forwarding the whole `_embedded` blob.

For an API that needs a key, the key goes here and nowhere else:

```python
params["apikey"] = settings.ticketmaster_api_key
```

### 2. Make the call with a timeout and translate transport errors

```python
async def _get_json(path, params=None):
    try:
        async with httpx.AsyncClient(timeout=10, headers=_headers()) as client:
            response = await client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise ExternalAPIUnavailable("The book catalogue timed out.") from exc
    except httpx.HTTPError as exc:
        raise ExternalAPIUnavailable() from exc
    if response.status_code == 404: raise ResourceNotFound()
    if response.status_code in (400, 422): raise InvalidQuery()
    if response.status_code >= 500: raise ExternalAPIUnavailable()
    return response.json()
```

Every external call is wrapped **once**, in one helper. All the service functions use it.
Never let `httpx.ConnectError` or a `KeyError` from a missing JSON field escape this module.

### 3. Define small typed result models

```python
class BookSummary(BaseModel):
    work_key: str
    title: str
    authors: list[str] = []
    first_publish_year: int | None = None
    ...
    url: str
```

This is *the shape the LLM will see*. Design it by asking "what does the model need to
answer the user and to call the next tool?" Here: an id (`work_key`) to chain into
`get_book_details`/`proceed_to_borrow`, plus display fields.

### 4. Parse defensively

```python
def parse_search_doc(doc):
    return BookSummary(
        work_key=_strip_prefix(doc.get("key", ""), "/works/"),
        title=doc.get("title", "Untitled"),
        authors=doc.get("author_name", [])[:3],
        subjects=doc.get("subject", [])[:5],
        ...
    )
```

`.get()` with defaults everywhere. Real APIs have missing fields on real records. Cap lists
(`[:3]`, `[:5]`) — a book with 80 subjects would otherwise eat your token budget.
`_text()` handles Open Library returning descriptions as either `str` or `{"value": str}` —
every API has quirks like this; put the quirk-handling here, once.

## Why `parse_search_doc` is a separate pure function

Because `tests/test_service_parsing.py` can feed it a captured payload and assert on the
result **without any network**. Capture one real response with `curl`, save it, test your
parser against it.

## Chaining calls

`get_book_details()` makes 1 + N requests: the work, then each author (Open Library only
gives author *keys* in the work payload). Note how a failure to fetch an author name is
logged and *swallowed* — a missing author name should not fail the whole request. Decide
deliberately, per call, whether a sub-failure is fatal.

## Exercises

1. Run `curl "https://openlibrary.org/search.json?q=dune&limit=1"` and compare the raw
   payload to `BookSummary`. Count the fields you dropped.
2. Add a `language` filter to `search_books` (Open Library supports `language:eng`).
   Thread it through service → tool. Notice the tool change is two lines.
3. Write a 10-line script that calls `openlibrary.search_books("python")` directly with
   `asyncio.run`. No LLM. This is the "service works standalone" test.
