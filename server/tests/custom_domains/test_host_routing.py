from fastapi.testclient import TestClient

from server import db as db_module
from server.app import create_app
from server.cookies import COOKIE_NAME
from server.custom_domains.claims import DomainClaimStore
from server.custom_domains.transitions import DomainClaimStateMachine


class StubAuth:
    def start_device_flow(self):
        return {
            "device_code": "device-code",
            "user_code": "USER-CODE",
            "verification_uri": "https://example.com/device",
        }

    def authenticate(self, authorization):
        return None


class NullAnalytics:
    def start(self):
        pass

    async def stop(self):
        pass

    def record(self, event):
        pass


def make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr("server.app.SITES_DIR", tmp_path)
    app = create_app()
    app.state.auth_service = StubAuth()
    app.state.analytics = NullAnalytics()
    return TestClient(app)


def test_tenant_host_cannot_reach_control_routes(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    headers = {"host": "tenant.localhost:8080"}

    assert client.get("/health", headers=headers).status_code == 404
    assert client.get("/sites", headers=headers).status_code == 404
    assert client.get("/openapi.json", headers=headers).status_code == 404
    assert client.post("/auth/device", headers=headers).status_code == 405


def test_tenant_host_serves_files_at_control_route_paths(tmp_path, monkeypatch):
    site_dir = tmp_path / "tenant"
    (site_dir / "static").mkdir(parents=True)
    (site_dir / "health.html").write_text("tenant health")
    (site_dir / "openapi.json").write_text('{"tenant": true}')
    (site_dir / "static" / "style.css").write_text("tenant styles")
    client = make_client(tmp_path, monkeypatch)
    headers = {"host": "tenant.localhost:8080"}

    assert client.get("/health", headers=headers).text == "tenant health"
    assert client.get("/openapi.json", headers=headers).json() == {"tenant": True}
    assert client.get("/static/style.css", headers=headers).text == "tenant styles"


def test_tenant_custom_404_is_served_as_a_file_response(tmp_path, monkeypatch):
    site_dir = tmp_path / "tenant"
    site_dir.mkdir()
    (site_dir / "404.html").write_text("tenant not found")
    client = make_client(tmp_path, monkeypatch)

    response = client.get("/missing", headers={"host": "tenant.localhost:8080"})

    assert response.status_code == 404
    assert response.text == "tenant not found"
    assert response.headers["content-length"] == str(len("tenant not found"))


def test_control_routes_require_the_control_host(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    assert client.get("/health", headers={"host": "localhost:8080"}).status_code == 200
    assert client.get("/health", headers={"host": "attacker.example"}).status_code == 421


def test_verified_custom_domain_exposes_only_reserved_challenge(tmp_path, monkeypatch):
    token = "bdc_test"
    monkeypatch.setattr(
        "server.custom_domains.runtime.CustomDomainsRuntime.resolve_challenge",
        lambda self, hostname, path: (
            (7, "my-site", token)
            if hostname == "www.example.com"
            and path == f"/.well-known/buzz-domain-check/{token}"
            else None
        ),
    )
    client = make_client(tmp_path, monkeypatch)
    headers = {"host": "www.example.com"}

    response = client.get(f"/.well-known/buzz-domain-check/{token}", headers=headers)

    assert response.status_code == 200
    assert response.text == "buzz-domain-check=bdc_test;site=my-site"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-buzz-domain-claim"] == "7"
    assert client.get("/health", headers=headers).status_code == 421
    assert client.post(f"/.well-known/buzz-domain-check/{token}", headers=headers).status_code == 405


def test_reserved_challenge_namespace_never_falls_through_to_static_files(
    tmp_path, monkeypatch
):
    site_dir = tmp_path / "tenant" / ".well-known" / "buzz-domain-check"
    site_dir.mkdir(parents=True)
    (site_dir / "attacker-token").write_text("site-controlled")
    client = make_client(tmp_path, monkeypatch)

    response = client.get(
        "/.well-known/buzz-domain-check/attacker-token",
        headers={"host": "tenant.localhost:8080"},
    )

    assert response.status_code == 404
    assert response.text == "404 Not Found"
    assert response.headers["cache-control"] == "no-store"


def test_challenge_token_is_bound_to_its_verified_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
        claim = store.prepare_routes(True)[0]
    monkeypatch.setattr("server.config.CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr("server.config.CUSTOM_DOMAIN_ROUTING_ENABLED", True)
    client = make_client(tmp_path, monkeypatch)

    expected = client.get(claim.challenge_path, headers={"host": claim.hostname})
    wrong_host = client.get(claim.challenge_path, headers={"host": "other.example.com"})

    assert expected.status_code == 200
    assert expected.headers["x-buzz-domain-claim"] == str(claim.id)
    assert wrong_host.status_code == 404

    with db_module.db() as conn:
        DomainClaimStore(conn).cancel(claim.id, "my-site")
    withdrawn = client.get(claim.challenge_path, headers={"host": claim.hostname})
    assert withdrawn.status_code == 404


def test_activated_custom_domain_serves_canonical_site_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    site_dir = tmp_path / "my-site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("custom domain content")
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
        claim = store.get(claim.id, "my-site")
        DomainClaimStateMachine(conn).apply_activation_decision(claim, None)
    monkeypatch.setattr("server.config.CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr("server.config.CUSTOM_DOMAIN_ROUTING_ENABLED", True)
    client = make_client(tmp_path, monkeypatch)
    headers = {"host": "www.example.com"}

    response = client.get("/", headers=headers)

    assert response.status_code == 200
    assert response.text == "custom domain content"
    assert client.get("/health", headers=headers).status_code == 404
    method = client.post("/", headers=headers)
    assert method.status_code == 405
    assert method.headers["allow"] == "GET, HEAD"

    with db_module.db() as conn:
        DomainClaimStore(conn).cancel(claim.id, "my-site")
    assert client.get("/", headers=headers).status_code == 421


def test_multiple_aliases_serve_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    site_dir = tmp_path / "my-site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("shared content")
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        store = DomainClaimStore(conn)
        claims = []
        for hostname in ("one.example.com", "two.example.com"):
            claim = store.create("my-site", hostname)
            store.record_check(claim.id, "my-site", (claim.verification_value,))
            claims.append(claim)
        for claim in store.prepare_routes(True):
            store.mark_routed(claim.id, claim.route_generation)
            current = store.get(claim.id, "my-site")
            DomainClaimStateMachine(conn).apply_activation_decision(current, None)
    monkeypatch.setattr("server.config.CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr("server.config.CUSTOM_DOMAIN_ROUTING_ENABLED", True)
    client = make_client(tmp_path, monkeypatch)

    assert client.get("/", headers={"host": "one.example.com"}).text == "shared content"
    assert client.get("/", headers={"host": "two.example.com"}).text == "shared content"

    with db_module.db() as conn:
        DomainClaimStore(conn).cancel(claims[0].id, "my-site")

    assert client.get("/", headers={"host": "one.example.com"}).status_code == 421
    assert client.get("/", headers={"host": "two.example.com"}).status_code == 200
    assert client.get("/", headers={"host": "my-site.localhost:8080"}).status_code == 200


def test_cookie_authenticated_mutations_reject_tenant_origin(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    headers = {
        "host": "localhost:8080",
        "origin": "http://tenant.localhost:8080",
        "cookie": f"{COOKIE_NAME}=invalid-session",
    }

    response = client.post(
        "/deploy",
        headers=headers,
        files={"file": ("site.zip", b"not-used", "application/zip")},
    )

    assert response.status_code == 403


def test_cookie_authenticated_mutations_allow_control_origin(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    headers = {
        "host": "localhost:8080",
        "origin": "http://localhost:8080",
        "cookie": f"{COOKIE_NAME}=invalid-session",
    }

    response = client.post(
        "/deploy",
        headers=headers,
        files={"file": ("site.zip", b"not-used", "application/zip")},
    )

    assert response.status_code == 401


def test_cookie_authenticated_mutations_require_origin(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/deploy",
        headers={
            "host": "localhost:8080",
            "cookie": f"{COOKIE_NAME}=invalid-session",
        },
        files={"file": ("site.zip", b"not-used", "application/zip")},
    )

    assert response.status_code == 403


def test_cookie_authenticated_mutations_reject_cross_scheme_origin(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/deploy",
        headers={
            "host": "localhost:8080",
            "origin": "https://localhost:8080",
            "cookie": f"{COOKIE_NAME}=invalid-session",
        },
        files={"file": ("site.zip", b"not-used", "application/zip")},
    )

    assert response.status_code == 403
