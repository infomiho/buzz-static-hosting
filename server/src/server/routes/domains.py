import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .. import config
from ..api_models import (
    CreateDomainClaimRequest,
    CustomDomainCapabilityResponse,
    DomainClaimResponse,
    ErrorResponse,
)
from ..custom_domains import (
    DnsTxtResolver,
    DomainCheckUnavailable,
    DomainClaim,
    DomainClaimLimits,
    DomainClaimStore,
    DomainQuotaExceeded,
    InvalidHostname,
    normalize_hostname,
)
from ..cloudflare_diagnostics import (
    CloudflareDiagnostic,
    CloudflareDiagnosticStore,
    CloudflareRangeError,
    load_cloudflare_ranges,
)
from ..db import db
from ..dependencies import (
    Identity,
    require_custom_domain_admission_enabled,
    require_custom_domain_control_ready,
    require_user,
)
from ..exceptions import BadRequest, Conflict
from ..site_store import SiteStore

router = APIRouter(prefix="/sites/{site_name}/domains")
capabilities_router = APIRouter(prefix="/capabilities")


def domain_limits() -> DomainClaimLimits:
    return DomainClaimLimits(
        per_site=config.MAX_CUSTOM_DOMAINS_PER_SITE,
        per_user=config.MAX_CUSTOM_DOMAINS_PER_USER,
        server_wide=config.MAX_CUSTOM_DOMAINS_SERVER_WIDE,
    )


def cloudflare_diagnostic_response(diagnostic: CloudflareDiagnostic | None) -> dict | None:
    if not diagnostic:
        return None
    return {
        "generation": diagnostic.route_generation,
        "checked_at": diagnostic.checked_at,
        "ranges_version": diagnostic.ranges_version,
        "dns": {"status": diagnostic.dns_status, "error": diagnostic.dns_error},
        "edge_tls": {
            "status": diagnostic.edge_tls_status,
            "error": diagnostic.edge_tls_error,
        },
        "edge_http": {
            "status": diagnostic.edge_http_status,
            "error": diagnostic.edge_http_error,
            "status_code": diagnostic.edge_http_status_code,
            "address": diagnostic.edge_address,
            "cf_ray": diagnostic.cf_ray,
            "cf_cache_status": diagnostic.cf_cache_status,
            "redirect_location": diagnostic.redirect_location,
        },
        "http_forwarding": {
            "status": diagnostic.http_forward_status,
            "error": diagnostic.http_forward_error,
            "status_code": diagnostic.http_forward_status_code,
        },
        "origin": {
            "status": diagnostic.origin_status,
            "error": diagnostic.origin_error,
        },
    }


def domain_response(
    claim: DomainClaim, diagnostic: CloudflareDiagnostic | None = None
) -> dict:
    return {
        "id": claim.id,
        "hostname": claim.hostname,
        "site_name": claim.site_name,
        "status": claim.status,
        "verification": {
            "type": "TXT",
            "name": claim.verification_name,
            "value": claim.verification_value,
        },
        "created_at": claim.created_at,
        "expires_at": claim.expires_at,
        "verified_at": claim.verified_at,
        "last_checked_at": claim.last_checked_at,
        "last_error": claim.last_error,
        "route_status": claim.route_status,
        "route_generation": claim.route_generation,
        "route_error": claim.route_error,
        "challenge_path": claim.challenge_path,
        "challenge_seen_at": claim.challenge_seen_at,
        "activated_at": claim.activated_at,
        "activation_checked_at": claim.activation_checked_at,
        "activation_error": claim.activation_error,
        "removal_requested_at": claim.removal_requested_at,
        "withdrawn_at": claim.withdrawn_at,
        "mode": claim.claim_mode,
        "cloudflare_diagnostics": cloudflare_diagnostic_response(diagnostic),
    }


@capabilities_router.get(
    "/custom-domains",
    response_model=CustomDomainCapabilityResponse,
    operation_id="getCustomDomainCapability",
    summary="Check custom-domain capability",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def custom_domain_capability(
    request: Request,
    _identity: Annotated[Identity, Depends(require_user)],
):
    control = getattr(request.app.state, "traefik_control", None)
    runtime_ready = bool(control and control.is_ready())
    diagnostic_runtime_ready = bool(
        getattr(request.app.state, "custom_domain_runtime_ready", False)
    )
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
    targets = [
        {
            "type": "A" if ipaddress.ip_address(address).version == 4 else "AAAA",
            "value": address,
        }
        for address in sorted(
            config.CUSTOM_DOMAIN_INGRESS_IPS,
            key=lambda value: (ipaddress.ip_address(value).version, value),
        )
    ]
    cloudflare_detail = None
    try:
        load_cloudflare_ranges()
    except CloudflareRangeError as exc:
        cloudflare_detail = {
            "range_data_missing": "Cloudflare IP range data is missing",
            "range_data_invalid": "Cloudflare IP range data is invalid",
            "range_data_stale": "Cloudflare IP range data is stale",
        }[exc.code]
    if not config.CLOUDFLARE_DIAGNOSTICS_ENABLED:
        cloudflare_detail = "Cloudflare proxy diagnostics admission is not enabled"
    elif not config.CUSTOM_DOMAIN_ADMISSION_ENABLED:
        cloudflare_detail = "New custom domain claims are not enabled on this Buzz server"
    elif not config.CUSTOM_DOMAIN_ROUTING_ENABLED:
        cloudflare_detail = "Custom domain routing is not configured"
    elif not control_ready:
        cloudflare_detail = detail
    elif not diagnostic_runtime_ready:
        cloudflare_detail = "Cloudflare diagnostic runtime is not configured"
    return {
        "status": status,
        "detail": detail,
        "enabled": config.CUSTOM_DOMAINS_ENABLED,
        "control_ready": control_ready,
        "admission_enabled": config.CUSTOM_DOMAIN_ADMISSION_ENABLED,
        "routing_enabled": routing_ready,
        "routing_targets": targets,
        "cloudflare": {
            "admission_enabled": config.CLOUDFLARE_DIAGNOSTICS_ENABLED,
            "ready": cloudflare_detail is None,
            "detail": cloudflare_detail,
        },
    }


def require_owned_site(site_name: str, owner_id: int) -> None:
    with db() as conn:
        SiteStore(conn, config.SITES_DIR).get_by_name(site_name, owner_id)


@router.get(
    "",
    response_model=list[DomainClaimResponse],
    operation_id="listDomainClaims",
    summary="List custom domain claims",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def list_domain_claims(
    site_name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    with db() as conn:
        claims = DomainClaimStore(conn).list_for_site(site_name)
        diagnostic_store = CloudflareDiagnosticStore(conn)
        return [
            domain_response(
                claim,
                diagnostic_store.get(claim.id, claim.route_generation)
                if claim.claim_mode == "cloudflare"
                else None,
            )
            for claim in claims
        ]


@router.post(
    "",
    response_model=DomainClaimResponse,
    status_code=201,
    operation_id="createDomainClaim",
    summary="Create a custom domain claim",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    dependencies=[
        Depends(require_custom_domain_control_ready),
    ],
)
async def create_domain_claim(
    request: Request,
    site_name: str,
    data: CreateDomainClaimRequest,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    try:
        hostname = normalize_hostname(data.hostname, config.DOMAIN)
    except InvalidHostname as exc:
        raise BadRequest(str(exc))
    if data.mode == "direct":
        require_custom_domain_admission_enabled()
    else:
        if not config.CUSTOM_DOMAIN_ADMISSION_ENABLED:
            raise HTTPException(
                status_code=503,
                detail="New custom domain claims are not enabled on this Buzz server",
            )
        if not config.CLOUDFLARE_DIAGNOSTICS_ENABLED:
            raise HTTPException(
                status_code=503,
                detail="Cloudflare proxy diagnostics admission is not enabled",
            )
        if not getattr(request.app.state, "custom_domain_runtime_ready", False):
            raise HTTPException(
                status_code=503,
                detail="Cloudflare diagnostic runtime is not configured",
            )
        if not config.CUSTOM_DOMAIN_ROUTING_ENABLED:
            raise HTTPException(
                status_code=503,
                detail="Custom domain routing is not configured",
            )
        try:
            load_cloudflare_ranges()
        except CloudflareRangeError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Cloudflare diagnostics unavailable: {exc.code}",
            ) from exc
    with db() as conn:
        try:
            claim = DomainClaimStore(conn).create(
                site_name,
                hostname,
                limits=domain_limits(),
                claim_mode=data.mode,
            )
        except DomainQuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return domain_response(claim)


@router.post(
    "/{claim_id}/check",
    response_model=DomainClaimResponse,
    operation_id="checkDomainClaim",
    summary="Check custom domain ownership",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_custom_domain_control_ready)],
)
async def check_domain_claim(
    request: Request,
    site_name: str,
    claim_id: int,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    with db() as conn:
        claim = DomainClaimStore(conn).get(claim_id, site_name)
    if claim.status == "verified":
        return domain_response(claim)
    retry_after = claim.check_retry_after()
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Wait before checking this custom domain again",
            headers={"Retry-After": str(retry_after)},
        )
    with db() as conn:
        try:
            claim = DomainClaimStore(conn).reserve_check(claim_id, site_name)
        except Conflict as exc:
            raise HTTPException(
                status_code=429,
                detail="Wait before checking this custom domain again",
                headers={"Retry-After": "60"},
            ) from exc
    resolver: DnsTxtResolver = request.app.state.domain_txt_resolver
    try:
        values = await run_in_threadpool(resolver.lookup, claim.verification_name)
    except DomainCheckUnavailable:
        with db() as conn:
            checked = DomainClaimStore(conn).record_check_error(
                claim_id, site_name, "dns_unavailable"
            )
        return domain_response(checked)
    with db() as conn:
        return domain_response(
            DomainClaimStore(conn).record_check(claim_id, site_name, values)
        )


@router.delete(
    "/{claim_id}",
    status_code=204,
    operation_id="cancelDomainClaim",
    summary="Cancel a custom domain claim",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def cancel_domain_claim(
    site_name: str,
    claim_id: int,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    with db() as conn:
        pending_withdrawal = DomainClaimStore(conn).cancel(
            claim_id,
            site_name,
        )
    if pending_withdrawal:
        return Response(status_code=202)
    return Response(status_code=204)
