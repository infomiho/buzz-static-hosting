from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.parse import quote
from urllib.request import Request, urlopen

from .errors import ClaimConflict, ClaimNotFound

EMPTY_SNAPSHOT = b"{}\n"
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_RUNTIME_RESPONSE_BYTES = 1024 * 1024
PROVIDER_POLL_MAX_AGE = timedelta(seconds=15)
logger = logging.getLogger(__name__)


class TraefikRuntimeClient:
    def __init__(
        self,
        api_url: str,
        authorization: str | None,
        https_entrypoint: str,
        service: str,
        open_url: Callable[..., Any] = urlopen,
    ):
        self._api_url = api_url.rstrip("/")
        self._authorization = authorization
        self._https_entrypoint = https_entrypoint
        self._service = service
        self._open_url = open_url

    def readiness(self) -> dict[str, dict[str, Any]]:
        entrypoints = self._check_entrypoint()
        service = self._check_service()
        return {
            "runtime_api": {
                "ok": entrypoints["reachable"] or service["reachable"],
            },
            "https_entrypoint": {
                "ok": entrypoints["found"],
                "expected": self._https_entrypoint,
            },
            "service": {
                "ok": service["found"],
                "expected": self._service,
            },
        }

    def _check_entrypoint(self) -> dict[str, bool]:
        try:
            payload = self._get_json("/entrypoints")
            names = {
                item.get("name")
                for item in payload
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            return {"reachable": True, "found": self._https_entrypoint in names}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"reachable": False, "found": False}

    def _check_service(self) -> dict[str, bool]:
        try:
            payload = self._get_json(f"/http/services/{quote(self._service, safe='@')}")
            found = (
                isinstance(payload, dict)
                and payload.get("status") == "enabled"
                and not payload.get("errors")
            )
            return {"reachable": True, "found": found}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"reachable": False, "found": False}

    def _get_json(self, path: str) -> Any:
        headers = {"Accept": "application/json"}
        if self._authorization:
            headers["Authorization"] = self._authorization
        request = Request(f"{self._api_url}{path}", headers=headers)
        with self._open_url(request, timeout=2) as response:
            body = response.read(MAX_RUNTIME_RESPONSE_BYTES + 1)
        if len(body) > MAX_RUNTIME_RESPONSE_BYTES:
            raise ValueError("Traefik runtime response is too large")
        return json.loads(body)

    def router(self, name: str) -> dict[str, Any] | None:
        from urllib.error import HTTPError

        qualified_name = quote(f"{name}@http", safe="@")
        try:
            payload = self._get_json(f"/http/routers/{qualified_name}")
        except HTTPError as exc:
            if exc.code == 404:
                routers = self._get_json("/http/routers")
                if not isinstance(routers, list):
                    raise ValueError("Traefik router collection is not an array") from exc
                names = {
                    item.get("name")
                    for item in routers
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                }
                if f"{name}@http" not in names:
                    return None
                raise ValueError("Traefik router detail and collection responses disagree") from exc
            raise
        if not isinstance(payload, dict):
            raise ValueError("Traefik router response is not an object")
        return payload


class TraefikControlServer:
    def __init__(
        self,
        token: str,
        port: int,
        runtime_client: TraefikRuntimeClient | None,
        snapshot_provider: Callable[[], bytes] | None = None,
        host: str = "0.0.0.0",
        operator_token: str | None = None,
        handoff_provider: Callable[[], list[dict[str, Any]]] | None = None,
        cancel_provider: Callable[[int], dict[str, Any]] | None = None,
    ):
        if not token:
            raise ValueError("Traefik control token must not be empty")
        self._token = token
        self._operator_tokens = tuple(
            token.strip() for token in (operator_token or "").split(",") if token.strip()
        )
        self._handoff_provider = handoff_provider
        self._cancel_provider = cancel_provider
        self._runtime_client = runtime_client
        self._snapshot_provider = snapshot_provider or (lambda: EMPTY_SNAPSHOT)
        self._last_successful_poll: tuple[datetime, frozenset[str]] | None = None
        self._runtime_checks: dict[str, dict[str, Any]] = {
            "runtime_api": {"ok": False, "reason": "not_observed"}
            if runtime_client
            else {"ok": False, "reason": "not_configured"}
        }
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_port

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="buzz-traefik-control",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._thread:
            self._server.server_close()
            return
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
        self._thread = None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        control = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path.startswith("/operator/"):
                    if not self._require_operator_auth():
                        return
                    if path != "/operator/domain-transitions":
                        self.send_error(404)
                        return
                    if not control._handoff_provider:
                        self._send_error_json(503, "Operator endpoint unavailable")
                        return
                    logger.info("Custom-domain operator transition list read")
                    try:
                        transitions = control._handoff_provider()
                    except Exception:
                        logger.exception("Could not read custom-domain operator transitions")
                        self._send_error_json(500, "Could not read transitions")
                        return
                    body = json.dumps(
                        {"transitions": transitions}, separators=(",", ":"), sort_keys=True
                    ).encode() + b"\n"
                    self._send_json(body)
                    return
                if not control._authorized(self.headers.get("Authorization")):
                    self.send_error(401)
                    return
                if path == "/traefik":
                    try:
                        body = control._snapshot()
                    except Exception:
                        logger.exception("Could not build Traefik custom-domain snapshot")
                        self.send_error(500)
                        return
                    try:
                        self._send_json(body)
                    except OSError:
                        logger.warning("Traefik disconnected before receiving its snapshot")
                    else:
                        control._record_poll(body)
                    return
                if path == "/ready":
                    self._send_json(control._readiness_payload())
                    return
                self.send_error(404)

            def do_POST(self) -> None:
                prefix = "/operator/domain-transitions/"
                suffix = "/cancel"
                path = urlsplit(self.path).path
                if path.startswith("/operator/") and not self._require_operator_auth():
                    return
                if path == "/operator/domain-transitions":
                    self._send_method_not_allowed("GET")
                    return
                if not path.startswith(prefix) or not path.endswith(suffix):
                    self.send_error(404)
                    return
                raw_claim_id = path[len(prefix) : -len(suffix)]
                try:
                    claim_id = int(raw_claim_id)
                    if claim_id <= 0 or str(claim_id) != raw_claim_id:
                        raise ValueError
                except ValueError:
                    self.send_error(404)
                    return
                if not control._cancel_provider:
                    self._send_error_json(503, "Operator endpoint unavailable")
                    return
                try:
                    result = control._cancel_provider(claim_id)
                except ClaimNotFound as exc:
                    self._send_error_json(404, str(exc))
                    return
                except ClaimConflict as exc:
                    self._send_error_json(409, str(exc))
                    return
                except Exception:
                    logger.exception(
                        "Custom-domain operator transition cancellation failed for claim %d",
                        claim_id,
                    )
                    self._send_error_json(500, "Could not cancel transition")
                    return
                logger.info(
                    "Custom-domain operator transition cancelled for claim %d",
                    claim_id,
                )
                self._send_json(
                    json.dumps(result, separators=(",", ":"), sort_keys=True).encode()
                    + b"\n"
                )

            def do_HEAD(self) -> None:
                path = urlsplit(self.path).path
                if path.startswith("/operator/"):
                    if not self._require_operator_auth():
                        return
                    if path == "/operator/domain-transitions":
                        self._send_method_not_allowed("GET")
                    else:
                        self.send_error(404)
                    return
                if not control._authorized(self.headers.get("Authorization")):
                    self.send_error(401)
                    return
                if path not in {"/traefik", "/ready"}:
                    self.send_error(404)
                    return
                body = control._snapshot() if path == "/traefik" else control._readiness_payload()
                self._send_json(body, include_body=False)

            def do_PUT(self) -> None:
                self._reject_operator_method()

            def do_PATCH(self) -> None:
                self._reject_operator_method()

            def do_DELETE(self) -> None:
                self._reject_operator_method()

            def do_OPTIONS(self) -> None:
                self._reject_operator_method()

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_json(self, body: bytes, include_body: bool = True) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if include_body:
                    self.wfile.write(body)

            def _send_error_json(
                self, status: int, detail: str, headers: dict[str, str] | None = None
            ) -> None:
                body = json.dumps({"detail": detail}, separators=(",", ":")).encode() + b"\n"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_method_not_allowed(self, allow: str) -> None:
                body = b'{"detail":"Method Not Allowed"}\n'
                self.send_response(405)
                self.send_header("Allow", allow)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _reject_operator_method(self) -> None:
                path = urlsplit(self.path).path
                if path.startswith("/operator/") and not self._require_operator_auth():
                    return
                if path == "/operator/domain-transitions":
                    self._send_method_not_allowed("GET")
                    return
                prefix = "/operator/domain-transitions/"
                if path.startswith(prefix) and path.endswith("/cancel"):
                    self._send_method_not_allowed("POST")
                    return
                self.send_error(404)

            def _require_operator_auth(self) -> bool:
                if control._operator_authorized(self.headers.get("Authorization")):
                    return True
                self._send_error_json(
                    401,
                    "Unauthorized",
                    {"WWW-Authenticate": "Bearer"},
                )
                return False

        return Handler

    def _authorized(self, authorization: str | None) -> bool:
        if authorization is None:
            return False
        return secrets.compare_digest(authorization, f"Bearer {self._token}")

    def _operator_authorized(self, authorization: str | None) -> bool:
        if not authorization or not self._operator_tokens:
            return False
        return any(
            secrets.compare_digest(authorization, f"Bearer {token}")
            for token in self._operator_tokens
        )

    def set_operator_handlers(
        self,
        handoff_provider: Callable[[], list[dict[str, Any]]],
        cancel_provider: Callable[[int], dict[str, Any]],
    ) -> None:
        with self._lock:
            self._handoff_provider = handoff_provider
            self._cancel_provider = cancel_provider

    def _record_poll(self, body: bytes) -> None:
        payload = json.loads(body)
        routers = payload.get("http", {}).get("routers", {})
        router_names = frozenset(routers) if isinstance(routers, dict) else frozenset()
        with self._lock:
            self._last_successful_poll = (datetime.now(timezone.utc), router_names)
        logger.info("Traefik custom-domain provider poll served %d routers", len(router_names))

    def withdrawal_snapshot_acknowledged(self, router_name: str, since: str) -> bool:
        requested_at = datetime.fromisoformat(since)
        with self._lock:
            poll = self._last_successful_poll
        return bool(
            poll and poll[0] >= requested_at and router_name not in poll[1]
        )

    def is_ready(self) -> bool:
        return self._readiness()[0]

    def refresh_readiness(self) -> None:
        if not self._runtime_client:
            return
        checks = self._runtime_client.readiness()
        with self._lock:
            self._runtime_checks = checks

    def _snapshot(self) -> bytes:
        body = self._snapshot_provider()
        if len(body) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Traefik custom-domain snapshot is too large")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Traefik custom-domain snapshot must be an object")
        return body

    def _readiness_payload(self) -> bytes:
        ready, checks = self._readiness()
        return json.dumps(
            {
                "status": "ready" if ready else "not_ready",
                "checks": checks,
                "manual_checks": [
                    "buzz-custom resolver is configured with HTTP-01",
                    "ACME storage is durable and writable",
                    "ports 80 and 443 are publicly reachable",
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode() + b"\n"

    def _readiness(self) -> tuple[bool, dict[str, dict[str, Any]]]:
        with self._lock:
            last_polled_at = self._last_successful_poll[0] if self._last_successful_poll else None
        poll_fresh = bool(
            last_polled_at
            and datetime.now(timezone.utc) - last_polled_at <= PROVIDER_POLL_MAX_AGE
        )
        checks: dict[str, dict[str, Any]] = {
            "provider_poll": {
                "ok": poll_fresh,
                "last_polled_at": last_polled_at.isoformat() if last_polled_at else None,
            }
        }
        with self._lock:
            checks.update(self._runtime_checks)
        ready = all(check["ok"] for check in checks.values())
        return ready, checks
