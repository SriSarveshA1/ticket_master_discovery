"""
Book tools  (assignment analogue: event_tools.py -> search_events, get_event_details,
search_events_by_location, search_events_by_date).

HOW THE LLM USES A TOOL
-----------------------
`@tool` turns a Python function into a schema the LLM can see:

    name        -> the function name           (search_books)
    description -> the docstring               (what it does, WHEN to use it)
    parameters  -> the type hints + Annotated  (what to pass)

The LLM never sees your code. It only sees that schema. So:
  * the docstring is a prompt -- write it for the model, not for humans,
  * parameter names and descriptions decide how well the model fills them in,
  * keep each tool focused ("one job") so the model can pick between them.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool

from backend.services import openlibrary
from backend.tools._helpers import error_payload, no_results, to_json


@tool
async def search_books(
    query: Annotated[str, "Free-text search: a title, topic, series or keywords, e.g. 'dune' or 'python programming'."],
    author: Annotated[str | None, "Optional author name to restrict results, e.g. 'Frank Herbert'."] = None,
    limit: Annotated[int, "How many results to return (1-10). Default 5."] = 5,
) -> str:
    """
    Search the book catalogue by keywords, optionally filtered by author.
    Use this for general requests like "find sci-fi novels", "books about black holes",
    or "novels by Agatha Christie". Returns a JSON list of books, each with a
    `work_key` you can pass to `get_book_details` or `proceed_to_borrow`.
    """
    try:
        results = await openlibrary.search_books(query, author=author, limit=limit)
    except Exception as exc:  # noqa: BLE001 - we deliberately convert everything to text
        return error_payload(exc, context="search_books")
    if not results:
        return no_results("books")
    return to_json(results)


@tool
async def search_books_by_subject(
    subject: Annotated[str, "A subject/genre, e.g. 'science fiction', 'cooking', 'history'."],
    year_from: Annotated[int | None, "Earliest first-publication year, e.g. 2015."] = None,
    year_to: Annotated[int | None, "Latest first-publication year, e.g. 2024."] = None,
    limit: Annotated[int, "How many results to return (1-10). Default 5."] = 5,
) -> str:
    """
    Browse books by subject/genre, optionally within a publication-year range.
    Use this when the user names a genre or period rather than a specific title,
    e.g. "recent fantasy books" or "history books from the 1990s".
    Returns a JSON list of books with `work_key`s.
    """
    try:
        results = await openlibrary.search_books(
            "", subject=subject, year_from=year_from, year_to=year_to, limit=limit
        )
    except Exception as exc:  # noqa: BLE001
        return error_payload(exc, context="search_books_by_subject")
    if not results:
        return no_results(f"books on '{subject}'")
    return to_json(results)


@tool
async def get_book_details(
    work_key: Annotated[str, "The book's work id from a previous search result, e.g. 'OL893414W'."],
) -> str:
    """
    Get full details for ONE book: description, subjects, authors, cover and its page URL.
    Use this when the user asks "tell me more about number 2" or wants a summary
    of a specific book. Always pass the `work_key` from an earlier search result.
    """
    try:
        details = await openlibrary.get_book_details(work_key)
    except Exception as exc:  # noqa: BLE001
        return error_payload(exc, context="get_book_details")
    return to_json(details)
