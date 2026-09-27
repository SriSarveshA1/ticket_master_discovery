# 04 — Custom LangChain Tools

**Files:** `backend/tools/book_tools.py`, `author_tools.py`, `action_tools.py`, `_helpers.py`, `__init__.py`

## What a tool actually is

A tool is a Python function plus a **schema the LLM can read**. `@tool` builds the schema
from three things:

```python
@tool
async def search_books(
    query: Annotated[str, "Free-text search: a title, topic, series or keywords..."],
    author: Annotated[str | None, "Optional author name to restrict results..."] = None,
    limit: Annotated[int, "How many results to return (1-10). Default 5."] = 5,
) -> str:
    """
    Search the book catalogue by keywords, optionally filtered by author.
    Use this for general requests like "find sci-fi novels" ...
    Returns a JSON list of books, each with a `work_key` you can pass to ...
    """
```

| Part of the function | What the LLM sees | Why it matters |
|---|---|---|
| function name | `"name": "search_books"` | The model refers to tools by name |
| docstring | `"description": "Search the book catalogue..."` | **This is a prompt.** It tells the model *when* to pick this tool |
| type hints + `Annotated[...]` | JSON schema for `parameters` | Decides how well the model fills in arguments |

Run this to see exactly what the model sees:

```python
from backend.tools.book_tools import search_books
print(search_books.tool_call_schema.model_json_schema())
```

The model never sees your function body. Design the *signature and docstring* for the
model; design the *body* for correctness.

## Rules that make tools work well

### 1. One job per tool

The assignment says: multiple focused tools, not one giant function. Why? Tool selection is
a classification problem for the LLM. Six clear options with distinct descriptions are easy
to classify. One tool with 15 optional parameters is hard to use correctly.

Compare:
* `search_books(query, author)` — "I know a title/topic"
* `search_books_by_subject(subject, year_from, year_to)` — "I know a genre/period"

They hit the *same* service function. The split exists for the LLM's benefit.

### 2. Docstrings say WHEN, not just WHAT

Bad: `"""Search books."""`
Good: `"""...Use this for general requests like 'find sci-fi novels'... Returns a JSON list with `work_key` you can pass to `get_book_details`..."""`

Telling the model what comes *out* (and that the id chains into other tools) is how you get
multi-step behaviour like search → details without hard-coding it.

### 3. Return text, ideally JSON

Whatever your tool returns becomes the content of a `ToolMessage` in the conversation.
`to_json()` in `_helpers.py` serialises Pydantic models compactly (`exclude_none=True` —
don't pay tokens for nulls).

### 4. Never raise — return an error payload

```python
try:
    results = await openlibrary.search_books(query, author=author, limit=limit)
except Exception as exc:
    return error_payload(exc, context="search_books")   # -> {"error": "...friendly..."}
if not results:
    return no_results("books")                            # -> {"results": [], "message": "..."}
return to_json(results)
```

If a tool raises, the whole agent run fails and the user gets a 500. If it returns
`{"error": "The catalogue is temporarily unavailable"}`, the model reads that and says so
in plain English. **This is how graceful error handling reaches the conversation.** The
same applies to "no results" — an empty list with a hint lets the model suggest alternatives.

### 5. Make ids first-class

Every search result carries `work_key`. Every detail/action tool takes `work_key`. The model
learns the pattern from the docstrings and chains calls correctly. In Ticketmaster this is
the event `id` and venue `id`.

## Read-only vs action tools — `tools/__init__.py`

```python
READ_ONLY_TOOLS = [search_books, search_books_by_subject, get_book_details, search_authors, get_author_details]
ACTION_TOOLS    = [proceed_to_borrow]
ALL_TOOLS = READ_ONLY_TOOLS + ACTION_TOOLS
TOOLS_REQUIRING_APPROVAL = {tool.name for tool in ACTION_TOOLS}
```

The agent gets `ALL_TOOLS`. The HITL middleware gets `TOOLS_REQUIRING_APPROVAL`. Adding a new
action tool = append to one list. This is the assignment's "Recommended Tool Classification"
made executable.

## The action tool — `action_tools.py`

```python
@tool
def proceed_to_borrow(work_key, title, author, year=None) -> str:
    """Hand the user off to Open Library to read or borrow the SELECTED book.
    Call this ONLY when the user has clearly chosen one specific book...
    This operation requires human approval and will pause until the user approves."""
    return json.dumps({"status": "approved", "url": book_url(work_key), ...})
```

Two subtleties:

* The tool contains **zero approval logic**. The pause is added around it by middleware
  (doc 07). Keeps the tool testable and keeps policy in one place.
* It takes `title`/`author`/`year` it does not strictly need, because those arguments are
  what we **show the human** on the approval card. The tool has not run yet at that point, so
  the arguments are the only data we have.

## Exercises

1. Call a tool directly, no agent:
   `asyncio.run(get_book_details.ainvoke({"work_key": "OL893414W"}))`. Then with
   `"work_key": "bogus"`. Observe the error payload.
2. Add `search_books_by_author(author_key)` using `/authors/{key}/works.json`. Write the
   docstring first, then the body.
3. Deliberately make `get_book_details` raise instead of returning `error_payload`. Run the
   app, pass a bad id through chat, and see what the user experiences. Revert.
