import re
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from ..templating import templates
from ..access import (
    AccessNotSiteOwner,
    AccessPolicy,
    AccessPolicyNotFound,
    AccessReaderIsOwner,
    AccessService,
    AccessSiteNotFound,
)
from ..api_models import (
    AddSiteReaderRequest,
    ErrorResponse,
    GitHubUserResponse,
    SiteAccessResponse,
    SiteReaderResponse,
)
from ..auth_service import AuthService, Identity
from ..dependencies import (
    get_auth_service,
    get_identity,
    require_authenticated_user,
    require_user,
)
from ..github import GitHubLookupFailed, GitHubUserNotFound
from ..github_login import GitHubUser
from ..settings import Settings
from ..site_path import InvalidPath, normalized_url_path
from ..utils import extract_subdomain

router = APIRouter()
GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
OWNER_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication is required."},
    403: {"model": ErrorResponse, "description": "Site ownership is required."},
    404: {"model": ErrorResponse, "description": "The site was not found."},
}
GITHUB_RESPONSES = {
    **OWNER_RESPONSES,
    400: {"model": ErrorResponse, "description": "The GitHub username is invalid."},
    404: {
        "model": ErrorResponse,
        "description": "The site or GitHub user was not found.",
    },
    409: {"model": ErrorResponse, "description": "The site is not private."},
    502: {"model": ErrorResponse, "description": "GitHub lookup failed."},
}


async def _github_profile(request: Request, login: str) -> dict:
    login = login.strip()
    if not GITHUB_LOGIN.fullmatch(login) or "--" in login:
        raise HTTPException(status_code=400, detail="Enter a valid GitHub username")
    try:
        profile = await run_in_threadpool(
            request.app.state.github_client.get_user_by_login, login
        )
    except GitHubUserNotFound:
        raise HTTPException(status_code=404, detail="GitHub user not found")
    except GitHubLookupFailed:
        raise HTTPException(status_code=502, detail="GitHub lookup failed, try again")
    if profile.get("type") != "User":
        raise HTTPException(status_code=400, detail="Only GitHub user accounts can be added")
    if not isinstance(profile.get("id"), int) or not isinstance(profile.get("login"), str):
        raise HTTPException(status_code=502, detail="GitHub returned an invalid user profile")
    return profile


def _require_private_owner(
    request: Request, site_name: str, owner_id: int
) -> AccessPolicy:
    try:
        policy = _service(request).get_policy(site_name, owner_id)
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    if not policy:
        raise HTTPException(status_code=409, detail="Make the site private first")
    return policy


def _service(request: Request) -> AccessService:
    return request.app.state.access


def _raise_access_error(error: Exception) -> None:
    if isinstance(error, AccessSiteNotFound):
        raise HTTPException(status_code=404, detail="Site not found")
    if isinstance(error, AccessNotSiteOwner):
        raise HTTPException(status_code=403, detail="You do not own this site")
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
    summary="Get site visibility",
    tags=["Access"],
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
    return {"private": policy is not None}


@router.put(
    "/sites/{site_name}/access",
    response_model=SiteAccessResponse,
    operation_id="makeSitePrivate",
    summary="Make a site private",
    tags=["Access"],
)
async def set_site_access(
    request: Request,
    site_name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    try:
        _service(request).set_policy(site_name, identity.user.id)
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    return {"private": True}


@router.delete(
    "/sites/{site_name}/access",
    status_code=204,
    operation_id="makeSitePublic",
    summary="Make a site public",
    tags=["Access"],
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


@router.get(
    "/sites/{site_name}/access/readers",
    response_model=list[SiteReaderResponse],
    operation_id="listSiteReaders",
    summary="List private-site readers",
    tags=["Access"],
    responses=OWNER_RESPONSES,
)
async def list_site_readers(
    request: Request,
    site_name: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    try:
        readers = _service(request).list_readers(site_name, identity.user.id)
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    return [
        {
            "id": reader.user_id,
            "github_login": reader.github_login,
            "github_name": reader.github_name,
            "avatar_url": reader.avatar_url,
        }
        for reader in readers
    ]


@router.post(
    "/sites/{site_name}/access/readers",
    response_model=SiteReaderResponse,
    operation_id="addSiteReader",
    summary="Add a private-site reader",
    tags=["Access"],
    responses=GITHUB_RESPONSES,
)
async def add_site_reader(
    request: Request,
    site_name: str,
    data: AddSiteReaderRequest,
    identity: Annotated[Identity, Depends(require_user)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    login = data.github_login.strip()
    _require_private_owner(request, site_name, identity.user.id)
    profile = await _github_profile(request, login)

    principal = auth.ensure_github_principal(
        GitHubUser(
            id=profile["id"],
            login=profile["login"],
            name=profile.get("name"),
            avatar_url=profile.get("avatar_url"),
        )
    )
    try:
        _service(request).add_reader(
            site_name, identity.user.id, principal.id, principal.github_login
        )
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    except AccessReaderIsOwner:
        raise HTTPException(status_code=409, detail="The site owner already has access")
    except AccessPolicyNotFound:
        raise HTTPException(status_code=409, detail="Make the site private first")
    return {
        "id": principal.id,
        "github_login": principal.github_login,
        "github_name": principal.github_name,
        "avatar_url": profile.get("avatar_url"),
    }


@router.get(
    "/sites/{site_name}/access/github-users/{github_login}",
    response_model=GitHubUserResponse,
    operation_id="resolveGitHubUser",
    summary="Resolve a GitHub user for private-site access",
    tags=["Access"],
    responses=GITHUB_RESPONSES,
)
async def resolve_github_user(
    request: Request,
    site_name: str,
    github_login: str,
    identity: Annotated[Identity, Depends(require_user)],
):
    _require_private_owner(request, site_name, identity.user.id)
    profile = await _github_profile(request, github_login)
    return {
        "github_id": profile["id"],
        "github_login": profile["login"],
        "github_name": profile.get("name"),
        "avatar_url": profile.get("avatar_url"),
    }


@router.delete(
    "/sites/{site_name}/access/readers/{reader_id}",
    status_code=204,
    operation_id="removeSiteReader",
    summary="Remove a private-site reader",
    tags=["Access"],
    responses={
        **OWNER_RESPONSES,
        409: {"model": ErrorResponse, "description": "The site is not private."},
    },
)
async def remove_site_reader(
    request: Request,
    site_name: str,
    reader_id: int,
    identity: Annotated[Identity, Depends(require_user)],
):
    try:
        _service(request).remove_reader(site_name, identity.user.id, reader_id)
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    except AccessPolicyNotFound:
        raise HTTPException(status_code=409, detail="This site is not private")
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
        policy = _service(request).reader_policy(site, identity.user.id)
    except AccessSiteNotFound:
        raise HTTPException(status_code=404, detail="Site not found")
    except AccessNotSiteOwner:
        return templates.TemplateResponse(
            request,
            "access_denied.html",
            {"hostname": hostname, "user": identity.user},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if not policy:
        raise HTTPException(status_code=409, detail="This site is not private")
    return templates.TemplateResponse(
        request,
        "access_authorize.html",
        {"site": site, "hostname": hostname, "return_path": return_path},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/access/authorize", response_class=HTMLResponse, include_in_schema=False)
async def confirm_access(
    request: Request,
    identity: Annotated[Identity, Depends(require_authenticated_user)],
    site: str = Form(),
    host: str = Form(),
    path: str = Form(),
):
    hostname, return_path = _validated_destination(request, site, host, path)
    if not identity.session_id:
        raise HTTPException(status_code=403, detail="A Buzz session is required")
    try:
        code = _service(request).authorize(
            site, hostname, return_path, identity.user.id, identity.session_id
        )
    except (AccessSiteNotFound, AccessNotSiteOwner) as error:
        _raise_access_error(error)
    except AccessPolicyNotFound:
        raise HTTPException(status_code=409, detail="This site is not private")

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
