import hashlib
import re
import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.auth_service import AuthService
from server.cookies import COOKIE_NAME
from server.github import FakeGitHubClient


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def app(make_app, database):
    application = make_app()
    application.state.auth_service = AuthService(
        db=database.connect, github=FakeGitHubClient(), github_client_id="test-id"
    )
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def user_and_token(database):
    with database.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
            (42, "alice", "Alice"),
        )
        user_id = cursor.lastrowid
        token = "buzz_sess_" + secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=30)
        conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (_hash(token), user_id, expires_at.isoformat()),
        )
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

    def test_expired_cookie_shows_login(self, database, client):
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
                (42, "alice", "Alice"),
            )
            token = "buzz_sess_" + secrets.token_urlsafe(32)
            expired = datetime.now() - timedelta(days=1)
            conn.execute(
                "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
                (_hash(token), 1, expired.isoformat()),
            )

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
        self, client, database, user_and_token, tmp_path
    ):
        user_id, token = user_and_token
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO sites (name, owner_id, size_bytes) VALUES ('my-site', ?, 0)",
                (user_id,),
            )
        (tmp_path / "my-site").mkdir()
        client.cookies.set(COOKIE_NAME, token)

        response = client.get("/dashboard/sites/my-site")

        assert response.status_code == 200
        assert "Custom domains" in response.text
        assert "Custom-domain services are disabled or not ready" in response.text
        assert "Try again later" in response.text
        assert "Add custom domain" not in response.text

    def test_site_detail_shows_pending_verification_record(
        self, make_app, database, user_and_token, tmp_path
    ):
        user_id, token = user_and_token
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO sites (name, owner_id, size_bytes) VALUES ('my-site', ?, 0)",
                (user_id,),
            )
            conn.execute("""INSERT INTO custom_domain_claims
                (id, hostname, site_name, verification_token, status, created_at, expires_at,
                 last_error)
                VALUES (1, 'www.example.com', 'my-site', 'bdv_test', 'pending',
                        '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                        'txt_mismatch')""")
            conn.execute("""INSERT INTO custom_domain_claims
                (id, hostname, site_name, verification_token, status, created_at, expires_at,
                 challenge_token, route_status, route_generation, activated_at, claim_mode,
                 health_checked_at, activation_error, removal_requested_at)
                VALUES
                  (2, 'active.example.com', 'my-site', 'bdv_active', 'verified',
                   '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'bdc_active', 'routed', 1, '2026-07-16T00:00:00+00:00',
                   'cloudflare', CURRENT_TIMESTAMP, NULL, NULL),
                  (3, 'checking.example.com', 'my-site', 'bdv_checking', 'verified',
                   '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'bdc_checking', 'routed', 1, NULL, 'direct', NULL, NULL, NULL),
                  (4, 'broken.example.com', 'my-site', 'bdv_broken', 'verified',
                   '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'bdc_broken', 'routed', 1, '2026-07-16T00:00:00+00:00',
                   'direct', NULL, 'origin_unavailable', NULL),
                  (5, 'leaving.example.com', 'my-site', 'bdv_leaving', 'verified',
                   '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'bdc_leaving', 'removing', 1, '2026-07-16T00:00:00+00:00',
                   'direct', NULL, NULL, CURRENT_TIMESTAMP),
                  (6, 'updating.example.com', 'my-site', 'bdv_updating', 'verified',
                   '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'bdc_updating', 'routed', 1, '2026-07-16T00:00:00+00:00',
                   'direct', CURRENT_TIMESTAMP, NULL, NULL),
                  (7, 'stale.example.com', 'my-site', 'bdv_stale', 'verified',
                   '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'bdc_stale', 'routed', 1, '2026-07-16T00:00:00+00:00',
                   'direct', NULL, 'dns_unavailable', NULL),
                  (8, 'connecting.example.com', 'my-site', 'bdv_connecting', 'verified',
                   '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'bdc_connecting', 'publishing', 1, NULL,
                   'direct', NULL, NULL, NULL)""")
            conn.execute("""INSERT INTO custom_domain_mode_transitions
                (claim_id, mode_generation, source_mode, target_mode, state, started_at,
                 deadline_at, observed_mode, error)
                VALUES
                  (3, 0, NULL, 'cloudflare', 'observing',
                   '2026-07-16T01:00:00+00:00', NULL, 'direct', NULL),
                  (4, 0, NULL, 'direct', 'failed',
                   '2026-07-16T01:00:00+00:00', NULL, 'direct', 'origin_unavailable'),
                  (6, 0, 'direct', 'cloudflare', 'observing',
                   '2026-07-16T01:00:00+00:00', '2099-07-17T00:00:00+00:00',
                   'cloudflare', NULL)""")
        (tmp_path / "my-site").mkdir()

        app = make_app(
            custom_domains_enabled=True,
            traefik_control_token="configured",
            custom_domain_ingress_ips=frozenset({"8.8.8.8"}),
            max_custom_domains_per_site=10,
        )
        app.state.auth_service = AuthService(
            db=database.connect, github=FakeGitHubClient(), github_client_id="test-id"
        )
        app.state.custom_domains.control = type(
            "ReadyControlPlane", (), {"is_ready": lambda self: True}
        )()
        app.state.custom_domains.runtime_ready = True
        app.state.custom_domains.range_state = type(
            "RangeState", (), {"error": None}
        )()
        app.state.custom_domains.transition_coordinator = object()
        client = TestClient(app)
        client.cookies.set(COOKIE_NAME, token)

        response = client.get("/dashboard/sites/my-site")

        assert response.status_code == 200
        assert "www.example.com" in response.text
        assert "_buzz.www.example.com" in response.text
        assert "buzz-domain-verification=bdv_test" in response.text
        assert "Verify ownership" in response.text
        assert "Verify domain ownership" in response.text
        assert "Add the DNS records below to prove ownership" in response.text
        assert "Point the domain to Buzz" in response.text
        assert "8.8.8.8" in response.text
        assert "Check ownership" in response.text
        assert response.text.count('data-copy-target="domain-') >= 4
        assert 'data-copy-target="domain-ownership-1-name"' in response.text
        assert 'data-copy-target="domain-ownership-1-value"' in response.text
        assert 'data-copy-target="domain-routing-1-1-value"' in response.text
        assert 'aria-label="Copy TXT record value"' in response.text
        assert "If this setup expires, add the domain again." in response.text
        assert "The TXT record does not match yet" in response.text
        assert "navigator.clipboard.writeText(target.textContent.trim())" in response.text
        assert "button.textContent = 'Copied'" in response.text

        def domain_tag(claim_id):
            match = re.search(
                rf'<details[^>]+data-domain-claim="{claim_id}"[^>]*>', response.text
            )
            assert match
            return match.group(0)

        assert " open" in domain_tag(1)
        assert " open" not in domain_tag(2)
        assert " open" in domain_tag(3)
        assert " open" in domain_tag(4)
        assert " open" in domain_tag(5)
        assert " open" not in domain_tag(6)
        assert " open" in domain_tag(7)
        assert " open" not in domain_tag(8)

        assert 'data-domain-state="verify_ownership"' in domain_tag(1)
        assert 'data-next-action="check_ownership"' in domain_tag(1)
        assert 'data-domain-state="connected"' in domain_tag(2)
        assert 'data-next-action="visit"' in domain_tag(2)
        assert "Buzz is serving your site on this domain." in response.text
        assert "Visit domain" in response.text
        assert 'data-domain-state="configure_dns"' in domain_tag(3)
        assert 'data-next-action="configure_dns"' in domain_tag(3)
        assert "Buzz detected DNS settings that do not match" in response.text
        assert 'data-domain-state="connecting"' in domain_tag(8)
        assert 'data-next-action="wait"' in domain_tag(8)
        assert "Buzz is preparing the secure connection." in response.text
        assert "No action needed" in response.text
        assert 'data-domain-state="action_needed"' in domain_tag(4)
        assert "Buzz could not validate this domain. Check its DNS settings." in response.text
        assert "Retry connection" in response.text
        assert 'data-domain-state="removing"' in domain_tag(5)
        assert "Buzz is safely withdrawing this domain." in response.text
        assert "Withdrawal in progress" in response.text
        assert 'data-domain-state="updating"' in domain_tag(6)
        assert "DNS change detected. Buzz is validating the new connection." in response.text
        assert "retains the current authorization" in response.text
        assert 'data-domain-state="action_needed"' in domain_tag(7)
        assert "Buzz will retry automatically" in response.text
        assert "No DNS change is needed yet." in response.text

        assert "bdc_checking" in response.text
        assert "bdc_active" in response.text
        assert response.text.count("Connected through Cloudflare") == 2
        assert "Ownership verified" not in response.text
        assert response.text.count('class="disclosure-label') >= 3
        assert response.text.count('<span aria-hidden="true">&#10003;</span>') == 1
        assert 'class="sr-only">Connected</span>' in response.text
        assert re.search(r'<details[^>]*class="[^"]*manage-domain', response.text)
        assert response.text.index("Manage domain") < response.text.index("Cancel update")
        assert response.text.index("Manage domain") < response.text.index("Remove domain")
        assert "Cancel transition" not in response.text
        assert "Consecutive failures" not in response.text
        assert 'name="mode"' not in response.text
        assert "Buzz detects direct and Cloudflare connections automatically" in response.text
        assert response.text.index("Analytics") < response.text.index("Custom domains")
        assert response.text.index("Files") < response.text.index("Custom domains")
        assert 'id="remove-domain-dialog"' in response.text
        assert "Buzz will stop serving this hostname" in response.text
        assert 'id="remove-domain-error"' in response.text
        assert "removeDialog.close();\n                showDomainError" not in response.text
        assert "Add custom domain" in response.text
        assert "8 of 10 aliases used for this site" in response.text


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
    def _lockout_auth(self, connect):
        return AuthService(
            db=connect,
            github=FakeGitHubClient(),
            github_client_id="test-id",
            allowed_github_users=frozenset({"someone-else"}),
        )

    def test_login_poll_denied_returns_403_with_login(self, app, database):
        app.state.auth_service = self._lockout_auth(database.connect)
        client = TestClient(app)

        start = client.post("/dashboard/login/start").json()
        res = client.post("/dashboard/login/poll", json={"device_code": start["device_code"]})

        assert res.status_code == 403
        assert "alice" in res.json()["detail"]

    def test_revoked_session_cookie_shows_login_page(self, app, database, user_and_token):
        _, token = user_and_token
        app.state.auth_service = self._lockout_auth(database.connect)
        client = TestClient(app)

        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/")

        assert res.status_code == 200
        assert "Login with GitHub" in res.text

    def test_revoked_bearer_session_returns_403(self, app, database, user_and_token):
        _, token = user_and_token
        app.state.auth_service = self._lockout_auth(database.connect)
        client = TestClient(app)

        res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert res.status_code == 403
        assert "alice" in res.json()["detail"]
