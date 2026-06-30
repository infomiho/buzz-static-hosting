from __future__ import annotations

import secrets

from .config import ADJECTIVES, NOUNS, DOMAIN


def generate_subdomain() -> str:
    return f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.randbelow(9000) + 1000}"


def extract_subdomain(host: str | None) -> str | None:
    if not host:
        return None
    host = host.split(":")[0]
    if DOMAIN and host.endswith("." + DOMAIN):
        sub = host[: -(len(DOMAIN) + 1)]
        return sub if sub else None

    parts = host.split(".")
    if len(parts) >= 2 and parts[-1] == "localhost" and parts[0] != "localhost":
        return parts[0]
    return None
