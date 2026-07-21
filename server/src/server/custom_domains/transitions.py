from __future__ import annotations

import sqlite3
import uuid
import json
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Callable

from .claims import (
    HEALTH_FRESHNESS_SECONDS,
    DomainClaim,
    DomainClaimStore,
)
from .evidence import (
    ClaimEvidence,
    DnsObservation,
    DomainPathEvidenceStore,
    EvidenceResult,
)
from .machine_edges import (
    ACTIVE_STATES as _ACTIVE_STATES,
    ACTIVE_STATE_ORDER,
    ACTIVE_STATES_SQL,
    LEASE_AVAILABLE_SQL,
    LEASE_HELD_SQL,
    PRE_DEADLINE_STATES_SQL,
    RESERVED_KEY_SQL,
    TRANSITION_GENERATION_KEY_SQL,
    TransitionState,
    claim_routed_exists,
    claim_scope,
    lease_held,
    reserved_transition_exists,
    state_in,
)
from .observation import (
    STABLE_OBSERVATIONS_REQUIRED,
    TrackedObservation,
    advance,
    parse_timestamp,
)
from .probes import MAX_CONCURRENT_CLAIM_CHECKS
from .errors import ClaimConflict

if TYPE_CHECKING:
    from .cloudflare import CloudflareDiagnostic

logger = logging.getLogger(__name__)

MAX_COORDINATOR_CONCURRENCY = MAX_CONCURRENT_CLAIM_CHECKS
MAX_COORDINATOR_CANDIDATES = 40
MAX_COORDINATOR_SCAN = 1000
COORDINATOR_PASS_SECONDS = 10


@dataclass(frozen=True)
class DomainModeTransition:
    claim_id: int
    mode_generation: int
    probe_generation: int
    source_mode: str | None
    target_mode: str
    state: str
    started_at: str
    deadline_at: str | None
    checked_at: str | None
    completed_at: str | None
    answer_fingerprint: str | None
    confirmed_fingerprint: str | None
    confirmed_at: str | None
    stable_observation_count: int
    first_target_observed_at: str | None
    last_target_observed_at: str | None
    observed_mode: str | None
    observed_ttl: int | None
    max_target_ttl: int
    error: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    automatic_retarget: bool


@dataclass(frozen=True)
class ProbeReservation:
    claim_id: int
    route_generation: int
    mode_generation: int
    probe_generation: int
    owner: str
    source_mode: str | None
    target_mode: str
    deadline_at: str | None
    lease_expires_at: str


@dataclass(frozen=True)
class HandoffAssessment:
    """Everything a single advance() needs, gathered off the wire by the
    coordinator so the apply transaction opens no network connection. The
    evidence is a frozen value; its ``target_error`` reads are pure."""

    evidence: ClaimEvidence
    source_health: EvidenceResult | None
    cloudflare_diagnostic: "CloudflareDiagnostic | None"
    cloudflare_target_enabled: bool

    @property
    def observation(self) -> DnsObservation:
        return self.evidence.dns

    @property
    def confirmed_dns(self) -> DnsObservation | None:
        return self.evidence.confirmed_dns

    @property
    def common_error(self) -> EvidenceResult | None:
        return self.evidence.common_error

    def target_error(self, mode: str) -> EvidenceResult | None:
        return self.evidence.target_error(mode)


class Outcome(StrEnum):
    lost_lease = "lost_lease"
    observing = "observing"
    validating = "validating"
    action_needed = "action_needed"
    retargeted = "retargeted"
    source_failed_target_preserved = "source_failed_target_preserved"
    source_unhealthy_retained = "source_unhealthy_retained"
    common_failed = "common_failed"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"
    deadline_completed = "deadline_completed"
    deadline_cancelled = "deadline_cancelled"
    deadline_failed = "deadline_failed"


class DomainClaimStateMachine:
    ACTIVE_STATES = _ACTIVE_STATES

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, claim_id: int) -> DomainModeTransition | None:
        row = self._conn.execute(
            "SELECT * FROM custom_domain_mode_transitions WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        if not row:
            return None
        values = dict(row)
        values.setdefault("max_target_ttl", values.get("observed_ttl") or 0)
        values.setdefault("confirmed_fingerprint", None)
        values.setdefault("confirmed_at", None)
        values["automatic_retarget"] = bool(values["automatic_retarget"])
        return DomainModeTransition(**values)

    def managed_candidates(self) -> list[DomainClaim]:
        rows = self._conn.execute(
            """SELECT claims.* FROM custom_domain_claims AS claims
            LEFT JOIN custom_domain_mode_transitions AS transitions
              ON transitions.claim_id = claims.id
             AND transitions.mode_generation = claims.mode_generation
            WHERE claims.status = 'verified' AND claims.route_status = 'routed'
              AND claims.removal_requested_at IS NULL
              AND (claims.automatic_mode = 1 OR claims.activated_at IS NOT NULL
                   OR claims.health_checked_at IS NOT NULL
                   OR transitions.state IN
                     ('observing', 'validating', 'action_needed', 'deadline_evaluation'))
            ORDER BY CASE WHEN transitions.deadline_at IS NOT NULL
                                AND julianday(transitions.deadline_at) <= julianday('now')
                           THEN 0 ELSE 1 END,
                     COALESCE(transitions.checked_at, claims.health_checked_at,
                              claims.activation_checked_at) IS NOT NULL,
                     COALESCE(transitions.checked_at, claims.health_checked_at,
                              claims.activation_checked_at), claims.id
            LIMIT ?""",
            (MAX_COORDINATOR_SCAN,),
        ).fetchall()
        return [DomainClaimStore.from_row(row) for row in rows]

    def active_handoffs(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT transitions.claim_id, transitions.source_mode,
                      transitions.target_mode, transitions.state,
                      transitions.started_at, transitions.deadline_at,
                      transitions.checked_at, transitions.error,
                      claims.hostname,
                      CASE WHEN claims.activated_at IS NOT NULL
                             AND claims.health_checked_at IS NOT NULL
                              AND julianday(claims.health_checked_at) >= julianday('now', ?)
                           THEN claims.claim_mode END AS effective_mode,
                      transitions.observed_mode, transitions.observed_ttl,
                       transitions.answer_fingerprint,
                       transitions.confirmed_fingerprint,
                       transitions.confirmed_at,
                      transitions.stable_observation_count,
                      transitions.first_target_observed_at,
                      transitions.last_target_observed_at,
                      transitions.max_target_ttl,
                      claims.health_checked_at,
                      claims.common_failure_count,
                      claims.activation_checked_at,
                      claims.activation_error,
                       claims.health_failure_count,
                       claims.activated_at,
                       diagnostics.checked_at AS cloudflare_checked_at,
                       diagnostics.ranges_version AS cloudflare_ranges_version,
                       diagnostics.dns_status AS cloudflare_dns_status,
                       diagnostics.dns_error AS cloudflare_dns_error,
                       diagnostics.edge_tls_status AS cloudflare_edge_tls_status,
                       diagnostics.edge_tls_error AS cloudflare_edge_tls_error,
                       diagnostics.edge_http_status AS cloudflare_edge_http_status,
                       diagnostics.edge_http_error AS cloudflare_edge_http_error,
                       diagnostics.origin_status AS cloudflare_origin_status,
                       diagnostics.origin_error AS cloudflare_origin_error,
                       diagnostics.ownership_status AS cloudflare_ownership_status,
                        diagnostics.ownership_error AS cloudflare_ownership_error,
                        evidence.checked_at AS path_checked_at,
                        evidence.path_mode, evidence.observed_addresses,
                        evidence.answer_fingerprint AS path_answer_fingerprint,
                        evidence.confirmation_fingerprint,
                        evidence.common_result, evidence.path_result
               FROM custom_domain_mode_transitions AS transitions
               JOIN custom_domain_claims AS claims ON claims.id = transitions.claim_id
                LEFT JOIN custom_domain_cloudflare_diagnostics AS diagnostics
                 ON diagnostics.claim_id = claims.id
                AND diagnostics.route_generation = claims.route_generation
                AND diagnostics.mode_generation = transitions.mode_generation
                 AND diagnostics.probe_generation = transitions.probe_generation
                LEFT JOIN custom_domain_path_evidence AS evidence
                  ON evidence.id = (SELECT current.id
                    FROM custom_domain_path_evidence AS current
                    WHERE current.claim_id = claims.id
                      AND current.route_generation = claims.route_generation
                      AND current.mode_generation = claims.mode_generation
                      AND current.probe_generation = transitions.probe_generation
                    ORDER BY current.id DESC LIMIT 1)
               WHERE transitions.mode_generation = claims.mode_generation
                 AND transitions.state IN
                   ('observing', 'validating', 'action_needed', 'deadline_evaluation')
               ORDER BY transitions.deadline_at IS NULL, transitions.deadline_at,
                        transitions.claim_id"""
            , (f"-{HEALTH_FRESHNESS_SECONDS} seconds",)
        ).fetchall()
        handoffs = [dict(row) for row in rows]
        for handoff in handoffs:
            if handoff["observed_addresses"] is not None:
                handoff["observed_addresses"] = json.loads(
                    handoff["observed_addresses"]
                )
        return handoffs

    def start(
        self,
        claim_id: int,
        route_generation: int,
        target_mode: str,
        now: datetime | None = None,
        automatic_retarget: bool = False,
    ) -> DomainModeTransition:
        if target_mode not in {"direct", "cloudflare"}:
            raise ClaimConflict("Unsupported transition target")
        now = now or datetime.now(timezone.utc)
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
        claim = self._conn.execute(
            """SELECT * FROM custom_domain_claims
            WHERE id = ? AND route_generation = ? AND status = 'verified'
              AND route_status = 'routed' AND site_name IS NOT NULL
              AND removal_requested_at IS NULL""",
            (claim_id, route_generation),
        ).fetchone()
        if not claim:
            raise ClaimConflict("This custom domain cannot transition")
        current = self.get(claim_id)
        if current and current.state in self.ACTIVE_STATES:
            raise ClaimConflict("This custom domain already has an active transition")
        source_mode = claim["claim_mode"] if claim["activated_at"] else None
        if source_mode == target_mode:
            raise ClaimConflict("The target mode is already effective")
        mode_generation = claim["mode_generation"] + 1
        deadline = now + timedelta(hours=24) if source_mode else None
        self._conn.execute(
            "UPDATE custom_domain_claims SET mode_generation = ? WHERE id = ?",
            (mode_generation, claim_id),
        )
        self._conn.execute(
            """INSERT INTO custom_domain_mode_transitions
            (claim_id, mode_generation, probe_generation, source_mode, target_mode,
             state, started_at, deadline_at, automatic_retarget)
            VALUES (?, ?, 0, ?, ?, 'observing', ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
              mode_generation=excluded.mode_generation, probe_generation=0,
              source_mode=excluded.source_mode, target_mode=excluded.target_mode,
              automatic_retarget=excluded.automatic_retarget,
              state='observing', started_at=excluded.started_at,
               deadline_at=excluded.deadline_at, checked_at=NULL, completed_at=NULL,
                answer_fingerprint=NULL, confirmed_fingerprint=NULL, confirmed_at=NULL,
                stable_observation_count=0,
               first_target_observed_at=NULL, last_target_observed_at=NULL,
               observed_mode=NULL, observed_ttl=NULL, max_target_ttl=0, error=NULL,
              lease_owner=NULL, lease_expires_at=NULL""",
            (
                claim_id,
                mode_generation,
                source_mode,
                target_mode,
                now.isoformat(),
                deadline.isoformat() if deadline else None,
                automatic_retarget,
            ),
        )
        return self.get(claim_id)  # type: ignore[return-value]

    def reserve(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        owner: str,
        lease_seconds: int = 15,
    ) -> ProbeReservation | None:
        if not owner or not 1 <= lease_seconds <= 15:
            raise ValueError("Probe leases require an owner and last at most 15 seconds")
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET probe_generation = probe_generation + 1, lease_owner = ?,
                lease_expires_at = datetime('now', ?)
            WHERE {TRANSITION_GENERATION_KEY_SQL}
              AND {ACTIVE_STATES_SQL}
              AND {LEASE_AVAILABLE_SQL}
              AND {claim_routed_exists()}""",
            (
                owner,
                f"+{lease_seconds} seconds",
                claim_id,
                mode_generation,
                claim_id,
                route_generation,
                mode_generation,
            ),
        )
        if not cursor.rowcount:
            return None
        transition = self.get(claim_id)
        return ProbeReservation(
            claim_id,
            route_generation,
            transition.mode_generation,
            transition.probe_generation,
            owner,
            transition.source_mode,
            transition.target_mode,
            transition.deadline_at,
            transition.lease_expires_at,
        )

    def _renew(self, reservation: ProbeReservation) -> bool:
        return self._renew_probe(
            reservation.claim_id,
            reservation.mode_generation,
            reservation.probe_generation,
            reservation.owner,
        )

    def release(self, reservation: ProbeReservation) -> bool:
        return self._release_probe(
            reservation.claim_id,
            reservation.mode_generation,
            reservation.probe_generation,
            reservation.owner,
        )

    def _record_observation(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        probe_generation: int,
        owner: str,
        observed_mode: str,
        fingerprint: str | None,
        ttl: int,
        error: str | None = None,
    ) -> bool:
        transition = self.get(claim_id)
        if not transition:
            return False
        database_now = self._conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')"
        ).fetchone()[0]
        observation = DnsObservation(
            observed_mode, ttl=ttl, fingerprint=fingerprint, error=error
        )
        tracked = TrackedObservation(
            target_mode=transition.target_mode,
            automatic_retarget=transition.automatic_retarget,
            observed_mode=transition.observed_mode,
            answer_fingerprint=transition.answer_fingerprint,
            stable_observation_count=transition.stable_observation_count,
            max_target_ttl=transition.max_target_ttl,
            first_target_observed_at=transition.first_target_observed_at,
            last_target_observed_at=transition.last_target_observed_at,
        )
        decision = advance(tracked, observation, parse_timestamp(database_now))
        # Flat SET is safe only because the WHERE guard proves the row is
        # unchanged since get(); a mismatched row updates zero rows and the
        # decision is discarded, never retried.
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET checked_at = ?, observed_mode = ?, observed_ttl = ?,
                answer_fingerprint = ?, max_target_ttl = ?, error = ?, state = ?,
                stable_observation_count = ?,
                first_target_observed_at = ?, last_target_observed_at = ?
            WHERE {RESERVED_KEY_SQL} AND {LEASE_HELD_SQL}
              AND {claim_routed_exists()}""",
            (
                database_now,
                observed_mode,
                ttl,
                fingerprint if decision.record_answer else transition.answer_fingerprint,
                decision.max_target_ttl,
                error,
                decision.state,
                decision.stable_observation_count,
                database_now
                if decision.start_target_run
                else transition.first_target_observed_at,
                database_now
                if decision.accept_target_sample
                else transition.last_target_observed_at,
                claim_id,
                mode_generation,
                probe_generation,
                owner,
                claim_id,
                route_generation,
                mode_generation,
            ),
        )
        return cursor.rowcount > 0

    def _record_reserved_observation(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        observation: DnsObservation,
    ) -> bool:
        return self._record_observation(
            claim.id,
            claim.route_generation,
            reservation.mode_generation,
            reservation.probe_generation,
            reservation.owner,
            observation.mode,
            observation.fingerprint,
            observation.ttl,
            observation.error,
        )

    def _set_action_needed(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        probe_generation: int,
        error: str,
        owner: str,
    ) -> bool:
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET state = '{TransitionState.ACTION_NEEDED}', error = ?,
                checked_at = CURRENT_TIMESTAMP,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE {RESERVED_KEY_SQL} AND {LEASE_HELD_SQL}
              AND {claim_routed_exists()}""",
            (
                error,
                claim_id,
                mode_generation,
                probe_generation,
                owner,
                claim_id,
                route_generation,
                mode_generation,
            ),
        )
        return cursor.rowcount > 0

    def _set_reserved_action_needed(
        self, claim: DomainClaim, reservation: ProbeReservation, error: str
    ) -> bool:
        return self._set_action_needed(
            claim.id,
            claim.route_generation,
            reservation.mode_generation,
            reservation.probe_generation,
            error,
            reservation.owner,
        )

    def _record_reserved_confirmation(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        observation: DnsObservation,
    ) -> bool:
        if not observation.fingerprint:
            return False
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET confirmed_fingerprint = ?, confirmed_at = CURRENT_TIMESTAMP
            WHERE {RESERVED_KEY_SQL}
              AND target_mode = ? AND observed_mode = target_mode
              AND answer_fingerprint = ?
              AND stable_observation_count >= {STABLE_OBSERVATIONS_REQUIRED}
              AND {LEASE_HELD_SQL}
              AND {claim_routed_exists()}""",
            (
                observation.fingerprint,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                reservation.target_mode,
                observation.fingerprint,
                reservation.owner,
                claim.id,
                claim.route_generation,
                reservation.mode_generation,
            ),
        )
        return cursor.rowcount > 0

    def _release_probe(
        self, claim_id: int, mode_generation: int, probe_generation: int, owner: str
    ) -> bool:
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET lease_owner = NULL, lease_expires_at = NULL
            WHERE {RESERVED_KEY_SQL} AND lease_owner = ?""",
            (claim_id, mode_generation, probe_generation, owner),
        )
        return cursor.rowcount > 0

    def _renew_probe(
        self,
        claim_id: int,
        mode_generation: int,
        probe_generation: int,
        owner: str,
        lease_seconds: int = 15,
    ) -> bool:
        if not owner or not 1 <= lease_seconds <= 15:
            raise ValueError("Probe leases require an owner and last at most 15 seconds")
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET lease_expires_at = datetime('now', ?)
            WHERE {RESERVED_KEY_SQL} AND {LEASE_HELD_SQL}
              AND {ACTIVE_STATES_SQL}
              AND {claim_routed_exists(route_generation=False)}""",
            (
                f"+{lease_seconds} seconds",
                claim_id,
                mode_generation,
                probe_generation,
                owner,
                claim_id,
                mode_generation,
            ),
        )
        return cursor.rowcount > 0

    def _deadline_due(self, claim_id: int, mode_generation: int) -> bool:
        return bool(
            self._conn.execute(
                """SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND deadline_at IS NOT NULL
                  AND julianday(deadline_at) <= julianday('now')""",
                (claim_id, mode_generation),
            ).fetchone()
        )

    def _cancel_transition(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        probe_generation: int,
        source_mode: str | None,
        owner: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        transition = self.get(claim_id)
        if not transition or transition.mode_generation != mode_generation:
            return False
        new_generation = mode_generation + 1
        activation = "activated" if transition.source_mode else "not_activated"
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims
            SET mode_generation = ?,
                automatic_mode = CASE WHEN ? THEN 0 ELSE automatic_mode END,
                activation_error = NULL, health_failure_count = 0,
                common_failure_count = 0,
                health_checked_at = CASE WHEN ? THEN NULL ELSE ? END,
                activation_checked_at = CASE WHEN ? THEN activation_checked_at ELSE ? END
            WHERE {claim_scope(activation=activation)}
              AND {reserved_transition_exists(extra="source_mode IS ?")}""",
            (
                new_generation,
                transition.source_mode is None,
                transition.source_mode is None,
                now.isoformat(),
                transition.source_mode is None,
                now.isoformat(),
                claim_id,
                route_generation,
                mode_generation,
                claim_id,
                mode_generation,
                probe_generation,
                source_mode,
                owner,
            ),
        )
        if not cursor.rowcount:
            return False
        self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET mode_generation = ?, probe_generation = probe_generation + 1,
                state = '{TransitionState.CANCELLED}', completed_at = ?,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE {RESERVED_KEY_SQL} AND lease_owner = ?""",
            (
                new_generation,
                now.isoformat(),
                claim_id,
                mode_generation,
                probe_generation,
                owner,
            ),
        )
        return True

    def cancel(
        self, claim: DomainClaim, reservation: ProbeReservation
    ) -> bool:
        return self._cancel_transition(
            claim.id,
            claim.route_generation,
            reservation.mode_generation,
            reservation.probe_generation,
            reservation.source_mode,
            reservation.owner,
        )

    def _resolve_deadline(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        probe_generation: int,
        target_healthy: bool,
        effective_healthy: bool,
        now: datetime | None = None,
        owner: str = "",
    ) -> str | None:
        now = now or datetime.now(timezone.utc)
        transition = self.get(claim_id)
        if (
            not owner
            or not transition
            or transition.mode_generation != mode_generation
            or transition.probe_generation != probe_generation
            or not self._deadline_due(claim_id, mode_generation)
        ):
            return None
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET state = '{TransitionState.DEADLINE_EVALUATION}'
            WHERE {RESERVED_KEY_SQL} AND {LEASE_HELD_SQL}
              AND {PRE_DEADLINE_STATES_SQL}""",
            (claim_id, mode_generation, probe_generation, owner),
        )
        if not cursor.rowcount:
            return None
        if target_healthy and self._complete(
            claim_id=claim_id,
            route_generation=route_generation,
            mode_generation=mode_generation,
            probe_generation=probe_generation,
            owner=owner,
            now=now,
        ):
            return "completed"
        if effective_healthy and self._cancel_transition(
            claim_id,
            route_generation,
            mode_generation,
            probe_generation,
            transition.source_mode,
            owner,
            now,
        ):
            return "cancelled"
        failed = self._fail_reserved_ids(
            claim_id, route_generation, mode_generation, probe_generation,
            owner, "transition_deadline_failed", now
        )
        if failed:
            return "failed"
        return None

    def resolve_deadline(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        target_healthy: bool,
        effective_healthy: bool,
    ) -> str | None:
        return self._resolve_deadline(
            claim.id,
            claim.route_generation,
            reservation.mode_generation,
            reservation.probe_generation,
            target_healthy,
            effective_healthy,
            owner=reservation.owner,
        )

    def _fail_reserved(
        self, claim: DomainClaim, reservation: ProbeReservation, error: str
    ) -> bool:
        return self._fail_reserved_ids(
            claim.id,
            claim.route_generation,
            reservation.mode_generation,
            reservation.probe_generation,
            reservation.owner,
            error,
        )

    def _fail_reserved_ids(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        probe_generation: int,
        owner: str,
        error: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims
            SET activated_at = NULL, activation_checked_at = ?, activation_error = ?
            WHERE {claim_scope(include_removal=False)}
              AND {reserved_transition_exists()}""",
            (
                now.isoformat(), error, claim_id, route_generation, mode_generation,
                claim_id, mode_generation, probe_generation, owner,
            ),
        )
        if not cursor.rowcount:
            return False
        self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET state = '{TransitionState.FAILED}', error = ?, completed_at = ?,
                probe_generation = probe_generation + 1,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE {RESERVED_KEY_SQL} AND lease_owner = ?""",
            (
                error, now.isoformat(), claim_id, mode_generation,
                probe_generation, owner,
            ),
        )
        return True

    def apply_continuous_health(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        error: str | None,
        transient: bool = False,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        if error:
            cursor = self._conn.execute(
                f"""UPDATE custom_domain_claims
                SET health_checked_at = ?,
                    health_failure_count = CASE WHEN ? THEN health_failure_count + 1 ELSE 3 END,
                    activation_checked_at = ?, activation_error = ?,
                    activated_at = CASE
                        WHEN NOT ? OR health_failure_count + 1 >= 3 THEN NULL
                        ELSE activated_at END
                WHERE {claim_scope()}""",
                (
                    now.isoformat(),
                    transient,
                    now.isoformat(),
                    error,
                    transient,
                    claim_id,
                    route_generation,
                    mode_generation,
                ),
            )
        else:
            cursor = self._conn.execute(
                f"""UPDATE custom_domain_claims
                SET health_checked_at = ?, health_failure_count = 0,
                    activation_checked_at = ?, activation_error = NULL
                WHERE {claim_scope()}""",
                (
                    now.isoformat(),
                    now.isoformat(),
                    claim_id,
                    route_generation,
                    mode_generation,
                ),
            )
        if not cursor.rowcount:
            return False
        row = self._conn.execute(
            "SELECT activated_at FROM custom_domain_claims WHERE id = ?", (claim_id,)
        ).fetchone()
        return row["activated_at"] is None

    def apply_activation_decision(
        self,
        claim: DomainClaim,
        error: str | None,
        transient: bool = False,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        if error:
            cursor = self._conn.execute(
                """UPDATE custom_domain_claims
                SET health_failure_count = CASE
                        WHEN ? THEN health_failure_count + 1 ELSE health_failure_count END,
                    activation_checked_at = ?, activation_error = ?,
                    activated_at = CASE
                        WHEN NOT ? OR health_failure_count + 1 >= 3 THEN NULL
                        ELSE activated_at END
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND claim_mode = ? AND status = 'verified'
                  AND route_status = 'routed' AND route_error IS NULL""",
                (
                    transient,
                    now.isoformat(),
                    error,
                    transient,
                    claim.id,
                    claim.route_generation,
                    claim.mode_generation,
                    claim.claim_mode,
                ),
            )
        else:
            cursor = self._conn.execute(
                """UPDATE custom_domain_claims
                SET activated_at = COALESCE(activated_at, ?), activation_checked_at = ?,
                    activation_error = NULL, health_checked_at = ?,
                    health_failure_count = 0, common_failure_count = 0
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND claim_mode = ? AND status = 'verified'
                  AND route_status = 'routed' AND route_error IS NULL""",
                (
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    claim.id,
                    claim.route_generation,
                    claim.mode_generation,
                    claim.claim_mode,
                ),
            )
        return cursor.rowcount > 0

    def apply_common_health(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        error: str | None = None,
        transient: bool = False,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        if error:
            cursor = self._conn.execute(
                f"""UPDATE custom_domain_claims
                SET common_failure_count = CASE
                        WHEN ? THEN common_failure_count + 1 ELSE 3 END,
                    activation_checked_at = ?, activation_error = ?,
                    activated_at = CASE
                        WHEN NOT ? OR common_failure_count + 1 >= 3 THEN NULL
                        ELSE activated_at END
                WHERE {claim_scope()}""",
                (
                    transient,
                    now.isoformat(),
                    error,
                    transient,
                    claim_id,
                    route_generation,
                    mode_generation,
                ),
            )
        else:
            cursor = self._conn.execute(
                f"""UPDATE custom_domain_claims
                SET health_checked_at = ?, common_failure_count = 0
                WHERE {claim_scope(activation="activated")}""",
                (now.isoformat(), claim_id, route_generation, mode_generation),
            )
        if not cursor.rowcount:
            return False
        row = self._conn.execute(
            "SELECT activated_at FROM custom_domain_claims WHERE id = ?", (claim_id,)
        ).fetchone()
        return row["activated_at"] is None

    def _apply_reserved_common_failure(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        error: str,
        transient: bool,
        now: datetime | None = None,
    ) -> bool | None:
        now = now or datetime.now(timezone.utc)
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims
            SET common_failure_count = CASE
                    WHEN ? THEN common_failure_count + 1 ELSE 3 END,
                activation_checked_at = ?, activation_error = ?,
                activated_at = CASE
                    WHEN NOT ? OR common_failure_count + 1 >= 3 THEN NULL
                    ELSE activated_at END
            WHERE {claim_scope(include_removal=False)}
              AND {reserved_transition_exists()}""",
            (
                transient,
                now.isoformat(),
                error,
                transient,
                claim.id,
                claim.route_generation,
                reservation.mode_generation,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                reservation.owner,
            ),
        )
        if not cursor.rowcount:
            return None
        row = self._conn.execute(
            "SELECT activated_at FROM custom_domain_claims WHERE id = ?", (claim.id,)
        ).fetchone()
        return row["activated_at"] is None

    def _apply_reserved_common_success(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
    ) -> bool:
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims
            SET common_failure_count = CASE
                    WHEN activated_at IS NOT NULL THEN 0 ELSE common_failure_count END
            WHERE {claim_scope()}
              AND {reserved_transition_exists()}""",
            (
                claim.id,
                claim.route_generation,
                reservation.mode_generation,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                reservation.owner,
            ),
        )
        return cursor.rowcount > 0

    def _apply_reserved_continuous_health(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        error: str | None,
        transient: bool = False,
        now: datetime | None = None,
    ) -> bool | None:
        if reservation.source_mode is None:
            return None
        now = now or datetime.now(timezone.utc)
        if error:
            health_update = """health_failure_count = CASE
                    WHEN ? THEN health_failure_count + 1 ELSE 3 END,
                activation_error = ?, activated_at = CASE
                    WHEN NOT ? OR health_failure_count + 1 >= 3 THEN NULL
                    ELSE activated_at END"""
            health_parameters = (transient, error, transient)
        else:
            health_update = "health_failure_count = 0, activation_error = NULL"
            health_parameters = ()
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims
            SET health_checked_at = ?, activation_checked_at = ?, {health_update}
            WHERE {claim_scope(claim_mode=True, activation="activated")}
              AND {reserved_transition_exists(extra="source_mode = ?")}""",
            (
                now.isoformat(),
                now.isoformat(),
                *health_parameters,
                claim.id,
                claim.route_generation,
                reservation.mode_generation,
                reservation.source_mode,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                reservation.source_mode,
                reservation.owner,
            ),
        )
        if not cursor.rowcount:
            return None
        row = self._conn.execute(
            "SELECT activated_at FROM custom_domain_claims WHERE id = ?", (claim.id,)
        ).fetchone()
        return row["activated_at"] is None

    def _preserve_reserved_target_after_source_failure(
        self, claim: DomainClaim, reservation: ProbeReservation
    ) -> bool:
        new_generation = reservation.mode_generation + 1
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims SET mode_generation = ?
            WHERE {claim_scope(activation="not_activated")}
              AND {reserved_transition_exists(extra="source_mode = ?")}""",
            (
                new_generation,
                claim.id,
                claim.route_generation,
                reservation.mode_generation,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                reservation.source_mode,
                reservation.owner,
            ),
        )
        if not cursor.rowcount:
            return False
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET mode_generation = ?, probe_generation = probe_generation + 1,
                source_mode = NULL, state = '{TransitionState.OBSERVING}',
                deadline_at = NULL, automatic_retarget = 0,
                checked_at = NULL, completed_at = NULL, answer_fingerprint = NULL,
                confirmed_fingerprint = NULL, confirmed_at = NULL,
                stable_observation_count = 0, first_target_observed_at = NULL,
                last_target_observed_at = NULL, observed_mode = NULL,
                observed_ttl = NULL, max_target_ttl = 0, error = NULL,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE {RESERVED_KEY_SQL} AND source_mode = ? AND lease_owner = ?""",
            (
                new_generation,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                reservation.source_mode,
                reservation.owner,
            ),
        )
        return cursor.rowcount > 0

    def _retarget_reserved_automatic_onboarding(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        target_mode: str,
    ) -> bool:
        if target_mode not in {"direct", "cloudflare"}:
            return False
        new_generation = reservation.mode_generation + 1
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims SET mode_generation = ?
            WHERE {claim_scope(activation="not_activated", automatic=True)}
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE {RESERVED_KEY_SQL}
                  AND source_mode IS NULL AND target_mode <> ?
                  AND automatic_retarget = 1 AND observed_mode = ?
                  AND answer_fingerprint IS NOT NULL
                  AND stable_observation_count >= {STABLE_OBSERVATIONS_REQUIRED}
                  AND {LEASE_HELD_SQL}
                  AND {PRE_DEADLINE_STATES_SQL})""",
            (
                new_generation,
                claim.id,
                claim.route_generation,
                reservation.mode_generation,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                target_mode,
                target_mode,
                reservation.owner,
            ),
        )
        if not cursor.rowcount:
            return False
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET mode_generation = ?, probe_generation = probe_generation + 1,
                target_mode = ?, state = '{TransitionState.OBSERVING}',
                checked_at = NULL, completed_at = NULL, answer_fingerprint = NULL,
                confirmed_fingerprint = NULL, confirmed_at = NULL,
                stable_observation_count = 0, first_target_observed_at = NULL,
                last_target_observed_at = NULL, observed_mode = NULL,
                observed_ttl = NULL, max_target_ttl = 0, error = NULL,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE {RESERVED_KEY_SQL}
              AND source_mode IS NULL AND automatic_retarget = 1
              AND lease_owner = ?""",
            (
                new_generation,
                target_mode,
                claim.id,
                reservation.mode_generation,
                reservation.probe_generation,
                reservation.owner,
            ),
        )
        return cursor.rowcount > 0

    def retry(
        self,
        claim_id: int,
        route_generation: int,
        now: datetime | None = None,
    ) -> DomainModeTransition:
        transition = self.get(claim_id)
        if transition and transition.state in self.ACTIVE_STATES:
            raise ClaimConflict("This custom domain already has an active transition")
        if not transition or transition.state != "failed":
            raise ClaimConflict("This transition cannot be retried")
        restarted = self.start(claim_id, route_generation, transition.target_mode, now)
        self._conn.execute(
            """UPDATE custom_domain_mode_transitions SET probe_generation = ?
            WHERE claim_id = ? AND mode_generation = ?""",
            (
                transition.probe_generation + 1,
                claim_id,
                restarted.mode_generation,
            ),
        )
        return self.get(claim_id)  # type: ignore[return-value]

    def _complete(
        self,
        claim_id: int,
        route_generation: int,
        mode_generation: int,
        probe_generation: int,
        owner: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        transition = self.get(claim_id)
        if not transition:
            return False
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims
            SET claim_mode = ?, activated_at = COALESCE(activated_at, ?),
                activation_checked_at = ?, activation_error = NULL,
                health_checked_at = ?, health_failure_count = 0,
                common_failure_count = 0
            WHERE {claim_scope()}
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions AS transition
                WHERE transition.claim_id = ? AND transition.mode_generation = ?
                  AND transition.probe_generation = ? AND transition.target_mode = ?
                  AND transition.observed_mode = transition.target_mode
                  AND transition.answer_fingerprint IS NOT NULL
                  AND transition.confirmed_fingerprint = transition.answer_fingerprint
                  AND transition.confirmed_at IS NOT NULL
                  AND transition.stable_observation_count >= {STABLE_OBSERVATIONS_REQUIRED}
                  AND {lease_held("transition")}
                  AND {state_in(ACTIVE_STATE_ORDER, "transition.state")}
                  AND (transition.target_mode <> 'cloudflare' OR EXISTS (
                    SELECT 1 FROM custom_domain_cloudflare_diagnostics AS diagnostic
                    WHERE diagnostic.claim_id = transition.claim_id
                      AND diagnostic.route_generation = ?
                      AND diagnostic.mode_generation = transition.mode_generation
                      AND diagnostic.probe_generation = transition.probe_generation
                      AND diagnostic.answer_fingerprint = transition.confirmed_fingerprint)))""",
            (
                transition.target_mode,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                claim_id,
                route_generation,
                mode_generation,
                claim_id,
                mode_generation,
                probe_generation,
                transition.target_mode,
                owner,
                route_generation,
            ),
        )
        if not cursor.rowcount:
            return False
        self._conn.execute(
            f"""UPDATE custom_domain_mode_transitions
            SET state = '{TransitionState.COMPLETED}', completed_at = ?,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE {RESERVED_KEY_SQL}""",
            (now.isoformat(), claim_id, mode_generation, probe_generation),
        )
        return True

    def complete(
        self, claim: DomainClaim, reservation: ProbeReservation
    ) -> bool:
        return self._complete(
            claim.id,
            claim.route_generation,
            reservation.mode_generation,
            reservation.probe_generation,
            owner=reservation.owner,
        )

    def advance(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        assessment: HandoffAssessment,
    ) -> Outcome:
        """Apply one pass of off-the-wire evidence to a reserved transition in a
        single BEGIN IMMEDIATE transaction. The entry lease renewal is the one
        full legality check; nothing the row proves can change under a held
        lease, so the dispatched appliers run on a stable row. The branch order
        mirrors the coordinator's historical pass: source health, common
        health, observation, retarget, deadline resolution, target error,
        confirm and complete."""
        if not self._renew(reservation):
            return Outcome.lost_lease
        observation = assessment.observation
        source_health = assessment.source_health
        if not DomainPathEvidenceStore(self._conn).record(
            assessment.evidence,
            reservation.mode_generation,
            reservation.probe_generation,
            self._path_mode(reservation, source_health, observation),
            reservation,
        ):
            return Outcome.lost_lease
        if assessment.cloudflare_diagnostic is not None:
            from .cloudflare import CloudflareDiagnosticStore

            CloudflareDiagnosticStore(self._conn).record(
                assessment.cloudflare_diagnostic, reservation
            )

        if source_health is not None:
            outcome = self._advance_source(claim, reservation, source_health)
            if outcome is not None:
                return outcome

        common_error = assessment.common_error
        if common_error:
            return self._advance_common_failure(claim, reservation, common_error)

        if not self._apply_reserved_common_success(claim, reservation):
            return Outcome.lost_lease
        if not self._record_reserved_observation(claim, reservation, observation):
            return Outcome.lost_lease
        recorded = self.get(claim.id)
        if (
            recorded
            and recorded.source_mode is None
            and observation.mode != recorded.target_mode
            and recorded.stable_observation_count >= STABLE_OBSERVATIONS_REQUIRED
            and (observation.mode != "cloudflare" or assessment.cloudflare_target_enabled)
            and self._retarget_reserved_automatic_onboarding(
                claim, reservation, observation.mode
            )
        ):
            return Outcome.retargeted
        if not recorded:
            return Outcome.lost_lease

        if self._deadline_due(recorded.claim_id, recorded.mode_generation):
            return self._advance_deadline(claim, reservation, recorded, assessment)

        if (
            observation.mode != recorded.target_mode
            or recorded.stable_observation_count < STABLE_OBSERVATIONS_REQUIRED
        ):
            self.release(reservation)
            return Outcome(recorded.state)

        return self._advance_stable_target(claim, reservation, recorded, assessment)

    def _advance_source(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        source_health: EvidenceResult,
    ) -> Outcome | None:
        source_error = source_health.error if not source_health.healthy else None
        deactivated = self._apply_reserved_continuous_health(
            claim, reservation, source_error, source_health.transient
        )
        if deactivated is None:
            return Outcome.lost_lease
        if deactivated:
            self._preserve_reserved_target_after_source_failure(claim, reservation)
            return Outcome.source_failed_target_preserved
        if source_error:
            if self._deadline_due(reservation.claim_id, reservation.mode_generation):
                return self._deadline_outcome(
                    self.resolve_deadline(claim, reservation, False, False)
                )
            self.release(reservation)
            return Outcome.source_unhealthy_retained
        return None

    def _advance_common_failure(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        common_error: EvidenceResult,
    ) -> Outcome:
        error = common_error.error or "common_check_failed"
        deactivated = self._apply_reserved_common_failure(
            claim, reservation, error, common_error.transient
        )
        if deactivated is None:
            return Outcome.lost_lease
        if self._deadline_due(reservation.claim_id, reservation.mode_generation):
            return self._deadline_outcome(
                self.resolve_deadline(claim, reservation, False, False)
            )
        if deactivated and reservation.source_mode is not None:
            self._fail_reserved(claim, reservation, error)
            return Outcome.failed
        self.release(reservation)
        return Outcome.common_failed

    def _advance_deadline(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        recorded: DomainModeTransition,
        assessment: HandoffAssessment,
    ) -> Outcome:
        observation = assessment.observation
        observed_healthy = assessment.target_error(observation.mode) is None
        target_healthy = bool(
            recorded.stable_observation_count >= STABLE_OBSERVATIONS_REQUIRED
            and observation.mode == recorded.target_mode
            and observed_healthy
        )
        if target_healthy:
            confirmed_dns = assessment.confirmed_dns
            target_healthy = bool(
                confirmed_dns
                and self._record_reserved_confirmation(claim, reservation, confirmed_dns)
            )
        effective_healthy = bool(
            recorded.source_mode == observation.mode and observed_healthy
        )
        return self._deadline_outcome(
            self.resolve_deadline(
                claim, reservation, target_healthy, effective_healthy
            )
        )

    def _advance_stable_target(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        recorded: DomainModeTransition,
        assessment: HandoffAssessment,
    ) -> Outcome:
        target_error = assessment.target_error(recorded.target_mode)
        if target_error:
            self._set_reserved_action_needed(
                claim, reservation, target_error.error or "target_check_failed"
            )
            return Outcome.action_needed
        confirmed_dns = assessment.confirmed_dns
        if not confirmed_dns or not self._record_reserved_confirmation(
            claim, reservation, confirmed_dns
        ):
            self.release(reservation)
            return Outcome(recorded.state)
        if self.complete(claim, reservation):
            return Outcome.completed
        return Outcome(recorded.state)

    @staticmethod
    def _path_mode(
        reservation: ProbeReservation,
        source_health: EvidenceResult | None,
        observation: DnsObservation,
    ) -> str | None:
        if source_health is not None:
            return reservation.source_mode
        if observation.mode in {"direct", "cloudflare"}:
            return observation.mode
        return reservation.target_mode

    @staticmethod
    def _deadline_outcome(result: str | None) -> Outcome:
        return {
            "completed": Outcome.deadline_completed,
            "cancelled": Outcome.deadline_cancelled,
            "failed": Outcome.deadline_failed,
        }.get(result, Outcome.lost_lease)


class TransitionValidationFailed(Exception):
    def __init__(self, code: str, transient: bool = False):
        self.code = code
        self.transient = transient
        super().__init__(code)


class DomainTransitionCoordinator:
    def __init__(
        self,
        evidence_collector,
        cloudflare_diagnostician,
        admission_enabled: Callable[[], bool],
        cloudflare_target_enabled: Callable[[], bool],
        database: Callable,
        lease_owner: str = "domain-transition-coordinator",
    ):
        self._evidence_collector = evidence_collector
        self._cloudflare_diagnostician = cloudflare_diagnostician
        self._admission_enabled = admission_enabled
        self._cloudflare_target_enabled = cloudflare_target_enabled
        self._database = database
        self._lease_owner = f"{lease_owner}-{uuid.uuid4().hex}"
        self._executor = ThreadPoolExecutor(max_workers=MAX_COORDINATOR_CONCURRENCY)
        self._in_flight: set[int] = set()
        self._in_flight_lock = threading.Lock()

    def run_once(self) -> None:
        with self._database() as conn:
            candidates = DomainClaimStateMachine(conn).managed_candidates()
        with self._in_flight_lock:
            selected = [
                claim for claim in candidates if claim.id not in self._in_flight
            ][:MAX_COORDINATOR_CANDIDATES]
            self._in_flight.update(claim.id for claim in selected)
        futures = []
        for claim in selected:
            future = self._executor.submit(self._process_safely, claim)
            future.add_done_callback(lambda _future, claim_id=claim.id: self._finished(claim_id))
            futures.append(future)
        wait(futures, timeout=COORDINATOR_PASS_SECONDS)

    def _finished(self, claim_id: int) -> None:
        with self._in_flight_lock:
            self._in_flight.discard(claim_id)

    def _process_safely(self, claim: DomainClaim) -> None:
        try:
            self._process(claim)
        except Exception:
            logger.exception(
                "Domain evidence collection failed for claim %d generation %d",
                claim.id,
                claim.mode_generation,
            )

    def retry(self, claim_id: int, site_name: str) -> DomainModeTransition:
        with self._database() as conn:
            claim = DomainClaimStore(conn).get(claim_id, site_name)
            return DomainClaimStateMachine(conn).retry(
                claim.id, claim.route_generation
            )

    def cancel(self, claim_id: int, site_name: str) -> bool:
        with self._database() as conn:
            claim = DomainClaimStore(conn).get(claim_id, site_name)
            transitions = DomainClaimStateMachine(conn)
            transition = transitions.get(claim.id)
            if transition and transition.state == "cancelled":
                return True
            if not transition or transition.state not in transitions.ACTIVE_STATES:
                raise ClaimConflict("This transition cannot be cancelled")
            reservation = transitions.reserve(
                claim.id,
                claim.route_generation,
                transition.mode_generation,
                self._lease_owner,
            )
        if not reservation:
            raise ClaimConflict("This transition cannot be cancelled")
        if reservation.source_mode is None:
            with self._database() as conn:
                cancelled = DomainClaimStateMachine(conn).cancel(
                    claim, reservation
                )
            if not cancelled:
                raise ClaimConflict("This transition changed while cancellation was validated")
            return True
        # An active handoff cancels only if its effective (source) path is still
        # healthy. Collect that proof off the wire, then reconcile in one txn.
        evidence = self._evidence_collector.collect(claim, claim.claim_mode)
        effective_healthy = (
            evidence.common_error is None
            and evidence.dns.mode == claim.claim_mode
            and evidence.target_error(claim.claim_mode) is None
        )
        with self._database() as conn:
            machine = DomainClaimStateMachine(conn)
            if not DomainPathEvidenceStore(conn).record(
                evidence,
                reservation.mode_generation,
                reservation.probe_generation,
                claim.claim_mode,
                reservation,
            ) or not machine._renew(reservation):
                outcome = "changed"
            elif not effective_healthy:
                machine.release(reservation)
                outcome = "unhealthy"
            elif machine.cancel(claim, reservation):
                outcome = "cancelled"
            else:
                outcome = "changed"
        if outcome == "unhealthy":
            raise ClaimConflict("The effective domain path is not healthy")
        if outcome == "changed":
            raise ClaimConflict("This transition changed while cancellation was validated")
        return True

    def _process(self, claim: DomainClaim) -> None:
        deadline = time.monotonic() + COORDINATOR_PASS_SECONDS
        with self._database() as conn:
            transitions = DomainClaimStateMachine(conn)
            transition = transitions.get(claim.id)
            active_transition = bool(
                transition
                and transition.mode_generation == claim.mode_generation
                and transition.state in transitions.ACTIVE_STATES
            )
        if active_transition:
            self._process_transition(claim, transition, deadline)
            return

        evidence = self._evidence_collector.collect(
            claim, claim.claim_mode if claim.activated_at else None
        )
        with self._database() as conn:
            DomainPathEvidenceStore(conn).record(
                evidence,
                claim.mode_generation,
                0,
                claim.claim_mode if claim.activated_at else None,
            )
        if time.monotonic() > deadline:
            return
        common_error = evidence.common_error
        if common_error:
            if claim.activated_at:
                with self._database() as conn:
                    transitions = DomainClaimStateMachine(conn)
                    transitions.apply_common_health(
                        claim.id,
                        claim.route_generation,
                        claim.mode_generation,
                        common_error.error or "common_check_failed",
                        transient=common_error.transient,
                    )
            return
        observation = evidence.dns
        self._refresh_common_health(claim)
        target_mode = self._proposed_target(claim, observation)
        if not target_mode:
            unsupported = (
                not claim.activated_at
                and observation.mode == "cloudflare"
                and not self._cloudflare_target_enabled()
            )
            onboarding_error = "cloudflare_unsupported" if unsupported else None
            if claim.last_error != onboarding_error:
                with self._database() as conn:
                    DomainClaimStore(conn).set_onboarding_error(
                        claim.id, claim.route_generation, onboarding_error
                    )
            self._apply_stable_health(claim, evidence)
            return
        with self._database() as conn:
            if claim.last_error is not None:
                DomainClaimStore(conn).set_onboarding_error(
                    claim.id, claim.route_generation, None
                )
            DomainClaimStateMachine(conn).start(
                claim.id,
                claim.route_generation,
                target_mode,
                automatic_retarget=not claim.activated_at,
            )

    def _process_transition(
        self, claim: DomainClaim, transition: DomainModeTransition, deadline: float
    ) -> None:
        with self._database() as conn:
            reservation = DomainClaimStateMachine(conn).reserve(
                claim.id,
                claim.route_generation,
                transition.mode_generation,
                self._lease_owner,
            )
        if not reservation:
            return
        try:
            assessment = self._assess(claim, reservation, deadline)
        except Exception:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release(reservation)
            raise
        if assessment is None:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release(reservation)
            return
        with self._database() as conn:
            DomainClaimStateMachine(conn).advance(claim, reservation, assessment)

    def _assess(
        self, claim: DomainClaim, reservation: ProbeReservation, deadline: float
    ) -> HandoffAssessment | None:
        modes = tuple(
            mode
            for mode in (reservation.target_mode, reservation.source_mode)
            if mode is not None
        )
        evidence = self._evidence_collector.collect(claim, modes)
        if time.monotonic() > deadline:
            return None
        source_health = self._source_health(reservation, evidence)
        cloudflare_path = "cloudflare" in {
            reservation.source_mode,
            reservation.target_mode,
            evidence.dns.mode,
        }
        diagnostic = (
            self._cloudflare_diagnostician.diagnose_transition(
                claim, reservation, evidence, evidence
            )
            if cloudflare_path
            else None
        )
        return HandoffAssessment(
            evidence,
            source_health,
            diagnostic,
            self._cloudflare_target_enabled(),
        )

    @staticmethod
    def _source_health(reservation: ProbeReservation, evidence):
        if reservation.source_mode is None:
            return None
        common_error = evidence.common_error
        if common_error:
            return common_error
        if reservation.source_mode == "cloudflare" and not evidence.ranges.healthy:
            return evidence.ranges
        if evidence.dns.mode != reservation.source_mode:
            return None
        return evidence.target_error(reservation.source_mode) or EvidenceResult("healthy")

    def _proposed_target(
        self, claim: DomainClaim, observation: DnsObservation
    ) -> str | None:
        if (
            not claim.activated_at
            and claim.health_checked_at is not None
            and observation.mode == claim.claim_mode
        ):
            if claim.claim_mode == "cloudflare" and not self._cloudflare_target_enabled():
                return None
            return claim.claim_mode
        if not self._admission_enabled():
            return None
        if claim.activated_at:
            if observation.mode == "mixed":
                target = "cloudflare" if claim.claim_mode == "direct" else "direct"
            elif observation.mode in {"direct", "cloudflare"}:
                target = observation.mode if observation.mode != claim.claim_mode else None
            else:
                target = None
        elif (
            claim.automatic_mode or claim.health_checked_at is not None
        ) and observation.mode in {"direct", "cloudflare"}:
            target = observation.mode
        else:
            target = None
        if target == "cloudflare" and not self._cloudflare_target_enabled():
            return None
        return target

    def _apply_stable_health(
        self, claim: DomainClaim, evidence
    ) -> None:
        observation = evidence.dns
        if not claim.activated_at:
            return
        error = observation.error
        transient = observation.mode == "unavailable"
        if observation.mode != claim.claim_mode and not error:
            error = "dns_unexpected_address"
        if not error:
            if claim.claim_mode == "cloudflare":
                self._cloudflare_diagnostician.record_health(claim, evidence, evidence)
            target_error = evidence.target_error(claim.claim_mode)
            if target_error:
                error = target_error.error
                transient = target_error.transient
        with self._database() as conn:
            DomainClaimStateMachine(conn).apply_continuous_health(
                claim.id,
                claim.route_generation,
                claim.mode_generation,
                error,
                transient,
            )

    def _refresh_common_health(self, claim: DomainClaim) -> None:
        if not claim.activated_at:
            return
        with self._database() as conn:
            DomainClaimStateMachine(conn).apply_common_health(
                claim.id, claim.route_generation, claim.mode_generation
            )
