import ipaddress
import logging
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..templating import templates
from ..analytics import AnalyticsStore
from ..auth_service import AccessDenied, AuthService, Identity, InvalidSession
from ..cookies import (
    clear_session_cookie,
    oauth_browser_cookie_name,
    session_cookie_name,
    set_oauth_browser_cookie,
    set_session_cookie,
)
from ..custom_domains import DomainClaimLimits, DomainClaimStore, claim_views_for_site
from ..db import Database
from ..dependencies import (
    get_auth_service,
    get_database,
    get_github_oauth,
    get_passkey_service,
    get_settings,
    require_user,
)
from ..github_login import (
    GitHubOAuth,
    GitHubOAuthDenied,
    GitHubOAuthInvalidResponse,
    GitHubOAuthInvalidState,
    GitHubOAuthNotConfigured,
    GitHubOAuthUnavailable,
)
from ..passkeys import AuthenticationFailed, ChallengeExpired, PasskeyService
from ..settings import Settings
from ..site_store import SiteStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", include_in_schema=False)


def _session_response(token: str, settings: Settings) -> JSONResponse:
    response = JSONResponse(content={"status": "complete"})
    set_session_cookie(response, token, secure=not settings.dev_mode)
    return response


@router.get("/login/github")
async def github_login_start(
    request: Request,
    github_oauth: Annotated[GitHubOAuth, Depends(get_github_oauth)],
    settings: Annotated[Settings, Depends(get_settings)],
    next_path: Annotated[str | None, Query(alias="next")] = None,
):
    try:
        start = await github_oauth.start(
            next_path,
            request.cookies.get(oauth_browser_cookie_name(not settings.dev_mode)),
        )
    except GitHubOAuthNotConfigured:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
    response = RedirectResponse(url=start.authorization_url, status_code=302)
    set_oauth_browser_cookie(response, start.browser_nonce, secure=not settings.dev_mode)
    response.headers["Cache-Control"] = "no-store"
    return response


def _oauth_error_response(message: str) -> RedirectResponse:
    response = RedirectResponse(url="/?" + urlencode({"login_error": message}), status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/login/github/callback")
async def github_login_callback(
    request: Request,
    github_oauth: Annotated[GitHubOAuth, Depends(get_github_oauth)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
):
    try:
        completed = await github_oauth.complete(
            state=state,
            browser_nonce=request.cookies.get(oauth_browser_cookie_name(not settings.dev_mode)),
            code=code,
            error=error,
        )
    except GitHubOAuthInvalidState:
        return _oauth_error_response("Your GitHub sign-in expired. Try again.")
    except GitHubOAuthDenied:
        return _oauth_error_response("GitHub sign-in was cancelled.")
    except GitHubOAuthInvalidResponse:
        return _oauth_error_response("GitHub returned an invalid sign-in response.")
    except GitHubOAuthUnavailable:
        return _oauth_error_response("GitHub sign-in is temporarily unavailable. Try again.")
    except GitHubOAuthNotConfigured:
        return _oauth_error_response("GitHub OAuth is not configured.")

    try:
        result = auth.login_with_github(completed.user)
    except AccessDenied:
        return _oauth_error_response("This GitHub account is not allowed on this server.")
    response = RedirectResponse(url=completed.next_path, status_code=303)
    set_session_cookie(response, result.token, secure=not settings.dev_mode)
    response.headers["Cache-Control"] = "no-store"
    return response


class PasskeyLoginRequest(BaseModel):
    credential: dict[str, Any]


@router.post("/login/passkey/start")
async def login_passkey_start(
    passkeys: Annotated[PasskeyService, Depends(get_passkey_service)],
):
    return Response(
        content=passkeys.authentication_options(),
        media_type="application/json",
    )


@router.post("/login/passkey/finish")
async def login_passkey_finish(
    data: PasskeyLoginRequest,
    passkeys: Annotated[PasskeyService, Depends(get_passkey_service)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    try:
        user_id = passkeys.authenticate(data.credential)
        result = auth.login_by_user_id(user_id)
    except (ChallengeExpired, AuthenticationFailed, InvalidSession):
        raise HTTPException(status_code=400, detail="Passkey sign-in failed, try again")

    return _session_response(result.token, settings)


@router.get("/sites/{name}", response_class=HTMLResponse)
async def site_detail(
    request: Request,
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    domain = settings.control_host
    capability = request.app.state.custom_domains.capabilities()
    custom_domains_available = capability.control_ready
    custom_domains_configured = capability.status != "disabled"
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
    access_policy = request.app.state.access.get_policy(name, identity.user.id)
    access_readers = (
        request.app.state.access.list_readers(name, identity.user.id)
        if access_policy
        else []
    )
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
        "custom_domains_configured": custom_domains_configured,
        "custom_domain_can_add": custom_domain_can_add,
        "domain_routing_targets": domain_routing_targets,
        "custom_domain_quota": domain_quota,
        "domain_claims": domain_claims,
        "domain_connections": domain_connections,
        "domain_tasks": domain_tasks,
        "cloudflare_diagnostics": cloudflare_diagnostics,
        "access_policy": access_policy,
        "access_readers": access_readers,
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


@router.post("/logout")
async def logout(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    cookie_token = request.cookies.get(session_cookie_name(not settings.dev_mode))
    if cookie_token:
        try:
            auth.logout(f"Bearer {cookie_token}")
        except Exception:
            logger.warning("Failed to revoke session on logout", exc_info=True)

    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response, secure=not settings.dev_mode)
    return response
