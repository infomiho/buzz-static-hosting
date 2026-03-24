import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

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
def app(test_db):
    db, conn = test_db
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
        res = client.post("/dashboard/logout", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/"
        assert COOKIE_NAME in res.headers.get("set-cookie", "")
        # Cookie should be cleared (max-age=0)
        assert "Max-Age=0" in res.headers.get("set-cookie", "")
