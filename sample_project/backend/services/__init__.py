"""
Service layer.

Everything that knows HOW to talk to an external system lives here.
Tools (in `backend/tools/`) call these functions; they never build URLs or
parse raw JSON themselves. This separation means:

  * tools stay tiny and readable (the LLM only sees their signature + docstring),
  * you can unit-test parsing without a network,
  * swapping Open Library for Ticketmaster touches only this folder.
"""
