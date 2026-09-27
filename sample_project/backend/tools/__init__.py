"""
Tool registry.

CONCEPT (assignment "Recommended Tool Classification"):

  * READ-ONLY tools  -> safe to run automatically. They only fetch information.
  * ACTION tools     -> have a consequence outside our system (here: sending the
                        user to an external site). They MUST pause for human
                        approval before executing.

The agent receives ALL tools; the Human-in-the-Loop middleware is told which
names require approval (see backend/agent.py).
"""

from backend.tools.action_tools import proceed_to_borrow
from backend.tools.author_tools import get_author_details, search_authors
from backend.tools.book_tools import get_book_details, search_books, search_books_by_subject

READ_ONLY_TOOLS = [
    search_books,
    search_books_by_subject,
    get_book_details,
    search_authors,
    get_author_details,
]

ACTION_TOOLS = [
    proceed_to_borrow,
]

ALL_TOOLS = READ_ONLY_TOOLS + ACTION_TOOLS

# Names the HITL middleware must interrupt on.
TOOLS_REQUIRING_APPROVAL = {tool.name for tool in ACTION_TOOLS}

__all__ = [
    "ALL_TOOLS",
    "READ_ONLY_TOOLS",
    "ACTION_TOOLS",
    "TOOLS_REQUIRING_APPROVAL",
]
