"""
Domain-specific exceptions.

CONCEPT: Translate low-level failures (HTTP 503, timeouts, JSON decode errors)
into a small set of meaningful exceptions. Callers can then decide how to react
without knowing anything about `httpx`.

    service layer raises  ->  tool catches and returns a friendly string to the LLM
                          ->  or API layer catches and returns a proper HTTP status
"""


class ExternalAPIError(Exception):
    """Base class for anything that goes wrong talking to the external API."""

    user_message = "The external service returned an unexpected error."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.user_message)
        self.user_message = message or self.user_message


class ExternalAPIUnavailable(ExternalAPIError):
    """Network error, timeout, or 5xx from the external API."""

    user_message = "The book catalogue service is temporarily unavailable. Please try again shortly."


class ResourceNotFound(ExternalAPIError):
    """The requested book/author id does not exist."""

    user_message = "I couldn't find anything matching that id."


class InvalidQuery(ExternalAPIError):
    """We were asked to search with parameters the API rejects."""

    user_message = "That search request was invalid."
