import hashlib
import secrets
from datetime import datetime, timedelta

import pytest

from server.auth_service import (
    AccessDenied, AuthService, Identity, User,
    DeviceFlowDenied, DeviceFlowExpired, DeviceFlowPending, DeviceFlowSlowDown,
    InvalidSession, SiteNotFound, NotSiteOwner, TokenNotFound,
)
from server.github import FakeGitHubClient


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _insert_user(conn, github_id=42, login="alice", name="Alice") -> int:
    cursor = conn.execute(
        "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
        (github_id, login, name),
    )
    return cursor.lastrowid


def _insert_session(conn, token: str, user_id: int, expires_at: datetime) -> None:
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash(token), user_id, expires_at.isoformat()),
    )


def _insert_site(conn, name: str, owner_id: int) -> None:
    conn.execute(
        "INSERT INTO sites (name, owner_id, size_bytes) VALUES (?, ?, ?)",
        (name, owner_id, 1024),
    )


def _insert_deploy_token(conn, token: str, user_id: int, site_name: str, expires_at=None) -> None:
    conn.execute(
        "INSERT INTO deployment_tokens (id, name, site_name, user_id, expires_at) VALUES (?, ?, ?, ?, ?)",
        (_hash(token), "test token", site_name, user_id, expires_at.isoformat() if expires_at else None),
    )


class TestAuthenticate:
    def test_valid_session_token(self, database):
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_session(conn, token, user_id, datetime.now() + timedelta(days=30))

        auth = AuthService(db=database.connect)
        identity = auth.authenticate(f"Bearer {token}")

        assert identity is not None
        assert identity.user.id == user_id
        assert identity.user.github_login == "alice"
        assert identity.token_type == "session"
        assert identity.site_name is None

    def test_expired_session_returns_none(self, database):
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_session(conn, token, user_id, datetime.now() - timedelta(days=1))

        auth = AuthService(db=database.connect)
        assert auth.authenticate(f"Bearer {token}") is None

    def test_valid_deploy_token(self, database):
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)
            _insert_deploy_token(conn, token, user_id, "my-site")

        auth = AuthService(db=database.connect)
        identity = auth.authenticate(f"Bearer {token}")

        assert identity is not None
        assert identity.user.id == user_id
        assert identity.token_type == "deploy"
        assert identity.site_name == "my-site"

    def test_expired_deploy_token_returns_none(self, database):
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)
            _insert_deploy_token(conn, token, user_id, "my-site", expires_at=datetime.now() - timedelta(days=1))

        auth = AuthService(db=database.connect)
        assert auth.authenticate(f"Bearer {token}") is None

    def test_deploy_token_updates_last_used(self, database):
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)
            _insert_deploy_token(conn, token, user_id, "my-site")

        auth = AuthService(db=database.connect)
        auth.authenticate(f"Bearer {token}")

        with database.connect() as conn:
            row = conn.execute(
                "SELECT last_used_at FROM deployment_tokens WHERE id = ?", (_hash(token),)
            ).fetchone()
        assert row["last_used_at"] is not None

    def test_no_token_returns_none(self, database):
        auth = AuthService(db=database.connect)
        assert auth.authenticate(None) is None
        assert auth.authenticate("") is None

    def test_unknown_prefix_returns_none(self, database):
        auth = AuthService(db=database.connect)
        assert auth.authenticate("Bearer unknown_prefix_abc123") is None

    def test_bearer_only_returns_none(self, database):
        auth = AuthService(db=database.connect)
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
    def _make_auth(self, connect, **github_overrides):
        github = FakeGitHubClient()
        for k, v in github_overrides.items():
            setattr(github, k, v)
        return AuthService(db=connect, github=github, github_client_id="test-client-id")

    def test_full_login_flow(self, database):
        auth = self._make_auth(database.connect)

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

    def test_poll_pending(self, database):
        auth = self._make_auth(database.connect, poll_response={"error": "authorization_pending"})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowPending):
            auth.poll_device_flow(start["device_code"])

    def test_poll_slow_down(self, database):
        auth = self._make_auth(database.connect, poll_response={"error": "slow_down", "interval": 10})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowSlowDown) as exc_info:
            auth.poll_device_flow(start["device_code"])
        assert exc_info.value.interval == 10

    def test_poll_expired(self, database):
        auth = self._make_auth(database.connect, poll_response={"error": "expired_token"})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowExpired):
            auth.poll_device_flow(start["device_code"])

    def test_poll_denied(self, database):
        auth = self._make_auth(database.connect, poll_response={"error": "access_denied"})

        start = auth.start_device_flow()
        with pytest.raises(DeviceFlowDenied):
            auth.poll_device_flow(start["device_code"])

    def test_poll_unknown_device_code(self, database):
        auth = self._make_auth(database.connect)

        with pytest.raises(DeviceFlowExpired):
            auth.poll_device_flow("nonexistent")

    def test_upserts_existing_user(self, database):
        with database.connect() as conn:
            _insert_user(conn, github_id=42, login="old_login", name="Old Name")

        auth = self._make_auth(database.connect, user={"id": 42, "login": "new_login", "name": "New Name"})

        start = auth.start_device_flow()
        result = auth.poll_device_flow(start["device_code"])

        assert result.user.github_login == "new_login"
        assert result.user.github_name == "New Name"

        with database.connect() as conn:
            row = conn.execute("SELECT github_login FROM users WHERE github_id = 42").fetchone()
        assert row["github_login"] == "new_login"

    def test_creates_new_user(self, database):
        auth = self._make_auth(database.connect, user={"id": 99, "login": "newuser", "name": "New"})

        start = auth.start_device_flow()
        result = auth.poll_device_flow(start["device_code"])

        assert result.user.github_login == "newuser"

        with database.connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE github_id = 99").fetchone()
        assert row is not None


class TestLogout:
    def test_logout_invalidates_session(self, database):
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_session(conn, token, user_id, datetime.now() + timedelta(days=30))

        auth = AuthService(db=database.connect)
        assert auth.authenticate(f"Bearer {token}") is not None

        auth.logout(f"Bearer {token}")
        assert auth.authenticate(f"Bearer {token}") is None

    def test_logout_invalid_token_raises(self, database):
        auth = AuthService(db=database.connect)
        with pytest.raises(InvalidSession):
            auth.logout("Bearer buzz_deploy_abc")

    def test_logout_empty_raises(self, database):
        auth = AuthService(db=database.connect)
        with pytest.raises(InvalidSession):
            auth.logout("")


class TestDeployTokenCrud:
    def test_create_and_list(self, database):
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)

        auth = AuthService(db=database.connect)
        created = auth.create_deploy_token(user_id, "my-site", "CI deploy")

        assert created.raw_token.startswith("buzz_deploy_")
        assert created.name == "CI deploy"
        assert created.site_name == "my-site"

        tokens = auth.list_deploy_tokens(user_id)
        assert len(tokens) == 1
        assert tokens[0].site_name == "my-site"
        assert tokens[0].name == "CI deploy"

    def test_create_site_not_found(self, database):
        with database.connect() as conn:
            user_id = _insert_user(conn)
        auth = AuthService(db=database.connect)

        with pytest.raises(SiteNotFound):
            auth.create_deploy_token(user_id, "nonexistent", "token")

    def test_create_not_owner(self, database):
        with database.connect() as conn:
            owner_id = _insert_user(conn, github_id=1, login="owner")
            other_id = _insert_user(conn, github_id=2, login="other")
            _insert_site(conn, "my-site", owner_id)

        auth = AuthService(db=database.connect)
        with pytest.raises(NotSiteOwner):
            auth.create_deploy_token(other_id, "my-site", "token")

    def test_delete(self, database):
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)

        auth = AuthService(db=database.connect)
        created = auth.create_deploy_token(user_id, "my-site")
        assert len(auth.list_deploy_tokens(user_id)) == 1

        auth.delete_deploy_token(user_id, created.id_prefix)
        assert len(auth.list_deploy_tokens(user_id)) == 0

    def test_delete_not_found(self, database):
        with database.connect() as conn:
            user_id = _insert_user(conn)
        auth = AuthService(db=database.connect)

        with pytest.raises(TokenNotFound):
            auth.delete_deploy_token(user_id, "nonexistent")

    def test_created_token_authenticates(self, database):
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)

        auth = AuthService(db=database.connect)
        created = auth.create_deploy_token(user_id, "my-site")

        identity = auth.authenticate(f"Bearer {created.raw_token}")
        assert identity is not None
        assert identity.token_type == "deploy"
        assert identity.site_name == "my-site"


class TestAccessControl:
    def _make_auth(self, connect, allow_registration=True, allowed_github_users=None, **github_overrides):
        github = FakeGitHubClient()
        for k, v in github_overrides.items():
            setattr(github, k, v)
        return AuthService(
            db=connect,
            github=github,
            github_client_id="test-client-id",
            allow_registration=allow_registration,
            allowed_github_users=allowed_github_users,
        )

    def _login(self, auth):
        start = auth.start_device_flow()
        return auth.poll_device_flow(start["device_code"])

    def test_registration_off_keeps_existing_user(self, database):
        with database.connect() as conn:
            _insert_user(conn)

        auth = self._make_auth(database.connect, allow_registration=False)
        result = self._login(auth)

        assert result.user.github_login == "alice"

    def test_registration_off_denies_new_user(self, database):
        auth = self._make_auth(database.connect, allow_registration=False)

        with pytest.raises(AccessDenied):
            self._login(auth)

        with database.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 0

    def test_allowlist_wins_over_disabled_registration(self, database):
        auth = self._make_auth(
            database.connect, allow_registration=False, allowed_github_users=frozenset({"alice"})
        )

        result = self._login(auth)

        assert result.user.github_login == "alice"
        with database.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 1

    def test_allowlist_denies_unlisted_new_user(self, database):
        auth = self._make_auth(database.connect, allowed_github_users=frozenset({"bob"}))

        with pytest.raises(AccessDenied):
            self._login(auth)

    def test_allowlist_matches_case_insensitively(self, database):
        auth = self._make_auth(
            database.connect,
            allowed_github_users=frozenset({"alice"}),
            user={"id": 42, "login": "AlIcE", "name": "Alice"},
        )

        result = self._login(auth)

        assert result.user.github_login == "AlIcE"

    def test_allowlist_revokes_existing_session(self, database):
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_session(conn, token, user_id, datetime.now() + timedelta(days=30))

        auth = AuthService(db=database.connect, allowed_github_users=frozenset({"bob"}))

        with pytest.raises(AccessDenied):
            auth.authenticate(f"Bearer {token}")

    def test_allowlist_revokes_existing_deploy_token(self, database):
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)
            _insert_deploy_token(conn, token, user_id, "my-site")

        auth = AuthService(db=database.connect, allowed_github_users=frozenset({"bob"}))

        with pytest.raises(AccessDenied):
            auth.authenticate(f"Bearer {token}")

    def test_allowlist_keeps_listed_existing_user(self, database):
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_session(conn, token, user_id, datetime.now() + timedelta(days=30))

        auth = AuthService(db=database.connect, allowed_github_users=frozenset({"alice"}))
        identity = auth.authenticate(f"Bearer {token}")

        assert identity is not None
        assert identity.user.id == user_id

    def test_registration_toggle_does_not_revoke_sessions(self, database):
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_session(conn, token, user_id, datetime.now() + timedelta(days=30))

        auth = AuthService(db=database.connect, allow_registration=False)
        identity = auth.authenticate(f"Bearer {token}")

        assert identity is not None

    def test_denied_login_consumes_device_code(self, database):
        auth = self._make_auth(database.connect, allow_registration=False)
        start = auth.start_device_flow()

        with pytest.raises(AccessDenied):
            auth.poll_device_flow(start["device_code"])

        with pytest.raises(DeviceFlowExpired):
            auth.poll_device_flow(start["device_code"])

    def test_revoked_deploy_token_does_not_update_last_used(self, database):
        token = "buzz_deploy_" + secrets.token_urlsafe(32)
        with database.connect() as conn:
            user_id = _insert_user(conn)
            _insert_site(conn, "my-site", user_id)
            _insert_deploy_token(conn, token, user_id, "my-site")

        auth = AuthService(db=database.connect, allowed_github_users=frozenset({"bob"}))

        with pytest.raises(AccessDenied):
            auth.authenticate(f"Bearer {token}")

        with database.connect() as conn:
            row = conn.execute(
                "SELECT last_used_at FROM deployment_tokens WHERE id = ?", (_hash(token),)
            ).fetchone()
        assert row["last_used_at"] is None
