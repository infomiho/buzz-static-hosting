import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.analytics import init_analytics_schema
from server.app import create_app
from server.auth_service import AuthService
from server.cookies import COOKIE_NAME
from server.github import FakeGitHubClient
from server import config
from server.search_console import (
    FakeSearchConsoleClient,
    SearchConsoleError,
    build_search_terms_payload,
    create_search_console_client,
    load_service_account_credentials,
    map_search_terms_rows,
)


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
CREATE TABLE sites (
    name TEXT PRIMARY KEY,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    size_bytes INTEGER,
    owner_id INTEGER
);
"""


class TestBuildSearchTermsPayload:
    def test_queries_by_keyword_filtered_to_site(self):
        payload = build_search_terms_payload("mysite.example.com", date(2026, 6, 1), date(2026, 6, 28), 10)
        assert payload["startDate"] == "2026-06-01"
        assert payload["endDate"] == "2026-06-28"
        assert payload["dimensions"] == ["query"]
        assert payload["rowLimit"] == 10
        assert payload["dimensionFilterGroups"] == [{
            "filters": [{
                "dimension": "page",
                "operator": "contains",
                "expression": "://mysite.example.com/",
            }],
        }]


class TestMapSearchTermsRows:
    def test_maps_metrics(self):
        rows = [{"keys": ["static hosting"], "clicks": 12.0, "impressions": 340.0, "ctr": 0.0353, "position": 8.26}]
        assert map_search_terms_rows(rows) == [
            {"term": "static hosting", "clicks": 12, "impressions": 340, "ctr": 3.5, "position": 8.3},
        ]

    def test_skips_rows_without_keys(self):
        assert map_search_terms_rows([{"clicks": 1}]) == []

    def test_defaults_missing_metrics_to_zero(self):
        assert map_search_terms_rows([{"keys": ["buzz"]}]) == [
            {"term": "buzz", "clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0},
        ]


class TestLoadServiceAccountCredentials:
    def test_accepts_inline_json(self):
        value = '{"client_email": "a@b.iam.gserviceaccount.com", "private_key": "key"}'
        credentials = load_service_account_credentials(value)
        assert credentials["client_email"] == "a@b.iam.gserviceaccount.com"

    def test_accepts_path(self, tmp_path):
        key_file = tmp_path / "key.json"
        key_file.write_text('{"client_email": "a@b.iam.gserviceaccount.com", "private_key": "key"}')
        credentials = load_service_account_credentials(str(key_file))
        assert credentials["private_key"] == "key"

    def test_rejects_incomplete_key(self):
        with pytest.raises(ValueError):
            load_service_account_credentials('{"client_email": "a@b.c"}')


class TestCreateSearchConsoleClient:
    def test_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(config, "GSC_CREDENTIALS", None)
        assert create_search_console_client() is None

    def test_returns_none_on_unreadable_credentials(self, monkeypatch):
        monkeypatch.setattr(config, "GSC_CREDENTIALS", "/nonexistent/key.json")
        monkeypatch.setattr(config, "GSC_PROPERTY", "sc-domain:example.com")
        assert create_search_console_client() is None

    def test_returns_none_without_property_or_domain(self, monkeypatch):
        monkeypatch.setattr(config, "GSC_CREDENTIALS", '{"client_email": "a@b.c", "private_key": "key"}')
        monkeypatch.setattr(config, "GSC_PROPERTY", None)
        monkeypatch.setattr(config, "DOMAIN", None)
        assert create_search_console_client() is None


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
    monkeypatch.setattr("server.routes.dashboard.db", db)
    app = create_app()
    app.state.auth_service = AuthService(db=db, github=FakeGitHubClient(), github_client_id="test-id")
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
    user_id = cursor.lastrowid
    conn.execute("INSERT INTO sites (name, size_bytes, owner_id) VALUES (?, ?, ?)", ("mysite", 100, user_id))

    token = "buzz_sess_" + secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=30)
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, expires_at.isoformat()),
    )
    conn.commit()
    return user_id, token


class FailingSearchConsoleClient:
    def query_search_terms(self, site_host, start, end, limit=10):
        raise SearchConsoleError("boom")


class TestSearchTermsRoute:
    def test_requires_auth(self, client):
        res = client.get("/dashboard/sites/mysite/search-terms")
        assert res.status_code == 401

    def test_unknown_site_returns_404(self, client, user_and_token):
        _, token = user_and_token
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/dashboard/sites/other-site/search-terms")
        assert res.status_code == 404

    def test_not_configured(self, app, client, user_and_token):
        _, token = user_and_token
        app.state.search_console = None
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/dashboard/sites/mysite/search-terms")
        assert res.status_code == 200
        assert res.json() == {"configured": False, "terms": []}

    def test_returns_terms_for_site_host(self, app, client, user_and_token):
        _, token = user_and_token
        fake = FakeSearchConsoleClient()
        app.state.search_console = fake
        client.cookies.set(COOKIE_NAME, token)

        res = client.get("/dashboard/sites/mysite/search-terms")

        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is True
        assert data["terms"] == fake.terms
        call = fake.calls[0]
        assert call["site_host"] == "mysite.localhost:8080"
        assert call["end"] == date.today() - timedelta(days=2)
        assert call["start"] == date.today() - timedelta(days=29)

    def test_search_console_failure_returns_502(self, app, client, user_and_token):
        _, token = user_and_token
        app.state.search_console = FailingSearchConsoleClient()
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/dashboard/sites/mysite/search-terms")
        assert res.status_code == 502
