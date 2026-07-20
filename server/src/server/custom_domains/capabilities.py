from __future__ import annotations

from dataclasses import dataclass

from .. import config


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


def domain_capabilities(app) -> DomainCapabilities:
    control = getattr(app.state, "traefik_control", None)
    runtime_ready = bool(control and control.is_ready())
    control_ready = bool(
        config.CUSTOM_DOMAINS_ENABLED
        and config.TRAEFIK_CONTROL_TOKEN
        and runtime_ready
    )
    routing_ready = bool(
        config.CUSTOM_DOMAIN_ROUTING_ENABLED
        and config.CUSTOM_DOMAIN_INGRESS_IPS
    )
    if not config.CUSTOM_DOMAINS_ENABLED:
        status = "disabled"
        detail = "Custom domains are not enabled on this Buzz server"
    elif not config.TRAEFIK_CONTROL_TOKEN or control is None:
        status = "unready"
        detail = "Custom domains are enabled but the control plane is not configured"
    elif not runtime_ready:
        status = "unready"
        detail = "Custom domain control plane is not ready"
    elif not config.CUSTOM_DOMAIN_ADMISSION_ENABLED:
        status = "unready"
        detail = "New custom domain claims are not enabled on this Buzz server"
    elif not routing_ready:
        status = "unready"
        detail = "Custom domain production routing is not configured"
    else:
        status = "ready"
        detail = None

    diagnostician = getattr(app.state, "cloudflare_diagnostician", None)
    range_state = getattr(app.state, "cloudflare_range_state", None)
    range_error = (
        diagnostician.range_error
        if diagnostician
        else range_state.error if range_state else "range_data_missing"
    )
    if range_error:
        cloudflare_detail = {
            "range_data_missing": "Cloudflare IP range data is missing",
            "range_data_invalid": "Cloudflare IP range data is invalid",
            "range_data_stale": "Cloudflare IP range data is stale",
        }.get(range_error, "Cloudflare IP range data is unavailable")
    else:
        cloudflare_detail = None
    if not config.CLOUDFLARE_DIAGNOSTICS_ENABLED:
        cloudflare_detail = "Cloudflare proxy diagnostics admission is not enabled"
    elif not config.CUSTOM_DOMAIN_ADMISSION_ENABLED:
        cloudflare_detail = "New custom domain claims are not enabled on this Buzz server"
    elif not config.CUSTOM_DOMAIN_ROUTING_ENABLED:
        cloudflare_detail = "Custom domain routing is not configured"
    elif not control_ready:
        cloudflare_detail = detail
    elif not getattr(app.state, "custom_domain_runtime_ready", False):
        cloudflare_detail = "Cloudflare diagnostic runtime is not configured"
    cloudflare_ready = cloudflare_detail is None

    automatic_admission = bool(
        getattr(app.state, "automatic_domain_transition_admission_enabled", False)
    )
    coordinator_ready = bool(
        getattr(app.state, "domain_transition_coordinator", None)
    )
    cloudflare_target_ready = bool(
        cloudflare_ready and config.CLOUDFLARE_ACTIVATION_ENABLED
    )
    automatic_ready = bool(
        automatic_admission
        and status == "ready"
        and cloudflare_target_ready
        and coordinator_ready
    )
    if not automatic_admission:
        automatic_detail = "Automatic domain transitions are not enabled"
    elif status != "ready":
        automatic_detail = detail
    elif not cloudflare_target_ready:
        automatic_detail = (
            cloudflare_detail
            or "Cloudflare activation is not enabled for automatic transitions"
        )
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
