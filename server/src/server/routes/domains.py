from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .. import config
from ..api_models import CreateDomainClaimRequest, DomainClaimResponse, ErrorResponse
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


def domain_limits() -> DomainClaimLimits:
    return DomainClaimLimits(
        per_site=config.MAX_CUSTOM_DOMAINS_PER_SITE,
        per_user=config.MAX_CUSTOM_DOMAINS_PER_USER,
        server_wide=config.MAX_CUSTOM_DOMAINS_SERVER_WIDE,
    )


def domain_response(claim: DomainClaim) -> dict:
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
        503: {"model": ErrorResponse},
    },
)
async def list_domain_claims(
    site_name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    with db() as conn:
        return [domain_response(claim) for claim in DomainClaimStore(conn).list_for_site(site_name)]


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
        Depends(require_custom_domain_admission_enabled),
    ],
)
async def create_domain_claim(
    site_name: str,
    data: CreateDomainClaimRequest,
    identity: Annotated[Identity, Depends(require_user)],
):
    require_owned_site(site_name, identity.user.id)
    try:
        hostname = normalize_hostname(data.hostname, config.DOMAIN)
    except InvalidHostname as exc:
        raise BadRequest(str(exc))
    with db() as conn:
        try:
            claim = DomainClaimStore(conn).create(
                site_name, hostname, limits=domain_limits()
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
        503: {"model": ErrorResponse},
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
