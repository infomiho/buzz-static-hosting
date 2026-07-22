"""Buzz-native device authorization grant.

The CLI starts a grant and polls with the secret device code; the user opens
the verification page in a browser, authenticates however the dashboard allows,
and approves the short user code. The service only records which user approved
a grant; minting the resulting session is the caller's business.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from .pending_store import PendingStore

# Consonant-only alphabet: no vowels (no accidental words) and no lookalikes.
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
USER_CODE_GROUP_LENGTH = 4
DEVICE_CODE_TTL_SECONDS = 900
POLL_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


class DeviceCodeExpired(Exception):
    pass


def _generate_user_code() -> str:
    groups = [
        "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_GROUP_LENGTH))
        for _ in range(2)
    ]
    return "-".join(groups)


def normalize_user_code(user_code: str) -> str:
    cleaned = "".join(ch for ch in user_code.upper() if ch in USER_CODE_ALPHABET)
    if len(cleaned) != USER_CODE_GROUP_LENGTH * 2:
        return ""
    return f"{cleaned[:USER_CODE_GROUP_LENGTH]}-{cleaned[USER_CODE_GROUP_LENGTH:]}"


class DeviceAuthorizationService:
    def __init__(self, store: PendingStore, verification_uri: str) -> None:
        self._store = store
        self._verification_uri = verification_uri

    def start(self) -> DeviceAuthorization:
        device_code = secrets.token_urlsafe(32)
        user_code = _generate_user_code()
        entry = {"device_code": device_code, "user_code": user_code, "user_id": None}
        self._store.put(
            f"device:code:{device_code}", entry, ttl_seconds=DEVICE_CODE_TTL_SECONDS
        )
        self._store.put(
            f"device:user:{user_code}", entry, ttl_seconds=DEVICE_CODE_TTL_SECONDS
        )
        return DeviceAuthorization(
            device_code=device_code,
            user_code=user_code,
            verification_uri=self._verification_uri,
            interval=POLL_INTERVAL_SECONDS,
            expires_in=DEVICE_CODE_TTL_SECONDS,
        )

    def approve(self, user_code: str, user_id: int) -> bool:
        normalized = normalize_user_code(user_code)
        if not normalized:
            return False
        entry = self._store.get(f"device:user:{normalized}")
        if entry is None or entry["user_id"] is not None:
            return False
        entry["user_id"] = user_id
        return True

    def poll(self, device_code: str) -> int | None:
        """The approving user's id, or None while the grant is still pending."""
        entry = self._store.get(f"device:code:{device_code}")
        if entry is None:
            raise DeviceCodeExpired()
        if entry["user_id"] is None:
            return None
        self._store.consume(f"device:code:{device_code}")
        self._store.consume(f"device:user:{entry['user_code']}")
        return entry["user_id"]
