"""Custom-domain errors. These carry no HTTP vocabulary; the host maps them
to responses (see the exception handlers in server.app)."""
from __future__ import annotations


class CustomDomainError(Exception):
    """Base class for custom-domain errors."""


class InvalidHostname(CustomDomainError, ValueError):
    """A hostname is not usable as a custom domain."""


class DomainCheckUnavailable(CustomDomainError):
    """A DNS ownership check could not be completed."""


class DomainQuotaExceeded(CustomDomainError):
    """A site, user, or server has reached its custom-domain quota."""


class ClaimConflict(CustomDomainError):
    """The requested claim or transition state conflicts with existing state."""


class ClaimNotFound(CustomDomainError):
    """A referenced claim or site does not exist."""


class UnsupportedClaimMode(CustomDomainError):
    """The requested claim mode is not supported."""
