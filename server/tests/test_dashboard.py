import hashlib
import re
import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.auth_service import AuthService
from server.cookies import COOKIE_NAME, OAUTH_BROWSER_COOKIE_NAME
from server.github_login import GitHubOAuth
from server.pending_store import PendingStore


class FakeOAuthClient:
    async def get_authorization_url(self, _redirect_uri, **kwargs):
        return "https://github.com/login/oauth/authorize?state=" + kwargs["state"]

    async def get_access_token(self, _code, _redirect_uri, code_verifier=None):
        assert code_verifier
        return {"access_token": "token"}

    async def get_profile(self, _token):
        return {"id": 42, "login": "alice", "name": "Alice"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def app(make_app, database):
    application = make_app()
    application.state.github_oauth = GitHubOAuth(
        PendingStore(),
        "test-id",
        "test-secret",
        "http://localhost:8080/dashboard/login/github/callback",
        oauth_client=FakeOAuthClient(),
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
        assert 'id="github-login" href="/dashboard/login/github?next="' in res.text
        assert "login-pending" not in res.text
        assert "login/github/start" not in res.text

    def test_authenticated_without_sites_shows_first_run(self, client, user_and_token):
        _, token = user_and_token
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/")
        assert res.status_code == 200
        assert "Deploy your first site" in res.text
        assert "buzz config server" in res.text
        # A brand-new account has nothing to count and no token to revoke.
        assert "Welcome back" not in res.text
        assert "Deploy Tokens" not in res.text

    def test_authenticated_with_sites_shows_dashboard(
        self, client, database, user_and_token
    ):
        user_id, token = user_and_token
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO sites (name, owner_id, size_bytes) VALUES ('a-site', ?, 1)",
                (user_id,),
            )
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/")
        assert res.status_code == 200
        assert "Dashboard" in res.text
        assert "alice" in res.text
        assert "Sites" in res.text
        assert "Deploy Tokens" in res.text
        assert "Deploy your first site" not in res.text

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


def test_dialog_controller_loads_before_site_detail_module(
    client, database, user_and_token, tmp_path
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
    assert response.text.index('/static/dialogs.js?v=') < response.text.index(
        '/static/site-detail.js?v='
    )


def test_dashboard_module_owns_server_data_and_deploy_handler(
    client, database, user_and_token
):
    user_id, token = user_and_token
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO sites (name, owner_id, size_bytes) VALUES ('a-site', ?, 1)",
            (user_id,),
        )
    client.cookies.set(COOKIE_NAME, token)

    response = client.get("/")

    assert re.search(r'<script type="module" src="/static/dashboard\.js\?v=[^" ]+"', response.text)
    assert 'id="dashboard-root" data-domain="localhost:8080"' in response.text
    assert 'id="deploy-site"' in response.text
    assert "onclick=\"openDeployDialog" not in response.text
    assert "const DOMAIN" not in response.text


def test_first_run_references_dashboard_module_and_data_root(client, user_and_token):
    _, token = user_and_token
    client.cookies.set(COOKIE_NAME, token)

    response = client.get("/")

    assert re.search(r'<script type="module" src="/static/dashboard\.js\?v=[^" ]+"', response.text)
    assert 'id="dashboard-root" data-domain="localhost:8080"' in response.text
    assert 'id="first-run-deploy"' in response.text


def test_site_detail_module_owns_site_data_and_redeploy_handler(
    client, database, user_and_token, tmp_path
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

    assert re.search(r'<script type="module" src="/static/site-detail\.js\?v=[^" ]+"', response.text)
    assert re.search(
        r'id="site-detail-root"[^>]+data-site-name="my-site"[^>]+data-created-at="[^"]+"',
        response.text,
    )
    assert 'id="redeploy-site"' in response.text
    assert "onclick=\"openDeployDialog" not in response.text
    assert "const SITE_NAME" not in response.text


class TestCustomDomains:
    def test_site_detail_hides_domains_section_when_feature_disabled(
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
        assert ">Stats<" in response.text
        assert ">Files<" in response.text
        assert "Custom domains" not in response.text
        assert "Custom-domain services are disabled or not ready" not in response.text
        assert "Add custom domain" not in response.text

    def test_site_detail_shows_domains_section_when_enabled_but_unready(
        self, make_app, database, user_and_token, tmp_path
    ):
        user_id, token = user_and_token
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO sites (name, owner_id, size_bytes) VALUES ('my-site', ?, 0)",
                (user_id,),
            )
        (tmp_path / "my-site").mkdir()
        app = make_app(custom_domains_enabled=True)
        unready_client = TestClient(app)
        unready_client.cookies.set(COOKIE_NAME, token)

        response = unready_client.get("/dashboard/sites/my-site")

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

        def domain_tag(claim_id):
            match = re.search(
                rf'<details[^>]+data-domain-claim="{claim_id}"[^>]*>', response.text
            )
            assert match
            return match.group(0)

        def domain_panel(claim_id):
            """Markup for one claim, from its <details> tag up to the next claim's."""
            tag = domain_tag(claim_id)
            start = response.text.index(tag)
            body_start = start + len(tag)
            next_claim = response.text.find("data-domain-claim=", body_start)
            return response.text[body_start:next_claim if next_claim != -1 else len(response.text)]

        assert " open" in domain_tag(1)
        assert " open" not in domain_tag(2)
        assert " open" in domain_tag(3)
        assert " open" in domain_tag(4)
        assert " open" not in domain_tag(5)
        assert " open" not in domain_tag(6)
        assert " open" not in domain_tag(7)
        assert " open" not in domain_tag(8)

        assert 'data-domain-state="verify_ownership"' in domain_tag(1)
        assert 'data-next-action="check_ownership"' in domain_tag(1)
        assert 'data-domain-state="connected"' in domain_tag(2)
        assert 'data-next-action="visit"' in domain_tag(2)
        assert "Buzz is serving your site on this domain." in response.text
        assert "Visit domain" in response.text
        assert "Check status" in domain_panel(3)
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
        checked_actions = 0
        for claim_id in range(1, 9):
            panel = domain_panel(claim_id)
            if "Technical details" not in panel:
                continue
            for action in ("Cancel update", "Remove domain"):
                if action in panel:
                    assert panel.index(action) < panel.index("Technical details")
                    checked_actions += 1
        assert checked_actions >= 2
        assert "Cancel transition" not in response.text
        assert "Consecutive failures" not in response.text
        assert 'name="mode"' not in response.text
        assert "Buzz detects direct and Cloudflare connections automatically" in response.text
        assert response.text.index(">Stats<") < response.text.index(">Files<")
        assert response.text.index(">Files<") < response.text.index(">Custom domains<")
        assert 'id="remove-domain-dialog"' in response.text
        assert "Buzz will stop serving" in response.text
        assert "after withdrawing its route" in response.text
        assert 'id="remove-domain-error"' in response.text
        assert "removeDialog.close();\n                showDomainError" not in response.text
        assert "Add custom domain" in response.text
        assert "8 of 10 aliases used for this site" in response.text


class TestLoginFlow:
    @staticmethod
    def _start(client, next_path="/"):
        response = client.get(
            "/dashboard/login/github",
            params={"next": next_path},
            follow_redirects=False,
        )
        state = response.headers["location"].split("state=", 1)[1]
        return response, state

    def test_github_login_start_redirects_with_state_cookie(self, app):
        client = TestClient(app, base_url="https://testserver")

        response, state = self._start(client, "/device")

        assert response.status_code == 302
        assert response.headers["location"] == "https://github.com/login/oauth/authorize?state=" + state
        assert OAUTH_BROWSER_COOKIE_NAME in response.headers["set-cookie"]

    def test_github_callback_sets_session_and_returns_to_next_path(self, app):
        client = TestClient(app, base_url="https://testserver")
        _, state = self._start(client, "/device")

        response = client.get(
            "/dashboard/login/github/callback",
            params={"state": state, "code": "code"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/device"
        assert COOKIE_NAME in response.headers["set-cookie"]
        assert OAUTH_BROWSER_COOKIE_NAME not in response.headers["set-cookie"]
        assert client.get("/device").status_code == 200

    def test_github_callback_rejects_external_next_path(self, app):
        client = TestClient(app, base_url="https://testserver")
        _, state = self._start(client, "https://evil.example")

        response = client.get(
            "/dashboard/login/github/callback",
            params={"state": state, "code": "code"},
            follow_redirects=False,
        )

        assert response.headers["location"] == "/"

    def test_github_callback_rejects_invalid_or_replayed_state(self, app):
        client = TestClient(app, base_url="https://testserver")
        _, state = self._start(client)

        invalid = client.get(
            "/dashboard/login/github/callback",
            params={"state": "other", "code": "code"},
            follow_redirects=False,
        )
        completed = client.get(
            "/dashboard/login/github/callback",
            params={"state": state, "code": "code"},
            follow_redirects=False,
        )
        replayed = client.get(
            "/dashboard/login/github/callback",
            params={"state": state, "code": "code"},
            follow_redirects=False,
        )

        assert "login_error=" in invalid.headers["location"]
        assert completed.status_code == 303
        assert "login_error=" in replayed.headers["location"]

    def test_parallel_github_logins_share_a_browser_binding(self, app):
        client = TestClient(app, base_url="https://testserver")
        _, first_state = self._start(client)
        _, second_state = self._start(client)

        first = client.get(
            "/dashboard/login/github/callback",
            params={"state": first_state, "code": "first"},
            follow_redirects=False,
        )
        second = client.get(
            "/dashboard/login/github/callback",
            params={"state": second_state, "code": "second"},
            follow_redirects=False,
        )

        assert first.status_code == 303
        assert second.status_code == 303

    def test_github_callback_handles_denial(self, app):
        client = TestClient(app, base_url="https://testserver")
        _, state = self._start(client)

        response = client.get(
            "/dashboard/login/github/callback",
            params={"state": state, "error": "access_denied"},
            follow_redirects=False,
        )

        assert "GitHub+sign-in+was+cancelled" in response.headers["location"]


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
            allowed_github_users=frozenset({"someone-else"}),
        )

    def test_github_callback_denied_returns_login_error(self, app, database):
        app.state.auth_service = self._lockout_auth(database.connect)
        client = TestClient(app, base_url="https://testserver")

        _, state = TestLoginFlow._start(client)
        res = client.get(
            "/dashboard/login/github/callback",
            params={"state": state, "code": "code"},
            follow_redirects=False,
        )

        assert res.status_code == 303
        assert "not+allowed" in res.headers["location"]

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
