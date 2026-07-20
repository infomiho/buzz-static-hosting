from __future__ import annotations

import logging

from .claims import DomainClaim, DomainClaimStore
from ..db import db
from .evidence import DomainEvidenceCollector, DomainPathEvidenceStore
from .transitions import DomainClaimStateMachine

MAX_CANDIDATES_PER_PASS = 10
logger = logging.getLogger(__name__)


class DomainActivator:
    def __init__(
        self,
        evidence_collector: DomainEvidenceCollector,
    ):
        self._evidence_collector = evidence_collector

    def run_once(self) -> None:
        with db() as conn:
            claims = DomainClaimStore(conn).activation_candidates()
        for claim in claims[:MAX_CANDIDATES_PER_PASS]:
            try:
                evidence = self._evidence_collector.collect(claim, "direct")
                target_error = evidence.target_error("direct")
                error = target_error.error if target_error else None
                if error == "dns_timeout":
                    error = "dns_unavailable"
                transient = target_error.transient if target_error else False
                with db() as conn:
                    DomainPathEvidenceStore(conn).record(
                        evidence, claim.mode_generation, 0, "direct"
                    )
                    activated = DomainClaimStateMachine(conn).apply_activation_decision(
                        claim, error, transient
                    )
                if error:
                    if activated:
                        logger.warning(
                            "Custom domain %d generation %d activation failed: %s",
                            claim.id,
                            claim.route_generation,
                            error,
                        )
                    continue
            except Exception:
                self._record_error(claim, "activation_check_failed")
                logger.exception(
                    "Custom domain %d generation %d validation failed unexpectedly",
                    claim.id,
                    claim.route_generation,
                )
                continue
            if activated:
                logger.info(
                    "Custom domain %d generation %d activated",
                    claim.id,
                    claim.route_generation,
                )

    @staticmethod
    def _record_error(claim: DomainClaim, error: str) -> None:
        with db() as conn:
            changed = DomainClaimStateMachine(conn).apply_activation_decision(
                claim,
                error,
                transient=error in {"dns_unavailable", "origin_unavailable"},
            )
        if changed:
            logger.warning(
                "Custom domain %d generation %d activation failed: %s",
                claim.id,
                claim.route_generation,
                error,
            )
