import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from server.auth_service import (
    AuthService, Identity, User,
    DeviceFlowDenied, DeviceFlowExpired, DeviceFlowPending, DeviceFlowSlowDown,
    InvalidSession, SiteNotFound, NotSiteOwner, TokenNotFound,
)
from server.github import FakeGitHubClient


SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id INTEGER UNIQUE NOT NULL,
    github_login TEXT NOT NULL,
    github_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE deployment_tokens (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    site_name TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    last_used_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE sites (
    name TEXT PRIMARY KEY,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    size_bytes INTEGER,
    owner_id INTEGER
);
"""

_test_conn: sqlite3.Connection | None = None


def make_test_db():
    global _test_conn
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _test_conn = conn

    @contextmanager
    def db():
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return db


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _insert_user(conn, github_id=42, login="alice", name="Alice") -> int:
    cursor = conn.execute(
        "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
        (github_id, login, name),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_session(conn, token: str, user_id: int, expires_at: datetime) -> None:
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash(token), user_id, expires_at.isoformat()),
    )
    conn.commit()


def _insert_deploy_token(conn, token: str, user_id: int, site_name: str, expires_at=None) -> None:
    conn.execute(
        "INSERT INTO deployment_tokens (id, name, site_name, user_id, expires_at) VALUES (?, ?, ?, ?, ?)",
        (_hash(token), "test token", site_name, user_id, expires_at.isoformat() if expires_at else None),
    )
    conn.commit()


class TestAuthenticate:
    def test_valid_session_token(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        _insert_session(_test_conn, token, user_id, datetime.now() + timedelta(days=30))

        auth = AuthService(db=db)
        identity = auth.authenticate(f"Bearer {token}")

        assert identity is not None
        assert identity.user.id == user_id
        assert identity.user.github_login == "alice"
        assert identity.token_type == "session"
        assert identity.site_name is None

    def test_expired_session_returns_none(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        _insert_session(_test_conn, token, user_id, datetime.now() - timedelta(days=1))

        auth = AuthService(db=db)
        assert auth.authenticate(f"Bearer {token}") is None

    def test_valid_deploy_token(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        _insert_deploy_token(_test_conn, token, user_id, "my-site")

        auth = AuthService(db=db)
        identity = auth.authenticate(f"Bearer {token}")

        assert identity is not None
        assert identity.user.id == user_id
        assert identity.token_type == "deploy"
        assert identity.site_name == "my-site"

    def test_expired_deploy_token_returns_none(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        _insert_deploy_token(_test_conn, token, user_id, "my-site", expires_at=datetime.now() - timedelta(days=1))

        auth = AuthService(db=db)
        assert auth.authenticate(f"Bearer {token}") is None

    def test_deploy_token_updates_last_used(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        _insert_deploy_token(_test_conn, token, user_id, "my-site")

        auth = AuthService(db=db)
        auth.authenticate(f"Bearer {token}")

        row = _test_conn.execute(
            "SELECT last_used_at FROM deployment_tokens WHERE id = ?", (_hash(token),)
        ).fetchone()
        assert row["last_used_at"] is not None

    def test_no_token_returns_none(self):
        db = make_test_db()
        auth = AuthService(db=db)
        assert auth.authenticate(None) is None
        assert auth.authenticate("") is None

    def test_unknown_prefix_returns_none(self):
        db = make_test_db()
        auth = AuthService(db=db)
        assert auth.authenticate("Bearer unknown_prefix_abc123") is None

    def test_bearer_only_returns_none(self):
        db = make_test_db()
        auth = AuthService(db=db)
        assert auth.authenticate("Bearer ") is None


class TestCanDeployTo:
    def test_session_token_can_deploy_anywhere(self):
        identity = Identity(
            user=User(id=1, github_login="alice", github_name="Alice"),
            token_type="session",
        )
        assert identity.can_deploy_to("any-site") is True
        assert identity.can_deploy_to("other-site") is True

    def test_deploy_token_scoped_to_matching_site(self):
        identity = Identity(
            user=User(id=1, github_login="alice", github_name="Alice"),
            token_type="deploy",
            site_name="my-site",
        )
        assert identity.can_deploy_to("my-site") is True

    def test_deploy_token_rejects_different_site(self):
        identity = Identity(
            user=User(id=1, github_login="alice", github_name="Alice"),
            token_type="deploy",
            site_name="my-site",
        )
        assert identity.can_deploy_to("other-site") is False


class TestDeviceFlow:
    def _make_auth(self, db, **github_overrides):
        github = FakeGitHubClient()
        for k, v in github_overrides.items():
            setattr(github, k, v)
        return AuthService(db=db, github=github, github_client_id="test-client-id")

    def test_full_login_flow(self):
        db = make_test_db()
        auth = self._make_auth(db)

        start = auth.start_device_flow()
        assert "device_code" in start
        assert "user_code" in start

        result = auth.poll_device_flow(start["device_code"])
        assert result.token.startswith("buzz_sess_")
        assert result.user.github_login == "alice"

        # Session token works
        identity = auth.authenticate(f"Bearer {result.token}")
        assert identity is not None
        assert identity.user.github_login == "alice"

    def test_poll_pending(self):
        db = make_test_db()
        auth = self._make_auth(db, poll_response={"error": "authorization_pending"})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowPending):
            auth.poll_device_flow(start["device_code"])

    def test_poll_slow_down(self):
        db = make_test_db()
        auth = self._make_auth(db, poll_response={"error": "slow_down", "interval": 10})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowSlowDown) as exc_info:
            auth.poll_device_flow(start["device_code"])
        assert exc_info.value.interval == 10

    def test_poll_expired(self):
        db = make_test_db()
        auth = self._make_auth(db, poll_response={"error": "expired_token"})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowExpired):
            auth.poll_device_flow(start["device_code"])

    def test_poll_denied(self):
        db = make_test_db()
        auth = self._make_auth(db, poll_response={"error": "access_denied"})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowDenied):
            auth.poll_device_flow(start["device_code"])

    def test_poll_unknown_device_code(self):
        db = make_test_db()
        auth = self._make_auth(db)

        with pytest.raises(DeviceFlowExpired):
            auth.poll_device_flow("nonexistent")

    def test_upserts_existing_user(self):
        db = make_test_db()
        _insert_user(_test_conn, github_id=42, login="old_login", name="Old Name")

        auth = self._make_auth(db, user={"id": 42, "login": "new_login", "name": "New Name"})

        start = auth.start_device_flow()
        result = auth.poll_device_flow(start["device_code"])

        assert result.user.github_login == "new_login"
        assert result.user.github_name == "New Name"

        row = _test_conn.execute("SELECT github_login FROM users WHERE github_id = 42").fetchone()
        assert row["github_login"] == "new_login"

    def test_creates_new_user(self):
        db = make_test_db()
        auth = self._make_auth(db, user={"id": 99, "login": "newuser", "name": "New"})

        start = auth.start_device_flow()
        result = auth.poll_device_flow(start["device_code"])

        assert result.user.github_login == "newuser"

        row = _test_conn.execute("SELECT id FROM users WHERE github_id = 99").fetchone()
        assert row is not None


def _insert_site(conn, name: str, owner_id: int) -> None:
    conn.execute(
        "INSERT INTO sites (name, owner_id, size_bytes) VALUES (?, ?, ?)",
        (name, owner_id, 1024),
    )
    conn.commit()


class TestLogout:
    def test_logout_invalidates_session(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        _insert_session(_test_conn, token, user_id, datetime.now() + timedelta(days=30))

        auth = AuthService(db=db)
        assert auth.authenticate(f"Bearer {token}") is not None

        auth.logout(f"Bearer {token}")
        assert auth.authenticate(f"Bearer {token}") is None

    def test_logout_invalid_token_raises(self):
        db = make_test_db()
        auth = AuthService(db=db)
        with pytest.raises(InvalidSession):
            auth.logout("Bearer buzz_deploy_abc")

    def test_logout_empty_raises(self):
        db = make_test_db()
        auth = AuthService(db=db)
        with pytest.raises(InvalidSession):
            auth.logout("")


class TestDeployTokenCrud:
    def test_create_and_list(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        _insert_site(_test_conn, "my-site", user_id)

        auth = AuthService(db=db)
        created = auth.create_deploy_token(user_id, "my-site", "CI deploy")

        assert created.raw_token.startswith("buzz_deploy_")
        assert created.name == "CI deploy"
        assert created.site_name == "my-site"

        tokens = auth.list_deploy_tokens(user_id)
        assert len(tokens) == 1
        assert tokens[0].site_name == "my-site"
        assert tokens[0].name == "CI deploy"

    def test_create_site_not_found(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        auth = AuthService(db=db)

        with pytest.raises(SiteNotFound):
            auth.create_deploy_token(user_id, "nonexistent", "token")

    def test_create_not_owner(self):
        db = make_test_db()
        owner_id = _insert_user(_test_conn, github_id=1, login="owner")
        other_id = _insert_user(_test_conn, github_id=2, login="other")
        _insert_site(_test_conn, "my-site", owner_id)

        auth = AuthService(db=db)
        with pytest.raises(NotSiteOwner):
            auth.create_deploy_token(other_id, "my-site", "token")

    def test_delete(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        _insert_site(_test_conn, "my-site", user_id)

        auth = AuthService(db=db)
        created = auth.create_deploy_token(user_id, "my-site")
        assert len(auth.list_deploy_tokens(user_id)) == 1

        auth.delete_deploy_token(user_id, created.id_prefix)
        assert len(auth.list_deploy_tokens(user_id)) == 0

    def test_delete_not_found(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        auth = AuthService(db=db)

        with pytest.raises(TokenNotFound):
            auth.delete_deploy_token(user_id, "nonexistent")

    def test_created_token_authenticates(self):
        db = make_test_db()
        user_id = _insert_user(_test_conn)
        _insert_site(_test_conn, "my-site", user_id)

        auth = AuthService(db=db)
        created = auth.create_deploy_token(user_id, "my-site")

        identity = auth.authenticate(f"Bearer {created.raw_token}")
        assert identity is not None
        assert identity.token_type == "deploy"
        assert identity.site_name == "my-site"
