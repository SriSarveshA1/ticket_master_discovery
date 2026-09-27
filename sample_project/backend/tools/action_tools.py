"""
Action tools  (assignment analogue: proceed_to_booking).

CONCEPT: EXTERNAL ACTION + HUMAN-IN-THE-LOOP
--------------------------------------------
This tool does something with a consequence *outside* our app: it hands the
user off to an external website. The assignment says such operations must
NOT run until the human explicitly approves.

Notice the tool itself contains NO approval logic. It is a plain function.
The pause is added *around* it by `HumanInTheLoopMiddleware` in agent.py,
which is configured with this tool's name. That keeps the tool testable and
keeps "policy" (what needs approval) in one place.

Why does the tool take title/author/year when it only needs the URL?
  -> Because the interrupt shows those arguments to the human. We want the
     confirmation card to read "You selected: Dune by Frank Herbert (1965)",
     so we make the LLM supply that data as arguments. (See describe_action
     in agent.py.)

IMPORTANT LIMITATION (mirrors the assignment): we cannot borrow/buy on the
user's behalf. We only return the URL. Never claim "your book has been borrowed".
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import tool

from backend.services.openlibrary import book_url


@tool
def proceed_to_borrow(
    work_key: Annotated[str, "The selected book's work id, e.g. 'OL893414W'."],
    title: Annotated[str, "The selected book's title, shown to the user for confirmation."],
    author: Annotated[str, "The selected book's main author, shown to the user for confirmation."],
    year: Annotated[int | None, "First publication year, if known."] = None,
) -> str:
    """
    Hand the user off to Open Library to read or borrow the SELECTED book.
    Call this ONLY when the user has clearly chosen one specific book and wants
    to read/borrow/get it. This operation requires human approval and will pause
    until the user approves. Returns the page URL where the user can continue.
    """
    return json.dumps(
        {
            "status": "approved",
            "title": title,
            "author": author,
            "year": year,
            "url": book_url(work_key),
            "note": "The user must complete reading/borrowing on Open Library. Do not claim it is done.",
        }
    )
