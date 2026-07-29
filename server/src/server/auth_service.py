from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .exceptions import Forbidden
from .github_login import GitHubUser

logger = logging.getLogger(__name__)

SESSION_TOKEN_PREFIX = "buzz_sess_"
DEPLOY_TOKEN_PREFIX = "buzz_deploy_"
DEV_SESSION_ID = hashlib.sha256(b"buzz_dev_session").hexdigest()


@dataclass(frozen=True)
class User:
    id: int
    github_login: str
    github_name: str | None
    control_admitted: bool = True


@dataclass(frozen=True)
class Identity:
    user: User
    token_type: str
    site_name: str | None = None
    session_id: str | None = None

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


class AccessDenied(Forbidden):
    def __init__(self, github_login: str):
        self.github_login = github_login
        super().__init__(
            f"GitHub account '{github_login}' is not allowed on this Buzz server. "
            "Ask the server operator for access."
        )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_session_token() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def _generate_deploy_token() -> str:
    return DEPLOY_TOKEN_PREFIX + secrets.token_urlsafe(32)


class AuthService:
    def __init__(
        self,
        db: Callable,
        allow_registration: bool = True,
        allowed_github_users: frozenset[str] | None = None,
    ) -> None:
        self._db = db
        self._allow_registration = allow_registration
        self._allowed_github_users = frozenset(
            login.lower() for login in (allowed_github_users or frozenset())
        )

    def _ensure_allowed(self, login: str, *, is_new_user: bool, github_id: int | None = None) -> None:
        if self._allowed_github_users:
            if login.lower() not in self._allowed_github_users:
                logger.warning(
                    "Blocked GitHub user %r (github_id=%s): not in BUZZ_ALLOWED_GITHUB_USERS",
                    login, github_id,
                )
                raise AccessDenied(login)
            return
        if is_new_user and not self._allow_registration:
            logger.warning(
                "Blocked new GitHub user %r (github_id=%s): registration is disabled",
                login, github_id,
            )
            raise AccessDenied(login)

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

    def user_is_allowed(self, user_id: int) -> bool:
        """Return whether a Principal is admitted to the control plane."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT github_login, control_admitted FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return bool(row and self._control_allowed_row(row))

    def _control_allowed_row(self, row) -> bool:
        if not row["control_admitted"]:
            return False
        return not self._allowed_github_users or (
            row["github_login"].lower() in self._allowed_github_users
        )

    def _can_gain_control(self, login: str) -> bool:
        if self._allowed_github_users:
            return login.lower() in self._allowed_github_users
        return self._allow_registration

    def login_with_github(self, github_user: GitHubUser) -> LoginResult:
        """Resolve a GitHub identity to a Buzz user and mint a session."""
        user = self._upsert_user(github_user)
        return LoginResult(token=self._create_session(user.id), user=user)

    def _upsert_user(self, github_user: GitHubUser) -> User:
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id, control_admitted FROM users WHERE github_id = ?",
                (github_user.id,),
            ).fetchone()

            if existing:
                user_id = existing["id"]
                control_admitted = bool(existing["control_admitted"])
                if not control_admitted and self._can_gain_control(github_user.login):
                    control_admitted = True
                conn.execute(
                    "UPDATE users SET github_login = ?, github_name = ?, "
                    "control_admitted = ? WHERE id = ?",
                    (
                        github_user.login,
                        github_user.name,
                        int(control_admitted),
                        user_id,
                    ),
                )
            else:
                self._ensure_allowed(
                    github_user.login, is_new_user=True, github_id=github_user.id
                )
                cursor = conn.execute(
                    "INSERT INTO users "
                    "(github_id, github_login, github_name, control_admitted) "
                    "VALUES (?, ?, ?, 1)",
                    (github_user.id, github_user.login, github_user.name),
                )
                user_id = cursor.lastrowid
                control_admitted = True

            self._upsert_github_identity(conn, user_id, github_user, authenticated=True)

        return User(
            id=user_id,
            github_login=github_user.login,
            github_name=github_user.name,
            control_admitted=control_admitted,
        )

    def ensure_github_principal(self, github_user: GitHubUser) -> User:
        """Resolve a selected GitHub account without granting control access."""
        with self._db() as conn:
            principal = conn.execute(
                "INSERT INTO users "
                "(github_id, github_login, github_name, control_admitted) "
                "VALUES (?, ?, ?, 0) "
                "ON CONFLICT(github_id) DO UPDATE SET "
                "github_login = excluded.github_login, "
                "github_name = excluded.github_name "
                "RETURNING id, control_admitted",
                (github_user.id, github_user.login, github_user.name),
            ).fetchone()
            user_id = principal["id"]
            control_admitted = bool(principal["control_admitted"])
            self._upsert_github_identity(conn, user_id, github_user, authenticated=False)
        return User(
            id=user_id,
            github_login=github_user.login,
            github_name=github_user.name,
            control_admitted=control_admitted,
        )

    @staticmethod
    def _upsert_github_identity(
        conn, user_id: int, github_user: GitHubUser, *, authenticated: bool
    ) -> None:
        conn.execute(
            "INSERT INTO principal_identities "
            "(user_id, provider, subject, login_snapshot, name_snapshot, "
            "avatar_url_snapshot, last_authenticated_at) "
            "VALUES (?, 'github', ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, subject) DO UPDATE SET "
            "login_snapshot = excluded.login_snapshot, "
            "name_snapshot = excluded.name_snapshot, "
            "avatar_url_snapshot = excluded.avatar_url_snapshot, "
            "last_authenticated_at = COALESCE(excluded.last_authenticated_at, last_authenticated_at)",
            (
                user_id,
                str(github_user.id),
                github_user.login,
                github_user.name,
                github_user.avatar_url,
                datetime.now().isoformat() if authenticated else None,
            ),
        )

    def login_by_user_id(self, user_id: int) -> LoginResult:
        """Session for an already-authenticated user (passkey or device grant)."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT id, github_login, github_name, control_admitted "
                "FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            raise InvalidSession()
        if not self._control_allowed_row(row):
            raise AccessDenied(row["github_login"])
        user = User(
            id=row["id"],
            github_login=row["github_login"],
            github_name=row["github_name"],
            control_admitted=bool(row["control_admitted"]),
        )
        return LoginResult(token=self._create_session(user.id), user=user)

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
                "SELECT s.user_id, u.github_login, u.github_name, u.control_admitted "
                "FROM sessions s JOIN users u ON s.user_id = u.id "
                "WHERE s.id = ? AND s.expires_at > ?",
                (token_hash, now),
            ).fetchone()
        if not row:
            return None
        return Identity(
            user=User(
                id=row["user_id"],
                github_login=row["github_login"],
                github_name=row["github_name"],
                control_admitted=bool(row["control_admitted"]),
            ),
            token_type="session",
            session_id=token_hash,
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
                "SELECT dt.user_id, dt.site_name, u.github_login, u.github_name, "
                "u.control_admitted "
                "FROM deployment_tokens dt JOIN users u ON dt.user_id = u.id "
                "WHERE dt.id = ? AND (dt.expires_at IS NULL OR dt.expires_at > ?)",
                (token_hash, now),
            ).fetchone()
            if not row:
                return None
            if not self._control_allowed_row(row):
                raise AccessDenied(row["github_login"])
            conn.execute(
                "UPDATE deployment_tokens SET last_used_at = ? WHERE id = ?",
                (now, token_hash),
            )
        return Identity(
            user=User(
                id=row["user_id"],
                github_login=row["github_login"],
                github_name=row["github_name"],
                control_admitted=bool(row["control_admitted"]),
            ),
            token_type="deploy",
            site_name=row["site_name"],
        )
