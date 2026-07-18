from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from concurrent.futures import Future, wait
from dataclasses import dataclass
from typing import Callable

import dns.exception
import dns.rdatatype
import dns.resolver

from .domain_probes import (
    ADDRESS_FAMILIES,
    ActivationFailed,
    CloudflareRangeState,
    CloudflareRanges,
    EdgeProbeResult,
    MAX_RESOLVED_ADDRESSES,
    PROBE_EXECUTOR,
    ProbeExecutor,
    probe_cloudflare_edge,
    probe_origin,
)
from .custom_domains import DnsTxtResolver, DomainCheckUnavailable, DomainClaim

MAX_CNAME_DEPTH = 8
PROBE_PHASE_SECONDS = 5


@dataclass(frozen=True)
class AddressAnswer:
    status: str
    values: tuple[str, ...] = ()
    ttl: int = 0
    target: str | None = None

    @classmethod
    def addresses(cls, values: tuple[str, ...], ttl: int) -> AddressAnswer:
        return cls("addresses", values=values, ttl=ttl)

    @classmethod
    def no_answer(cls) -> AddressAnswer:
        return cls("no_answer")

    @classmethod
    def cname(cls, target: str, ttl: int) -> AddressAnswer:
        return cls("cname", ttl=ttl, target=target.lower().rstrip("."))


@dataclass(frozen=True)
class DnsObservation:
    mode: str
    addresses: tuple[str, ...] = ()
    ttl: int = 0
    fingerprint: str | None = None
    error: str | None = None


def lookup_address_family(name: str, family: str) -> AddressAnswer:
    try:
        answer = dns.resolver.resolve(
            name, family, lifetime=5, raise_on_no_answer=False, search=False
        )
    except dns.resolver.NXDOMAIN:
        return AddressAnswer("nxdomain")
    except dns.resolver.NoAnswer:
        return AddressAnswer.no_answer()
    except (dns.resolver.LifetimeTimeout, dns.exception.Timeout):
        return AddressAnswer("timeout")
    except dns.exception.DNSException:
        return AddressAnswer("invalid")
    try:
        cname_depth = sum(
            1
            for rrset in answer.response.answer
            if str(rrset.rdtype) == "5" or rrset.rdtype == dns.rdatatype.CNAME
        )
        if cname_depth > MAX_CNAME_DEPTH:
            return AddressAnswer("invalid")
        canonical_name = str(answer.canonical_name).lower().rstrip(".")
        queried_name = name.lower().rstrip(".")
        if canonical_name != queried_name:
            ttl = max((rrset.ttl for rrset in answer.response.answer), default=0)
            return AddressAnswer.cname(canonical_name, ttl)
        if answer.rrset is None:
            return AddressAnswer.no_answer()
        return AddressAnswer.addresses(
            tuple(record.address for record in answer), answer.rrset.ttl
        )
    except (AttributeError, TypeError, ValueError):
        return AddressAnswer("invalid")


class DomainDnsObserver:
    def __init__(
        self,
        lookup: Callable[[str, str], AddressAnswer] = lookup_address_family,
        ingress_addresses: frozenset[str] = frozenset(),
        cloudflare_ranges: CloudflareRanges | None = None,
        cloudflare_range_state: CloudflareRangeState | None = None,
        executor: ProbeExecutor = PROBE_EXECUTOR,
    ):
        self._lookup = lookup
        self._ingress_addresses = ingress_addresses
        self._cloudflare_range_state = cloudflare_range_state or CloudflareRangeState(
            cloudflare_ranges
        )
        self._executor = executor

    def observe(self, hostname: str, deadline: float | None = None) -> DnsObservation:
        deadline = deadline or time.monotonic() + PROBE_PHASE_SECONDS
        values: set[str] = set()
        ttl = 0
        futures = tuple(
            self._executor.submit(self._resolve_family, hostname, family, deadline)
            for family in ADDRESS_FAMILIES
        )
        done, pending = wait(
            futures, timeout=max(0, deadline - time.monotonic())
        )
        if len(done) != len(futures):
            for future in pending:
                future.cancel()
            return DnsObservation("unavailable", error="dns_timeout")
        answers = tuple(future.result() for future in futures)
        for answer, chain_ttl in answers:
            ttl = max(ttl, chain_ttl)
            if answer.status in {"timeout", "invalid"}:
                return DnsObservation("unavailable", error=f"dns_{answer.status}")
            if answer.status == "nxdomain":
                return DnsObservation("unavailable", error="dns_nxdomain")
            if answer.status == "addresses":
                values.update(answer.values)
        if not values:
            return DnsObservation("unavailable", error="dns_no_addresses")
        if len(values) > MAX_RESOLVED_ADDRESSES:
            return DnsObservation("unavailable", error="dns_too_many_addresses")

        normalized = []
        kinds = set()
        for raw in values:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                return DnsObservation("unavailable", error="dns_invalid_address")
            if not address.is_global:
                return DnsObservation("unsupported", error="dns_non_public_address")
            value = str(address)
            normalized.append(value)
            if value in self._ingress_addresses:
                kinds.add("direct")
            elif self._cloudflare_range_state.contains(address):
                kinds.add("cloudflare")
            else:
                kinds.add("unsupported")

        addresses = tuple(
            sorted(normalized, key=lambda value: (ipaddress.ip_address(value).version, value))
        )
        fingerprint = hashlib.sha256("\n".join(addresses).encode("ascii")).hexdigest()
        if kinds == {"direct"}:
            mode = "direct"
        elif kinds == {"cloudflare"}:
            mode = "cloudflare"
        elif kinds == {"direct", "cloudflare"}:
            mode = "mixed"
        else:
            mode = "unsupported"
        return DnsObservation(mode, addresses, ttl, fingerprint)

    @property
    def cloudflare_range_state(self) -> CloudflareRangeState:
        return self._cloudflare_range_state

    def _resolve_family(
        self, hostname: str, family: str, deadline: float
    ) -> tuple[AddressAnswer, int]:
        name = hostname.lower().rstrip(".")
        seen = {name}
        ttl = 0
        for _ in range(MAX_CNAME_DEPTH + 1):
            if time.monotonic() >= deadline:
                return AddressAnswer("timeout"), ttl
            answer = self._lookup(name, family)
            ttl = max(ttl, answer.ttl)
            if answer.status != "cname":
                return answer, ttl
            if not answer.target or answer.target in seen:
                return AddressAnswer("invalid"), ttl
            seen.add(answer.target)
            name = answer.target
        return AddressAnswer("invalid"), ttl


@dataclass(frozen=True)
class EvidenceResult:
    status: str
    error: str | None = None
    transient: bool = False

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


@dataclass(frozen=True)
class ClaimEvidence:
    claim: DomainClaim
    ownership: EvidenceResult
    dns: DnsObservation
    router: EvidenceResult
    origin: EvidenceResult
    ranges: EvidenceResult = EvidenceResult("healthy")
    edge: tuple[EdgeProbeResult, ...] | None = None
    confirmed_dns: DnsObservation | None = None

    @property
    def common_error(self) -> EvidenceResult | None:
        failures = tuple(
            result
            for result in (self.ownership, self.router, self.origin)
            if not result.healthy
        )
        return next((result for result in failures if not result.transient), None) or next(
            iter(failures), None
        )

    def target_error(self, mode: str) -> EvidenceResult | None:
        common_error = self.common_error
        if common_error:
            return common_error
        if mode == "cloudflare" and not self.ranges.healthy:
            return self.ranges
        if self.dns.mode != mode or not self.dns.addresses:
            if self.dns.error:
                return EvidenceResult(
                    "failed",
                    self.dns.error,
                    transient=self.dns.mode == "unavailable"
                    and self.dns.error in {"dns_timeout", "dns_invalid"},
                )
            return EvidenceResult(
                "failed",
                "dns_unexpected_address"
                if mode == "direct"
                else "dns_non_cloudflare_address",
            )
        if self.confirmed_dns is None:
            return EvidenceResult("failed", "dns_confirmation_missing")
        if self.confirmed_dns.fingerprint != self.dns.fingerprint:
            return EvidenceResult("failed", "dns_answer_changed", transient=True)
        if mode == "cloudflare":
            if self.edge is None or len(self.edge) != len(self.dns.addresses):
                return EvidenceResult("failed", "edge_confirmation_missing")
            by_family: dict[int, list[EdgeProbeResult]] = {}
            for address, result in zip(self.dns.addresses, self.edge):
                by_family.setdefault(ipaddress.ip_address(address).version, []).append(result)
            healthy_families = {
                family
                for family, results in by_family.items()
                if all(
                    result.tls_status == "healthy" and result.http_status == "healthy"
                    for result in results
                )
            }
            skipped_families = {
                family
                for family, results in by_family.items()
                if healthy_families
                and all(
                    result.tls_error == "edge_address_family_unavailable"
                    and result.http_error is None
                    for result in results
                )
            }
            failures = [
                result
                for family, results in by_family.items()
                if family not in skipped_families
                for result in results
                if result.tls_status != "healthy" or result.http_status != "healthy"
            ]
            failure = next(
                (
                    result
                    for result in failures
                    if (result.tls_error or result.http_error)
                    != "edge_address_family_unavailable"
                ),
                None,
            )
            if failure:
                error = (
                    failure.tls_error
                    or failure.http_error
                    or "cloudflare_target_unhealthy"
                )
                return EvidenceResult(
                    "failed",
                    error,
                    transient=error in {"edge_unavailable", "cloudflare_525"},
                )
            if failures:
                return EvidenceResult("failed", "edge_unavailable", transient=True)
        return None


class DomainPathEvidenceStore:
    def __init__(self, conn):
        self._conn = conn

    def record(
        self,
        evidence: ClaimEvidence,
        mode_generation: int,
        probe_generation: int,
        path_mode: str | None,
        reservation=None,
    ) -> bool:
        common_error = evidence.common_error
        path_error = evidence.target_error(path_mode) if path_mode else None
        reservation_guard = ""
        parameters: list = [
            evidence.claim.id,
            evidence.claim.route_generation,
            mode_generation,
            probe_generation,
            path_mode,
            evidence.dns.mode,
            json.dumps(evidence.dns.addresses, separators=(",", ":")),
            evidence.dns.fingerprint,
            evidence.confirmed_dns.fingerprint if evidence.confirmed_dns else None,
            common_error.error if common_error else "healthy",
            path_error.error if path_error else "healthy" if path_mode else "not_checked",
            evidence.claim.id,
            evidence.claim.route_generation,
            mode_generation,
        ]
        if reservation:
            reservation_guard = """AND EXISTS (
                SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = claims.id AND mode_generation = claims.mode_generation
                  AND probe_generation = ? AND lease_owner = ?
                  AND lease_expires_at > datetime('now'))"""
            parameters.extend((reservation.probe_generation, reservation.owner))
        cursor = self._conn.execute(
            f"""INSERT INTO custom_domain_path_evidence
            (claim_id, route_generation, mode_generation, probe_generation, checked_at,
             path_mode, observed_mode, observed_addresses, answer_fingerprint,
             confirmation_fingerprint, common_result, path_result)
            SELECT ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
                   ?, ?, ?, ?, ?, ?, ?
            FROM custom_domain_claims AS claims
            WHERE claims.id = ? AND claims.route_generation = ?
              AND claims.mode_generation = ? AND claims.status = 'verified'
              AND claims.route_status = 'routed' AND claims.removal_requested_at IS NULL
              {reservation_guard}""",
            parameters,
        )
        return cursor.rowcount > 0


class DomainEvidenceCollector:
    def __init__(
        self,
        observer: DomainDnsObserver,
        origin_host: str,
        router_validator: Callable[[DomainClaim], None],
        ownership_resolver: Callable[[str], tuple[str, ...]] | None = None,
        origin_probe: Callable[[str, DomainClaim], None] = probe_origin,
        edge_probe: Callable[[str, DomainClaim], EdgeProbeResult] = probe_cloudflare_edge,
        cloudflare_range_state: CloudflareRangeState | None = None,
        executor: ProbeExecutor = PROBE_EXECUTOR,
    ):
        self._observer = observer
        self._origin_host = origin_host
        self._router_validator = router_validator
        self._ownership_resolver = ownership_resolver or DnsTxtResolver().lookup
        self._origin_probe = origin_probe
        self._edge_probe = edge_probe
        self._cloudflare_range_state = (
            cloudflare_range_state or observer.cloudflare_range_state
        )
        self._executor = executor

    @property
    def cloudflare_range_state(self) -> CloudflareRangeState:
        return self._cloudflare_range_state

    def collect(
        self, claim: DomainClaim, target_mode: str | tuple[str, ...] | None = None
    ) -> ClaimEvidence:
        deadline = time.monotonic() + PROBE_PHASE_SECONDS
        ownership = self._executor.submit(self._ownership, claim)
        router = self._executor.submit(self._router, claim)
        origin = self._executor.submit(self._origin, claim)
        dns = self._observer.observe(claim.hostname, deadline)
        evidence = ClaimEvidence(
            claim,
            self._result(ownership, deadline, "ownership_dns_unavailable"),
            dns,
            self._result(router, deadline, "runtime_api_unavailable"),
            self._result(origin, deadline, "origin_unavailable"),
            self._range_result(),
        )
        target_modes = (target_mode,) if isinstance(target_mode, str) else target_mode or ()
        if (
            not target_modes
            or evidence.common_error
            or evidence.dns.mode not in target_modes
            or not evidence.dns.addresses
            or (evidence.dns.mode == "cloudflare" and not evidence.ranges.healthy)
        ):
            return evidence
        edge_futures = (
            tuple(
                self._executor.submit(self._edge_probe, address, evidence.claim)
                for address in evidence.dns.addresses
            )
                if evidence.dns.mode == "cloudflare"
            else ()
        )
        confirmation = self._observer.observe(evidence.claim.hostname, deadline)
        edge = tuple(
            self._edge_result(future, deadline, address)
            for future, address in zip(edge_futures, evidence.dns.addresses)
        )
        return ClaimEvidence(
            evidence.claim,
            evidence.ownership,
            evidence.dns,
            evidence.router,
            evidence.origin,
            evidence.ranges,
            edge,
            confirmation,
        )

    @staticmethod
    def _result(
        future: Future[EvidenceResult], deadline: float, timeout_error: str
    ) -> EvidenceResult:
        remaining = max(0, deadline - time.monotonic())
        try:
            return future.result(timeout=remaining)
        except TimeoutError:
            future.cancel()
            return EvidenceResult("failed", timeout_error, transient=True)

    @staticmethod
    def _edge_result(
        future: Future[EdgeProbeResult], deadline: float, address: str
    ) -> EdgeProbeResult:
        remaining = max(0, deadline - time.monotonic())
        try:
            return future.result(timeout=remaining)
        except TimeoutError:
            future.cancel()
            return EdgeProbeResult(
                "failed", "edge_unavailable", "not_checked", None, address=address
            )

    def _range_result(self) -> EvidenceResult:
        error = self._cloudflare_range_state.error
        return EvidenceResult("failed", error) if error else EvidenceResult("healthy")

    def _ownership(self, claim: DomainClaim) -> EvidenceResult:
        if not claim.site_name or not claim.challenge_token:
            return EvidenceResult("failed", "site_identity_mismatch")
        try:
            values = self._ownership_resolver(claim.verification_name)
        except DomainCheckUnavailable:
            return EvidenceResult("failed", "ownership_dns_unavailable", transient=True)
        if claim.verification_value not in values:
            return EvidenceResult("failed", "ownership_txt_mismatch")
        return EvidenceResult("healthy")

    def _router(self, claim: DomainClaim) -> EvidenceResult:
        try:
            self._router_validator(claim)
        except Exception as exc:
            if not hasattr(exc, "code"):
                raise
            return EvidenceResult(
                "failed",
                getattr(exc, "code", "router_check_failed"),
                getattr(exc, "transient", False),
            )
        return EvidenceResult("healthy")

    def _origin(self, claim: DomainClaim) -> EvidenceResult:
        try:
            self._origin_probe(self._origin_host, claim)
        except ActivationFailed as exc:
            return EvidenceResult(
                "failed", exc.code, transient=exc.code == "origin_unavailable"
            )
        return EvidenceResult("healthy")
