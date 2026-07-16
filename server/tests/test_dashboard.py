import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.analytics import init_analytics_schema
from server.app import create_app
from server.auth_service import AuthService
from server.cookies import COOKIE_NAME
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


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def test_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    init_analytics_schema(conn)

    @contextmanager
    def db():
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return db, conn


@pytest.fixture
def app(test_db, monkeypatch):
    db, conn = test_db
    monkeypatch.setattr("server.routes.sites.db", db)
    app = create_app()
    github = FakeGitHubClient()
    app.state.auth_service = AuthService(db=db, github=github, github_client_id="test-id")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def user_and_token(test_db):
    db, conn = test_db
    cursor = conn.execute(
        "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
        (42, "alice", "Alice"),
    )
    conn.commit()
    user_id = cursor.lastrowid

    token = "buzz_sess_" + secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=30)
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash(token), user_id, expires_at.isoformat()),
    )
    conn.commit()
    return user_id, token


class TestRootRoute:
    def test_unauthenticated_shows_login_page(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "Login with GitHub" in res.text

    def test_authenticated_shows_dashboard(self, client, user_and_token):
        _, token = user_and_token
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/")
        assert res.status_code == 200
        assert "Dashboard" in res.text
        assert "alice" in res.text
        assert "Sites" in res.text
        assert "Deploy Tokens" in res.text

    def test_expired_cookie_shows_login(self, test_db, client):
        _, conn = test_db
        conn.execute(
            "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
            (42, "alice", "Alice"),
        )
        conn.commit()

        token = "buzz_sess_" + secrets.token_urlsafe(32)
        expired = datetime.now() - timedelta(days=1)
        conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (_hash(token), 1, expired.isoformat()),
        )
        conn.commit()

        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/")
        assert res.status_code == 200
        assert "Login with GitHub" in res.text


class TestCookieAuthOnApiRoutes:
    def test_get_sites_with_cookie(self, client, user_and_token):
        _, token = user_and_token
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/sites")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_sites_without_auth_returns_401(self, client):
        res = client.get("/sites")
        assert res.status_code == 401


class TestCustomDomains:
    def test_site_detail_shows_disabled_operator_state(
        self, test_db, client, user_and_token, tmp_path, monkeypatch
    ):
        db, conn = test_db
        user_id, token = user_and_token
        conn.execute(
            "INSERT INTO sites (name, owner_id, size_bytes) VALUES ('my-site', ?, 0)",
            (user_id,),
        )
        conn.execute("""CREATE TABLE custom_domain_claims (
            id INTEGER PRIMARY KEY,
            site_name TEXT,
            status TEXT,
            expires_at TEXT
        )""")
        conn.commit()
        (tmp_path / "my-site").mkdir()
        monkeypatch.setattr("server.routes.dashboard.db", db)
        monkeypatch.setattr("server.routes.dashboard.SITES_DIR", tmp_path)
        monkeypatch.setattr("server.config.CUSTOM_DOMAINS_ENABLED", False)
        client.cookies.set(COOKIE_NAME, token)

        response = client.get("/dashboard/sites/my-site")

        assert response.status_code == 200
        assert "Custom domains" in response.text
        assert "control plane is disabled or not ready" in response.text
        assert "Add custom domain" not in response.text

    def test_site_detail_shows_pending_verification_record(
        self, test_db, client, user_and_token, tmp_path, monkeypatch
    ):
        db, conn = test_db
        user_id, token = user_and_token
        conn.execute(
            "INSERT INTO sites (name, owner_id, size_bytes) VALUES ('my-site', ?, 0)",
            (user_id,),
        )
        conn.execute("""CREATE TABLE custom_domain_claims (
            id INTEGER PRIMARY KEY,
            hostname TEXT NOT NULL,
            site_name TEXT,
            verification_token TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            verified_at TEXT,
            last_checked_at TEXT,
            last_error TEXT,
            challenge_token TEXT,
            route_status TEXT NOT NULL DEFAULT 'not_routed',
            route_generation INTEGER NOT NULL DEFAULT 0,
            route_error TEXT,
            route_updated_at TEXT,
            removal_requested_at TEXT,
            withdrawn_at TEXT,
            challenge_seen_at TEXT,
            activated_at TEXT,
            activation_checked_at TEXT,
            activation_error TEXT
        )""")
        conn.execute("""INSERT INTO custom_domain_claims
            (id, hostname, site_name, verification_token, status, created_at, expires_at)
            VALUES (1, 'www.example.com', 'my-site', 'bdv_test', 'pending',
                    '2026-07-16T00:00:00+00:00', '2026-07-17T00:00:00+00:00')""")
        conn.commit()
        (tmp_path / "my-site").mkdir()
        monkeypatch.setattr("server.routes.dashboard.db", db)
        monkeypatch.setattr("server.routes.dashboard.SITES_DIR", tmp_path)
        monkeypatch.setattr("server.config.CUSTOM_DOMAINS_ENABLED", True)
        monkeypatch.setattr("server.config.CUSTOM_DOMAIN_ADMISSION_ENABLED", True)
        monkeypatch.setattr("server.config.CUSTOM_DOMAIN_ROUTING_ENABLED", True)
        monkeypatch.setattr("server.config.CUSTOM_DOMAIN_INGRESS_IPS", frozenset({"8.8.8.8"}))
        monkeypatch.setattr("server.config.TRAEFIK_CONTROL_TOKEN", "configured")
        client.app.state.traefik_control = type(
            "ReadyControlPlane", (), {"is_ready": lambda self: True}
        )()
        client.cookies.set(COOKIE_NAME, token)

        response = client.get("/dashboard/sites/my-site")

        assert response.status_code == 200
        assert "www.example.com" in response.text
        assert "_buzz.www.example.com" in response.text
        assert "buzz-domain-verification=bdv_test" in response.text
        assert "Waiting for DNS verification" in response.text
        assert '<details class="border-2 border-ink" data-domain-claim="1">' in response.text
        assert '<details class="border-2 border-ink" data-domain-claim="1" open>' not in response.text
        assert response.text.index("Analytics") < response.text.index("Custom domains")
        assert response.text.index("Files") < response.text.index("Custom domains")
        assert 'id="remove-domain-dialog"' in response.text
        assert "Buzz will stop tracking its ownership" in response.text
        assert "Add custom domain" in response.text
        assert "1 of 5 aliases used for this site" in response.text


class TestLoginFlow:
    def test_login_start_returns_device_code(self, client):
        res = client.post("/dashboard/login/start")
        assert res.status_code == 200
        data = res.json()
        assert "device_code" in data
        assert "user_code" in data
        assert "verification_uri" in data

    def test_login_poll_pending(self, app, client):
        app.state.auth_service._github.poll_response = {"error": "authorization_pending"}
        start = client.post("/dashboard/login/start").json()
        res = client.post("/dashboard/login/poll", json={"device_code": start["device_code"]})
        assert res.status_code == 200
        assert res.json()["status"] == "pending"
        assert COOKIE_NAME not in res.cookies

    def test_login_poll_success_sets_cookie(self, client):
        start = client.post("/dashboard/login/start").json()
        res = client.post("/dashboard/login/poll", json={"device_code": start["device_code"]})
        assert res.status_code == 200
        assert res.json()["status"] == "complete"
        assert COOKIE_NAME in res.cookies

    def test_login_poll_expired(self, app, client):
        app.state.auth_service._github.poll_response = {"error": "expired_token"}
        start = client.post("/dashboard/login/start").json()
        res = client.post("/dashboard/login/poll", json={"device_code": start["device_code"]})
        assert res.status_code == 400


class TestLogout:
    def test_logout_clears_cookie_and_redirects(self, client, user_and_token):
        _, token = user_and_token
        client.cookies.set(COOKIE_NAME, token)
        res = client.post(
            "/dashboard/logout",
            headers={"origin": "http://testserver"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/"
        assert COOKIE_NAME in res.headers.get("set-cookie", "")
        # Cookie should be cleared (max-age=0)
        assert "Max-Age=0" in res.headers.get("set-cookie", "")


class TestAccessControl:
    def _lockout_auth(self, db):
        return AuthService(
            db=db,
            github=FakeGitHubClient(),
            github_client_id="test-id",
            allowed_github_users=frozenset({"someone-else"}),
        )

    def test_login_poll_denied_returns_403_with_login(self, app, test_db):
        db, _ = test_db
        app.state.auth_service = self._lockout_auth(db)
        client = TestClient(app)

        start = client.post("/dashboard/login/start").json()
        res = client.post("/dashboard/login/poll", json={"device_code": start["device_code"]})

        assert res.status_code == 403
        assert "alice" in res.json()["detail"]

    def test_revoked_session_cookie_shows_login_page(self, app, test_db, user_and_token):
        db, _ = test_db
        _, token = user_and_token
        app.state.auth_service = self._lockout_auth(db)
        client = TestClient(app)

        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/")

        assert res.status_code == 200
        assert "Login with GitHub" in res.text

    def test_revoked_bearer_session_returns_403(self, app, test_db, user_and_token):
        db, _ = test_db
        _, token = user_and_token
        app.state.auth_service = self._lockout_auth(db)
        client = TestClient(app)

        res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert res.status_code == 403
        assert "alice" in res.json()["detail"]
