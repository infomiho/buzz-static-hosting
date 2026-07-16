from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import ssl
from collections.abc import Callable

from .custom_domains import DomainClaim, DomainClaimStore
from .db import db

MAX_RESPONSE_BYTES = 16 * 1024
MAX_CANDIDATES_PER_PASS = 10
PROBE_TIMEOUT_SECONDS = 3
logger = logging.getLogger(__name__)


class ActivationFailed(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def resolve_addresses(hostname: str) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ActivationFailed("dns_unavailable") from exc
    return tuple(sorted({answer[4][0] for answer in answers}))


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


class DomainActivator:
    def __init__(
        self,
        allowed_addresses: frozenset[str],
        origin_host: str,
        resolver: Callable[[str], tuple[str, ...]] = resolve_addresses,
        probe: Callable[[str, DomainClaim], None] = probe_origin,
    ):
        self._allowed_addresses = allowed_addresses
        self._origin_host = origin_host
        self._resolver = resolver
        self._probe = probe

    def run_once(self) -> None:
        with db() as conn:
            claims = DomainClaimStore(conn).activation_candidates()
        for claim in claims[:MAX_CANDIDATES_PER_PASS]:
            try:
                self._validate_dns(claim.hostname)
                self._probe(self._origin_host, claim)
            except ActivationFailed as exc:
                self._record_error(claim, exc.code)
                continue
            except Exception:
                self._record_error(claim, "activation_check_failed")
                logger.exception(
                    "Custom domain %d generation %d validation failed unexpectedly",
                    claim.id,
                    claim.route_generation,
                )
                continue
            try:
                with db() as conn:
                    activated = DomainClaimStore(conn).mark_activated(
                        claim.id, claim.route_generation
                    )
                if activated:
                    logger.info("Custom domain %d generation %d activated", claim.id, claim.route_generation)
            except Exception:
                logger.exception(
                    "Custom domain %d generation %d activation failed unexpectedly",
                    claim.id,
                    claim.route_generation,
                )

    def _validate_dns(self, hostname: str) -> None:
        addresses = self._resolver(hostname)
        if not addresses:
            raise ActivationFailed("dns_no_addresses")
        for raw in addresses:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise ActivationFailed("dns_unexpected_address") from exc
            if not address.is_global or str(address) not in self._allowed_addresses:
                raise ActivationFailed("dns_unexpected_address")

    @staticmethod
    def _record_error(claim: DomainClaim, error: str) -> None:
        with db() as conn:
            changed = DomainClaimStore(conn).record_activation_error(
                claim.id, claim.route_generation, error
            )
        if changed:
            logger.warning(
                "Custom domain %d generation %d activation failed: %s",
                claim.id,
                claim.route_generation,
                error,
            )
