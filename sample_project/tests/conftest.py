"""
Shared pytest fixtures.

`make_client(*ai_messages)` builds a fresh FastAPI app whose agent uses a
scripted fake LLM, with the Open Library service patched to return canned
data. Every test gets an isolated in-memory checkpointer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend.main import create_app
from backend.services import openlibrary
from tests.fake_llm import replies

FAKE_BOOKS = [
    openlibrary.BookSummary(
        work_key="OL893414W",
        title="Dune",
        authors=["Frank Herbert"],
        first_publish_year=1965,
        subjects=["Science fiction"],
        rating=4.3,
        ebook_access="borrowable",
        url="https://openlibrary.org/works/OL893414W",
    ),
    openlibrary.BookSummary(
        work_key="OL46125W",
        title="Foundation",
        authors=["Isaac Asimov"],
        first_publish_year=1951,
        subjects=["Science fiction"],
        rating=4.1,
        ebook_access="borrowable",
        url="https://openlibrary.org/works/OL46125W",
    ),
]

FAKE_DETAILS = openlibrary.BookDetails(
    work_key="OL46125W",
    title="Foundation",
    authors=["Isaac Asimov"],
    description="A galactic empire is falling...",
    subjects=["Science fiction"],
    first_publish_date="1951",
    url="https://openlibrary.org/works/OL46125W",
)


@pytest.fixture
def anyio_backend() -> str:
    """Run `@pytest.mark.anyio` tests on asyncio only (trio is not installed)."""
    return "asyncio"


@pytest.fixture
def patched_openlibrary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace network calls with canned data. Tools call `openlibrary.<fn>` via
    the module object, so patching the module attribute is enough."""

    async def fake_search(query, **kwargs):
        return FAKE_BOOKS

    async def fake_details(work_key):
        return FAKE_DETAILS

    monkeypatch.setattr(openlibrary, "search_books", fake_search)
    monkeypatch.setattr(openlibrary, "get_book_details", fake_details)


@pytest.fixture
def make_client(patched_openlibrary) -> Iterator[Callable[..., TestClient]]:
    """Factory fixture: `client = make_client(msg1, msg2, ...)`."""
    clients: list[TestClient] = []

    def _make(*ai_messages) -> TestClient:
        app = create_app(model=replies(ai_messages), checkpointer=InMemorySaver())
        client = TestClient(app)
        client.__enter__()  # triggers lifespan -> builds agent
        clients.append(client)
        return client

    yield _make

    for c in clients:
        c.__exit__(None, None, None)
