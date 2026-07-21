import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from server.analytics import AnalyticsEvent, AnalyticsStore, build_analytics_event
from server.site_store import SiteStore


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
        event = build_analytics_event(
            request(), "my-site", "/", 200, 12, "text/html", visitor_secret="test-secret"
        )

        assert event is not None
        assert event.is_pageview
        assert not event.is_not_found
        assert event.path == "/"
        assert event.visitor_hash is not None

    def test_skips_static_assets(self):
        event = build_analytics_event(
            request("/style.css"), "my-site", "/style.css", 200, 12, "text/css",
            visitor_secret="test-secret",
        )

        assert event is None

    def test_skips_opted_out_requests(self):
        assert build_analytics_event(
            request(headers={"dnt": "1"}), "my-site", "/", 200, 12, "text/html",
            visitor_secret="test-secret",
        ) is None
        assert build_analytics_event(
            request(headers={"sec-gpc": "1"}), "my-site", "/", 200, 12, "text/html",
            visitor_secret="test-secret",
        ) is None

    def test_skips_prefetch_requests(self):
        assert build_analytics_event(
            request(headers={"purpose": "prefetch"}), "my-site", "/", 200, 12, "text/html",
            visitor_secret="test-secret",
        ) is None

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
            visitor_secret="test-secret",
        )

        assert event is not None
        assert event.referrer == "example.com"
        assert event.campaign == "newsletter / email / launch"
        assert event.country == "HR"

    def test_same_site_alias_referrer_is_internal(self):
        event = build_analytics_event(
            request(headers={"referer": "https://two.example.com/page"}),
            "my-site",
            "/",
            200,
            12,
            "text/html",
            {"one.example.com", "two.example.com", "my-site.buzz.example.com"},
            visitor_secret="test-secret",
        )

        assert event is not None
        assert event.referrer is None

    def test_www_alias_is_not_equivalent_to_exact_apex_alias(self):
        event = build_analytics_event(
            request(headers={"referer": "https://www.example.com/page"}),
            "my-site",
            "/",
            200,
            12,
            "text/html",
            {"example.com"},
            visitor_secret="test-secret",
        )

        assert event is not None
        assert event.referrer == "example.com"

    def test_skips_bot_user_agents(self):
        event = build_analytics_event(
            request(headers={"user-agent": "Googlebot"}),
            "my-site",
            "/",
            200,
            12,
            "text/html",
            visitor_secret="test-secret",
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
            visitor_secret="test-secret",
        )

        assert event is not None
        assert event.country is None


class TestAnalyticsStore:
    def test_records_summary_dimensions_and_private_visitors(self, database):
        with database.connect() as conn:
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

    def test_summary_zero_fills_30_day_series(self, database):
        with database.connect() as conn:
            summary = AnalyticsStore(conn).summary("my-site")

        assert len(summary["series"]) == 30
        assert all(day["views"] == 0 and day["visitors"] == 0 for day in summary["series"])

    def test_total_views_by_site_zero_fills_missing_analytics(self, database):
        with database.connect() as conn:
            store = AnalyticsStore(conn)
            store.record(AnalyticsEvent(
                site_name="my-site",
                path="/",
                day="2026-06-30",
                bytes_sent=100,
                is_pageview=True,
                is_not_found=False,
                visitor_hash="visitor",
            ))
            store.record(AnalyticsEvent(
                site_name="my-site",
                path="/about",
                day="2026-06-30",
                bytes_sent=100,
                is_pageview=True,
                is_not_found=False,
                visitor_hash="visitor",
            ))

            assert store.total_views_by_site(["my-site", "quiet-site"]) == {"my-site": 2, "quiet-site": 0}

    def test_prunes_old_visitor_hashes(self, database):
        with database.connect() as conn:
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
    def test_hosted_site_requests_record_analytics_events(self, make_app, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "index.html").write_text("hello")
        (site / "style.css").write_text("body{}")

        app = make_app()
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

    def test_alias_lookup_failure_does_not_break_static_serving(self, make_app, tmp_path):
        site = tmp_path / "my-site"
        site.mkdir()
        (site / "index.html").write_text("hello")

        app = make_app(custom_domains_enabled=True)
        capture = CaptureAnalytics()
        app.state.analytics = capture

        def failed_lookup(site_name):
            raise sqlite3.OperationalError("database unavailable")

        app.state.custom_domains.activated_hostnames_for_site = failed_lookup

        with TestClient(app) as client:
            response = client.get(
                "/",
                headers={
                    "host": "my-site.localhost:8080",
                    "accept": "text/html",
                    "user-agent": "Mozilla/5.0",
                    "referer": "https://external.example/page",
                },
            )

        assert response.status_code == 200
        assert response.text == "hello"
        assert capture.events[0].referrer == "external.example"

    def test_site_analytics_route_requires_site_ownership(self, make_app, database):
        with database.connect() as conn:
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

        with TestClient(make_app(dev_mode=True)) as client:
            res = client.get("/dashboard/sites/my-site/analytics")

        assert res.status_code == 200
        assert res.json()["totals"]["views"] == 1

    def test_sites_route_includes_total_views(self, make_app, database):
        with database.connect() as conn:
            conn.execute("INSERT INTO sites (name, size_bytes, owner_id) VALUES ('my-site', 10, 1)")
            conn.execute("INSERT INTO sites (name, size_bytes, owner_id) VALUES ('quiet-site', 5, 1)")
            conn.execute("INSERT INTO sites (name, size_bytes, owner_id) VALUES ('other-site', 20, 2)")
            AnalyticsStore(conn).record(AnalyticsEvent(
                site_name="my-site",
                path="/",
                day="2026-06-30",
                bytes_sent=100,
                is_pageview=True,
                is_not_found=False,
                visitor_hash="visitor",
            ))

        res = TestClient(make_app(dev_mode=True)).get("/sites")

        assert res.status_code == 200
        sites = {site["name"]: site for site in res.json()}
        assert sites["my-site"]["total_views"] == 1
        assert sites["quiet-site"]["total_views"] == 0
        assert "other-site" not in sites

    def test_deleting_site_removes_analytics(self, database, tmp_path):
        with database.connect() as conn:
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
            conn.commit()

            SiteStore(conn, tmp_path).delete("my-site", 1)

            assert conn.execute("SELECT COUNT(*) FROM analytics_daily").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM analytics_dimensions").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM analytics_visitors").fetchone()[0] == 0
