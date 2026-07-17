import json

import pytest

from server import db as db_module
from server.custom_domains import DomainClaimStore
from server.domain_routing import DomainRouteReconciler, build_traefik_snapshot


@pytest.fixture
def routing_db(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute(
            "INSERT INTO users (id, github_id, github_login) VALUES (1, 1, 'alice')"
        )
        conn.execute("INSERT INTO sites (name, owner_id) VALUES ('my-site', 1)")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
    return db_module.db


class FakeRuntimeClient:
    def __init__(self, router=None):
        self.router_response = router
        self.names = []

    def router(self, name):
        self.names.append(name)
        if callable(self.router_response):
            return self.router_response(name)
        return self.router_response


def expected_router(hostname="www.example.com"):
    return {
        "status": "enabled",
        "rule": f"Host(`{hostname}`)",
        "service": "buzz@docker",
        "entryPoints": ["https"],
        "tls": {"certResolver": "buzz-custom"},
    }


def reconciler(runtime, enabled=True):
    return DomainRouteReconciler(
        runtime,
        "https",
        "buzz@docker",
        "buzz-custom",
        routing_enabled=lambda: enabled,
        withdrawal_snapshot_acknowledged=lambda _name, _since: True,
    )


def test_snapshot_is_empty_without_routable_claims(routing_db):
    assert build_traefik_snapshot("https", "buzz@docker", "buzz-custom") == b"{}\n"


def test_snapshot_contains_deterministic_exact_router(routing_db):
    with routing_db() as conn:
        claim = DomainClaimStore(conn).prepare_routes(True)[0]

    first = build_traefik_snapshot("https", "buzz@docker", "buzz-custom")
    second = build_traefik_snapshot("https", "buzz@docker", "buzz-custom")
    payload = json.loads(first)

    assert first == second
    assert payload == {
        "http": {
            "routers": {
                claim.route_name: {
                    "entryPoints": ["https"],
                    "rule": "Host(`www.example.com`)",
                    "service": "buzz@docker",
                    "tls": {"certResolver": "buzz-custom"},
                }
            }
        }
    }


def test_snapshot_is_deterministic_with_multiple_aliases(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        for hostname in ("two.example.com", "three.example.com"):
            claim = store.create("my-site", hostname)
            store.record_check(claim.id, "my-site", (claim.verification_value,))
        claims = store.prepare_routes(True)

    first = build_traefik_snapshot("https", "buzz@docker", "buzz-custom")
    second = build_traefik_snapshot("https", "buzz@docker", "buzz-custom")
    routers = json.loads(first)["http"]["routers"]

    assert first == second
    assert set(routers) == {claim.route_name for claim in claims}
    assert {router["rule"] for router in routers.values()} == {
        "Host(`www.example.com`)",
        "Host(`two.example.com`)",
        "Host(`three.example.com`)",
    }


def test_reconciler_isolates_alias_failures(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        second = store.create("my-site", "two.example.com")
        store.record_check(second.id, "my-site", (second.verification_value,))

    def router(name):
        if name.startswith("buzz-domain-1-"):
            raise RuntimeError("unexpected failure")
        return expected_router("two.example.com")

    reconciler(FakeRuntimeClient(router)).run_once()

    with routing_db() as conn:
        claims = DomainClaimStore(conn).list_for_site("my-site")
    assert {claim.hostname: claim.route_status for claim in claims} == {
        "two.example.com": "routed",
        "www.example.com": "publishing",
    }


def test_reconciler_acknowledges_matching_runtime_router(routing_db):
    runtime = FakeRuntimeClient(expected_router())

    reconciler(runtime).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.route_status == "routed"
    assert runtime.names == [claim.route_name]


def test_reconciler_records_runtime_mismatch(routing_db):
    runtime = FakeRuntimeClient({**expected_router(), "service": "wrong@docker"})

    reconciler(runtime).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.route_status == "publishing"
    assert claim.route_error == "router_configuration_mismatch"


def test_reconciler_rejects_router_with_runtime_errors(routing_db):
    runtime = FakeRuntimeClient({**expected_router(), "errors": ["service unavailable"]})

    reconciler(runtime).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.route_status == "publishing"
    assert claim.route_error == "router_configuration_mismatch"


def test_reconciler_rejects_malformed_router_tls(routing_db):
    runtime = FakeRuntimeClient({**expected_router(), "tls": "invalid"})

    reconciler(runtime).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.route_status == "publishing"
    assert claim.route_error == "router_configuration_mismatch"


@pytest.mark.parametrize(
    ("router", "error"),
    [
        (None, "router_not_observed"),
        ({**expected_router(), "service": "wrong@docker"}, "router_configuration_mismatch"),
    ],
)
def test_active_cloudflare_route_failure_stops_serving_immediately(
    routing_db, router, error
):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET claim_mode = 'cloudflare' WHERE id = ?",
            (claim.id,),
        )
    reconciler(FakeRuntimeClient(expected_router())).run_once()
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        store.apply_cloudflare_activation(claim.id, claim.route_generation, None)

    reconciler(FakeRuntimeClient(router)).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.route_error == error
    assert claim.activation_error == error


def test_healthy_cloudflare_router_clears_route_error_for_revalidation(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET claim_mode = 'cloudflare' WHERE id = ?",
            (claim.id,),
        )
    reconciler(FakeRuntimeClient(expected_router())).run_once()
    reconciler(FakeRuntimeClient(None)).run_once()
    reconciler(FakeRuntimeClient(expected_router())).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.route_error is None
    assert claim.activated_at is None


def test_user_removal_waits_for_runtime_absence(routing_db):
    reconciler(FakeRuntimeClient(expected_router())).run_once()
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        assert store.cancel(claim.id, "my-site") is True

    reconciler(FakeRuntimeClient(None)).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.status == "cancelled"
    assert claim.route_status == "removed"
    assert claim.withdrawn_at is not None


def test_withdrawal_waits_for_provider_snapshot_acknowledgement(routing_db):
    reconciler(FakeRuntimeClient(expected_router())).run_once()
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        store.cancel(claim.id, "my-site")

    runtime = FakeRuntimeClient(None)
    DomainRouteReconciler(
        runtime,
        "https",
        "buzz@docker",
        "buzz-custom",
        routing_enabled=lambda: True,
        withdrawal_snapshot_acknowledged=lambda _name, _since: False,
    ).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.status == "verified"
    assert claim.route_status == "removing"
    assert runtime.names == []


def test_operator_disable_withdraws_without_cancelling_ownership(routing_db):
    reconciler(FakeRuntimeClient(expected_router())).run_once()

    reconciler(FakeRuntimeClient(None), enabled=False).run_once()

    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.status == "verified"
    assert claim.route_status == "not_routed"
    assert claim.withdrawn_at is not None


def test_public_challenge_is_recorded_for_current_generation(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.prepare_routes(True)[0]
        found = store.find_challenge(claim.hostname, claim.challenge_token)
        store.mark_challenge_seen(claim.id, claim.route_generation)

    with routing_db() as conn:
        updated = DomainClaimStore(conn).get(claim.id, "my-site")
    assert found is not None
    assert updated.challenge_seen_at is not None


def test_new_route_generation_resets_public_challenge_evidence(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        first = store.prepare_routes(True)[0]
        store.mark_challenge_seen(first.id, first.route_generation)
        store.mark_routed(first.id, first.route_generation)
        removing = store.prepare_routes(False)[0]
        store.finish_withdrawal(removing.id, removing.route_generation)
        second = store.prepare_routes(True)[0]

    assert second.route_generation == first.route_generation + 1
    assert second.challenge_token != first.challenge_token
    assert second.challenge_seen_at is None
