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
from typing import Callable

from .claims import (
    HEALTH_FRESHNESS_SECONDS,
    DomainClaim,
    DomainClaimStore,
)
from ..db import db
from .evidence import DnsObservation, DomainPathEvidenceStore, EvidenceResult
from .probes import MAX_CONCURRENT_CLAIM_CHECKS
from .errors import ClaimConflict

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


class DomainClaimStateMachine:
    ACTIVE_STATES = ("observing", "validating", "action_needed", "deadline_evaluation")

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

    def reserve_probe(
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
            """UPDATE custom_domain_mode_transitions
            SET probe_generation = probe_generation + 1, lease_owner = ?,
                lease_expires_at = datetime('now', ?)
            WHERE claim_id = ? AND mode_generation = ?
              AND state IN ('observing', 'validating', 'action_needed', 'deadline_evaluation')
              AND (lease_expires_at IS NULL OR lease_expires_at <= datetime('now'))
              AND EXISTS (
                SELECT 1 FROM custom_domain_claims
                WHERE id = ? AND route_generation = ?
                  AND mode_generation = ? AND status = 'verified'
                  AND route_status = 'routed' AND removal_requested_at IS NULL)""",
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

    def renew_reservation(self, reservation: ProbeReservation) -> bool:
        return self._renew_probe(
            reservation.claim_id,
            reservation.mode_generation,
            reservation.probe_generation,
            reservation.owner,
        )

    def release_reservation(self, reservation: ProbeReservation) -> bool:
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
        release_lease: bool = True,
    ) -> bool:
        transition = self.get(claim_id)
        if not transition:
            return False
        target_observed = observed_mode == transition.target_mode and fingerprint is not None
        tracked_observation = target_observed or bool(
            transition.automatic_retarget
            and observed_mode in {"direct", "cloudflare"}
            and fingerprint is not None
        )
        same_answer = bool(
            tracked_observation
            and observed_mode == transition.observed_mode
            and fingerprint == transition.answer_fingerprint
        )
        database_now = self._conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')"
        ).fetchone()[0]
        separated = False
        if same_answer and transition.last_target_observed_at:
            last_observed = datetime.fromisoformat(transition.last_target_observed_at)
            if last_observed.tzinfo is None:
                last_observed = last_observed.replace(tzinfo=timezone.utc)
            separated = datetime.fromisoformat(database_now) - last_observed >= timedelta(
                seconds=max(60, ttl, transition.max_target_ttl)
            )
        stable_increment = same_answer and separated
        accepted_target_sample = tracked_observation and (
            not same_answer or stable_increment
        )
        state = "validating" if target_observed else "observing"
        cursor = self._conn.execute(
            """UPDATE custom_domain_mode_transitions
            SET checked_at = ?, observed_mode = ?, observed_ttl = ?,
                answer_fingerprint = CASE WHEN ? THEN ? ELSE answer_fingerprint END,
                max_target_ttl = CASE
                    WHEN ? AND ? THEN MAX(max_target_ttl, ?)
                    WHEN ? THEN ? ELSE max_target_ttl END,
                error = ?, state = ?,
                stable_observation_count = CASE
                    WHEN ? THEN stable_observation_count + 1
                    WHEN ? THEN 1 ELSE stable_observation_count END,
                first_target_observed_at = CASE
                    WHEN ? AND ? THEN COALESCE(first_target_observed_at, ?)
                    WHEN ? THEN ? ELSE first_target_observed_at END,
                last_target_observed_at = CASE WHEN ? THEN ?
                    ELSE last_target_observed_at END,
                lease_owner = CASE WHEN ? THEN NULL ELSE lease_owner END,
                lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND lease_owner = ? AND lease_expires_at > datetime('now')
              AND EXISTS (SELECT 1 FROM custom_domain_claims
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND status = 'verified' AND route_status = 'routed'
                  AND removal_requested_at IS NULL)""",
            (
                database_now,
                observed_mode,
                ttl,
                tracked_observation,
                fingerprint,
                tracked_observation,
                same_answer,
                ttl,
                tracked_observation,
                ttl,
                error,
                state,
                stable_increment,
                tracked_observation,
                tracked_observation,
                same_answer,
                database_now,
                tracked_observation,
                database_now,
                accepted_target_sample,
                database_now,
                release_lease,
                release_lease,
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

    def record_reserved_observation(
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
            release_lease=False,
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
            """UPDATE custom_domain_mode_transitions
            SET state = 'action_needed', error = ?, checked_at = CURRENT_TIMESTAMP,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND lease_owner = ? AND lease_expires_at > datetime('now')
              AND EXISTS (SELECT 1 FROM custom_domain_claims
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND status = 'verified' AND route_status = 'routed'
                  AND removal_requested_at IS NULL)""",
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

    def set_reserved_action_needed(
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

    def record_reserved_confirmation(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        observation: DnsObservation,
    ) -> bool:
        if not observation.fingerprint:
            return False
        cursor = self._conn.execute(
            """UPDATE custom_domain_mode_transitions
            SET confirmed_fingerprint = ?, confirmed_at = CURRENT_TIMESTAMP
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND target_mode = ? AND observed_mode = target_mode
              AND answer_fingerprint = ? AND stable_observation_count >= 2
              AND lease_owner = ? AND lease_expires_at > datetime('now')
              AND EXISTS (SELECT 1 FROM custom_domain_claims
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND status = 'verified' AND route_status = 'routed'
                  AND removal_requested_at IS NULL)""",
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
            """UPDATE custom_domain_mode_transitions
            SET lease_owner = NULL, lease_expires_at = NULL
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND lease_owner = ?""",
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
            """UPDATE custom_domain_mode_transitions
            SET lease_expires_at = datetime('now', ?)
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND lease_owner = ? AND lease_expires_at > datetime('now')
              AND state IN
                ('observing', 'validating', 'action_needed', 'deadline_evaluation')
              AND EXISTS (SELECT 1 FROM custom_domain_claims
                WHERE id = ? AND mode_generation = ? AND status = 'verified'
                  AND route_status = 'routed' AND removal_requested_at IS NULL)""",
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

    def deadline_due(self, claim_id: int, mode_generation: int) -> bool:
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
        activation_guard = (
            "activated_at IS NOT NULL" if transition.source_mode else "activated_at IS NULL"
        )
        cursor = self._conn.execute(
            f"""UPDATE custom_domain_claims
            SET mode_generation = ?,
                automatic_mode = CASE WHEN ? THEN 0 ELSE automatic_mode END,
                activation_error = NULL, health_failure_count = 0,
                common_failure_count = 0,
                health_checked_at = CASE WHEN ? THEN NULL ELSE ? END,
                activation_checked_at = CASE WHEN ? THEN activation_checked_at ELSE ? END
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND status = 'verified' AND route_status = 'routed' AND {activation_guard}
              AND removal_requested_at IS NULL
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
                  AND source_mode IS ? AND lease_owner = ?
                  AND lease_expires_at > datetime('now')
                  AND state IN ('observing', 'validating', 'action_needed', 'deadline_evaluation'))""",
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
            """UPDATE custom_domain_mode_transitions
            SET mode_generation = ?, probe_generation = probe_generation + 1,
                state = 'cancelled', completed_at = ?, lease_owner = NULL,
                lease_expires_at = NULL
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND lease_owner = ?""",
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

    def cancel_reserved(
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
            or not self.deadline_due(claim_id, mode_generation)
        ):
            return None
        cursor = self._conn.execute(
            """UPDATE custom_domain_mode_transitions SET state = 'deadline_evaluation'
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND lease_owner = ? AND lease_expires_at > datetime('now')
              AND state IN ('observing', 'validating', 'action_needed')""",
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

    def resolve_reserved_deadline(
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

    def fail_reserved(
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
            """UPDATE custom_domain_claims
            SET activated_at = NULL, activation_checked_at = ?, activation_error = ?
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND status = 'verified' AND route_status = 'routed'
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
                  AND lease_owner = ? AND lease_expires_at > datetime('now')
                  AND state IN
                    ('observing', 'validating', 'action_needed', 'deadline_evaluation'))""",
            (
                now.isoformat(), error, claim_id, route_generation, mode_generation,
                claim_id, mode_generation, probe_generation, owner,
            ),
        )
        if not cursor.rowcount:
            return False
        self._conn.execute(
            """UPDATE custom_domain_mode_transitions
            SET state = 'failed', error = ?, completed_at = ?,
                probe_generation = probe_generation + 1,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND lease_owner = ?""",
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
                """UPDATE custom_domain_claims
                SET health_checked_at = ?,
                    health_failure_count = CASE WHEN ? THEN health_failure_count + 1 ELSE 3 END,
                    activation_checked_at = ?, activation_error = ?,
                    activated_at = CASE
                        WHEN NOT ? OR health_failure_count + 1 >= 3 THEN NULL
                        ELSE activated_at END
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND status = 'verified' AND route_status = 'routed'
                  AND removal_requested_at IS NULL""",
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
                """UPDATE custom_domain_claims
                SET health_checked_at = ?, health_failure_count = 0,
                    activation_checked_at = ?, activation_error = NULL
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND status = 'verified' AND route_status = 'routed'
                  AND removal_requested_at IS NULL""",
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
                """UPDATE custom_domain_claims
                SET common_failure_count = CASE
                        WHEN ? THEN common_failure_count + 1 ELSE 3 END,
                    activation_checked_at = ?, activation_error = ?,
                    activated_at = CASE
                        WHEN NOT ? OR common_failure_count + 1 >= 3 THEN NULL
                        ELSE activated_at END
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND status = 'verified' AND route_status = 'routed'
                  AND removal_requested_at IS NULL""",
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
                """UPDATE custom_domain_claims
                SET health_checked_at = ?, common_failure_count = 0
                WHERE id = ? AND route_generation = ? AND mode_generation = ?
                  AND status = 'verified' AND route_status = 'routed'
                  AND activated_at IS NOT NULL AND removal_requested_at IS NULL""",
                (now.isoformat(), claim_id, route_generation, mode_generation),
            )
        if not cursor.rowcount:
            return False
        row = self._conn.execute(
            "SELECT activated_at FROM custom_domain_claims WHERE id = ?", (claim_id,)
        ).fetchone()
        return row["activated_at"] is None

    def apply_reserved_common_failure(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        error: str,
        transient: bool,
        now: datetime | None = None,
    ) -> bool | None:
        now = now or datetime.now(timezone.utc)
        cursor = self._conn.execute(
            """UPDATE custom_domain_claims
            SET common_failure_count = CASE
                    WHEN ? THEN common_failure_count + 1 ELSE 3 END,
                activation_checked_at = ?, activation_error = ?,
                activated_at = CASE
                    WHEN NOT ? OR common_failure_count + 1 >= 3 THEN NULL
                    ELSE activated_at END
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND status = 'verified' AND route_status = 'routed'
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
                  AND lease_owner = ? AND lease_expires_at > datetime('now')
                  AND state IN
                    ('observing', 'validating', 'action_needed', 'deadline_evaluation'))""",
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

    def apply_reserved_common_success(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
    ) -> bool:
        cursor = self._conn.execute(
            """UPDATE custom_domain_claims
            SET common_failure_count = CASE
                    WHEN activated_at IS NOT NULL THEN 0 ELSE common_failure_count END
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND status = 'verified' AND route_status = 'routed'
              AND removal_requested_at IS NULL
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
                  AND lease_owner = ? AND lease_expires_at > datetime('now')
                  AND state IN
                    ('observing', 'validating', 'action_needed', 'deadline_evaluation'))""",
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

    def apply_reserved_continuous_health(
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
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND claim_mode = ? AND activated_at IS NOT NULL
              AND status = 'verified' AND route_status = 'routed'
              AND removal_requested_at IS NULL
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
                  AND source_mode = ? AND lease_owner = ?
                  AND lease_expires_at > datetime('now')
                  AND state IN
                    ('observing', 'validating', 'action_needed', 'deadline_evaluation'))""",
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

    def preserve_reserved_target_after_source_failure(
        self, claim: DomainClaim, reservation: ProbeReservation
    ) -> bool:
        new_generation = reservation.mode_generation + 1
        cursor = self._conn.execute(
            """UPDATE custom_domain_claims SET mode_generation = ?
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND activated_at IS NULL AND status = 'verified'
              AND route_status = 'routed' AND removal_requested_at IS NULL
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
                  AND source_mode = ? AND lease_owner = ?
                  AND lease_expires_at > datetime('now')
                  AND state IN
                    ('observing', 'validating', 'action_needed', 'deadline_evaluation'))""",
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
            """UPDATE custom_domain_mode_transitions
            SET mode_generation = ?, probe_generation = probe_generation + 1,
                source_mode = NULL, state = 'observing', deadline_at = NULL,
                automatic_retarget = 0,
                checked_at = NULL, completed_at = NULL, answer_fingerprint = NULL,
                confirmed_fingerprint = NULL, confirmed_at = NULL,
                stable_observation_count = 0, first_target_observed_at = NULL,
                last_target_observed_at = NULL, observed_mode = NULL,
                observed_ttl = NULL, max_target_ttl = 0, error = NULL,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
              AND source_mode = ? AND lease_owner = ?""",
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

    def retarget_reserved_automatic_onboarding(
        self,
        claim: DomainClaim,
        reservation: ProbeReservation,
        target_mode: str,
    ) -> bool:
        if target_mode not in {"direct", "cloudflare"}:
            return False
        new_generation = reservation.mode_generation + 1
        cursor = self._conn.execute(
            """UPDATE custom_domain_claims SET mode_generation = ?
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND automatic_mode = 1 AND activated_at IS NULL
              AND status = 'verified' AND route_status = 'routed'
              AND removal_requested_at IS NULL
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
                  AND source_mode IS NULL AND target_mode <> ?
                  AND automatic_retarget = 1 AND observed_mode = ?
                  AND answer_fingerprint IS NOT NULL
                  AND stable_observation_count >= 2 AND lease_owner = ?
                  AND lease_expires_at > datetime('now')
                  AND state IN ('observing', 'validating', 'action_needed'))""",
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
            """UPDATE custom_domain_mode_transitions
            SET mode_generation = ?, probe_generation = probe_generation + 1,
                target_mode = ?, state = 'observing', checked_at = NULL,
                completed_at = NULL, answer_fingerprint = NULL,
                confirmed_fingerprint = NULL, confirmed_at = NULL,
                stable_observation_count = 0, first_target_observed_at = NULL,
                last_target_observed_at = NULL, observed_mode = NULL,
                observed_ttl = NULL, max_target_ttl = 0, error = NULL,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?
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
            """UPDATE custom_domain_claims
            SET claim_mode = ?, activated_at = COALESCE(activated_at, ?),
                activation_checked_at = ?, activation_error = NULL,
                health_checked_at = ?, health_failure_count = 0,
                common_failure_count = 0
            WHERE id = ? AND route_generation = ? AND mode_generation = ?
              AND status = 'verified' AND route_status = 'routed'
              AND removal_requested_at IS NULL
              AND EXISTS (SELECT 1 FROM custom_domain_mode_transitions AS transition
                WHERE transition.claim_id = ? AND transition.mode_generation = ?
                  AND transition.probe_generation = ? AND transition.target_mode = ?
                  AND transition.observed_mode = transition.target_mode
                  AND transition.answer_fingerprint IS NOT NULL
                  AND transition.confirmed_fingerprint = transition.answer_fingerprint
                  AND transition.confirmed_at IS NOT NULL
                  AND transition.stable_observation_count >= 2
                  AND transition.lease_owner = ?
                  AND transition.lease_expires_at > datetime('now')
                  AND transition.state IN
                    ('observing', 'validating', 'action_needed', 'deadline_evaluation')
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
            """UPDATE custom_domain_mode_transitions
            SET state = 'completed', completed_at = ?, lease_owner = NULL,
                lease_expires_at = NULL
            WHERE claim_id = ? AND mode_generation = ? AND probe_generation = ?""",
            (now.isoformat(), claim_id, mode_generation, probe_generation),
        )
        return True

    def complete_reserved(
        self, claim: DomainClaim, reservation: ProbeReservation
    ) -> bool:
        return self._complete(
            claim.id,
            claim.route_generation,
            reservation.mode_generation,
            reservation.probe_generation,
            owner=reservation.owner,
        )


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
        database: Callable = db,
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
            reservation = transitions.reserve_probe(
                claim.id,
                claim.route_generation,
                transition.mode_generation,
                self._lease_owner,
            )
        if not reservation:
            raise ClaimConflict("This transition cannot be cancelled")
        if reservation.source_mode is None:
            with self._database() as conn:
                cancelled = DomainClaimStateMachine(conn).cancel_reserved(
                    claim, reservation
                )
            if not cancelled:
                raise ClaimConflict("This transition changed while cancellation was validated")
            return True
        evidence = self._evidence_collector.collect(claim, claim.claim_mode)
        with self._database() as conn:
            if not DomainPathEvidenceStore(conn).record(
                evidence,
                reservation.mode_generation,
                reservation.probe_generation,
                claim.claim_mode,
                reservation,
            ):
                raise ClaimConflict("This transition changed while cancellation was validated")
        with self._database() as conn:
            if not DomainClaimStateMachine(conn).renew_reservation(reservation):
                raise ClaimConflict("This transition changed while cancellation was validated")
        common_error = evidence.common_error
        if common_error:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release_reservation(reservation)
            raise ClaimConflict("The effective domain path is not healthy")
        observation = evidence.dns
        if observation.mode != claim.claim_mode:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release_reservation(reservation)
            raise ClaimConflict("The effective domain path is not healthy")
        target_error = evidence.target_error(claim.claim_mode)
        if target_error:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release_reservation(reservation)
            raise ClaimConflict("The effective domain path is not healthy")
        with self._database() as conn:
            if not DomainClaimStateMachine(conn).renew_reservation(reservation):
                raise ClaimConflict("This transition changed while cancellation was validated")
        with self._database() as conn:
            cancelled = DomainClaimStateMachine(conn).cancel_reserved(
                claim, reservation
            )
        if not cancelled:
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
            transitions = DomainClaimStateMachine(conn)
            reservation = transitions.reserve_probe(
                claim.id,
                claim.route_generation,
                transition.mode_generation,
                self._lease_owner,
            )
        if not reservation:
            return
        try:
            modes = tuple(
                mode
                for mode in (transition.target_mode, transition.source_mode)
                if mode is not None
            )
            evidence = self._evidence_collector.collect(claim, modes)
        except Exception:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release_reservation(reservation)
            raise
        if time.monotonic() > deadline:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release_reservation(reservation)
            return
        source_health = self._source_health(reservation, evidence)
        path_mode = (
            reservation.source_mode
            if source_health is not None
            else evidence.dns.mode
            if evidence.dns.mode in {"direct", "cloudflare"}
            else reservation.target_mode
        )
        with self._database() as conn:
            if not DomainPathEvidenceStore(conn).record(
                evidence,
                reservation.mode_generation,
                reservation.probe_generation,
                path_mode,
                reservation,
            ):
                return
        cloudflare_recorded = False
        if source_health is not None and reservation.source_mode == "cloudflare":
            cloudflare_recorded = self._cloudflare_diagnostician.record_transition(
                claim, reservation, evidence, evidence
            )
            if not cloudflare_recorded:
                with self._database() as conn:
                    DomainClaimStateMachine(conn).release_reservation(reservation)
                return
        if source_health is not None:
            source_error = source_health.error if not source_health.healthy else None
            with self._database() as conn:
                transitions = DomainClaimStateMachine(conn)
                deactivated = transitions.apply_reserved_continuous_health(
                    claim,
                    reservation,
                    source_error,
                    source_health.transient,
                )
                if deactivated is None:
                    return
                if deactivated:
                    transitions.preserve_reserved_target_after_source_failure(
                        claim, reservation
                    )
                    return
                if source_error:
                    if transitions.deadline_due(
                        reservation.claim_id, reservation.mode_generation
                    ):
                        transitions.resolve_reserved_deadline(
                            claim, reservation, False, False
                        )
                    else:
                        transitions.release_reservation(reservation)
                    return
        common_error = evidence.common_error
        if common_error:
            with self._database() as conn:
                transitions = DomainClaimStateMachine(conn)
                error = common_error.error or "common_check_failed"
                deactivated = transitions.apply_reserved_common_failure(
                    claim, reservation, error, common_error.transient
                )
                if deactivated is None:
                    return
                if transitions.deadline_due(
                    reservation.claim_id, reservation.mode_generation
                ):
                    transitions.resolve_reserved_deadline(
                        claim, reservation, False, False
                    )
                elif deactivated and reservation.source_mode is not None:
                    transitions.fail_reserved(claim, reservation, error)
                else:
                    transitions.release_reservation(reservation)
            return
        observation = evidence.dns
        with self._database() as conn:
            transitions = DomainClaimStateMachine(conn)
            if not transitions.renew_reservation(reservation):
                return
            if not transitions.apply_reserved_common_success(claim, reservation):
                return
            if not transitions.record_reserved_observation(
                claim, reservation, observation
            ):
                return
            recorded = transitions.get(claim.id)
            if (
                recorded
                and recorded.source_mode is None
                and observation.mode != recorded.target_mode
                and recorded.stable_observation_count >= 2
                and (
                    observation.mode != "cloudflare"
                    or self._cloudflare_target_enabled()
                )
                and transitions.retarget_reserved_automatic_onboarding(
                    claim, reservation, observation.mode
                )
            ):
                return
        if recorded:
            with self._database() as conn:
                if not DomainClaimStateMachine(conn).renew_reservation(reservation):
                    return
        with self._database() as conn:
            deadline_due = bool(
                recorded
                and DomainClaimStateMachine(conn).deadline_due(
                    recorded.claim_id, recorded.mode_generation
                )
            )
        if recorded and deadline_due:
            if time.monotonic() > deadline:
                with self._database() as conn:
                    DomainClaimStateMachine(conn).release_reservation(reservation)
                return
            observed_healthy = evidence.target_error(observation.mode) is None
            if (
                observation.mode == "cloudflare"
                and not cloudflare_recorded
                and not self._cloudflare_diagnostician.record_transition(
                    claim, reservation, evidence, evidence
                )
            ):
                with self._database() as conn:
                    DomainClaimStateMachine(conn).release_reservation(reservation)
                return
            target_healthy = bool(
                recorded.stable_observation_count >= 2
                and observation.mode == recorded.target_mode
                and observed_healthy
            )
            if target_healthy:
                confirmed_dns = evidence.confirmed_dns
                with self._database() as conn:
                    target_healthy = bool(
                        confirmed_dns
                        and DomainClaimStateMachine(conn).record_reserved_confirmation(
                            claim, reservation, confirmed_dns
                        )
                    )
            effective_healthy = bool(
                recorded.source_mode == observation.mode and observed_healthy
            )
            with self._database() as conn:
                DomainClaimStateMachine(conn).resolve_reserved_deadline(
                    claim,
                    reservation,
                    target_healthy,
                    effective_healthy,
                )
            return
        if (
            not recorded
            or observation.mode != recorded.target_mode
            or recorded.stable_observation_count < 2
        ):
            if recorded:
                with self._database() as conn:
                    DomainClaimStateMachine(conn).release_reservation(reservation)
            return
        if time.monotonic() > deadline:
            with self._database() as conn:
                DomainClaimStateMachine(conn).release_reservation(reservation)
            return
        target_error = evidence.target_error(recorded.target_mode)
        if recorded.target_mode == "cloudflare" and not self._cloudflare_diagnostician.record_transition(
            claim, reservation, evidence, evidence
        ):
            with self._database() as conn:
                DomainClaimStateMachine(conn).release_reservation(reservation)
            return
        if target_error:
            with self._database() as conn:
                DomainClaimStateMachine(conn).set_reserved_action_needed(
                    claim,
                    reservation,
                    target_error.error or "target_check_failed",
                )
            return
        with self._database() as conn:
            transitions = DomainClaimStateMachine(conn)
            confirmed_dns = evidence.confirmed_dns
            if not confirmed_dns or not transitions.record_reserved_confirmation(
                claim, reservation, confirmed_dns
            ):
                transitions.release_reservation(reservation)
                return
            transitions.complete_reserved(claim, reservation)

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
