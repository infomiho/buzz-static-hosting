from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..access import (
    AccessNotSiteOwner,
    AccessPolicyNotFound,
    AccessService,
    AccessSiteNotFound,
    InvalidAccessPattern,
)
from ..api_models import SiteAccessRequest, SiteAccessResponse
from ..auth_service import Identity
from ..dependencies import get_identity, require_user
from ..settings import Settings
from ..site_path import InvalidPath, normalized_url_path
from ..utils import extract_subdomain

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
router = APIRouter()


def _service(request: Request) -> AccessService:
    return request.app.state.access


def _raise_access_error(error: Exception) -> None:
    if isinstance(error, AccessSiteNotFound):
        raise HTTPException(status_code=404, detail="Site not found")
    if isinstance(error, AccessNotSiteOwner):
        raise HTTPException(status_code=403, detail="You do not own this site")
    if isinstance(error, InvalidAccessPattern):
        raise HTTPException(status_code=400, detail=str(error))
    raise error


def _site_for_hostname(request: Request, hostname: str) -> str | None:
    settings: Settings = request.app.state.settings
    canonical = extract_subdomain(hostname, settings.domain)
    if canonical:
        return canonical
    return request.app.state.custom_domains.activated_site(hostname)


def _validated_destination(
    request: Request, site_name: str, hostname: str, return_path: str
) -> tuple[str, str]:
    hostname = hostname.strip().lower().rstrip(".")
    if _site_for_hostname(request, hostname) != site_name:
        raise HTTPException(
            status_code=400, detail="This hostname does not serve the site"
        )
    if len(return_path) > 2048:
        raise HTTPException(status_code=400, detail="Return path is too long")
    parsed = urlsplit(return_path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise HTTPException(status_code=400, detail="Invalid return path")
    try:
        path = normalized_url_path(parsed.path)
    except InvalidPath:
        raise HTTPException(status_code=400, detail="Invalid return path")
    target = path + (f"?{parsed.query}" if parsed.query else "")
    return hostname, target


def _destination_origin(hostname: str, settings: Settings) -> str:
    scheme = "http" if settings.dev_mode else "https"
    port = ""
    if settings.dev_mode and settings.domain and ":" in settings.domain:
        port = f":{settings.domain.rsplit(':', 1)[1]}"
    elif settings.dev_mode and not settings.domain:
        port = ":8080"
    return f"{scheme}://{hostname}{port}"


@router.get(
    "/sites/{site_name}/access",
    response_model=SiteAccessResponse,
    operation_id="getSiteAccess",
    summary="Get owner access protection",
    tags=["Buzz Access"],
)
async def get_site_access(
    request: Request,
    site_name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    try:
        policy = _service(request).get_policy(site_name, identity.user.id)
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    return {
        "enabled": policy is not None,
        "patterns": list(policy.patterns) if policy else [],
    }


@router.put(
    "/sites/{site_name}/access",
    response_model=SiteAccessResponse,
    operation_id="setSiteAccess",
    summary="Enable or update owner access protection",
    tags=["Buzz Access"],
)
async def set_site_access(
    request: Request,
    site_name: str,
    data: SiteAccessRequest,
    identity: Annotated[Identity, Depends(require_user)],
):
    try:
        policy = _service(request).set_policy(
            site_name, identity.user.id, data.patterns
        )
    except (AccessSiteNotFound, AccessNotSiteOwner, InvalidAccessPattern) as error:
        _raise_access_error(error)
    return {"enabled": True, "patterns": list(policy.patterns)}


@router.delete(
    "/sites/{site_name}/access",
    status_code=204,
    operation_id="disableSiteAccess",
    summary="Disable owner access protection",
    tags=["Buzz Access"],
)
async def disable_site_access(
    request: Request,
    site_name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    try:
        _service(request).delete_policy(site_name, identity.user.id)
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    return Response(status_code=204)


@router.get("/access/authorize", response_class=HTMLResponse, include_in_schema=False)
async def authorize_access(
    request: Request,
    site: str = Query(),
    host: str = Query(),
    path: str = Query(),
    identity: Annotated[Identity | None, Depends(get_identity)] = None,
):
    hostname, return_path = _validated_destination(request, site, host, path)
    if not identity:
        next_path = f"/access/authorize?{urlencode({'site': site, 'host': hostname, 'path': return_path})}"
        return RedirectResponse(
            url=f"/?next={quote(next_path, safe='')}", status_code=303
        )
    if identity.token_type != "session":
        raise HTTPException(status_code=403, detail="A Buzz session is required")
    try:
        policy = _service(request).get_policy(site, identity.user.id)
    except AccessSiteNotFound:
        raise HTTPException(status_code=404, detail="Site not found")
    except AccessNotSiteOwner:
        return templates.TemplateResponse(
            request,
            "access_denied.html",
            {"hostname": hostname},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if not policy:
        raise HTTPException(status_code=409, detail="Buzz Access is not enabled")
    return templates.TemplateResponse(
        request,
        "access_authorize.html",
        {
            "site": site,
            "hostname": hostname,
            "return_path": return_path,
            "user": identity.user,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/access/authorize", response_class=HTMLResponse, include_in_schema=False)
async def confirm_access(
    request: Request,
    identity: Annotated[Identity, Depends(require_user)],
    site: str = Form(),
    host: str = Form(),
    path: str = Form(),
):
    hostname, return_path = _validated_destination(request, site, host, path)
    if not identity.session_id:
        raise HTTPException(status_code=403, detail="A Buzz session is required")
    try:
        code = _service(request).authorize_owner(
            site, hostname, return_path, identity.user.id, identity.session_id
        )
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    except AccessPolicyNotFound:
        raise HTTPException(status_code=409, detail="Buzz Access is not enabled")

    settings: Settings = request.app.state.settings
    callback = (
        _destination_origin(hostname, settings) + "/.well-known/buzz-access/callback"
    )
    return templates.TemplateResponse(
        request,
        "access_handoff.html",
        {"callback": callback, "code": code},
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; "
                f"form-action {_destination_origin(hostname, settings)}; "
                "script-src 'unsafe-inline'"
            ),
        },
    )
