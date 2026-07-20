from __future__ import annotations

import http.client
import logging
import socket
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable

from .claims import (
    DomainClaim,
    DomainClaimStore,
)
from ..db import db
from .evidence import (
    ClaimEvidence,
    DomainEvidenceCollector,
    DomainPathEvidenceStore,
)
from .probes import (
    MAX_RESPONSE_BYTES,
    PROBE_TIMEOUT_SECONDS,
    CloudflareRangeState,
    EdgeProbeResult,
    probe_cloudflare_edge,
    probe_origin,
)
from .transitions import DomainClaimStateMachine

if TYPE_CHECKING:
    from .transitions import ProbeReservation

DIAGNOSTIC_INTERVAL = timedelta(seconds=60)
MAX_CANDIDATES_PER_PASS = 10
TRANSIENT_ACTIVATION_ERRORS = {
    "edge_unavailable",
    "origin_unavailable",
    "cloudflare_525",
}
logger = logging.getLogger(__name__)


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
    answer_fingerprint: str | None
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
    ownership_status: str
    ownership_error: str | None
    consecutive_failures: int = 0
    mode_generation: int = 0
    probe_generation: int = 0
    @property
    def activation_error(self) -> str | None:
        for status, error in (
            (self.ownership_status, self.ownership_error),
            (self.dns_status, self.dns_error),
            (self.edge_tls_status, self.edge_tls_error),
            (self.edge_http_status, self.edge_http_error),
            (self.origin_status, self.origin_error),
        ):
            if status != "healthy":
                return error or "cloudflare_check_failed"
        return None

    @property
    def allows_activation_grace(self) -> bool:
        return self.activation_error in TRANSIENT_ACTIVATION_ERRORS


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
             AND diagnostics.mode_generation = claims.mode_generation
              AND diagnostics.probe_generation = 0
            WHERE claims.claim_mode = 'cloudflare' AND claims.status = 'verified'
              AND claims.automatic_mode = 0
               AND claims.route_status = 'routed' AND claims.route_error IS NULL
              AND claims.activated_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = claims.id AND mode_generation = claims.mode_generation
                  AND state IN ('observing', 'validating', 'action_needed', 'deadline_evaluation'))
              AND (diagnostics.checked_at IS NULL OR diagnostics.checked_at <= ?)
            ORDER BY diagnostics.checked_at IS NOT NULL, diagnostics.checked_at, claims.id
            LIMIT ?""",
            (checked_before, MAX_CANDIDATES_PER_PASS),
        ).fetchall()
        return [DomainClaimStore.from_row(row) for row in rows]

    def get(
        self,
        claim_id: int,
        generation: int,
        mode_generation: int | None = None,
        probe_generation: int | None = None,
    ) -> CloudflareDiagnostic | None:
        generation_filter = ""
        parameters: list[int] = [claim_id, generation]
        if mode_generation is not None:
            generation_filter += " AND mode_generation = ?"
            parameters.append(mode_generation)
        if probe_generation is not None:
            generation_filter += " AND probe_generation = ?"
            parameters.append(probe_generation)
        row = self._conn.execute(
            f"""SELECT * FROM custom_domain_cloudflare_diagnostics
            WHERE claim_id = ? AND route_generation = ?
            {generation_filter}
            ORDER BY mode_generation DESC, probe_generation DESC LIMIT 1""",
            parameters,
        ).fetchone()
        if not row:
            return None
        values = dict(row)
        mode_generation = values.pop("mode_generation")
        probe_generation = values.pop("probe_generation")
        return CloudflareDiagnostic(
            **values, mode_generation=mode_generation, probe_generation=probe_generation
        )

    def record(
        self,
        diagnostic: CloudflareDiagnostic,
        reservation: ProbeReservation | None = None,
    ) -> bool:
        reservation_guard = ""
        mode_guard = "AND claims.claim_mode = 'cloudflare'"
        parameters: list = [
            diagnostic.claim_id,
            diagnostic.route_generation,
            diagnostic.mode_generation,
            diagnostic.probe_generation,
            diagnostic.checked_at,
            diagnostic.ranges_version,
            diagnostic.answer_fingerprint,
            diagnostic.dns_status,
            diagnostic.dns_error,
            diagnostic.edge_tls_status,
            diagnostic.edge_tls_error,
            diagnostic.edge_http_status,
            diagnostic.edge_http_error,
            diagnostic.edge_http_status_code,
            diagnostic.edge_address,
            diagnostic.cf_ray,
            diagnostic.cf_cache_status,
            diagnostic.redirect_location,
            diagnostic.http_forward_status,
            diagnostic.http_forward_error,
            diagnostic.http_forward_status_code,
            diagnostic.origin_status,
            diagnostic.origin_error,
            diagnostic.ownership_status,
            diagnostic.ownership_error,
            diagnostic.consecutive_failures,
        ]
        if reservation:
            mode_guard = ""
            reservation_guard = """AND EXISTS (
                SELECT 1 FROM custom_domain_mode_transitions AS transitions
                WHERE transitions.claim_id = claims.id
                  AND transitions.mode_generation = claims.mode_generation
                  AND transitions.probe_generation = ?
                  AND transitions.lease_owner = ?
                  AND transitions.lease_expires_at > datetime('now'))"""
        parameters.extend(
            (
                diagnostic.claim_id,
                diagnostic.route_generation,
                diagnostic.mode_generation,
            )
        )
        if reservation:
            parameters.extend((reservation.probe_generation, reservation.owner))
        conflict_action = (
            "DO NOTHING"
            if reservation
            else """DO UPDATE SET
              checked_at=excluded.checked_at, ranges_version=excluded.ranges_version,
              answer_fingerprint=excluded.answer_fingerprint,
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
              origin_status=excluded.origin_status, origin_error=excluded.origin_error,
              ownership_status=excluded.ownership_status,
              ownership_error=excluded.ownership_error,
              consecutive_failures=excluded.consecutive_failures
            WHERE excluded.checked_at > custom_domain_cloudflare_diagnostics.checked_at"""
        )
        cursor = self._conn.execute(
            f"""INSERT INTO custom_domain_cloudflare_diagnostics
            (claim_id, route_generation, mode_generation, probe_generation,
             checked_at, ranges_version, answer_fingerprint,
              dns_status, dns_error, edge_tls_status, edge_tls_error,
             edge_http_status, edge_http_error, edge_http_status_code,
             edge_address, cf_ray, cf_cache_status, redirect_location,
             http_forward_status, http_forward_error, http_forward_status_code,
              origin_status, origin_error, ownership_status, ownership_error,
              consecutive_failures)
             SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            FROM custom_domain_claims AS claims
            WHERE claims.id = ? AND claims.route_generation = ?
               AND claims.mode_generation = ?
               {mode_guard} AND claims.status = 'verified'
               AND claims.route_status = 'routed' AND claims.route_error IS NULL
              {reservation_guard}
            ON CONFLICT(claim_id, route_generation, mode_generation, probe_generation)
            {conflict_action}""",
            parameters,
        )
        return cursor.rowcount > 0


class CloudflareDiagnostician:
    def __init__(
        self,
        evidence_collector: DomainEvidenceCollector,
        http_probe: Callable[
            [str, DomainClaim], HttpForwardProbeResult
        ] = probe_cloudflare_http_forwarding,
        range_state: CloudflareRangeState | None = None,
        activation_enabled: bool = False,
    ):
        self._evidence_collector = evidence_collector
        self._http_probe = http_probe
        self._range_state = range_state or evidence_collector.cloudflare_range_state
        self._activation_enabled = activation_enabled

    def run_once(self) -> None:
        now = datetime.now(timezone.utc)

        with db() as conn:
            claims = CloudflareDiagnosticStore(conn).candidates(now)
        for claim in claims[:MAX_CANDIDATES_PER_PASS]:
            try:
                evidence = self._evidence_collector.collect(claim, "cloudflare")
                diagnostic = self._diagnose_evidence(
                    evidence, include_http_forwarding=True
                )
                diagnostic = replace(
                    diagnostic,
                    mode_generation=claim.mode_generation,
                    probe_generation=0,
                )
                with db() as conn:
                    DomainPathEvidenceStore(conn).record(
                        evidence, claim.mode_generation, 0, "cloudflare"
                    )
                    diagnostic_store = CloudflareDiagnosticStore(conn)
                    failures = (
                        claim.health_failure_count + 1
                        if diagnostic.allows_activation_grace
                        else 0
                    )
                    diagnostic = replace(diagnostic, consecutive_failures=failures)
                    recorded = diagnostic_store.record(diagnostic)
                    if recorded and self._activation_enabled:
                        self._apply_activation(conn, claim, evidence, diagnostic)
            except Exception:
                logger.exception(
                    "Cloudflare diagnostic failed for claim %d generation %d",
                    claim.id,
                    claim.route_generation,
                )

    @property
    def range_error(self) -> str | None:
        return self._range_state.error

    def _diagnose_evidence(
        self,
        evidence: ClaimEvidence,
        confirmed: ClaimEvidence | None = None,
        include_http_forwarding: bool = False,
    ) -> CloudflareDiagnostic:
        claim = evidence.claim
        confirmed = confirmed or evidence
        target_error = confirmed.target_error("cloudflare")
        if not evidence.ranges.healthy:
            dns_status, dns_error = "failed", evidence.ranges.error
        elif evidence.dns.mode == "cloudflare":
            dns_status, dns_error = "healthy", None
        elif evidence.dns.mode == "mixed":
            dns_status, dns_error = "failed", "dns_mixed_cloudflare_addresses"
        elif evidence.dns.mode == "direct":
            dns_status, dns_error = "failed", "dns_non_cloudflare_address"
        else:
            dns_status, dns_error = "failed", evidence.dns.error or "dns_non_cloudflare_address"
            if dns_error == "dns_timeout":
                dns_error = "dns_unavailable"
        edge = next(
            (
                result
                for result in (confirmed.edge or ())
                if result.tls_status != "healthy" or result.http_status != "healthy"
            ),
            (confirmed.edge or (EdgeProbeResult("not_checked", None, "not_checked", None),))[0],
        )
        if evidence.dns.mode == "cloudflare" and confirmed.edge and not target_error:
            edge = next(
                result
                for result in confirmed.edge
                if result.tls_status == "healthy" and result.http_status == "healthy"
            )
        http_forward = (
            self._http_probe(evidence.dns.addresses[0], claim)
            if include_http_forwarding
            and evidence.dns.mode == "cloudflare"
            and evidence.dns.addresses
            else HttpForwardProbeResult("not_checked", None)
        )
        origin_error = evidence.origin.error
        if origin_error == "tls_invalid":
            origin_error = "origin_tls_invalid"
        elif origin_error == "challenge_mismatch":
            origin_error = "origin_challenge_mismatch"
        return CloudflareDiagnostic(
            claim.id,
            claim.route_generation,
            datetime.now(timezone.utc).isoformat(),
            self._range_state.version,
            evidence.dns.fingerprint,
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
            evidence.origin.status,
            origin_error,
            evidence.ownership.status,
            evidence.ownership.error,
        )

    def record_transition(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        evidence: ClaimEvidence,
        confirmed: ClaimEvidence,
    ) -> bool:
        diagnostic = replace(
            self._diagnose_evidence(evidence, confirmed),
            mode_generation=reservation.mode_generation,
            probe_generation=reservation.probe_generation,
        )
        with db() as conn:
            return CloudflareDiagnosticStore(conn).record(diagnostic, reservation)

    def record_health(
        self, claim: DomainClaim, evidence: ClaimEvidence, confirmed: ClaimEvidence
    ) -> bool:
        diagnostic = replace(
            self._diagnose_evidence(evidence, confirmed),
            mode_generation=claim.mode_generation,
            probe_generation=0,
        )
        with db() as conn:
            return CloudflareDiagnosticStore(conn).record(diagnostic)

    def _apply_activation(
        self,
        conn,
        claim: DomainClaim,
        evidence: ClaimEvidence,
        diagnostic: CloudflareDiagnostic,
    ) -> None:
        target_error = evidence.target_error("cloudflare")
        error = target_error.error if target_error else None
        DomainClaimStateMachine(conn).apply_activation_decision(
            claim,
            error,
            transient=target_error.transient if target_error else False,
        )
