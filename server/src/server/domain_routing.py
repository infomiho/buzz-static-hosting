from __future__ import annotations

import json
import logging
from collections.abc import Callable

from .custom_domains import DomainClaim, DomainClaimStore
from .db import db
from .traefik_control import TraefikRuntimeClient

logger = logging.getLogger(__name__)


def build_traefik_snapshot(
    https_entrypoint: str,
    service: str,
    cert_resolver: str,
) -> bytes:
    with db() as conn:
        claims = DomainClaimStore(conn).routable_claims()
    if not claims:
        return b"{}\n"
    routers = {
        claim.route_name: {
            "entryPoints": [https_entrypoint],
            "rule": f"Host(`{claim.hostname}`)",
            "service": service,
            "tls": {"certResolver": cert_resolver},
        }
        for claim in claims
    }
    return json.dumps(
        {"http": {"routers": routers}},
        separators=(",", ":"),
        sort_keys=True,
    ).encode() + b"\n"


class DomainRouteReconciler:
    def __init__(
        self,
        runtime_client: TraefikRuntimeClient,
        https_entrypoint: str,
        service: str,
        cert_resolver: str,
        routing_enabled: Callable[[], bool],
        withdrawal_snapshot_acknowledged: Callable[[str, str], bool],
    ):
        self._runtime_client = runtime_client
        self._https_entrypoint = https_entrypoint
        self._service = service
        self._cert_resolver = cert_resolver
        self._routing_enabled = routing_enabled
        self._withdrawal_snapshot_acknowledged = withdrawal_snapshot_acknowledged

    def run_once(self) -> None:
        with db() as conn:
            claims = DomainClaimStore(conn).prepare_routes(self._routing_enabled())
        for claim in claims:
            try:
                if claim.route_status == "publishing":
                    self._reconcile_publication(claim)
                elif claim.route_status == "removing":
                    self._reconcile_withdrawal(claim)
            except Exception:
                logger.exception("Custom domain router %s reconciliation failed", claim.route_name)

    def _reconcile_publication(self, claim: DomainClaim) -> None:
        try:
            router = self._runtime_client.router(claim.route_name)
        except (OSError, ValueError):
            self._record_error(claim, "runtime_api_unavailable")
            return
        if router is None:
            self._record_error(claim, "router_not_observed")
            return
        if not self._matches_expected_router(claim, router):
            self._record_error(claim, "router_configuration_mismatch")
            return
        with db() as conn:
            DomainClaimStore(conn).mark_routed(claim.id, claim.route_generation)
        logger.info("Custom domain router %s acknowledged", claim.route_name)

    def _reconcile_withdrawal(self, claim: DomainClaim) -> None:
        withdrawal_started_at = claim.removal_requested_at or claim.route_updated_at
        if not withdrawal_started_at or not self._withdrawal_snapshot_acknowledged(
            claim.route_name, withdrawal_started_at
        ):
            self._record_error(claim, "withdrawal_snapshot_not_acknowledged")
            return
        try:
            router = self._runtime_client.router(claim.route_name)
        except (OSError, ValueError):
            self._record_error(claim, "runtime_api_unavailable")
            return
        if router is not None:
            self._record_error(claim, "router_still_present")
            return
        with db() as conn:
            DomainClaimStore(conn).finish_withdrawal(claim.id, claim.route_generation)
        logger.info("Custom domain router %s withdrawal acknowledged", claim.route_name)

    def _matches_expected_router(self, claim: DomainClaim, router: dict) -> bool:
        tls = router.get("tls")
        return (
            isinstance(tls, dict)
            and router.get("status") == "enabled"
            and router.get("errors") in (None, [])
            and router.get("rule") == f"Host(`{claim.hostname}`)"
            and router.get("service") == self._service
            and router.get("entryPoints") == [self._https_entrypoint]
            and tls.get("certResolver") == self._cert_resolver
        )

    def _record_error(self, claim: DomainClaim, error: str) -> None:
        with db() as conn:
            changed = DomainClaimStore(conn).record_route_error(
                claim.id,
                claim.route_generation,
                error,
            )
        if changed:
            logger.warning("Custom domain router %s: %s", claim.route_name, error)
