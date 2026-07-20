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
from ..custom_domains.claims import (
    DnsTxtResolver,
    DomainCheckUnavailable,
    DomainClaimLimits,
    DomainClaimStore,
    DomainQuotaExceeded,
    InvalidHostname,
    normalize_hostname,
)
from ..custom_domains.cloudflare import CloudflareDiagnostic
from ..custom_domains.errors import ClaimConflict
from ..db import db
from ..custom_domains.capabilities import domain_capabilities
from ..custom_domains.views import ClaimView, build_claim_view, claim_views_for_site
from ..dependencies import (
    Identity,
    require_custom_domain_admission_enabled,
    require_custom_domain_control_ready,
    require_user,
)
from ..exceptions import BadRequest
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
        "ownership": {
            "status": diagnostic.ownership_status,
            "error": diagnostic.ownership_error,
        },
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
        "consecutive_failures": diagnostic.consecutive_failures,
    }


def domain_response(view: ClaimView) -> dict:
    claim = view.claim
    connection = view.connection
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
        "effective_mode": connection.effective_mode,
        "observed_mode": connection.observed_mode,
        "target_mode": connection.target_mode,
        "connection_status": connection.status,
        "transition_started_at": connection.transition_started_at,
        "transition_deadline_at": connection.transition_deadline_at,
        "transition_error": connection.transition_error,
        "cloudflare_diagnostics": cloudflare_diagnostic_response(view.diagnostic),
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
    capability = domain_capabilities(request.app)
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
    return {
        "status": capability.status,
        "detail": capability.detail,
        "enabled": config.CUSTOM_DOMAINS_ENABLED,
        "control_ready": capability.control_ready,
        "admission_enabled": config.CUSTOM_DOMAIN_ADMISSION_ENABLED,
        "routing_enabled": capability.routing_ready,
        "routing_targets": targets,
        "automatic": {
            "admission_enabled": bool(
                getattr(
                    request.app.state,
                    "automatic_domain_transition_admission_enabled",
                    False,
                )
            ),
            "ready": capability.automatic_ready,
            "detail": capability.automatic_detail,
        },
        "cloudflare": {
            "admission_enabled": config.CLOUDFLARE_DIAGNOSTICS_ENABLED,
            "activation_enabled": config.CLOUDFLARE_ACTIVATION_ENABLED,
            "ready": capability.cloudflare_ready,
            "detail": capability.cloudflare_detail,
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
        return [domain_response(view) for view in claim_views_for_site(conn, site_name)]


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
    capability = domain_capabilities(request.app)
    automatic = data.mode is None
    if automatic and not capability.automatic_ready:
        raise HTTPException(
            status_code=503,
            detail=capability.automatic_detail
            or "Automatic domain transitions are not ready",
        )
    claim_mode = data.mode or "direct"
    if claim_mode == "direct":
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
        diagnostician = getattr(request.app.state, "cloudflare_diagnostician", None)
        range_error = (
            diagnostician.range_error
            if diagnostician
            else request.app.state.cloudflare_range_state.error
        )
        if range_error:
            raise HTTPException(
                status_code=503,
                detail=f"Cloudflare diagnostics unavailable: {range_error}",
            )
    with db() as conn:
        try:
            claim = DomainClaimStore(conn).create(
                site_name,
                hostname,
                limits=domain_limits(),
                claim_mode=claim_mode,
                automatic_mode=automatic,
            )
        except DomainQuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return domain_response(build_claim_view(conn, claim))


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
        with db() as conn:
            return domain_response(build_claim_view(conn, claim))
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
        except ClaimConflict as exc:
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
            return domain_response(build_claim_view(conn, checked))
    with db() as conn:
        claim = DomainClaimStore(conn).record_check(claim_id, site_name, values)
        return domain_response(build_claim_view(conn, claim))


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


def transition_coordinator(request: Request):
    coordinator = getattr(request.app.state, "domain_transition_coordinator", None)
    if not coordinator:
        raise HTTPException(
            status_code=503, detail="Automatic domain transitions are not configured"
        )
    return coordinator


@router.post(
    "/{claim_id}/transition/retry",
    response_model=DomainClaimResponse,
    operation_id="retryDomainTransition",
    summary="Retry a custom domain transition",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def retry_domain_transition(
    request: Request,
    site_name: str,
    claim_id: int,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    coordinator = transition_coordinator(request)
    await run_in_threadpool(coordinator.retry, claim_id, site_name)
    with db() as conn:
        claim = DomainClaimStore(conn).get(claim_id, site_name)
        return domain_response(build_claim_view(conn, claim))


@router.post(
    "/{claim_id}/transition/cancel",
    response_model=DomainClaimResponse,
    operation_id="cancelDomainTransition",
    summary="Cancel a custom domain transition",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def cancel_domain_transition(
    request: Request,
    site_name: str,
    claim_id: int,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    coordinator = transition_coordinator(request)
    await run_in_threadpool(coordinator.cancel, claim_id, site_name)
    with db() as conn:
        claim = DomainClaimStore(conn).get(claim_id, site_name)
        return domain_response(build_claim_view(conn, claim))
