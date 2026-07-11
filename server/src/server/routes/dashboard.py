import asyncio
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
from ..config import DOMAIN, SITES_DIR
from ..cookies import COOKIE_NAME, set_session_cookie, clear_session_cookie
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
    with db() as conn:
        store = SiteStore(conn, SITES_DIR)
        site = store.get_by_name(name, identity.user.id)
        files = store.list_files(name, identity.user.id)

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
