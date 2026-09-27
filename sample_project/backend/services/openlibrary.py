"""
Open Library HTTP client.

This is the analogue of `services/ticketmaster.py` in your assignment.

Responsibilities of a service module:
  1. Build the request (URL, query params, headers, API key if needed).
  2. Make the HTTP call with a timeout.
  3. Map transport/HTTP errors -> our own exceptions (see exceptions.py).
  4. Parse the (often huge) JSON into small, typed Python objects that contain
     ONLY what the LLM needs. Raw API payloads are noisy and expensive to put
     in a prompt.

Open Library endpoints used:
  GET /search.json?q=...&fields=...&limit=N        -> search books ("events.json")
  GET /works/{work_id}.json                        -> book details ("events/{id}")
  GET /search/authors.json?q=...                   -> search authors ("venues.json")
  GET /authors/{author_id}.json                    -> author details ("venues/{id}")
  GET /authors/{author_id}/works.json?limit=N      -> author's works

Docs: https://openlibrary.org/developers/api
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

from backend.config import settings
from backend.services.exceptions import (
    ExternalAPIError,
    ExternalAPIUnavailable,
    InvalidQuery,
    ResourceNotFound,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Typed result models -- the *shape of data the LLM sees*.
# Keep these small. Every field costs tokens on every turn.
# ---------------------------------------------------------------------------


class BookSummary(BaseModel):
    work_key: str  # e.g. "OL893414W"  (unique id we pass to other tools)
    title: str
    authors: list[str] = []
    first_publish_year: int | None = None
    subjects: list[str] = []
    rating: float | None = None
    ebook_access: str | None = None  # "public" | "borrowable" | "printdisabled" | "no_ebook"
    cover_url: str | None = None
    url: str


class BookDetails(BaseModel):
    work_key: str
    title: str
    authors: list[str] = []
    description: str | None = None
    subjects: list[str] = []
    first_publish_date: str | None = None
    cover_url: str | None = None
    url: str


class AuthorSummary(BaseModel):
    author_key: str  # e.g. "OL79034A"
    name: str
    birth_date: str | None = None
    death_date: str | None = None
    top_work: str | None = None
    work_count: int | None = None
    top_subjects: list[str] = []


class AuthorDetails(BaseModel):
    author_key: str
    name: str
    bio: str | None = None
    birth_date: str | None = None
    death_date: str | None = None
    notable_works: list[str] = []
    url: str


# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    # Open Library asks clients to identify themselves. For Ticketmaster you
    # would instead add `params["apikey"] = settings.ticketmaster_api_key`.
    return {"User-Agent": f"BookDiscoveryAgent/1.0 ({settings.openlibrary_contact})"}


async def _get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET <base_url><path> and return parsed JSON, translating errors."""
    url = f"{settings.openlibrary_base_url}{path}"
    try:
        async with httpx.AsyncClient(
            timeout=settings.external_api_timeout_seconds,
            headers=_headers(),
            follow_redirects=True,
        ) as client:
            logger.debug("GET %s params=%s", url, params)
            response = await client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise ExternalAPIUnavailable("The book catalogue timed out.") from exc
    except httpx.HTTPError as exc:  # DNS failure, connection refused, ...
        raise ExternalAPIUnavailable() from exc

    if response.status_code == 404:
        raise ResourceNotFound()
    if response.status_code in (400, 422):
        raise InvalidQuery()
    if response.status_code >= 500:
        raise ExternalAPIUnavailable()
    if response.status_code != 200:
        raise ExternalAPIError(f"Unexpected status {response.status_code} from catalogue.")

    try:
        return response.json()
    except ValueError as exc:
        raise ExternalAPIError("The catalogue returned malformed data.") from exc


# ---------------------------------------------------------------------------
# Small parsing helpers (pure functions -> easy to unit test)
# ---------------------------------------------------------------------------


def _strip_prefix(key: str, prefix: str) -> str:
    """'/works/OL1W' -> 'OL1W'."""
    return key[len(prefix):] if key.startswith(prefix) else key


def _cover_url(cover_id: int | None, size: str = "M") -> str | None:
    return f"https://covers.openlibrary.org/b/id/{cover_id}-{size}.jpg" if cover_id else None


def _text(value: Any) -> str | None:
    """Open Library returns descriptions either as a str or {"type":..., "value": str}."""
    if isinstance(value, dict):
        return value.get("value")
    return value if isinstance(value, str) else None


def book_url(work_key: str) -> str:
    return f"https://openlibrary.org/works/{_strip_prefix(work_key, '/works/')}"


def author_url(author_key: str) -> str:
    return f"https://openlibrary.org/authors/{_strip_prefix(author_key, '/authors/')}"


def parse_search_doc(doc: dict[str, Any]) -> BookSummary:
    work_key = _strip_prefix(doc.get("key", ""), "/works/")
    return BookSummary(
        work_key=work_key,
        title=doc.get("title", "Untitled"),
        authors=doc.get("author_name", [])[:3],
        first_publish_year=doc.get("first_publish_year"),
        subjects=doc.get("subject", [])[:5],
        rating=round(doc["ratings_average"], 2) if doc.get("ratings_average") else None,
        ebook_access=doc.get("ebook_access"),
        cover_url=_cover_url(doc.get("cover_i")),
        url=book_url(work_key),
    )


# ---------------------------------------------------------------------------
# Public API of this module -- what the tools call
# ---------------------------------------------------------------------------

_SEARCH_FIELDS = "key,title,author_name,first_publish_year,subject,ratings_average,ebook_access,cover_i"


async def search_books(
    query: str,
    *,
    author: str | None = None,
    subject: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 5,
) -> list[BookSummary]:
    """
    Search the catalogue. Open Library supports a Lucene-like syntax, so we
    compose filters into the `q` string:  dune author:herbert subject:fiction
    """
    if not query and not author and not subject:
        raise InvalidQuery("Provide at least a query, an author, or a subject.")

    parts: list[str] = []
    if query:
        parts.append(query)
    if author:
        parts.append(f'author:"{author}"')
    if subject:
        parts.append(f'subject:"{subject}"')
    if year_from or year_to:
        parts.append(f"first_publish_year:[{year_from or '*'} TO {year_to or '*'}]")

    data = await _get_json(
        "/search.json",
        params={"q": " ".join(parts), "fields": _SEARCH_FIELDS, "limit": max(1, min(limit, 10))},
    )
    return [parse_search_doc(doc) for doc in data.get("docs", [])]


async def get_book_details(work_key: str) -> BookDetails:
    work_id = _strip_prefix(work_key.strip(), "/works/")
    if not work_id.startswith("OL") or not work_id.endswith("W"):
        raise InvalidQuery(f"'{work_key}' is not a valid work id (expected e.g. OL893414W).")

    data = await _get_json(f"/works/{work_id}.json")

    # Resolve author names (the work endpoint only gives author *keys*).
    author_names: list[str] = []
    for entry in data.get("authors", [])[:2]:
        key = (entry.get("author") or {}).get("key")
        if not key:
            continue
        try:
            author = await _get_json(f"{key}.json")
            if author.get("name"):
                author_names.append(author["name"])
        except ExternalAPIError:
            # A missing author name is not worth failing the whole request.
            logger.warning("Could not resolve author %s", key)

    covers = data.get("covers") or []
    description = _text(data.get("description"))
    return BookDetails(
        work_key=work_id,
        title=data.get("title", "Untitled"),
        authors=author_names,
        description=(description[:1200] + "...") if description and len(description) > 1200 else description,
        subjects=(data.get("subjects") or [])[:8],
        first_publish_date=data.get("first_publish_date"),
        cover_url=_cover_url(covers[0] if covers else None, size="L"),
        url=book_url(work_id),
    )


async def search_authors(name: str, *, limit: int = 5) -> list[AuthorSummary]:
    if not name.strip():
        raise InvalidQuery("Author name cannot be empty.")
    data = await _get_json("/search/authors.json", params={"q": name, "limit": max(1, min(limit, 10))})
    results: list[AuthorSummary] = []
    for doc in data.get("docs", []):
        results.append(
            AuthorSummary(
                author_key=doc.get("key", ""),
                name=doc.get("name", "Unknown"),
                birth_date=doc.get("birth_date"),
                death_date=doc.get("death_date"),
                top_work=doc.get("top_work"),
                work_count=doc.get("work_count"),
                top_subjects=(doc.get("top_subjects") or [])[:5],
            )
        )
    return results


async def get_author_details(author_key: str) -> AuthorDetails:
    author_id = _strip_prefix(author_key.strip(), "/authors/")
    if not author_id.startswith("OL") or not author_id.endswith("A"):
        raise InvalidQuery(f"'{author_key}' is not a valid author id (expected e.g. OL79034A).")

    data = await _get_json(f"/authors/{author_id}.json")
    works: list[str] = []
    try:
        works_data = await _get_json(f"/authors/{author_id}/works.json", params={"limit": 5})
        works = [w.get("title", "") for w in works_data.get("entries", []) if w.get("title")]
    except ExternalAPIError:
        logger.warning("Could not load works for author %s", author_id)

    bio = _text(data.get("bio"))
    return AuthorDetails(
        author_key=author_id,
        name=data.get("name", "Unknown"),
        bio=(bio[:800] + "...") if bio and len(bio) > 800 else bio,
        birth_date=data.get("birth_date"),
        death_date=data.get("death_date"),
        notable_works=works,
        url=author_url(author_id),
    )
