from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from server import config
from server import db as db_module
from server.app import create_app
from server.auth_service import Identity, User
from server.dependencies import get_identity


class FakeTxtResolver:
    def __init__(self):
        self.values = ()
        self.names = []

    def lookup(self, name):
        self.names.append(name)
        return self.values


class ReadyControlPlane:
    def is_ready(self):
        return True


class UnreadyControlPlane:
    def is_ready(self):
        return False


@pytest.fixture
def domain_api(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_ADMISSION_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_DIAGNOSTICS_ENABLED", False)
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_ROUTING_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_INGRESS_IPS", frozenset({"8.8.8.8"}))
    monkeypatch.setattr(config, "MAX_CUSTOM_DOMAINS_PER_SITE", 5)
    monkeypatch.setattr(config, "MAX_CUSTOM_DOMAINS_PER_USER", 20)
    monkeypatch.setattr(config, "MAX_CUSTOM_DOMAINS_SERVER_WIDE", 1000)
    monkeypatch.setattr(config, "TRAEFIK_CONTROL_TOKEN", "configured")
    monkeypatch.setattr(config, "DOMAIN", "buzz.example.com")
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute(
            "INSERT INTO users (id, github_id, github_login) VALUES (1, 1, 'dev'), (2, 2, 'other')"
        )
        conn.execute(
            "INSERT INTO sites (name, owner_id) VALUES ('my-site', 1), ('other-site', 2)"
        )
    app = create_app()
    app.state.traefik_control = ReadyControlPlane()
    app.state.custom_domain_runtime_ready = True
    resolver = FakeTxtResolver()
    app.state.domain_txt_resolver = resolver
    return TestClient(app), resolver


def test_domain_claim_lifecycle(domain_api):
    client, resolver = domain_api

    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "WWW.Example.COM"},
    )

    assert created.status_code == 201
    claim = created.json()
    assert claim["hostname"] == "www.example.com"
    assert claim["status"] == "pending"
    assert claim["verification"]["name"] == "_buzz.www.example.com"

    resolver.values = (claim["verification"]["value"],)
    checked = client.post(f"/sites/my-site/domains/{claim['id']}/check")
    assert checked.status_code == 200
    assert checked.json()["status"] == "verified"
    assert resolver.names == ["_buzz.www.example.com"]

    listed = client.get("/sites/my-site/domains")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "verified"

    cancelled = client.delete(f"/sites/my-site/domains/{claim['id']}")
    assert cancelled.status_code == 204
    assert client.get("/sites/my-site/domains").json()[0]["status"] == "cancelled"


def test_custom_domain_capability_reports_ready_targets(domain_api, monkeypatch):
    client, _ = domain_api
    monkeypatch.setattr(
        config,
        "CUSTOM_DOMAIN_INGRESS_IPS",
        frozenset({"2001:4860:4860::8888", "8.8.8.8"}),
    )

    response = client.get("/capabilities/custom-domains")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "detail": None,
        "enabled": True,
        "control_ready": True,
        "admission_enabled": True,
        "routing_enabled": True,
        "routing_targets": [
            {"type": "A", "value": "8.8.8.8"},
            {"type": "AAAA", "value": "2001:4860:4860::8888"},
        ],
        "cloudflare": {
            "admission_enabled": False,
            "ready": False,
            "detail": "Cloudflare proxy diagnostics admission is not enabled",
        },
    }


def test_custom_domain_capability_distinguishes_disabled_and_unready(
    domain_api, monkeypatch
):
    client, _ = domain_api
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", False)
    disabled = client.get("/capabilities/custom-domains").json()
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", True)
    client.app.state.traefik_control = UnreadyControlPlane()
    unready = client.get("/capabilities/custom-domains").json()

    assert disabled["status"] == "disabled"
    assert disabled["detail"] == "Custom domains are not enabled on this Buzz server"
    assert unready["status"] == "unready"
    assert unready["detail"] == "Custom domain control plane is not ready"


def test_custom_domain_capability_reports_closed_admission(domain_api, monkeypatch):
    client, _ = domain_api
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_ADMISSION_ENABLED", False)

    response = client.get("/capabilities/custom-domains")

    assert response.status_code == 200
    assert response.json()["status"] == "unready"
    assert response.json()["detail"] == (
        "New custom domain claims are not enabled on this Buzz server"
    )


def test_cloudflare_diagnostic_claim_requires_explicit_operator_admission(
    domain_api, monkeypatch
):
    client, _ = domain_api

    disabled = client.post(
        "/sites/my-site/domains",
        json={"hostname": "proxy.example.com", "mode": "cloudflare"},
    )
    monkeypatch.setattr(config, "CLOUDFLARE_DIAGNOSTICS_ENABLED", True)
    enabled = client.post(
        "/sites/my-site/domains",
        json={"hostname": "proxy.example.com", "mode": "cloudflare"},
    )

    assert disabled.status_code == 503
    assert disabled.json()["detail"] == (
        "Cloudflare proxy diagnostics admission is not enabled"
    )
    assert enabled.status_code == 201
    assert enabled.json()["mode"] == "cloudflare"
    assert enabled.json()["cloudflare_diagnostics"] is None


def test_cloudflare_capability_is_independent_of_direct_ingress(
    domain_api, monkeypatch
):
    client, _ = domain_api
    monkeypatch.setattr(config, "CLOUDFLARE_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_INGRESS_IPS", frozenset())

    capability = client.get("/capabilities/custom-domains").json()

    assert capability["status"] == "unready"
    assert capability["cloudflare"] == {
        "admission_enabled": True,
        "ready": True,
        "detail": None,
    }


def test_cloudflare_capability_requires_diagnostic_runtime(domain_api, monkeypatch):
    client, _ = domain_api
    monkeypatch.setattr(config, "CLOUDFLARE_DIAGNOSTICS_ENABLED", True)
    client.app.state.custom_domain_runtime_ready = False

    capability = client.get("/capabilities/custom-domains").json()

    assert capability["cloudflare"] == {
        "admission_enabled": True,
        "ready": False,
        "detail": "Cloudflare diagnostic runtime is not configured",
    }

    response = client.post(
        "/sites/my-site/domains",
        json={"hostname": "proxy.example.com", "mode": "cloudflare"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Cloudflare diagnostic runtime is not configured"
    )


def test_custom_domain_capability_rejects_deployment_tokens(domain_api):
    client, _ = domain_api
    identity = Identity(
        user=User(id=1, github_login="dev", github_name="Dev"),
        token_type="deploy",
        site_name="my-site",
    )
    client.app.dependency_overrides[get_identity] = lambda: identity
    config.DEV_MODE = False
    try:
        response = client.get("/capabilities/custom-domains")
    finally:
        client.app.dependency_overrides.clear()
        config.DEV_MODE = True

    assert response.status_code == 403
    assert response.json()["detail"] == "Deploy tokens cannot perform this operation"


def test_failed_txt_check_returns_actionable_state(domain_api):
    client, _ = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com"},
    ).json()

    response = client.post(f"/sites/my-site/domains/{created['id']}/check")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["last_error"] == "txt_mismatch"

    repeated = client.post(f"/sites/my-site/domains/{created['id']}/check")
    assert repeated.status_code == 429
    assert int(repeated.headers["Retry-After"]) > 0


def test_multiple_aliases_can_be_created_and_removed_independently(domain_api):
    client, _ = domain_api
    first = client.post(
        "/sites/my-site/domains", json={"hostname": "one.example.com"}
    ).json()
    second = client.post(
        "/sites/my-site/domains", json={"hostname": "two.example.com"}
    ).json()

    removed = client.delete(f"/sites/my-site/domains/{first['id']}")
    claims = client.get("/sites/my-site/domains").json()

    assert removed.status_code == 204
    assert {claim["id"]: claim["status"] for claim in claims} == {
        second["id"]: "pending",
        first["id"]: "cancelled",
    }


def test_domain_quota_returns_actionable_response(domain_api, monkeypatch):
    client, _ = domain_api
    monkeypatch.setattr(config, "MAX_CUSTOM_DOMAINS_PER_SITE", 1)
    client.post("/sites/my-site/domains", json={"hostname": "one.example.com"})

    response = client.post(
        "/sites/my-site/domains", json={"hostname": "two.example.com"}
    )

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "This site has reached its custom-domain limit of 1. "
        "Remove an alias before adding another."
    )


def test_domain_admission_requires_live_control_plane_readiness(domain_api):
    client, _ = domain_api
    client.app.state.traefik_control = UnreadyControlPlane()

    response = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Custom domain control plane is not ready"


def test_domain_admission_requires_production_routing_configuration(
    domain_api, monkeypatch
):
    client, _ = domain_api
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_INGRESS_IPS", frozenset())

    response = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Custom domain production routing is not configured"


def test_domain_claim_requires_site_ownership(domain_api):
    client, _ = domain_api

    response = client.post(
        "/sites/other-site/domains",
        json={"hostname": "www.example.com"},
    )

    assert response.status_code == 403


def test_domain_claim_rejects_deployment_token(domain_api):
    client, _ = domain_api
    identity = Identity(
        user=User(id=1, github_login="dev", github_name="Dev"),
        token_type="deploy",
        site_name="my-site",
    )
    client.app.dependency_overrides[get_identity] = lambda: identity
    config.DEV_MODE = False
    try:
        response = client.get("/sites/my-site/domains")
    finally:
        client.app.dependency_overrides.clear()
        config.DEV_MODE = True

    assert response.status_code == 403
    assert response.json()["detail"] == "Deploy tokens cannot perform this operation"


def test_existing_domain_claims_remain_available_when_operator_disables_them(
    domain_api, monkeypatch
):
    client, _ = domain_api
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", False)

    response = client.get("/sites/my-site/domains")

    assert response.status_code == 200

    create_response = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com"},
    )
    assert create_response.status_code == 503
    assert create_response.json()["detail"] == "Custom domains are not enabled on this Buzz server"


def test_routed_domain_removal_waits_for_traefik_withdrawal(domain_api):
    client, resolver = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com"},
    ).json()
    resolver.values = (created["verification"]["value"],)
    client.post(f"/sites/my-site/domains/{created['id']}/check")
    with db_module.db() as conn:
        conn.execute(
            """UPDATE custom_domain_claims
            SET route_status = 'routed', route_generation = 1,
                challenge_token = 'bdc_test' WHERE id = ?""",
            (created["id"],),
        )

    response = client.delete(f"/sites/my-site/domains/{created['id']}")

    assert response.status_code == 202
    claim = client.get("/sites/my-site/domains").json()[0]
    assert claim["status"] == "verified"
    assert claim["route_status"] == "removing"
