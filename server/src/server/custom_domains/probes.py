from __future__ import annotations

import errno
import http.client
import ipaddress
import json
import socket
import ssl
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar

from .claims import DomainClaim

MAX_RESPONSE_BYTES = 16 * 1024
PROBE_TIMEOUT_SECONDS = 3
MAX_RANGE_AGE = timedelta(days=180)
MAX_HEADER_VALUE = 512
MAX_CONCURRENT_CLAIM_CHECKS = 20
MAX_RESOLVED_ADDRESSES = 16
ADDRESS_FAMILIES = ("A", "AAAA")
MAX_PROBE_WORKERS = MAX_CONCURRENT_CLAIM_CHECKS * (
    MAX_RESOLVED_ADDRESSES + len(ADDRESS_FAMILIES)
)
T = TypeVar("T")
RANGE_PATH = Path(__file__).parent / "resources" / "cloudflare-ip-ranges.json"


class ProbeExecutor:
    def __init__(self, max_workers: int = MAX_PROBE_WORKERS):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, function: Callable[..., T], *args) -> Future[T]:
        return self._executor.submit(function, *args)


PROBE_EXECUTOR = ProbeExecutor()


class ActivationFailed(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
        return any(
            address.version == network.version and address in network
            for network in self.networks
        )


@dataclass(frozen=True)
class CloudflareRangeState:
    ranges: CloudflareRanges | None = None
    load_error: str | None = None

    @property
    def error(self) -> str | None:
        if self.load_error:
            return self.load_error
        if not self.ranges:
            return "range_data_missing"
        now = datetime.now(timezone.utc)
        if self.ranges.published_at > now + timedelta(days=1):
            return "range_data_invalid"
        if now - self.ranges.published_at > MAX_RANGE_AGE:
            return "range_data_stale"
        return None

    @property
    def version(self) -> str | None:
        return self.ranges.version if self.ranges else None

    def contains(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return bool(not self.error and self.ranges and self.ranges.contains(address))


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


def probe_origin(origin_host: str, claim: DomainClaim) -> None:
    if not claim.site_name or not claim.challenge_path or not claim.challenge_token:
        raise ActivationFailed("challenge_mismatch")
    try:
        with socket.create_connection(
            (origin_host, 443), timeout=PROBE_TIMEOUT_SECONDS
        ) as connection:
            with ssl.create_default_context().wrap_socket(
                connection, server_hostname=claim.hostname
            ) as tls:
                request = (
                    f"GET {claim.challenge_path} HTTP/1.1\r\n"
                    f"Host: {claim.hostname}\r\n"
                    "Connection: close\r\n\r\n"
                )
                tls.sendall(request.encode("ascii"))
                response = http.client.HTTPResponse(tls)
                response.begin()
                body = response.read(MAX_RESPONSE_BYTES + 1)
    except ssl.SSLError as exc:
        raise ActivationFailed("tls_invalid") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ActivationFailed("origin_unavailable") from exc
    expected = f"buzz-domain-check={claim.challenge_token};site={claim.site_name}".encode()
    if (
        response.status != 200
        or len(body) > MAX_RESPONSE_BYTES
        or body != expected
        or response.getheader("X-Buzz-Domain-Claim") != str(claim.id)
    ):
        raise ActivationFailed("challenge_mismatch")


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
        return EdgeProbeResult(
            "failed", "edge_tls_invalid", "not_checked", None, address=address
        )
    except OSError as exc:
        error = (
            "edge_address_family_unavailable"
            if ipaddress.ip_address(address).version == 6
            and exc.errno in {errno.ENETUNREACH, errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL}
            else "edge_unavailable"
        )
        return EdgeProbeResult("failed", error, "not_checked", None, address=address)
    except http.client.HTTPException:
        return EdgeProbeResult(
            "failed", "edge_unavailable", "not_checked", None, address=address
        )

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
