import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from server.analytics import AnalyticsEvent, AnalyticsStore, build_analytics_event, init_analytics_schema
from server.app import create_app
from server.site_store import SiteStore


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sites ("
        "  name TEXT PRIMARY KEY,"
        "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  size_bytes INTEGER,"
        "  owner_id INTEGER"
        ")"
    )
    init_analytics_schema(conn)
    return conn


def request(path: str = "/", headers: dict[str, str] | None = None):
    return SimpleNamespace(
        method="GET",
        headers={
            "accept": "text/html",
            "host": "my-site.localhost:8080",
            "user-agent": "Mozilla/5.0",
            **(headers or {}),
        },
        url=SimpleNamespace(query=path.split("?", 1)[1] if "?" in path else ""),
        client=SimpleNamespace(host="203.0.113.10"),
    )


class TestBuildAnalyticsEvent:
    def test_counts_html_document_requests(self):
        event = build_analytics_event(request(), "my-site", "/", 200, 12, "text/html")

        assert event is not None
        assert event.is_pageview
        assert not event.is_not_found
        assert event.path == "/"
        assert event.visitor_hash is not None

    def test_skips_static_assets(self):
        event = build_analytics_event(request("/style.css"), "my-site", "/style.css", 200, 12, "text/css")

        assert event is None

    def test_skips_opted_out_requests(self):
        assert build_analytics_event(request(headers={"dnt": "1"}), "my-site", "/", 200, 12, "text/html") is None
        assert build_analytics_event(request(headers={"sec-gpc": "1"}), "my-site", "/", 200, 12, "text/html") is None

    def test_skips_prefetch_requests(self):
        assert build_analytics_event(request(headers={"purpose": "prefetch"}), "my-site", "/", 200, 12, "text/html") is None

    def test_extracts_referrer_campaign_and_country(self):
        event = build_analytics_event(
            request(
                "/?utm_source=newsletter&utm_medium=email&utm_campaign=launch",
                headers={"referer": "https://example.com/post", "cf-ipcountry": "hr"},
            ),
            "my-site",
            "/?utm_source=newsletter&utm_medium=email&utm_campaign=launch",
            200,
            12,
            "text/html",
        )

        assert event is not None
        assert event.referrer == "example.com"
        assert event.campaign == "newsletter / email / launch"
        assert event.country == "HR"

    def test_skips_bot_user_agents(self):
        event = build_analytics_event(
            request(headers={"user-agent": "Googlebot"}),
            "my-site",
            "/",
            200,
            12,
            "text/html",
        )

        assert event is None

    def test_ignores_unknown_country_values(self):
        event = build_analytics_event(
            request(headers={"cf-ipcountry": "XX"}),
            "my-site",
            "/",
            200,
            12,
            "text/html",
        )

        assert event is not None
        assert event.country is None


class TestAnalyticsStore:
    def test_records_summary_dimensions_and_private_visitors(self):
        conn = make_db()
        store = AnalyticsStore(conn)
        event = AnalyticsEvent(
            site_name="my-site",
            path="/",
            day="2026-06-30",
            bytes_sent=100,
            is_pageview=True,
            is_not_found=False,
            visitor_hash="same-visitor",
            referrer="example.com",
            campaign="newsletter / email / launch",
            country="HR",
        )

        store.record(event)
        store.record(event)
        store.record(AnalyticsEvent(
            site_name="my-site",
            path="/missing",
            day="2026-06-30",
            bytes_sent=10,
            is_pageview=False,
            is_not_found=True,
            visitor_hash="same-visitor",
        ))

        summary = store.summary("my-site")

        assert summary["totals"] == {"views": 2, "visitors": 1, "bytes": 210, "not_found": 1}
        assert summary["top_pages"] == [{"value": "/", "views": 2}]
        assert summary["not_found_paths"] == [{"value": "/missing", "views": 1}]
        assert summary["referrers"] == [{"value": "example.com", "views": 2}]
        assert summary["campaigns"] == [{"value": "newsletter / email / launch", "views": 2}]
        assert summary["countries"] == [{"value": "HR", "views": 2}]

        visitor = conn.execute("SELECT * FROM analytics_visitors").fetchone()
        assert visitor["visitor_hash"] == "same-visitor"
        assert "203.0.113" not in dict(visitor).values()

    def test_summary_zero_fills_30_day_series(self):
        conn = make_db()
        summary = AnalyticsStore(conn).summary("my-site")

        assert len(summary["series"]) == 30
        assert all(day["views"] == 0 and day["visitors"] == 0 for day in summary["series"])

    def test_prunes_old_visitor_hashes(self):
        conn = make_db()
        store = AnalyticsStore(conn)
        conn.execute(
            "INSERT INTO analytics_visitors (site_name, day, visitor_hash, created_at) VALUES (?, ?, ?, ?)",
            ("my-site", "2020-01-01", "old", "2020-01-01"),
        )

        store.prune_visitors()

        assert conn.execute("SELECT COUNT(*) FROM analytics_visitors").fetchone()[0] == 0


class CaptureAnalytics:
    def __init__(self):
        self.events = []

    def record(self, event):
        if event:
            self.events.append(event)

    def start(self):
        pass

    async def stop(self):
        pass


class TestAnalyticsIntegration:
    def test_hosted_site_requests_record_analytics_events(self, tmp_path, monkeypatch):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "index.html").write_text("hello")
        (site / "style.css").write_text("body{}")
        monkeypatch.setattr("server.app.SITES_DIR", tmp_path)

        app = create_app()
        capture = CaptureAnalytics()
        app.state.analytics = capture
        with TestClient(app) as client:
            client.get("/", headers={"host": "my-site.localhost:8080", "accept": "text/html", "user-agent": "Mozilla/5.0"})
            client.get("/style.css", headers={"host": "my-site.localhost:8080", "accept": "text/css", "user-agent": "Mozilla/5.0"})
            client.get("/missing", headers={"host": "my-site.localhost:8080", "accept": "text/html", "user-agent": "Mozilla/5.0"})

        assert [(event.path, event.is_pageview, event.is_not_found) for event in capture.events] == [
            ("/", True, False),
            ("/missing", False, True),
        ]

    def test_site_analytics_route_requires_site_ownership(self, monkeypatch):
        conn = make_db()
        conn.execute("INSERT INTO sites (name, size_bytes, owner_id) VALUES ('my-site', 0, 1)")
        AnalyticsStore(conn).record(AnalyticsEvent(
            site_name="my-site",
            path="/",
            day="2026-06-30",
            bytes_sent=100,
            is_pageview=True,
            is_not_found=False,
            visitor_hash="visitor",
        ))

        @contextmanager
        def test_db():
            yield conn

        monkeypatch.setattr("server.config.DEV_MODE", True)
        monkeypatch.setattr("server.routes.dashboard.db", test_db)
        app = create_app()

        with TestClient(app) as client:
            res = client.get("/dashboard/sites/my-site/analytics")

        assert res.status_code == 200
        assert res.json()["totals"]["views"] == 1

    def test_deleting_site_removes_analytics(self, tmp_path):
        conn = make_db()
        conn.execute("INSERT INTO sites (name, size_bytes, owner_id) VALUES ('my-site', 0, 1)")
        AnalyticsStore(conn).record(AnalyticsEvent(
            site_name="my-site",
            path="/",
            day="2026-06-30",
            bytes_sent=100,
            is_pageview=True,
            is_not_found=False,
            visitor_hash="visitor",
        ))

        SiteStore(conn, tmp_path).delete("my-site", 1)

        assert conn.execute("SELECT COUNT(*) FROM analytics_daily").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM analytics_dimensions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM analytics_visitors").fetchone()[0] == 0
