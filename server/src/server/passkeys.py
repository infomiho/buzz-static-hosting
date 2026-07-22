"""Passkey (WebAuthn) credentials bound to existing users.

The service owns the two WebAuthn ceremonies end to end: it mints and consumes
challenges through the pending store, verifies responses against the configured
RP ID and exact origin, and keeps the webauthn_credentials table. It knows
nothing about sessions or HTTP; authentication returns the credential owner's
user id and the caller decides what a login means.

The expected origin must stay pinned to the dashboard origin. Buzz serves
untrusted sites on subdomains of the RP ID, and the exact-origin check is what
keeps assertions triggered from those sites from being accepted here.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import IntegrityError
from typing import Any, Callable

import webauthn
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    exceptions as webauthn_exceptions,
    parse_client_data_json,
    parse_registration_credential_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .pending_store import PendingStore

CHALLENGE_TTL_SECONDS = 120
DEFAULT_PASSKEY_NAME = "Passkey"
MAX_PASSKEY_NAME_LENGTH = 60


@dataclass(frozen=True)
class PasskeyInfo:
    id: str
    name: str
    backed_up: bool
    created_at: str
    last_used_at: str | None


class PasskeyError(Exception):
    pass


class ChallengeExpired(PasskeyError):
    pass


class RegistrationFailed(PasskeyError):
    pass


class AuthenticationFailed(PasskeyError):
    pass


class PasskeyNotFound(PasskeyError):
    pass


def _normalize_name(name: str | None) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return DEFAULT_PASSKEY_NAME
    return cleaned[:MAX_PASSKEY_NAME_LENGTH]


class PasskeyService:
    def __init__(
        self,
        db: Callable,
        store: PendingStore,
        rp_id: str,
        rp_name: str,
        expected_origin: str | list[str],
    ) -> None:
        self._db = db
        self._store = store
        self._rp_id = rp_id
        self._rp_name = rp_name
        self._expected_origin = expected_origin

    def registration_options(self, user_id: int) -> str:
        with self._db() as conn:
            user = conn.execute(
                "SELECT github_login, github_name, webauthn_user_handle FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not user:
                raise PasskeyNotFound()
            user_handle = user["webauthn_user_handle"]
            if user_handle is None:
                user_handle = secrets.token_bytes(32)
                conn.execute(
                    "UPDATE users SET webauthn_user_handle = ? WHERE id = ?",
                    (user_handle, user_id),
                )
            existing = conn.execute(
                "SELECT id FROM webauthn_credentials WHERE user_id = ?", (user_id,)
            ).fetchall()

        options = webauthn.generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=user_handle,
            user_name=user["github_login"],
            user_display_name=user["github_name"] or user["github_login"],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                # Preferred, not required: matches google.com and keeps
                # authenticators without a verifier (e.g. macOS sans Touch ID)
                # usable. The UV flag is a risk signal, not a hard gate.
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(row["id"]))
                for row in existing
            ],
        )
        self._store.put(
            f"webauthn:reg:{user_id}", options.challenge, ttl_seconds=CHALLENGE_TTL_SECONDS
        )
        return webauthn.options_to_json(options)

    def register(
        self, user_id: int, credential: dict[str, Any], name: str | None = None
    ) -> PasskeyInfo:
        challenge = self._store.consume(f"webauthn:reg:{user_id}")
        if challenge is None:
            raise ChallengeExpired()

        try:
            verification = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._expected_origin,
            )
            parsed = parse_registration_credential_json(credential)
        except webauthn_exceptions.WebAuthnException as error:
            raise RegistrationFailed() from error

        credential_id = bytes_to_base64url(verification.credential_id)
        transports = [transport.value for transport in parsed.response.transports or []]
        passkey_name = _normalize_name(name)
        try:
            with self._db() as conn:
                row = conn.execute(
                    """INSERT INTO webauthn_credentials
                    (id, user_id, public_key, sign_count, transports, backed_up, name)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    RETURNING id, name, backed_up, created_at, last_used_at""",
                    (
                        credential_id,
                        user_id,
                        verification.credential_public_key,
                        verification.sign_count,
                        json.dumps(transports),
                        int(verification.credential_backed_up),
                        passkey_name,
                    ),
                ).fetchone()
        except IntegrityError as error:
            raise RegistrationFailed() from error
        return _info(row)

    def authentication_options(self) -> str:
        options = webauthn.generate_authentication_options(
            rp_id=self._rp_id,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        challenge_key = bytes_to_base64url(options.challenge)
        self._store.put(
            f"webauthn:auth:{challenge_key}", True, ttl_seconds=CHALLENGE_TTL_SECONDS
        )
        return webauthn.options_to_json(options)

    def authenticate(self, credential: dict[str, Any]) -> int:
        challenge_key = self._client_challenge(credential)
        if self._store.consume(f"webauthn:auth:{challenge_key}") is None:
            raise ChallengeExpired()

        credential_id = credential.get("id")
        if not isinstance(credential_id, str):
            raise AuthenticationFailed()
        with self._db() as conn:
            row = conn.execute(
                "SELECT user_id, public_key, sign_count FROM webauthn_credentials WHERE id = ?",
                (credential_id,),
            ).fetchone()
        if not row:
            raise AuthenticationFailed()

        try:
            verification = webauthn.verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge_key),
                expected_rp_id=self._rp_id,
                expected_origin=self._expected_origin,
                credential_public_key=row["public_key"],
                credential_current_sign_count=row["sign_count"],
            )
        except webauthn_exceptions.WebAuthnException as error:
            raise AuthenticationFailed() from error

        with self._db() as conn:
            conn.execute(
                "UPDATE webauthn_credentials SET sign_count = ?, last_used_at = ? WHERE id = ?",
                (verification.new_sign_count, datetime.now(timezone.utc).isoformat(), credential_id),
            )
        return row["user_id"]

    def list(self, user_id: int) -> list[PasskeyInfo]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id, name, backed_up, created_at, last_used_at "
                "FROM webauthn_credentials WHERE user_id = ? ORDER BY created_at, id",
                (user_id,),
            ).fetchall()
        return [_info(row) for row in rows]

    def delete(self, user_id: int, credential_id: str) -> None:
        with self._db() as conn:
            deleted = conn.execute(
                "DELETE FROM webauthn_credentials WHERE id = ? AND user_id = ?",
                (credential_id, user_id),
            ).rowcount
        if not deleted:
            raise PasskeyNotFound()

    @staticmethod
    def _client_challenge(credential: dict[str, Any]) -> str:
        try:
            client_data = parse_client_data_json(
                base64url_to_bytes(credential["response"]["clientDataJSON"])
            )
        except (KeyError, TypeError, webauthn_exceptions.WebAuthnException) as error:
            raise AuthenticationFailed() from error
        return bytes_to_base64url(client_data.challenge)


def _info(row) -> PasskeyInfo:
    return PasskeyInfo(
        id=row["id"],
        name=row["name"],
        backed_up=bool(row["backed_up"]),
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
    )
