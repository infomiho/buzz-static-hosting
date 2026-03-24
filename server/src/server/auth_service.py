from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .github import GitHubClient

SESSION_TOKEN_PREFIX = "buzz_sess_"
DEPLOY_TOKEN_PREFIX = "buzz_deploy_"


@dataclass(frozen=True)
class User:
    id: int
    github_login: str
    github_name: str | None


@dataclass(frozen=True)
class Identity:
    user: User
    token_type: str
    site_name: str | None = None

    def can_deploy_to(self, subdomain: str) -> bool:
        if self.site_name is None:
            return True
        return self.site_name == subdomain


@dataclass(frozen=True)
class LoginResult:
    token: str
    user: User


@dataclass(frozen=True)
class CreatedToken:
    id_prefix: str
    raw_token: str
    name: str
    site_name: str


@dataclass(frozen=True)
class DeployTokenInfo:
    id_prefix: str
    name: str
    site_name: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None


class SiteNotFound(Exception):
    pass


class NotSiteOwner(Exception):
    pass


class TokenNotFound(Exception):
    pass


class InvalidSession(Exception):
    pass


class DeviceFlowPending(Exception):
    pass


class DeviceFlowSlowDown(Exception):
    def __init__(self, interval: int):
        self.interval = interval


class DeviceFlowExpired(Exception):
    pass


class DeviceFlowDenied(Exception):
    pass


class DeviceFlowFailed(Exception):
    def __init__(self, detail: str):
        self.detail = detail


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_session_token() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def _generate_deploy_token() -> str:
    return DEPLOY_TOKEN_PREFIX + secrets.token_urlsafe(32)


class AuthService:
    def __init__(self, db: Callable, github: GitHubClient | None = None, github_client_id: str | None = None) -> None:
        self._db = db
        self._github = github
        self._github_client_id = github_client_id
        self._pending_codes: dict[str, dict] = {}

    def authenticate(self, bearer_token: str | None) -> Identity | None:
        if not bearer_token:
            return None

        token = bearer_token.removeprefix("Bearer ")
        if not token:
            return None

        token_hash = _hash_token(token)
        now = datetime.now().isoformat()

        if token.startswith(SESSION_TOKEN_PREFIX):
            return self._resolve_session(token_hash, now)

        if token.startswith(DEPLOY_TOKEN_PREFIX):
            return self._resolve_deploy_token(token_hash, now)

        return None

    def start_device_flow(self) -> dict:
        if not self._github or not self._github_client_id:
            raise DeviceFlowFailed("GitHub OAuth not configured")

        result = self._github.start_device_flow(self._github_client_id)

        if "device_code" not in result:
            raise DeviceFlowFailed("Failed to start device flow")

        self._pending_codes[result["device_code"]] = {
            "expires_at": datetime.now() + timedelta(seconds=result.get("expires_in", 900)),
        }

        return {
            "device_code": result["device_code"],
            "user_code": result["user_code"],
            "verification_uri": result.get("verification_uri", "https://github.com/login/device"),
            "interval": result.get("interval", 5),
            "expires_in": result.get("expires_in", 900),
        }

    def poll_device_flow(self, device_code: str) -> LoginResult:
        if not self._github or not self._github_client_id:
            raise DeviceFlowFailed("GitHub OAuth not configured")

        pending = self._pending_codes.get(device_code)
        if not pending:
            raise DeviceFlowExpired()

        if datetime.now() > pending["expires_at"]:
            del self._pending_codes[device_code]
            raise DeviceFlowExpired()

        result = self._github.poll_device_flow(self._github_client_id, device_code)

        if "error" in result:
            error = result["error"]
            if error == "authorization_pending":
                raise DeviceFlowPending()
            elif error == "slow_down":
                raise DeviceFlowSlowDown(result.get("interval", 10))
            elif error == "expired_token":
                del self._pending_codes[device_code]
                raise DeviceFlowExpired()
            elif error == "access_denied":
                del self._pending_codes[device_code]
                raise DeviceFlowDenied()
            else:
                del self._pending_codes[device_code]
                raise DeviceFlowFailed(result.get("error_description", error))

        access_token = result["access_token"]
        github_user = self._github.get_user(access_token)

        user = self._upsert_user(github_user)
        token = self._create_session(user.id)

        del self._pending_codes[device_code]

        return LoginResult(token=token, user=user)

    def _upsert_user(self, github_user: dict) -> User:
        with self._db() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE github_id = ?", (github_user["id"],)
            ).fetchone()

            if existing:
                user_id = existing["id"]
                conn.execute(
                    "UPDATE users SET github_login = ?, github_name = ? WHERE id = ?",
                    (github_user["login"], github_user.get("name"), user_id),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
                    (github_user["id"], github_user["login"], github_user.get("name")),
                )
                user_id = cursor.lastrowid

        return User(id=user_id, github_login=github_user["login"], github_name=github_user.get("name"))

    def _create_session(self, user_id: int) -> str:
        token = _generate_session_token()
        token_hash = _hash_token(token)
        expires_at = datetime.now() + timedelta(days=30)
        with self._db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at.isoformat()),
            )
        return token

    def _resolve_session(self, token_hash: str, now: str) -> Identity | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT s.user_id, u.github_login, u.github_name "
                "FROM sessions s JOIN users u ON s.user_id = u.id "
                "WHERE s.id = ? AND s.expires_at > ?",
                (token_hash, now),
            ).fetchone()
        if not row:
            return None
        return Identity(
            user=User(id=row["user_id"], github_login=row["github_login"], github_name=row["github_name"]),
            token_type="session",
        )

    def logout(self, raw_token: str) -> None:
        token = raw_token.removeprefix("Bearer ")
        if not token or not token.startswith(SESSION_TOKEN_PREFIX):
            raise InvalidSession()
        with self._db() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (_hash_token(token),))

    def create_deploy_token(self, user_id: int, site_name: str, name: str = "Deployment token") -> CreatedToken:
        with self._db() as conn:
            site = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (site_name,)).fetchone()
        if not site:
            raise SiteNotFound()
        if site["owner_id"] != user_id:
            raise NotSiteOwner()

        token = _generate_deploy_token()
        token_hash = _hash_token(token)
        with self._db() as conn:
            conn.execute(
                "INSERT INTO deployment_tokens (id, name, site_name, user_id) VALUES (?, ?, ?, ?)",
                (token_hash, name, site_name, user_id),
            )
        return CreatedToken(id_prefix=token_hash[:16], raw_token=token, name=name, site_name=site_name)

    def list_deploy_tokens(self, user_id: int) -> list[DeployTokenInfo]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id, name, site_name, created_at, expires_at, last_used_at "
                "FROM deployment_tokens WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [
            DeployTokenInfo(
                id_prefix=r["id"][:16],
                name=r["name"],
                site_name=r["site_name"],
                created_at=r["created_at"],
                expires_at=r["expires_at"],
                last_used_at=r["last_used_at"],
            )
            for r in rows
        ]

    def delete_deploy_token(self, user_id: int, token_id_prefix: str) -> None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT id FROM deployment_tokens WHERE id LIKE ? AND user_id = ?",
                (token_id_prefix + "%", user_id),
            ).fetchone()
            if not row:
                raise TokenNotFound()
            conn.execute("DELETE FROM deployment_tokens WHERE id = ?", (row["id"],))

    def _resolve_deploy_token(self, token_hash: str, now: str) -> Identity | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT dt.user_id, dt.site_name, u.github_login, u.github_name "
                "FROM deployment_tokens dt JOIN users u ON dt.user_id = u.id "
                "WHERE dt.id = ? AND (dt.expires_at IS NULL OR dt.expires_at > ?)",
                (token_hash, now),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE deployment_tokens SET last_used_at = ? WHERE id = ?",
                (now, token_hash),
            )
        return Identity(
            user=User(id=row["user_id"], github_login=row["github_login"], github_name=row["github_name"]),
            token_type="deploy",
            site_name=row["site_name"],
        )
