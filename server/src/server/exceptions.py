class BadRequest(Exception):
    """400 - invalid input from the client."""


class Forbidden(Exception):
    """403 - authenticated but not allowed."""


class NotFound(Exception):
    """404 - resource does not exist."""
