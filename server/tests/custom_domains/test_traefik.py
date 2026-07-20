import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.app import create_app
from server.custom_domains.traefik import (
    EMPTY_SNAPSHOT,
    TraefikControlServer,
    TraefikRuntimeClient,
)


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self, size):
        return self._body


def request(server, path, token="secret", method="GET"):
    return urlopen(
        Request(
            f"http://127.0.0.1:{server.port}{path}",
            headers={"Authorization": f"Bearer {token}"},
            method=method,
        )
    )


def test_control_server_serves_authenticated_empty_snapshot():
    server = TraefikControlServer("secret", 0, None, host="127.0.0.1")
    server.start()
    try:
        with request(server, "/traefik") as response:
            assert response.read() == EMPTY_SNAPSHOT
            assert response.headers["Cache-Control"] == "no-store"
    finally:
        server.stop()


def test_control_server_rejects_missing_or_wrong_token():
    server = TraefikControlServer("secret", 0, None, host="127.0.0.1")
    server.start()
    try:
        for token in (None, "wrong"):
            headers = {} if token is None else {"Authorization": f"Bearer {token}"}
            req = Request(f"http://127.0.0.1:{server.port}/traefik", headers=headers)
            try:
                urlopen(req)
                raise AssertionError("request unexpectedly succeeded")
            except HTTPError as error:
                assert error.code == 401
    finally:
        server.stop()


def test_operator_transition_endpoint_uses_dedicated_token():
    handoffs = [{"claim_id": 7, "hostname": "www.example.com"}]
    server = TraefikControlServer(
        "traefik-secret",
        0,
        None,
        operator_token="old-operator-secret, operator-secret",
        handoff_provider=lambda: handoffs,
        host="127.0.0.1",
    )
    server.start()
    try:
        with request(
            server, "/operator/domain-transitions?source=test", "operator-secret"
        ) as response:
            assert json.loads(response.read()) == {"transitions": handoffs}
            assert response.headers["Cache-Control"] == "no-store"
        with pytest.raises(HTTPError) as error:
            request(server, "/operator/domain-transitions", "traefik-secret")
        assert error.value.code == 401
    finally:
        server.stop()


def test_operator_routes_authenticate_before_route_and_method_handling():
    server = TraefikControlServer(
        "traefik-secret",
        0,
        None,
        operator_token="operator-secret",
        handoff_provider=lambda: [],
        host="127.0.0.1",
    )
    server.start()
    try:
        with pytest.raises(HTTPError) as missing:
            request(server, "/operator/missing", "wrong")
        assert missing.value.code == 401
        assert missing.value.headers["WWW-Authenticate"] == "Bearer"
        assert missing.value.headers["Cache-Control"] == "no-store"
        assert json.loads(missing.value.read()) == {"detail": "Unauthorized"}
        with pytest.raises(HTTPError) as method:
            request(server, "/operator/domain-transitions", "wrong", "POST")
        assert method.value.code == 401
        assert method.value.headers["WWW-Authenticate"] == "Bearer"
        with pytest.raises(HTTPError) as authorized_method:
            request(server, "/operator/domain-transitions", "operator-secret", "POST")
        assert authorized_method.value.code == 405
        assert authorized_method.value.headers["Allow"] == "GET"
        assert authorized_method.value.headers["Cache-Control"] == "no-store"
        with pytest.raises(HTTPError) as cancel_method:
            request(
                server,
                "/operator/domain-transitions/7/cancel",
                "wrong",
                "PUT",
            )
        assert cancel_method.value.code == 401
    finally:
        server.stop()


def test_operator_provider_failure_is_no_store_json():
    def fail():
        raise RuntimeError("provider failed")

    server = TraefikControlServer(
        "traefik-secret",
        0,
        None,
        operator_token="operator-secret",
        handoff_provider=fail,
        host="127.0.0.1",
    )
    server.start()
    try:
        with pytest.raises(HTTPError) as error:
            request(server, "/operator/domain-transitions", "operator-secret")
        assert error.value.code == 500
        assert error.value.headers["Content-Type"] == "application/json"
        assert error.value.headers["Cache-Control"] == "no-store"
        assert json.loads(error.value.read())["detail"] == "Could not read transitions"
    finally:
        server.stop()


def test_operator_transition_cancellation_maps_results_and_errors():
    actions = []

    def cancel(claim_id):
        actions.append(claim_id)
        if claim_id == 8:
            from server.custom_domains.errors import ClaimConflict

            raise ClaimConflict("Transition changed")
        if claim_id == 9:
            from server.custom_domains.errors import ClaimNotFound

            raise ClaimNotFound("Custom domain claim not found")
        return {"claim_id": claim_id, "state": "cancelled"}

    server = TraefikControlServer(
        "traefik-secret",
        0,
        None,
        operator_token="operator-secret",
        handoff_provider=lambda: [],
        cancel_provider=cancel,
        host="127.0.0.1",
    )
    server.start()
    try:
        with request(
            server,
            "/operator/domain-transitions/7/cancel",
            "operator-secret",
            "POST",
        ) as response:
            assert response.status == 200
            assert json.load(response) == {"claim_id": 7, "state": "cancelled"}
        for claim_id, status in ((8, 409), (9, 404)):
            with pytest.raises(HTTPError) as error:
                request(
                    server,
                    f"/operator/domain-transitions/{claim_id}/cancel",
                    "operator-secret",
                    "POST",
                )
            assert error.value.code == status
            assert json.loads(error.value.read())["detail"]
            error.value.close()
        with pytest.raises(HTTPError) as error:
            request(
                server,
                "/operator/domain-transitions/7/cancel",
                "traefik-secret",
                "POST",
            )
        assert error.value.code == 401
        assert actions == [7, 8, 9]
    finally:
        server.stop()


def test_control_server_rejects_malformed_snapshot_without_recording_poll():
    server = TraefikControlServer(
        "secret",
        0,
        None,
        snapshot_provider=lambda: b"not-json",
        host="127.0.0.1",
    )
    server.start()
    try:
        with pytest.raises(HTTPError) as error:
            request(server, "/traefik")
        assert error.value.code == 500
        assert not server.is_ready()
    finally:
        server.stop()


def test_readiness_reports_provider_poll_and_runtime_dependencies():
    def open_url(request, timeout):
        assert request.headers["Authorization"] == "Bearer runtime-secret"
        if request.full_url.endswith("/entrypoints"):
            return FakeResponse([{"name": "http"}, {"name": "https"}])
        assert request.full_url.endswith("/http/services/buzz@docker")
        return FakeResponse({"status": "enabled"})

    runtime_client = TraefikRuntimeClient(
        "http://traefik:8082/api",
        "Bearer runtime-secret",
        "https",
        "buzz@docker",
        open_url=open_url,
    )
    server = TraefikControlServer("secret", 0, runtime_client, host="127.0.0.1")
    server.start()
    try:
        with request(server, "/traefik"):
            pass
        server.refresh_readiness()
        with request(server, "/ready") as response:
            payload = json.load(response)
        assert payload["status"] == "ready"
        assert all(check["ok"] for check in payload["checks"].values())
    finally:
        server.stop()


def test_readiness_is_independent_from_public_health():
    server = TraefikControlServer("secret", 0, None, host="127.0.0.1")
    server.start()
    try:
        with request(server, "/ready") as response:
            payload = json.load(response)
        assert payload["status"] == "not_ready"
        assert payload["checks"]["runtime_api"]["reason"] == "not_configured"
    finally:
        server.stop()


def test_withdrawal_requires_a_post_request_snapshot():
    server = TraefikControlServer("secret", 0, None, host="127.0.0.1")
    server.start()
    try:
        requested_at = datetime.now(timezone.utc).isoformat()
        with request(server, "/traefik") as response:
            assert response.read() == EMPTY_SNAPSHOT
        assert server.withdrawal_snapshot_acknowledged("buzz-domain-1-g1", requested_at)
    finally:
        server.stop()


def test_readiness_rejects_stale_provider_poll(monkeypatch):
    runtime_client = TraefikRuntimeClient(
        "http://traefik:8082/api",
        None,
        "https",
        "buzz@docker",
        open_url=lambda request, timeout: FakeResponse(
            [{"name": "https"}]
            if request.full_url.endswith("/entrypoints")
            else {"status": "enabled"}
        ),
    )
    server = TraefikControlServer("secret", 0, runtime_client, host="127.0.0.1")
    server.start()
    try:
        with request(server, "/traefik"):
            pass
        server.refresh_readiness()
        monkeypatch.setattr(
            "server.custom_domains.traefik.PROVIDER_POLL_MAX_AGE", timedelta(seconds=-1)
        )
        assert not server.is_ready()
    finally:
        server.stop()


def test_readiness_rejects_service_runtime_errors():
    runtime_client = TraefikRuntimeClient(
        "http://traefik:8082/api",
        None,
        "https",
        "buzz@docker",
        open_url=lambda request, timeout: FakeResponse(
            [{"name": "https"}]
            if request.full_url.endswith("/entrypoints")
            else {"status": "enabled", "errors": ["unavailable"]}
        ),
    )
    server = TraefikControlServer("secret", 0, runtime_client, host="127.0.0.1")
    server.start()
    try:
        with request(server, "/traefik"):
            pass
        server.refresh_readiness()
        assert not server.is_ready()
    finally:
        server.stop()


def test_router_absence_requires_successful_runtime_collection_query():
    def open_url(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", None, None)

    runtime_client = TraefikRuntimeClient(
        "http://traefik:8082/api",
        None,
        "https",
        "buzz@docker",
        open_url=open_url,
    )

    with pytest.raises(HTTPError):
        runtime_client.router("buzz-domain-1-g1")


def test_disabled_custom_domains_do_not_start_control_listener(tmp_path, monkeypatch):
    class UnexpectedControlServer:
        def __init__(self, *args, **kwargs):
            raise AssertionError("control listener started while custom domains were disabled")

    monkeypatch.setattr("server.app.CUSTOM_DOMAINS_ENABLED", False)
    monkeypatch.setattr("server.app.TRAEFIK_CONTROL_TOKEN", "configured-but-disabled")
    monkeypatch.setattr("server.app.TraefikControlServer", UnexpectedControlServer)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()

    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"host": "localhost:8080"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_disabling_control_plane_is_rejected_while_routes_remain(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation)
            VALUES ('www.example.com', 'my-site', 'bdv_test', 'verified',
                    '2026-07-16T00:00:00+00:00', '2026-07-17T00:00:00+00:00',
                    'routed', 1)"""
        )
    monkeypatch.setattr("server.app.CUSTOM_DOMAINS_ENABLED", False)

    with pytest.raises(RuntimeError, match="Withdraw all custom-domain routers"):
        with TestClient(create_app()):
            pass


def test_disabling_cloudflare_activation_is_rejected_while_active_route_remains(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, activated_at)
            VALUES ('www.example.com', 'my-site', 'bdv_test', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 1, 'cloudflare', '2026-07-16T00:00:00+00:00')"""
        )
    monkeypatch.setattr("server.app.CLOUDFLARE_ACTIVATION_ENABLED", False)

    with pytest.raises(RuntimeError, match="Withdraw active Cloudflare routers"):
        with TestClient(create_app()):
            pass


@pytest.mark.parametrize(
    ("control_token", "api_url"),
    [(None, "http://traefik:8082/api"), ("secret", None)],
)
def test_active_cloudflare_claim_requires_complete_runtime(
    tmp_path, monkeypatch, control_token, api_url
):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, activated_at)
            VALUES ('www.example.com', 'my-site', 'bdv_test', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 1, 'cloudflare', '2026-07-16T00:00:00+00:00')"""
        )
    monkeypatch.setattr("server.app.CLOUDFLARE_ACTIVATION_ENABLED", True)
    monkeypatch.setattr("server.app.CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr("server.app.TRAEFIK_CONTROL_TOKEN", control_token)
    monkeypatch.setattr("server.app.TRAEFIK_API_URL", api_url)

    with pytest.raises(RuntimeError, match="complete custom-domain runtime"):
        with TestClient(create_app()):
            pass


def test_enabled_custom_domains_start_and_stop_control_listener(tmp_path, monkeypatch):
    events = []

    class FakeControlServer:
        def __init__(
            self,
            token,
            port,
            runtime_client,
            snapshot_provider=None,
            operator_token=None,
            handoff_provider=None,
        ):
            assert token == "secret"
            assert port == 8081
            assert snapshot_provider is not None
            events.append("created")

        def start(self):
            events.append("started")

        def stop(self):
            events.append("stopped")

    monkeypatch.setattr("server.app.CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr("server.app.TRAEFIK_CONTROL_TOKEN", "secret")
    monkeypatch.setattr("server.app.TRAEFIK_CONTROL_PORT", 8081)
    monkeypatch.setattr("server.app.TRAEFIK_API_URL", None)
    monkeypatch.setattr("server.app.TraefikControlServer", FakeControlServer)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()

    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"host": "localhost:8080"})
        assert response.status_code == 200
        assert events == ["created", "started"]

    assert events == ["created", "started", "stopped"]


def test_lifespan_runs_transition_detection_before_legacy_validators(tmp_path, monkeypatch):
    events = []
    observed = threading.Event()

    class Runtime:
        def __init__(self, *args):
            pass

    class Control:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def refresh_readiness(self):
            events.append("readiness")

        def withdrawal_snapshot_acknowledged(self, *_args):
            return True

        def set_operator_handlers(self, *_args):
            events.append("operator")

    class Loop:
        def __init__(self, name):
            self.name = name

        def run_once(self):
            events.append(self.name)
            if self.name == "cloudflare":
                observed.set()

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    monkeypatch.setattr("server.app.CUSTOM_DOMAINS_ENABLED", True)
    monkeypatch.setattr("server.app.TRAEFIK_CONTROL_TOKEN", "secret")
    monkeypatch.setattr("server.app.TRAEFIK_API_URL", "http://traefik/api")
    monkeypatch.setattr("server.app.TraefikRuntimeClient", Runtime)
    monkeypatch.setattr("server.app.TraefikControlServer", Control)
    monkeypatch.setattr("server.app.DomainRouteReconciler", lambda *a, **k: Loop("route"))
    monkeypatch.setattr("server.app.DomainActivator", lambda *a, **k: Loop("direct"))
    monkeypatch.setattr(
        "server.app.CloudflareDiagnostician", lambda *a, **k: Loop("cloudflare")
    )
    monkeypatch.setattr(
        "server.app.DomainTransitionCoordinator", lambda *a, **k: Loop("transition")
    )

    with TestClient(create_app()):
        assert observed.wait(2)

    assert events.index("transition") < events.index("direct")
    assert events.index("transition") < events.index("cloudflare")
