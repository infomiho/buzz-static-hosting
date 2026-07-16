from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .custom_domains import DomainClaim, DomainClaimStore
from .db import db
from .domain_activation import (
    MAX_CANDIDATES_PER_PASS,
    MAX_RESPONSE_BYTES,
    PROBE_TIMEOUT_SECONDS,
    ActivationFailed,
    probe_origin,
    resolve_addresses,
)

MAX_RANGE_AGE = timedelta(days=180)
DIAGNOSTIC_INTERVAL = timedelta(seconds=60)
MAX_RESOLVED_ADDRESSES = 16
MAX_HEADER_VALUE = 512
RANGE_PATH = Path(__file__).parent / "resources" / "cloudflare-ip-ranges.json"
logger = logging.getLogger(__name__)


class CloudflareRangeError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CloudflareRanges:
    version: str
    published_at: datetime
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]

    def contains(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(address.version == network.version and address in network for network in self.networks)


@dataclass(frozen=True)
class EdgeProbeResult:
    tls_status: str
    tls_error: str | None
    http_status: str
    http_error: str | None
    status_code: int | None = None
    address: str | None = None
    cf_ray: str | None = None
    cf_cache_status: str | None = None
    redirect_location: str | None = None


@dataclass(frozen=True)
class HttpForwardProbeResult:
    status: str
    error: str | None
    status_code: int | None = None


@dataclass(frozen=True)
class CloudflareDiagnostic:
    claim_id: int
    route_generation: int
    checked_at: str
    ranges_version: str | None
    dns_status: str
    dns_error: str | None
    edge_tls_status: str
    edge_tls_error: str | None
    edge_http_status: str
    edge_http_error: str | None
    edge_http_status_code: int | None
    edge_address: str | None
    cf_ray: str | None
    cf_cache_status: str | None
    redirect_location: str | None
    http_forward_status: str
    http_forward_error: str | None
    http_forward_status_code: int | None
    origin_status: str
    origin_error: str | None


def load_cloudflare_ranges(
    path: Path = RANGE_PATH,
    now: datetime | None = None,
) -> CloudflareRanges:
    now = now or datetime.now(timezone.utc)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise CloudflareRangeError("range_data_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CloudflareRangeError("range_data_invalid") from exc
    try:
        if data["schema_version"] != 1 or not isinstance(data["version"], str):
            raise ValueError
        published_at = datetime.fromisoformat(data["published_at"])
        if published_at.tzinfo is None:
            raise ValueError
        raw_networks = data["ipv4"] + data["ipv6"]
        if not data["ipv4"] or not data["ipv6"] or not all(
            isinstance(value, str) for value in raw_networks
        ):
            raise ValueError
        networks = tuple(ipaddress.ip_network(value, strict=True) for value in raw_networks)
        if any(not network.is_global for network in networks):
            raise ValueError
        for index, network in enumerate(networks):
            if any(network.overlaps(other) for other in networks[index + 1 :]):
                raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise CloudflareRangeError("range_data_invalid") from exc
    published_at = published_at.astimezone(timezone.utc)
    if published_at > now + timedelta(days=1):
        raise CloudflareRangeError("range_data_invalid")
    if now - published_at > MAX_RANGE_AGE:
        raise CloudflareRangeError("range_data_stale")
    return CloudflareRanges(data["version"], published_at, networks)


def _bounded_header(value: str | None) -> str | None:
    return value[:MAX_HEADER_VALUE] if value else None


def probe_cloudflare_edge(address: str, claim: DomainClaim) -> EdgeProbeResult:
    if not claim.challenge_path or not claim.challenge_token or not claim.site_name:
        return EdgeProbeResult("not_checked", None, "failed", "edge_challenge_mismatch")
    try:
        with socket.create_connection(
            (address, 443), timeout=PROBE_TIMEOUT_SECONDS
        ) as connection:
            with ssl.create_default_context().wrap_socket(
                connection, server_hostname=claim.hostname
            ) as tls:
                request = (
                    f"GET {claim.challenge_path} HTTP/1.1\r\n"
                    f"Host: {claim.hostname}\r\n"
                    "Accept: text/plain\r\n"
                    "Cache-Control: no-cache\r\n"
                    "Connection: close\r\n\r\n"
                )
                tls.sendall(request.encode("ascii"))
                response = http.client.HTTPResponse(tls)
                response.begin()
                body = response.read(MAX_RESPONSE_BYTES + 1)
    except ssl.SSLError:
        return EdgeProbeResult("failed", "edge_tls_invalid", "not_checked", None, address=address)
    except (OSError, http.client.HTTPException):
        return EdgeProbeResult("failed", "edge_unavailable", "not_checked", None, address=address)

    status = response.status
    cf_ray = _bounded_header(response.getheader("CF-Ray"))
    cache_status = _bounded_header(response.getheader("CF-Cache-Status"))
    location = _bounded_header(response.getheader("Location"))
    mitigated = (response.getheader("cf-mitigated") or "").lower()
    body_text = body[:MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore").lower()
    expected = f"buzz-domain-check={claim.challenge_token};site={claim.site_name}".encode()
    common = {
        "status_code": status,
        "address": address,
        "cf_ray": cf_ray,
        "cf_cache_status": cache_status,
        "redirect_location": location,
    }
    if len(body) > MAX_RESPONSE_BYTES:
        error = "edge_response_too_large"
    elif status == 525 or "error code: 525" in body_text:
        error = "cloudflare_525"
    elif status == 526 or "error code: 526" in body_text:
        error = "cloudflare_526"
    elif "error code: 1014" in body_text or "error 1014" in body_text:
        error = "cloudflare_1014"
    elif 300 <= status < 400:
        error = "edge_redirect"
    elif mitigated == "challenge":
        error = "edge_challenge_present"
    elif status == 403 and (cf_ray or "cloudflare" in body_text):
        error = "edge_waf_denied"
    elif (cache_status or "").upper() == "HIT" or response.getheader("Age"):
        error = "edge_cached_challenge"
    elif (
        status == 200
        and body == expected
        and response.getheader("X-Buzz-Domain-Claim") == str(claim.id)
    ):
        return EdgeProbeResult("healthy", None, "healthy", None, **common)
    else:
        error = "edge_challenge_mismatch"
    return EdgeProbeResult("healthy", None, "failed", error, **common)


def probe_cloudflare_http_forwarding(
    address: str, claim: DomainClaim
) -> HttpForwardProbeResult:
    if not claim.challenge_path or not claim.challenge_token or not claim.site_name:
        return HttpForwardProbeResult("failed", "http_forward_challenge_mismatch")
    try:
        with socket.create_connection(
            (address, 80), timeout=PROBE_TIMEOUT_SECONDS
        ) as connection:
            request = (
                f"GET {claim.challenge_path} HTTP/1.1\r\n"
                f"Host: {claim.hostname}\r\n"
                "Accept: text/plain\r\n"
                "Cache-Control: no-cache\r\n"
                "Connection: close\r\n\r\n"
            )
            connection.sendall(request.encode("ascii"))
            response = http.client.HTTPResponse(connection)
            response.begin()
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException):
        return HttpForwardProbeResult("failed", "http_forward_unavailable")
    expected = f"buzz-domain-check={claim.challenge_token};site={claim.site_name}".encode()
    if len(body) > MAX_RESPONSE_BYTES:
        error = "http_forward_response_too_large"
    elif 300 <= response.status < 400:
        return HttpForwardProbeResult("observed", "http_forward_redirect", response.status)
    elif response.status == 403:
        error = "http_forward_blocked"
    elif (response.getheader("CF-Cache-Status") or "").upper() == "HIT" or response.getheader(
        "Age"
    ):
        error = "http_forward_cached_challenge"
    elif (
        response.status == 200
        and body == expected
        and response.getheader("X-Buzz-Domain-Claim") == str(claim.id)
    ):
        return HttpForwardProbeResult("healthy", None, response.status)
    else:
        error = "http_forward_challenge_mismatch"
    return HttpForwardProbeResult("failed", error, response.status)


class CloudflareDiagnosticStore:
    def __init__(self, conn):
        self._conn = conn

    def candidates(self, now: datetime | None = None) -> list[DomainClaim]:
        now = now or datetime.now(timezone.utc)
        checked_before = (now - DIAGNOSTIC_INTERVAL).isoformat()
        rows = self._conn.execute(
            """SELECT claims.* FROM custom_domain_claims AS claims
            LEFT JOIN custom_domain_cloudflare_diagnostics AS diagnostics
              ON diagnostics.claim_id = claims.id
             AND diagnostics.route_generation = claims.route_generation
            WHERE claims.claim_mode = 'cloudflare' AND claims.status = 'verified'
              AND claims.route_status = 'routed'
              AND (diagnostics.checked_at IS NULL OR diagnostics.checked_at <= ?)
            ORDER BY diagnostics.checked_at IS NOT NULL, diagnostics.checked_at, claims.id""",
            (checked_before,),
        ).fetchall()
        return [DomainClaimStore._from_row(row) for row in rows]

    def get(self, claim_id: int, generation: int) -> CloudflareDiagnostic | None:
        row = self._conn.execute(
            """SELECT * FROM custom_domain_cloudflare_diagnostics
            WHERE claim_id = ? AND route_generation = ?""",
            (claim_id, generation),
        ).fetchone()
        return CloudflareDiagnostic(**dict(row)) if row else None

    def record(self, diagnostic: CloudflareDiagnostic) -> bool:
        values = tuple(diagnostic.__dict__.values())
        cursor = self._conn.execute(
            """INSERT INTO custom_domain_cloudflare_diagnostics
            (claim_id, route_generation, checked_at, ranges_version,
             dns_status, dns_error, edge_tls_status, edge_tls_error,
             edge_http_status, edge_http_error, edge_http_status_code,
             edge_address, cf_ray, cf_cache_status, redirect_location,
             http_forward_status, http_forward_error, http_forward_status_code,
             origin_status, origin_error)
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM custom_domain_claims
                WHERE id = ? AND route_generation = ? AND claim_mode = 'cloudflare'
                  AND status = 'verified' AND route_status = 'routed')
            ON CONFLICT(claim_id, route_generation) DO UPDATE SET
              checked_at=excluded.checked_at, ranges_version=excluded.ranges_version,
              dns_status=excluded.dns_status, dns_error=excluded.dns_error,
              edge_tls_status=excluded.edge_tls_status,
              edge_tls_error=excluded.edge_tls_error,
              edge_http_status=excluded.edge_http_status,
              edge_http_error=excluded.edge_http_error,
              edge_http_status_code=excluded.edge_http_status_code,
              edge_address=excluded.edge_address, cf_ray=excluded.cf_ray,
              cf_cache_status=excluded.cf_cache_status,
              redirect_location=excluded.redirect_location,
              http_forward_status=excluded.http_forward_status,
              http_forward_error=excluded.http_forward_error,
              http_forward_status_code=excluded.http_forward_status_code,
              origin_status=excluded.origin_status, origin_error=excluded.origin_error
            WHERE excluded.checked_at > custom_domain_cloudflare_diagnostics.checked_at""",
            values + (diagnostic.claim_id, diagnostic.route_generation),
        )
        return cursor.rowcount > 0


class CloudflareDiagnostician:
    def __init__(
        self,
        origin_host: str,
        resolver: Callable[[str], tuple[str, ...]] = resolve_addresses,
        edge_probe: Callable[[str, DomainClaim], EdgeProbeResult] = probe_cloudflare_edge,
        http_probe: Callable[
            [str, DomainClaim], HttpForwardProbeResult
        ] = probe_cloudflare_http_forwarding,
        origin_probe: Callable[[str, DomainClaim], None] = probe_origin,
        ranges: CloudflareRanges | None = None,
        range_error: str | None = None,
    ):
        self._origin_host = origin_host
        self._resolver = resolver
        self._edge_probe = edge_probe
        self._http_probe = http_probe
        self._origin_probe = origin_probe
        self._ranges = ranges
        self._range_error = range_error
        if ranges is None and range_error is None:
            try:
                self._ranges = load_cloudflare_ranges()
            except CloudflareRangeError as exc:
                self._range_error = exc.code

    def run_once(self) -> None:
        now = datetime.now(timezone.utc)
        if self._ranges:
            if self._ranges.published_at > now + timedelta(days=1):
                self._range_error = "range_data_invalid"
            elif now - self._ranges.published_at > MAX_RANGE_AGE:
                self._range_error = "range_data_stale"
        with db() as conn:
            claims = CloudflareDiagnosticStore(conn).candidates(now)
        for claim in claims[:MAX_CANDIDATES_PER_PASS]:
            try:
                diagnostic = self._diagnose(claim)
                with db() as conn:
                    CloudflareDiagnosticStore(conn).record(diagnostic)
            except Exception:
                logger.exception(
                    "Cloudflare diagnostic failed for claim %d generation %d",
                    claim.id,
                    claim.route_generation,
                )

    def _diagnose(self, claim: DomainClaim) -> CloudflareDiagnostic:
        checked_at = datetime.now(timezone.utc).isoformat()
        dns_status, dns_error, addresses = self._validate_dns(claim.hostname)
        if addresses:
            edge = self._edge_probe(addresses[0], claim)
            http_forward = self._http_probe(addresses[0], claim)
        else:
            edge = EdgeProbeResult("not_checked", None, "not_checked", None)
            http_forward = HttpForwardProbeResult("not_checked", None)
        try:
            self._origin_probe(self._origin_host, claim)
            origin_status, origin_error = "healthy", None
        except ActivationFailed as exc:
            origin_status = "failed"
            origin_error = {
                "tls_invalid": "origin_tls_invalid",
                "origin_unavailable": "origin_unavailable",
                "challenge_mismatch": "origin_challenge_mismatch",
            }.get(exc.code, "origin_check_failed")
        return CloudflareDiagnostic(
            claim.id,
            claim.route_generation,
            checked_at,
            self._ranges.version if self._ranges else None,
            dns_status,
            dns_error,
            edge.tls_status,
            edge.tls_error,
            edge.http_status,
            edge.http_error,
            edge.status_code,
            edge.address,
            edge.cf_ray,
            edge.cf_cache_status,
            edge.redirect_location,
            http_forward.status,
            http_forward.error,
            http_forward.status_code,
            origin_status,
            origin_error,
        )

    def _validate_dns(self, hostname: str) -> tuple[str, str | None, tuple[str, ...]]:
        if self._range_error or not self._ranges:
            return "failed", self._range_error or "range_data_missing", ()
        try:
            raw_addresses = self._resolver(hostname)
        except ActivationFailed as exc:
            return "failed", exc.code, ()
        if not raw_addresses:
            return "failed", "dns_no_addresses", ()
        if len(raw_addresses) > MAX_RESOLVED_ADDRESSES:
            return "failed", "dns_too_many_addresses", ()
        addresses = []
        cloudflare_count = 0
        for raw in raw_addresses:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                return "failed", "dns_non_cloudflare_address", ()
            if not address.is_global:
                return "failed", "dns_non_cloudflare_address", ()
            if self._ranges.contains(address):
                cloudflare_count += 1
            addresses.append(str(address))
        if cloudflare_count != len(addresses):
            error = (
                "dns_mixed_cloudflare_addresses"
                if cloudflare_count
                else "dns_non_cloudflare_address"
            )
            return "failed", error, ()
        return "healthy", None, tuple(sorted(addresses, key=lambda value: (":" in value, value)))
