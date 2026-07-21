import asyncio
import ipaddress
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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
from ..cookies import COOKIE_NAME, set_session_cookie, clear_session_cookie
from ..custom_domains import DomainClaimLimits, DomainClaimStore, claim_views_for_site
from ..db import Database
from ..dependencies import get_auth_service, get_database, get_settings, require_user
from ..search_console import SearchConsoleError
from ..settings import Settings
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
    settings: Annotated[Settings, Depends(get_settings)],
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
    set_session_cookie(response, result.token, secure=not settings.dev_mode)
    return response


@router.get("/sites/{name}", response_class=HTMLResponse)
async def site_detail(
    request: Request,
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    domain = settings.domain or "localhost:8080"
    capability = request.app.state.custom_domains.capabilities()
    custom_domains_available = capability.control_ready
    with database.connect() as conn:
        store = SiteStore(conn, settings.sites_dir)
        site = store.get_by_name(name, identity.user.id)
        files = store.list_files(name, identity.user.id)
        claim_store = DomainClaimStore(conn)
        views = claim_views_for_site(
            conn, name, statuses=frozenset({"pending", "verified"})
        )
        domain_claims = [view.claim for view in views]
        domain_connections = {view.claim.id: view.connection for view in views}
        domain_tasks = {view.claim.id: view.task for view in views}
        cloudflare_diagnostics = {
            view.claim.id: view.diagnostic
            for view in views
            if view.diagnostic is not None
        }
        domain_quota = claim_store.quota(
            name,
            DomainClaimLimits(
                per_site=settings.max_custom_domains_per_site,
                per_user=settings.max_custom_domains_per_user,
                server_wide=settings.max_custom_domains_server_wide,
            ),
        )

    custom_domain_can_add = capability.automatic_ready and not domain_quota.error
    domain_routing_targets = [
        {
            "type": "A" if ipaddress.ip_address(address).version == 4 else "AAAA",
            "value": address,
        }
        for address in sorted(
            settings.custom_domain_ingress_ips,
            key=lambda value: (ipaddress.ip_address(value).version, value),
        )
    ]

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
        "domain_routing_targets": domain_routing_targets,
        "custom_domain_quota": domain_quota,
        "domain_claims": domain_claims,
        "domain_connections": domain_connections,
        "domain_tasks": domain_tasks,
        "cloudflare_diagnostics": cloudflare_diagnostics,
    })


@router.get("/sites/{name}/analytics")
async def site_analytics(
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    with database.connect() as conn:
        SiteStore(conn, settings.sites_dir).get_by_name(name, identity.user.id)
        return AnalyticsStore(conn).summary(name)


@router.get("/sites/{name}/search-terms")
async def site_search_terms(
    request: Request,
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    with database.connect() as conn:
        SiteStore(conn, settings.sites_dir).get_by_name(name, identity.user.id)

    client = request.app.state.search_console
    if not client:
        return {"configured": False, "terms": []}

    end = date.today() - timedelta(days=SEARCH_TERMS_LAG_DAYS)
    start = end - timedelta(days=SEARCH_TERMS_WINDOW_DAYS - 1)
    domain = settings.domain or "localhost:8080"
    try:
        terms = await asyncio.to_thread(client.query_search_terms, f"{name}.{domain}", start, end)
    except SearchConsoleError:
        raise HTTPException(status_code=502, detail="Search Console request failed")
    return {"configured": True, "terms": terms}


@router.post("/logout")
async def logout(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            auth.logout(f"Bearer {cookie_token}")
        except Exception:
            logger.warning("Failed to revoke session on logout", exc_info=True)

    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response, secure=not settings.dev_mode)
    return response
