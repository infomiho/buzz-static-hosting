import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .access import ACCESS_GRANT_LIFETIME, AccessService, InvalidAccessCode
from .analytics import AnalyticsRecorder, build_analytics_event
from .api_models import HealthResponse
from .auth_service import DEV_SESSION_ID, AuthService, Identity
from .cookies import access_cookie_name, session_cookie_name, set_access_cookie
from .custom_domains import (
    DOMAIN_CHECK_PREFIX,
    ClaimConflict,
    ClaimNotFound,
    CustomDomainsConfig,
    CustomDomainsRuntime,
    UnsupportedClaimMode,
)
from .db import Database
from .dependencies import get_identity
from .device_authorization import DeviceAuthorizationService
from .exceptions import BadRequest, Conflict, Forbidden, NotFound, PayloadTooLarge
from .github import HttpGitHubClient
from .github_login import GitHubOAuth
from .passkeys import PasskeyService
from .pending_store import PendingStore
from .routes import access, account, auth, dashboard, device, domains, sites, tokens
from .settings import Settings
from .site_store import SiteStore
from .site_path import (
    InvalidPath,
    InvalidSubdomain,
    normalized_url_path,
    resolve_normalized_site_file,
)
from .templating import STATIC_DIR, templates
from .utils import extract_subdomain, is_control_host

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
    ".xml": "application/xml",
}


class RevalidatedStaticFiles(StaticFiles):
    """Dashboard assets are served from stable filenames, so without an explicit
    Cache-Control browsers apply heuristic freshness and keep serving a stale
    stylesheet after an upgrade. Force a revalidation; the ETag keeps it cheap."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class DeploymentBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != "/deploy":
            await self._app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        content_length = headers.get(b"content-length")
        try:
            body_too_large = bool(
                content_length and int(content_length) > self._max_body_bytes
            )
        except ValueError:
            body_too_large = True
        if body_too_large:
            await self._reject(scope, receive, send)
            return

        received_bytes = 0

        async def receive_with_limit() -> Message:
            nonlocal received_bytes
            message = await receive()
            received_bytes += len(message.get("body", b""))
            if received_bytes > self._max_body_bytes:
                raise PayloadTooLarge(
                    "Request body exceeds the configured deployment limit"
                )
            return message

        try:
            await self._app(scope, receive_with_limit, send)
        except PayloadTooLarge:
            # The deploy handler reads the full body before responding, so no
            # response bytes have gone out when the limit trips here.
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body exceeds the configured deployment limit"},
        )
        await response(scope, receive, send)


def origin_matches_host(origin: str, host: str, scheme: str) -> bool:
    try:
        parsed_origin = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed_origin.scheme == scheme
        and parsed_origin.netloc.lower() == host.lower()
    )


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    database = database or Database(settings.db_path)
    max_deploy_body_bytes = settings.max_archive_bytes + 1024 * 1024
    custom_domains = CustomDomainsRuntime(
        CustomDomainsConfig.from_settings(settings), connect=database.connect
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.access.load_visibility()
        await custom_domains.start()
        analytics_started = False
        try:
            app.state.analytics.start()
            analytics_started = True
            yield
        finally:
            if analytics_started:
                try:
                    await app.state.analytics.stop()
                except Exception:
                    logger.exception("Analytics shutdown failed")
            await custom_domains.stop()
            app.state.access.close()

    app = FastAPI(
        title="Buzz",
        description=(
            "HTTP API for deploying and managing sites on a self-hosted Buzz server. "
            "API operations are available only on the configured Buzz domain."
        ),
        version="0.1.0",
        openapi_tags=[
            {
                "name": "Authentication",
                "description": "Buzz device authorization and sessions.",
            },
            {"name": "Sites", "description": "Site deployment and ownership."},
            {
                "name": "Custom Domains",
                "description": "Custom hostname ownership claims.",
            },
            {
                "name": "Deployment Tokens",
                "description": "Site-scoped credentials for automated deployment.",
            },
            {
                "name": "Access",
                "description": "Private-site protection and reader access.",
            },
            {"name": "System", "description": "Server health."},
        ],
        lifespan=lifespan,
    )
    github_client = HttpGitHubClient()
    app.state.settings = settings
    app.state.database = database
    app.state.github_client = github_client
    app.state.auth_service = AuthService(
        db=database.connect,
        allow_registration=settings.allow_registration,
        allowed_github_users=settings.allowed_github_users,
    )
    if settings.dev_mode:
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO users (id, github_id, github_login, github_name) "
                "VALUES (1, 0, 'dev', 'Dev User') ON CONFLICT(id) DO NOTHING"
            )
            conn.execute(
                "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, 1, ?) "
                "ON CONFLICT(id) DO UPDATE SET expires_at = excluded.expires_at",
                (DEV_SESSION_ID, "9999-12-31T23:59:59"),
            )
    app.state.access = AccessService(
        database.connect,
        reader=database.reader(),
    )
    control_origin = settings.control_origin
    app.state.github_oauth = GitHubOAuth(
        PendingStore(),
        settings.github_client_id,
        settings.github_client_secret,
        f"{control_origin}/dashboard/login/github/callback",
    )
    app.state.passkeys = PasskeyService(
        db=database.connect,
        store=PendingStore(),
        rp_id=(settings.domain or "localhost").split(":", 1)[0],
        rp_name="Buzz",
        # Never widen this: user sites live on subdomains of the RP ID, and the
        # exact-origin check is what rejects assertions triggered from them.
        expected_origin=control_origin,
    )
    app.state.device_authorization = DeviceAuthorizationService(
        store=PendingStore(),
        verification_uri=f"{control_origin}/device",
    )
    app.state.analytics = AnalyticsRecorder(database.connect)
    app.state.custom_domains = custom_domains

    @app.exception_handler(BadRequest)
    async def bad_request_handler(request: Request, exc: BadRequest):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(Forbidden)
    async def forbidden_handler(request: Request, exc: Forbidden):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def conflict_handler(request: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def not_found_handler(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(UnsupportedClaimMode)
    async def unsupported_claim_mode_handler(request: Request, exc: UnsupportedClaimMode):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ClaimConflict)
    async def claim_conflict_handler(request: Request, exc: ClaimConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ClaimNotFound)
    async def claim_not_found_handler(request: Request, exc: ClaimNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(PayloadTooLarge)
    async def payload_too_large_handler(request: Request, exc: PayloadTooLarge):
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    app.add_middleware(
        DeploymentBodyLimitMiddleware,
        max_body_bytes=max_deploy_body_bytes,
    )

    @app.middleware("http")
    async def dispatch_by_host(request: Request, call_next):
        host = request.headers.get("host")
        challenge = custom_domains.resolve_challenge(request.url.hostname, request.url.path)
        if challenge:
            if request.method not in {"GET", "HEAD"}:
                return Response(
                    content="Method Not Allowed",
                    status_code=405,
                    headers={"Allow": "GET, HEAD"},
                    media_type="text/plain",
                )
            claim_id, site_name, token = challenge
            return Response(
                content=f"buzz-domain-check={token};site={site_name}",
                media_type="text/plain",
                headers={
                    "Cache-Control": "no-store",
                    "X-Buzz-Domain-Claim": str(claim_id),
                },
            )
        if request.url.path.startswith(DOMAIN_CHECK_PREFIX):
            return Response(
                content="404 Not Found",
                status_code=404,
                media_type="text/plain",
                headers={"Cache-Control": "no-store"},
            )
        subdomain = extract_subdomain(host, settings.domain)
        if subdomain:
            if request.url.path == "/.well-known/buzz-access/callback":
                return await complete_access_callback(request, subdomain, settings)
            if request.method not in {"GET", "HEAD"}:
                return Response(
                    content="Method Not Allowed",
                    status_code=405,
                    headers={"Allow": "GET, HEAD"},
                    media_type="text/plain",
                )
            return await serve_site(request, subdomain, settings)

        if not is_control_host(host, settings.domain):
            site_name = custom_domains.activated_site(request.url.hostname)
            if site_name:
                if request.url.path == "/.well-known/buzz-access/callback":
                    return await complete_access_callback(request, site_name, settings)
                if request.method not in {"GET", "HEAD"}:
                    return Response(
                        content="Method Not Allowed",
                        status_code=405,
                        headers={"Allow": "GET, HEAD"},
                        media_type="text/plain",
                    )
                return await serve_site(request, site_name, settings)
            return Response(
                content="Misdirected Request",
                status_code=421,
                media_type="text/plain",
            )

        request_origin = request.headers.get("origin") or request.headers.get("referer")
        control_scheme = "https" if settings.domain else request.url.scheme
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.cookies.get(session_cookie_name(not settings.dev_mode))
            and not (
                request_origin
                and origin_matches_host(request_origin, host or "", control_scheme)
            )
        ):
            return Response(
                content="Cross-origin request blocked",
                status_code=403,
                media_type="text/plain",
            )

        # Only the control host reaches here; hosted user sites are served
        # earlier and stay framable. The dashboard must not be.
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
    app.include_router(access.router)
    app.include_router(dashboard.router)
    app.include_router(account.router)
    app.include_router(device.router)
    app.include_router(sites.router, tags=["Sites"])
    app.include_router(domains.capabilities_router, tags=["Custom Domains"])
    app.include_router(domains.router, tags=["Custom Domains"])
    app.include_router(tokens.router, prefix="/tokens", tags=["Deployment Tokens"])

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="getHealth",
        summary="Check server health",
        tags=["System"],
    )
    async def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def landing(request: Request, identity: Identity | None = Depends(get_identity)):
        access_host = None
        next_path = request.query_params.get("next")
        if next_path:
            parsed_next = urlsplit(next_path)
            if parsed_next.path == "/access/authorize":
                access_host = parse_qs(parsed_next.query).get("host", [None])[0]
        page_context = {
            "domain": settings.control_host,
            "server_url": settings.control_origin,
            "access_host": access_host,
        }
        if identity and app.state.auth_service.user_is_allowed(identity.user.id):
            # Decided server-side so the first-run screen cannot flash in after
            # the sites request resolves.
            with database.connect() as conn:
                has_sites = bool(
                    SiteStore(conn, settings.sites_dir).list_for_owner(identity.user.id)
                )
            return templates.TemplateResponse(
                request,
                "dashboard.html",
                {**page_context, "user": identity.user, "has_sites": has_sites},
            )
        return templates.TemplateResponse(request, "login.html", page_context)

    @app.get("/{path:path}", include_in_schema=False)
    async def catch_all(request: Request, path: str):
        return Response(content="404 Not Found", status_code=404, media_type="text/plain")

    return app


async def complete_access_callback(
    request: Request, site_name: str, settings: Settings
) -> Response:
    if request.method != "POST":
        return Response(
            content="Method Not Allowed",
            status_code=405,
            headers={"Allow": "POST", "Cache-Control": "no-store"},
            media_type="text/plain",
        )
    origin = request.headers.get("origin")
    if origin and not origin_matches_host(
        origin, settings.control_host, settings.control_scheme
    ):
        # The short-lived code is single-use and bound to this exact hostname.
        # Origin varies across browser form redirects, so it is diagnostic only.
        logger.info(
            "Access handoff received origin %r instead of %s",
            origin,
            settings.control_origin,
        )
    async with request.form(max_files=0, max_fields=1) as form:
        code = form.get("code")
    if not isinstance(code, str):
        return Response(
            content="Invalid Access handoff",
            status_code=400,
            headers={"Cache-Control": "no-store"},
            media_type="text/plain",
        )
    try:
        grant = request.app.state.access.exchange_code(
            code, site_name, request.url.hostname or ""
        )
    except InvalidAccessCode:
        return Response(
            content="This Access handoff is invalid or expired",
            status_code=403,
            headers={"Cache-Control": "no-store"},
            media_type="text/plain",
        )
    response = RedirectResponse(grant.return_path, status_code=303)
    set_access_cookie(
        response,
        grant.token,
        secure=not settings.dev_mode,
        max_age=int(ACCESS_GRANT_LIFETIME.total_seconds()),
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def requested_site_path(request: Request) -> str | None:
    """The normalized path this request asks for, or None if it is unusable."""
    try:
        return normalized_url_path(request.scope.get("raw_path", b"").decode("ascii"))
    except (InvalidPath, UnicodeDecodeError):
        return None


async def serve_site(request: Request, site_name: str, settings: Settings) -> Response:
    hostname = request.url.hostname or ""
    try:
        decision = await request.app.state.access.check_request(
            site_name,
            hostname,
            request.cookies.get(access_cookie_name(not settings.dev_mode)),
        )
    except Exception:
        logger.exception("Access check failed for site %s", site_name)
        return Response(
            content="Access is temporarily unavailable",
            status_code=503,
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )

    # Deliberately ahead of path handling: every URL on a private site reaches
    # the same gate, so no path can steer the decision or reveal what exists.
    if decision.protected and not decision.authorized:
        return access_gate(request, site_name, hostname, settings)

    path = requested_site_path(request)
    if path is None:
        return Response(
            content="404 Not Found",
            status_code=404,
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )
    return await serve_static(
        request, site_name, path, settings, private=decision.protected
    )


def access_gate(
    request: Request, site_name: str, hostname: str, settings: Settings
) -> Response:
    control_origin = settings.control_origin
    path = requested_site_path(request) or "/"
    return_path = path + (f"?{request.url.query}" if request.url.query else "")
    authorize_url = (
        f"{control_origin}/access/authorize?"
        + urlencode({"site": site_name, "host": hostname, "path": return_path})
    )
    return templates.TemplateResponse(
        request,
        "access_gate.html",
        {
            "authorize_url": authorize_url,
            "control_origin": control_origin,
            "hostname": hostname,
        },
        status_code=401,
        headers={
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": f"default-src 'none'; style-src {control_origin}; base-uri 'none'; frame-ancestors 'none'",
        },
    )


async def serve_static(
    request: Request,
    subdomain: str,
    path: str,
    settings: Settings,
    private: bool,
) -> Response:
    try:
        filepath = resolve_normalized_site_file(settings.sites_dir, subdomain, path)
    except InvalidSubdomain:
        return Response(content="Site not found", status_code=404, media_type="text/plain")

    if filepath:
        content_type = CONTENT_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
        record_analytics(request, subdomain, path, 200, filepath.stat().st_size, content_type, settings)
        response = FileResponse(filepath, media_type=content_type)
        if private:
            protect_response(response)
        return response

    site_dir = (settings.sites_dir / subdomain).resolve()
    custom_404 = site_dir / "404.html"
    if site_dir.is_dir() and custom_404.is_file():
        record_analytics(request, subdomain, path, 404, custom_404.stat().st_size, "text/html", settings)
        response = FileResponse(custom_404, status_code=404, media_type="text/html")
        if private:
            protect_response(response)
        return response

    content = b"404 Not Found"
    record_analytics(request, subdomain, path, 404, len(content), "text/plain", settings)
    response = Response(content=content, status_code=404, media_type="text/plain")
    if private:
        protect_response(response)
    return response


def protect_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"


def record_analytics(
    request: Request,
    subdomain: str,
    path: str,
    status_code: int,
    bytes_sent: int,
    content_type: str,
    settings: Settings,
) -> None:
    internal_hosts = (
        {f"{subdomain}.{settings.domain.split(':', 1)[0]}"} if settings.domain else set()
    )
    event = build_analytics_event(
        request,
        subdomain,
        path,
        status_code,
        bytes_sent,
        content_type,
        internal_hosts,
        visitor_secret=settings.analytics_secret,
    )
    if not event:
        return
    if settings.custom_domains_enabled and event.referrer:
        try:
            internal_hosts.update(
                request.app.state.custom_domains.activated_hostnames_for_site(subdomain)
            )
            event = build_analytics_event(
                request,
                subdomain,
                path,
                status_code,
                bytes_sent,
                content_type,
                internal_hosts,
                visitor_secret=settings.analytics_secret,
            )
        except Exception:
            logger.warning(
                "Failed to resolve internal custom-domain referrers", exc_info=True
            )
    request.app.state.analytics.record(event)
