"""Authentication utilities."""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import SESSION_TOKEN_PREFIX, DEPLOY_TOKEN_PREFIX


def hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_session_token() -> str:
    """Generate a new session token."""
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def generate_deploy_token() -> str:
    """Generate a new deployment token."""
    return DEPLOY_TOKEN_PREFIX + secrets.token_urlsafe(32)


def github_request(url: str, data: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
    """Make a request to GitHub API."""
    headers = {"Accept": "application/json"}
    encoded_data = None
    if data:
        encoded_data = urlencode(data).encode()
    req = Request(url, data=encoded_data, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        return json.loads(e.read().decode())
