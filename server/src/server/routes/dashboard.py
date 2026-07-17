import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import config
from ..analytics import AnalyticsStore
from ..auth_service import (
    AuthService,
    DeviceFlowDenied,
    DeviceFlowExpired,
    DeviceFlowFailed,
    DeviceFlowPending,
    DeviceFlowSlowDown,
    Identity,
)
from ..config import DOMAIN, SITES_DIR
from ..cookies import COOKIE_NAME, set_session_cookie, clear_session_cookie
from ..custom_domains import DomainClaimLimits, DomainClaimStore
from ..cloudflare_diagnostics import (
    CloudflareDiagnosticStore,
    CloudflareRangeError,
    load_cloudflare_ranges,
)
from ..db import db
from ..dependencies import get_auth_service, require_user
from ..search_console import SearchConsoleError
from ..site_store import SiteStore

SEARCH_TERMS_LAG_DAYS = 2
SEARCH_TERMS_WINDOW_DAYS = 30

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", include_in_schema=False)


class PollRequest(BaseModel):
    device_code: str


@router.post("/login/start")
async def login_start(auth: Annotated[AuthService, Depends(get_auth_service)]):
    try:
        return auth.start_device_flow()
    except DeviceFlowFailed as e:
        raise HTTPException(status_code=500, detail=e.detail)


@router.post("/login/poll")
async def login_poll(
    data: PollRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    try:
        result = auth.poll_device_flow(data.device_code)
    except DeviceFlowPending:
        return {"status": "pending"}
    except DeviceFlowSlowDown as e:
        return {"status": "pending", "interval": e.interval}
    except DeviceFlowExpired:
        raise HTTPException(status_code=400, detail="Device code expired")
    except DeviceFlowDenied:
        raise HTTPException(status_code=400, detail="User denied access")
    except DeviceFlowFailed as e:
        raise HTTPException(status_code=400, detail=e.detail)

    response = JSONResponse(content={"status": "complete"})
    set_session_cookie(response, result.token)
    return response


@router.get("/sites/{name}", response_class=HTMLResponse)
async def site_detail(
    request: Request,
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    domain = DOMAIN or "localhost:8080"
    control = getattr(request.app.state, "traefik_control", None)
    custom_domains_available = bool(
        config.CUSTOM_DOMAINS_ENABLED
        and control
        and control.is_ready()
    )
    with db() as conn:
        store = SiteStore(conn, SITES_DIR)
        site = store.get_by_name(name, identity.user.id)
        files = store.list_files(name, identity.user.id)
        claim_store = DomainClaimStore(conn)
        domain_claims = [
            claim
            for claim in claim_store.list_for_site(name)
            if claim.status in {"pending", "verified"}
        ]
        diagnostic_store = CloudflareDiagnosticStore(conn)
        cloudflare_diagnostics = {
            claim.id: diagnostic_store.get(claim.id, claim.route_generation)
            for claim in domain_claims
            if claim.claim_mode == "cloudflare"
        }
        domain_quota = claim_store.quota(
            name,
            DomainClaimLimits(
                per_site=config.MAX_CUSTOM_DOMAINS_PER_SITE,
                per_user=config.MAX_CUSTOM_DOMAINS_PER_USER,
                server_wide=config.MAX_CUSTOM_DOMAINS_SERVER_WIDE,
            ),
        )

    direct_domains_available = bool(
        custom_domains_available
        and config.CUSTOM_DOMAIN_ADMISSION_ENABLED
        and config.CUSTOM_DOMAIN_ROUTING_ENABLED
        and config.CUSTOM_DOMAIN_INGRESS_IPS
        and not domain_quota.error
    )
    try:
        load_cloudflare_ranges()
        ranges_ready = True
    except CloudflareRangeError:
        ranges_ready = False
    cloudflare_diagnostics_available = bool(
        custom_domains_available
        and getattr(request.app.state, "custom_domain_runtime_ready", False)
        and config.CUSTOM_DOMAIN_ADMISSION_ENABLED
        and config.CUSTOM_DOMAIN_ROUTING_ENABLED
        and config.CLOUDFLARE_DIAGNOSTICS_ENABLED
        and ranges_ready
        and not domain_quota.error
    )
    custom_domain_can_add = direct_domains_available or cloudflare_diagnostics_available

    if domain and domain != "localhost:8080":
        site_url = f"https://{name}.{domain}"
    else:
        site_url = f"http://{name}.localhost:8080"

    return templates.TemplateResponse(request, "site_detail.html", {
        "user": identity.user,
        "site": site,
        "site_url": site_url,
        "files": files,
        "domain": domain,
        "custom_domains_available": custom_domains_available,
        "custom_domain_can_add": custom_domain_can_add,
        "direct_domains_available": direct_domains_available,
        "cloudflare_diagnostics_available": cloudflare_diagnostics_available,
        "cloudflare_activation_enabled": config.CLOUDFLARE_ACTIVATION_ENABLED,
        "custom_domain_quota": domain_quota,
        "domain_claims": domain_claims,
        "cloudflare_diagnostics": cloudflare_diagnostics,
    })


@router.get("/sites/{name}/analytics")
async def site_analytics(
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    with db() as conn:
        SiteStore(conn, SITES_DIR).get_by_name(name, identity.user.id)
        return AnalyticsStore(conn).summary(name)


@router.get("/sites/{name}/search-terms")
async def site_search_terms(
    request: Request,
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    with db() as conn:
        SiteStore(conn, SITES_DIR).get_by_name(name, identity.user.id)

    client = request.app.state.search_console
    if not client:
        return {"configured": False, "terms": []}

    end = date.today() - timedelta(days=SEARCH_TERMS_LAG_DAYS)
    start = end - timedelta(days=SEARCH_TERMS_WINDOW_DAYS - 1)
    domain = DOMAIN or "localhost:8080"
    try:
        terms = await asyncio.to_thread(client.query_search_terms, f"{name}.{domain}", start, end)
    except SearchConsoleError:
        raise HTTPException(status_code=502, detail="Search Console request failed")
    return {"configured": True, "terms": terms}


@router.post("/logout")
async def logout(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            auth.logout(f"Bearer {cookie_token}")
        except Exception:
            logger.warning("Failed to revoke session on logout", exc_info=True)

    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    return response
