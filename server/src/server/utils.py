from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from .config import ADJECTIVES, DOMAIN, NOUNS


_LOCAL_CONTROL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


def generate_subdomain() -> str:
    return f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.randbelow(9000) + 1000}"


def _hostname(host: str | None) -> str:
    if not host:
        return ""

    try:
        parsed = urlsplit(f"//{host.strip()}")
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return ""
        parsed.port
    except ValueError:
        return ""
    return (parsed.hostname or "").lower().rstrip(".")


def is_control_host(host: str | None) -> bool:
    hostname = _hostname(host)
    domain = _hostname(DOMAIN)
    if domain:
        return hostname == domain
    return hostname in _LOCAL_CONTROL_HOSTS


def extract_subdomain(host: str | None) -> str | None:
    hostname = _hostname(host)
    if not hostname:
        return None

    domain = _hostname(DOMAIN)
    if domain and hostname.endswith("." + domain):
        sub = hostname[: -(len(domain) + 1)]
        return sub if sub else None

    if not domain and hostname.endswith(".localhost"):
        return hostname.removesuffix(".localhost")
    return None
