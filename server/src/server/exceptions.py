class BadRequest(Exception):
    """400 - invalid input from the client."""


class Forbidden(Exception):
    """403 - authenticated but not allowed."""


class NotFound(Exception):
    """404 - resource does not exist."""


class PayloadTooLarge(Exception):
    """413 - request or expanded content exceeds a configured limit."""
