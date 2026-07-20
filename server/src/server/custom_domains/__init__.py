"""Custom domains: ownership claims, routing, activation, transitions.

This module's public interface is the names re-exported here. Internals (the
claim state machine, evidence collection, probes, Traefik control, the routing
reconciler, and the schema DDL) are deliberately not exported; import those
directly only from within the package or its tests.

Names resolve lazily (PEP 562) so importing the package never eagerly pulls in
the database-dependent submodules, which keeps db.py free to ship the schema.
"""
from __future__ import annotations

import importlib

_PUBLIC = {
    "CustomDomainsRuntime": "runtime",
    "DOMAIN_CHECK_PREFIX": "runtime",
    "CustomDomainsConfig": "config",
    "CustomDomainError": "errors",
    "ClaimConflict": "errors",
    "ClaimNotFound": "errors",
    "UnsupportedClaimMode": "errors",
    "InvalidHostname": "errors",
    "DomainCheckUnavailable": "errors",
    "DomainQuotaExceeded": "errors",
    "DomainClaim": "claims",
    "DomainClaimStore": "claims",
    "DomainClaimLimits": "claims",
    "DnsTxtResolver": "claims",
    "normalize_hostname": "claims",
    "ClaimView": "views",
    "build_claim_view": "views",
    "claim_views_for_site": "views",
    "CloudflareDiagnostic": "cloudflare",
}

__all__ = list(_PUBLIC)


def __getattr__(name: str):
    module = _PUBLIC.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f".{module}", __name__), name)
