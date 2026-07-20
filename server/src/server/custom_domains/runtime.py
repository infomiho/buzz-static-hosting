"""The custom-domains runtime: one object owning wiring order, the reconcile
loop, startup guards, capabilities, and request-time lookups. The host holds a
single instance and never learns the collaborators behind it."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Callable

from .. import config as server_config
from .activation import DomainActivator
from .capabilities import DomainCapabilities, compute_capabilities
from .claims import DnsTxtResolver, DomainClaimStore
from .cloudflare import CloudflareDiagnostician
from .config import CustomDomainsConfig
from .errors import ClaimNotFound
from .evidence import DomainDnsObserver, DomainEvidenceCollector
from .probes import CloudflareRangeError, CloudflareRangeState, load_cloudflare_ranges
from .routing import DomainRouteReconciler, build_traefik_snapshot
from .traefik import TraefikControlServer, TraefikRuntimeClient
from .transitions import (
    DomainClaimStateMachine,
    DomainTransitionCoordinator,
    TransitionValidationFailed,
)

logger = logging.getLogger(__name__)

DOMAIN_CHECK_PREFIX = "/.well-known/buzz-domain-check/"


class CustomDomainsRuntime:
    def __init__(self, config: CustomDomainsConfig, connect: Callable):
        self._config = config
        self._connect = connect
        self.txt_resolver = DnsTxtResolver()
        self.control: TraefikControlServer | None = None
        self.runtime_ready = False
        self.range_state = CloudflareRangeState(load_error="range_data_missing")
        self.diagnostician: CloudflareDiagnostician | None = None
        self.transition_coordinator: DomainTransitionCoordinator | None = None
        self.automatic_admission_enabled = config.automatic_admission_enabled
        self._reconciler_task: asyncio.Task | None = None
        self._stop_reconciler = asyncio.Event()

    # -- capabilities ----------------------------------------------------

    def capabilities(self) -> DomainCapabilities:
        return compute_capabilities(
            control=self.control,
            diagnostician=self.diagnostician,
            range_state=self.range_state,
            diagnostic_runtime_ready=self.runtime_ready,
            coordinator=self.transition_coordinator,
            automatic_admission=self.automatic_admission_enabled,
        )

    # -- request-time lookups --------------------------------------------

    def resolve_challenge(
        self, hostname: str | None, path: str
    ) -> tuple[int, str, str] | None:
        if (
            not server_config.CUSTOM_DOMAINS_ENABLED
            or not server_config.CUSTOM_DOMAIN_ROUTING_ENABLED
            or not hostname
            or not path.startswith(DOMAIN_CHECK_PREFIX)
        ):
            return None
        token = path.removeprefix(DOMAIN_CHECK_PREFIX)
        if not token or "/" in token:
            return None
        with self._connect() as conn:
            store = DomainClaimStore(conn)
            claim = store.find_challenge(hostname.lower().rstrip("."), token)
            if claim:
                store.mark_challenge_seen(claim.id, claim.route_generation)
        if not claim or not claim.site_name:
            return None
        return claim.id, claim.site_name, token

    def activated_site(self, hostname: str | None) -> str | None:
        if (
            not server_config.CUSTOM_DOMAINS_ENABLED
            or not server_config.CUSTOM_DOMAIN_ROUTING_ENABLED
            or not hostname
        ):
            return None
        with self._connect() as conn:
            claim = DomainClaimStore(conn).find_activated(hostname.lower().rstrip("."))
        return claim.site_name if claim else None

    def activated_hostnames_for_site(self, site_name: str) -> frozenset[str]:
        with self._connect() as conn:
            return DomainClaimStore(conn).activated_hostnames_for_site(site_name)

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        config = self._config
        self._refuse_unsafe_startup()
        if not (config.custom_domains_enabled and config.traefik_control_token):
            return
        with self._connect() as conn:
            DomainClaimStore(conn).prepare_routes(config.custom_domain_routing_enabled)
        runtime_client = None
        if config.traefik_api_url:
            runtime_client = TraefikRuntimeClient(
                config.traefik_api_url,
                config.traefik_api_authorization,
                config.traefik_https_entrypoint,
                config.traefik_service,
            )
        control_server = TraefikControlServer(
            config.traefik_control_token,
            config.traefik_control_port,
            runtime_client,
            snapshot_provider=lambda: build_traefik_snapshot(
                config.traefik_https_entrypoint,
                config.traefik_service,
                config.traefik_cert_resolver,
            ),
            operator_token=config.custom_domain_operator_token,
        )
        self.control = control_server
        if runtime_client:
            self._start_reconciliation(runtime_client, control_server)
        control_server.start()

    def _start_reconciliation(
        self,
        runtime_client: TraefikRuntimeClient,
        control_server: TraefikControlServer,
    ) -> None:
        config = self._config
        self.runtime_ready = True
        try:
            transition_ranges = load_cloudflare_ranges()
            transition_range_error = None
        except CloudflareRangeError as exc:
            transition_ranges = None
            transition_range_error = exc.code
        self.range_state = CloudflareRangeState(transition_ranges, transition_range_error)
        transition_observer = DomainDnsObserver(
            ingress_addresses=config.custom_domain_ingress_ips,
            cloudflare_range_state=self.range_state,
        )
        reconciler = DomainRouteReconciler(
            runtime_client,
            config.traefik_https_entrypoint,
            config.traefik_service,
            config.traefik_cert_resolver,
            routing_enabled=lambda: server_config.CUSTOM_DOMAIN_ROUTING_ENABLED,
            withdrawal_snapshot_acknowledged=(
                control_server.withdrawal_snapshot_acknowledged
            ),
        )

        def validate_transition_router(claim) -> None:
            try:
                router = runtime_client.router(claim.route_name)
            except (OSError, ValueError) as exc:
                raise TransitionValidationFailed(
                    "runtime_api_unavailable", transient=True
                ) from exc
            if router is None:
                raise TransitionValidationFailed("router_not_observed")
            if not reconciler.matches_expected_router(claim, router):
                raise TransitionValidationFailed("router_configuration_mismatch")

        evidence_collector = DomainEvidenceCollector(
            transition_observer,
            config.custom_domain_origin_host,
            validate_transition_router,
            cloudflare_range_state=self.range_state,
        )
        activator = DomainActivator(evidence_collector=evidence_collector)
        self.diagnostician = CloudflareDiagnostician(
            evidence_collector,
            activation_enabled=config.cloudflare_activation_enabled,
            range_state=self.range_state,
        )
        self.transition_coordinator = DomainTransitionCoordinator(
            evidence_collector,
            self.diagnostician,
            admission_enabled=lambda: self.capabilities().automatic_ready,
            cloudflare_target_enabled=lambda: bool(
                self.capabilities().cloudflare_ready
                and server_config.CLOUDFLARE_ACTIVATION_ENABLED
            ),
        )
        control_server.set_operator_handlers(
            self._active_handoffs,
            self._cancel_operator_transition,
        )
        diagnostician = self.diagnostician
        coordinator = self.transition_coordinator

        async def reconcile_routes() -> None:
            while not self._stop_reconciler.is_set():
                started_at = asyncio.get_running_loop().time()
                try:
                    await asyncio.to_thread(control_server.refresh_readiness)
                    await asyncio.to_thread(reconciler.run_once)
                    await asyncio.to_thread(coordinator.run_once)
                    await asyncio.to_thread(activator.run_once)
                    await asyncio.to_thread(diagnostician.run_once)
                except Exception:
                    logger.exception("Custom domain reconciliation failed")
                try:
                    elapsed = asyncio.get_running_loop().time() - started_at
                    interval = (
                        random.uniform(0.8, 1.2)
                        * config.custom_domain_reconcile_seconds
                    )
                    await asyncio.wait_for(
                        self._stop_reconciler.wait(),
                        timeout=max(0, interval - elapsed),
                    )
                except TimeoutError:
                    pass

        self._reconciler_task = asyncio.create_task(reconcile_routes())

    async def stop(self) -> None:
        if self._reconciler_task:
            self._stop_reconciler.set()
            try:
                await self._reconciler_task
            except Exception:
                logger.exception("Custom domain reconciler shutdown failed")
            self._reconciler_task = None
        if self.control:
            try:
                self.control.stop()
            finally:
                self.control = None
                self.runtime_ready = False
                self.transition_coordinator = None

    # -- internal --------------------------------------------------------

    def _refuse_unsafe_startup(self) -> None:
        config = self._config
        if not config.cloudflare_activation_enabled:
            with self._connect() as conn:
                if DomainClaimStore(conn).has_active_cloudflare_claim():
                    raise RuntimeError(
                        "Withdraw active Cloudflare routers before disabling Cloudflare activation"
                    )
        else:
            with self._connect() as conn:
                routed_cloudflare_claim = DomainClaimStore(
                    conn
                ).has_routed_cloudflare_claim()
            if routed_cloudflare_claim and not (
                config.custom_domains_enabled
                and config.traefik_control_token
                and config.traefik_api_url
            ):
                raise RuntimeError(
                    "Active Cloudflare domains require the complete custom-domain runtime"
                )
        if not config.custom_domains_enabled:
            with self._connect() as conn:
                if DomainClaimStore(conn).has_routed_claim():
                    raise RuntimeError(
                        "Withdraw all custom-domain routers before disabling custom domains"
                    )

    def _active_handoffs(self) -> list[dict]:
        with self._connect() as conn:
            return DomainClaimStateMachine(conn).active_handoffs()

    def _cancel_operator_transition(self, claim_id: int) -> dict:
        with self._connect() as conn:
            site_name = DomainClaimStore(conn).site_name_for(claim_id)
        if not site_name:
            raise ClaimNotFound("Custom domain claim not found")
        self.transition_coordinator.cancel(claim_id, site_name)
        return {"claim_id": claim_id, "state": "cancelled"}
