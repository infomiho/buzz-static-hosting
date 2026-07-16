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


def test_domain_admission_requires_live_control_plane_readiness(domain_api):
    client, _ = domain_api
    client.app.state.traefik_control = UnreadyControlPlane()

    response = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Custom domain control plane is not ready"


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
