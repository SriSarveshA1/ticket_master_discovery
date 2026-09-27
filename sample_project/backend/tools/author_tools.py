"""
Author tools  (assignment analogue: venue_tools.py -> search_venues, get_venue_details).

Just like venues are a *second entity type* alongside events in Ticketmaster,
authors are a second entity type alongside books here. Giving the agent tools
for both lets it answer questions like "who wrote this?" or "what else has this
author written?" without you writing any if/else routing.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool

from backend.services import openlibrary
from backend.tools._helpers import error_payload, no_results, to_json


@tool
async def search_authors(
    name: Annotated[str, "Full or partial author name, e.g. 'Ursula Le Guin'."],
    limit: Annotated[int, "How many results to return (1-10). Default 5."] = 5,
) -> str:
    """
    Find authors by name. Returns a JSON list with each author's `author_key`,
    life dates, most popular work and typical subjects.
    Use this when the user asks about a writer rather than a specific book.
    """
    try:
        results = await openlibrary.search_authors(name, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return error_payload(exc, context="search_authors")
    if not results:
        return no_results(f"authors matching '{name}'")
    return to_json(results)


@tool
async def get_author_details(
    author_key: Annotated[str, "The author id from a previous result, e.g. 'OL79034A'."],
) -> str:
    """
    Get an author's biography, life dates and a few notable works.
    Use after `search_authors` (or when a book result names an author you have a key for).
    """
    try:
        details = await openlibrary.get_author_details(author_key)
    except Exception as exc:  # noqa: BLE001
        return error_payload(exc, context="get_author_details")
    return to_json(details)
