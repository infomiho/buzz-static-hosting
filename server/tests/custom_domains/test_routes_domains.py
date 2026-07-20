from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from server import config
from server import db as db_module
from server.app import create_app
from server.auth_service import Identity, User
from server.dependencies import get_identity
from server.custom_domains.claims import DomainClaimStore
from server.custom_domains.transitions import (
    DomainClaimStateMachine,
    DomainTransitionCoordinator,
)
from server.custom_domains.evidence import DnsObservation


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
    app.state.custom_domains.control = ReadyControlPlane()
    app.state.custom_domains.runtime_ready = True
    app.state.custom_domains.range_state = type("RangeState", (), {"error": None})()
    app.state.custom_domains.transition_coordinator = object()
    resolver = FakeTxtResolver()
    app.state.custom_domains.txt_resolver = resolver
    return TestClient(app), resolver


def test_domain_claim_lifecycle(domain_api):
    client, resolver = domain_api

    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "WWW.Example.COM", "mode": "direct"},
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


def test_add_domain_requires_automatic_readiness(domain_api):
    client, _ = domain_api
    client.app.state.custom_domains.transition_coordinator = None
    unavailable = client.post(
        "/sites/my-site/domains", json={"hostname": "old.example.com"}
    )
    client.app.state.custom_domains.transition_coordinator = object()
    automatic = client.post(
        "/sites/my-site/domains", json={"hostname": "new.example.com"}
    )

    assert unavailable.status_code == 503
    assert automatic.status_code == 201
    assert automatic.json()["mode"] == "direct"
    assert automatic.json()["connection_status"] == "waiting_for_dns"
    with db_module.db() as conn:
        rows = conn.execute(
            "SELECT hostname, automatic_mode FROM custom_domain_claims ORDER BY id"
        ).fetchall()
    assert [(row["hostname"], row["automatic_mode"]) for row in rows] == [
        ("new.example.com", 1),
    ]


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
        "routing_enabled": True,
        "routing_targets": [
            {"type": "A", "value": "8.8.8.8"},
            {"type": "AAAA", "value": "2001:4860:4860::8888"},
        ],
        "automatic": {"ready": True, "detail": None},
        "cloudflare": {"supported": True, "detail": None},
    }


def test_custom_domain_capability_reports_automatic_runtime_readiness(domain_api):
    client, _ = domain_api
    client.app.state.custom_domains.transition_coordinator = None
    unready = client.get("/capabilities/custom-domains").json()["automatic"]
    client.app.state.custom_domains.transition_coordinator = object()
    ready = client.get("/capabilities/custom-domains").json()["automatic"]

    assert unready == {
        "ready": False,
        "detail": "Automatic domain transition runtime is not configured",
    }
    assert ready == {"ready": True, "detail": None}


def test_automatic_readiness_is_independent_of_cloudflare(domain_api):
    client, _ = domain_api
    # Cloudflare unsupported (stale ranges) must not block automatic onboarding
    # of direct domains.
    client.app.state.custom_domains.range_state = type(
        "RangeState", (), {"error": "range_data_stale"}
    )()

    capability = client.get("/capabilities/custom-domains").json()

    assert capability["automatic"]["ready"] is True
    assert capability["cloudflare"]["supported"] is False


def test_custom_domain_capability_distinguishes_disabled_and_unready(
    domain_api, monkeypatch
):
    client, _ = domain_api
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", False)
    disabled = client.get("/capabilities/custom-domains").json()
    monkeypatch.setattr(config, "CUSTOM_DOMAINS_ENABLED", True)
    client.app.state.custom_domains.control = UnreadyControlPlane()
    unready = client.get("/capabilities/custom-domains").json()

    assert disabled["status"] == "disabled"
    assert disabled["detail"] == "Custom domains are not enabled on this Buzz server"
    assert unready["status"] == "unready"
    assert unready["detail"] == "Custom domain control plane is not ready"


def test_cloudflare_capability_is_independent_of_direct_ingress(
    domain_api, monkeypatch
):
    client, _ = domain_api
    monkeypatch.setattr(config, "CUSTOM_DOMAIN_INGRESS_IPS", frozenset())

    capability = client.get("/capabilities/custom-domains").json()

    assert capability["status"] == "unready"
    assert capability["cloudflare"] == {"supported": True, "detail": None}


def test_cloudflare_capability_requires_diagnostic_runtime(domain_api):
    client, _ = domain_api
    client.app.state.custom_domains.runtime_ready = False

    capability = client.get("/capabilities/custom-domains").json()

    assert capability["cloudflare"] == {
        "supported": False,
        "detail": "Cloudflare diagnostic runtime is not configured",
    }


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
        json={"hostname": "www.example.com", "mode": "direct"},
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
        "/sites/my-site/domains", json={"hostname": "one.example.com", "mode": "direct"}
    ).json()
    second = client.post(
        "/sites/my-site/domains", json={"hostname": "two.example.com", "mode": "direct"}
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
    client.post("/sites/my-site/domains", json={"hostname": "one.example.com", "mode": "direct"})

    response = client.post(
        "/sites/my-site/domains", json={"hostname": "two.example.com", "mode": "direct"}
    )

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "This site has reached its custom-domain limit of 1. "
        "Remove an alias before adding another."
    )


def test_domain_admission_requires_live_control_plane_readiness(domain_api):
    client, _ = domain_api
    client.app.state.custom_domains.control = UnreadyControlPlane()

    response = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com", "mode": "direct"},
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
        json={"hostname": "www.example.com", "mode": "direct"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Custom domain production routing is not configured"


def test_domain_claim_requires_site_ownership(domain_api):
    client, _ = domain_api

    response = client.post(
        "/sites/other-site/domains",
        json={"hostname": "www.example.com", "mode": "direct"},
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
        json={"hostname": "www.example.com", "mode": "direct"},
    )
    assert create_response.status_code == 503
    assert create_response.json()["detail"] == "Custom domains are not enabled on this Buzz server"


def test_routed_domain_removal_waits_for_traefik_withdrawal(domain_api):
    client, resolver = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com", "mode": "direct"},
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


def test_transition_cancel_endpoint_retains_valid_effective_mode(domain_api):
    client, resolver = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "www.example.com", "mode": "direct"},
    ).json()
    resolver.values = (created["verification"]["value"],)
    client.post(f"/sites/my-site/domains/{created['id']}/check")
    with db_module.db() as conn:
        claims = DomainClaimStore(conn)
        claim = claims.prepare_routes(True)[0]
        claims.mark_routed(claim.id, claim.route_generation)
        claim = claims.get(claim.id, "my-site")
        DomainClaimStateMachine(conn).apply_activation_decision(claim, None)
        claim = claims.get(claim.id, "my-site")
        DomainClaimStateMachine(conn).start(
            claim.id, claim.route_generation, "cloudflare"
        )
    observation = type(
        "Observation",
        (),
        {
            "mode": "direct",
            "addresses": ("8.8.8.8",),
            "ttl": 60,
            "fingerprint": "direct",
            "error": None,
        },
    )()
    class Evidence:
        common_error = None

        def __init__(self, claim):
            self.claim = claim
            self.dns = observation
            self.confirmed_dns = observation

        def target_error(self, _mode):
            return None

    collector = type(
        "Collector",
        (),
        {
            "collect": lambda self, claim, _mode=None: Evidence(claim),
        },
    )()
    diagnostic_recorder = type(
        "DiagnosticRecorder",
        (),
        {"record_transition": lambda *_args: True, "record_health": lambda *_args: True},
    )()
    client.app.state.custom_domains.transition_coordinator = DomainTransitionCoordinator(
        collector,
        diagnostic_recorder,
        admission_enabled=lambda: False,
        cloudflare_target_enabled=lambda: False,
        database=db_module.db,
    )

    response = client.post(
        f"/sites/my-site/domains/{created['id']}/transition/cancel"
    )

    assert response.status_code == 200
    assert response.json()["effective_mode"] == "direct"
    assert response.json()["connection_status"] == "connected"
    assert response.json()["transition_error"] is None


def test_api_does_not_report_stale_activated_claim_as_connected(domain_api):
    client, resolver = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "stale.example.com", "mode": "direct"},
    ).json()
    resolver.values = (created["verification"]["value"],)
    client.post(f"/sites/my-site/domains/{created['id']}/check")
    with db_module.db() as conn:
        claims = DomainClaimStore(conn)
        claim = claims.prepare_routes(True)[0]
        claims.mark_routed(claim.id, claim.route_generation)
        claim = claims.get(claim.id, "my-site")
        DomainClaimStateMachine(conn).apply_activation_decision(claim, None)
        conn.execute(
            """UPDATE custom_domain_claims
            SET health_checked_at = datetime('now', '-11 minutes') WHERE id = ?""",
            (claim.id,),
        )

    response = client.get("/sites/my-site/domains").json()[0]

    assert response["connection_status"] == "action_needed"
    assert response["effective_mode"] is None


def test_api_does_not_report_withdrawn_claim_as_connected(domain_api):
    client, resolver = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "withdrawn.example.com", "mode": "direct"},
    ).json()
    resolver.values = (created["verification"]["value"],)
    client.post(f"/sites/my-site/domains/{created['id']}/check")
    with db_module.db() as conn:
        claims = DomainClaimStore(conn)
        claim = claims.prepare_routes(True)[0]
        claims.mark_routed(claim.id, claim.route_generation)
        claim = claims.get(claim.id, "my-site")
        DomainClaimStateMachine(conn).apply_activation_decision(claim, None)
        conn.execute(
            """UPDATE custom_domain_claims
            SET status = 'cancelled', route_status = 'removed',
                removal_requested_at = CURRENT_TIMESTAMP,
                withdrawn_at = CURRENT_TIMESTAMP
            WHERE id = ?""",
            (claim.id,),
        )

    response = client.get("/sites/my-site/domains").json()[0]

    assert response["connection_status"] == "waiting_for_dns"
    assert response["effective_mode"] is None


def test_completed_transition_does_not_project_historical_paths(domain_api):
    client, resolver = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "complete.example.com", "mode": "direct"},
    ).json()
    resolver.values = (created["verification"]["value"],)
    client.post(f"/sites/my-site/domains/{created['id']}/check")
    with db_module.db() as conn:
        claims = DomainClaimStore(conn)
        claim = claims.prepare_routes(True)[0]
        claims.mark_routed(claim.id, claim.route_generation)
        state = DomainClaimStateMachine(conn)
        transition = state.start(
            claim.id, claim.route_generation, "direct"
        )
        observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "test"
        )
        state.record_reserved_observation(claim, reservation, observation)
        state.release_reservation(reservation)
        conn.execute(
            "UPDATE custom_domain_mode_transitions SET last_target_observed_at = datetime('now', '-61 seconds') WHERE claim_id = ?",
            (claim.id,),
        )
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "test"
        )
        state.record_reserved_observation(claim, reservation, observation)
        state.record_reserved_confirmation(claim, reservation, observation)
        assert state.complete_reserved(claim, reservation)

    response = client.get("/sites/my-site/domains").json()[0]

    assert response["observed_mode"] is None
    assert response["target_mode"] is None
    assert response["transition_started_at"] is None


def test_verified_ownership_check_returns_current_transition_and_diagnostic(domain_api):
    client, resolver = domain_api
    created = client.post(
        "/sites/my-site/domains",
        json={"hostname": "proxy.example.com"},
    ).json()
    resolver.values = (created["verification"]["value"],)
    client.post(f"/sites/my-site/domains/{created['id']}/check")
    with db_module.db() as conn:
        claims = DomainClaimStore(conn)
        claim = claims.prepare_routes(True)[0]
        claims.mark_routed(claim.id, claim.route_generation)
        claim = claims.get(claim.id, "my-site")
        DomainClaimStateMachine(conn).apply_activation_decision(claim, None)
        # Make it an activated Cloudflare claim so a transition to direct is valid.
        conn.execute(
            "UPDATE custom_domain_claims SET claim_mode = 'cloudflare' WHERE id = ?",
            (claim.id,),
        )
        claim = claims.get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).start(
            claim.id, claim.route_generation, "direct"
        )
        conn.execute(
            """INSERT INTO custom_domain_cloudflare_diagnostics
            (claim_id, route_generation, mode_generation, probe_generation, checked_at,
             dns_status, edge_tls_status, edge_http_status, http_forward_status,
             origin_status, ownership_status)
            VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP, 'healthy', 'healthy', 'healthy',
                    'healthy', 'healthy', 'healthy')""",
            (claim.id, claim.route_generation, transition.mode_generation),
        )

    response = client.post(f"/sites/my-site/domains/{created['id']}/check")

    assert response.status_code == 200
    assert response.json()["target_mode"] == "direct"
    assert response.json()["cloudflare_diagnostics"]["dns"]["status"] == "healthy"
