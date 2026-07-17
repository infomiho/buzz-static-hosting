import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.app import create_app
from server.traefik_control import (
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


def request(server, path, token="secret"):
    return urlopen(
        Request(
            f"http://127.0.0.1:{server.port}{path}",
            headers={"Authorization": f"Bearer {token}"},
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
            "server.traefik_control.PROVIDER_POLL_MAX_AGE", timedelta(seconds=-1)
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
        def __init__(self, token, port, runtime_client, snapshot_provider=None):
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
