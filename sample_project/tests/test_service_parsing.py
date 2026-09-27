"""
Unit tests for the pure parsing helpers in the service layer.

Because parsing is separated from HTTP, we can feed it a captured API payload
and assert on the shape the LLM will see. No network needed.
"""

from __future__ import annotations

import pytest

from backend.services import openlibrary
from backend.services.exceptions import InvalidQuery

SAMPLE_DOC = {
    "author_name": ["Frank Herbert"],
    "cover_i": 11481354,
    "ebook_access": "borrowable",
    "first_publish_year": 1965,
    "key": "/works/OL893414W",
    "title": "Dune",
    "subject": ["Science fiction", "Fiction", "Ecology", "Politics", "Religion", "Extra subject"],
    "ratings_average": 4.3049326,
}


def test_parse_search_doc_trims_and_normalises():
    book = openlibrary.parse_search_doc(SAMPLE_DOC)
    assert book.work_key == "OL893414W"  # prefix stripped
    assert book.url == "https://openlibrary.org/works/OL893414W"
    assert book.rating == 4.3
    assert len(book.subjects) == 5  # capped to save tokens
    assert book.cover_url == "https://covers.openlibrary.org/b/id/11481354-M.jpg"


def test_text_helper_handles_both_shapes():
    assert openlibrary._text("plain") == "plain"
    assert openlibrary._text({"type": "/type/text", "value": "wrapped"}) == "wrapped"
    assert openlibrary._text(None) is None


@pytest.mark.anyio
async def test_invalid_work_key_rejected_before_network():
    with pytest.raises(InvalidQuery):
        await openlibrary.get_book_details("not-a-key")


@pytest.mark.anyio
async def test_empty_search_rejected():
    with pytest.raises(InvalidQuery):
        await openlibrary.search_books("")
