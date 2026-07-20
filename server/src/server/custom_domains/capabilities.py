from __future__ import annotations

from dataclasses import dataclass

from .. import config

_RANGE_DETAIL = {
    "range_data_missing": "Cloudflare IP range data is missing",
    "range_data_invalid": "Cloudflare IP range data is invalid",
    "range_data_stale": "Cloudflare IP range data is stale",
}


@dataclass(frozen=True)
class DomainCapabilities:
    status: str
    detail: str | None
    control_ready: bool
    routing_ready: bool
    cloudflare_ready: bool
    cloudflare_detail: str | None
    automatic_ready: bool
    automatic_detail: str | None


def compute_capabilities(
    *,
    control,
    diagnostician,
    range_state,
    diagnostic_runtime_ready: bool,
    coordinator,
) -> DomainCapabilities:
    runtime_ready = bool(control and control.is_ready())
    control_ready = bool(
        config.CUSTOM_DOMAINS_ENABLED
        and config.TRAEFIK_CONTROL_TOKEN
        and runtime_ready
    )
    routing_ready = bool(config.CUSTOM_DOMAIN_INGRESS_IPS)

    if not config.CUSTOM_DOMAINS_ENABLED:
        status = "disabled"
        detail = "Custom domains are not enabled on this Buzz server"
    elif not config.TRAEFIK_CONTROL_TOKEN or control is None:
        status = "unready"
        detail = "Custom domains are enabled but the control plane is not configured"
    elif not runtime_ready:
        status = "unready"
        detail = "Custom domain control plane is not ready"
    elif not routing_ready:
        status = "unready"
        detail = "Custom domain production routing is not configured"
    else:
        status = "ready"
        detail = None

    # Cloudflare support is derived from runtime health, not an operator flag:
    # the full reconcile runtime must be up and the bundled IP ranges valid.
    range_error = (
        diagnostician.range_error
        if diagnostician
        else range_state.error if range_state else "range_data_missing"
    )
    if not control_ready:
        cloudflare_detail = detail
    elif not diagnostic_runtime_ready:
        cloudflare_detail = "Cloudflare diagnostic runtime is not configured"
    elif range_error:
        cloudflare_detail = _RANGE_DETAIL.get(
            range_error, "Cloudflare IP range data is unavailable"
        )
    else:
        cloudflare_detail = None
    cloudflare_ready = cloudflare_detail is None

    # Automatic onboarding is the only path and is independent of Cloudflare:
    # a server without Cloudflare still onboards direct domains automatically.
    coordinator_ready = bool(coordinator)
    automatic_ready = bool(status == "ready" and coordinator_ready)
    if status != "ready":
        automatic_detail = detail
    elif not coordinator_ready:
        automatic_detail = "Automatic domain transition runtime is not configured"
    else:
        automatic_detail = None

    return DomainCapabilities(
        status,
        detail,
        control_ready,
        routing_ready,
        cloudflare_ready,
        cloudflare_detail,
        automatic_ready,
        automatic_detail,
    )
