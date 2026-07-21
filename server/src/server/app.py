import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .analytics import AnalyticsRecorder, build_analytics_event
from .api_models import HealthResponse
from .auth_service import AuthService, Identity
from .cookies import COOKIE_NAME
from .custom_domains import (
    ClaimConflict,
    ClaimNotFound,
    CustomDomainsConfig,
    CustomDomainsRuntime,
    DOMAIN_CHECK_PREFIX,
    UnsupportedClaimMode,
)
from .site_path import InvalidSubdomain, resolve_site_file
from .db import Database
from .dependencies import get_identity
from .settings import Settings
from .exceptions import BadRequest, Conflict, Forbidden, NotFound, PayloadTooLarge
from .github import HttpGitHubClient
from .routes import auth, dashboard, domains, sites, tokens
from .search_console import create_search_console_client
from .utils import extract_subdomain, is_control_host

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
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
                "description": "GitHub device authorization and sessions.",
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
        github=github_client,
        github_client_id=settings.github_client_id,
        allow_registration=settings.allow_registration,
        allowed_github_users=settings.allowed_github_users,
    )
    app.state.analytics = AnalyticsRecorder(database.connect)
    app.state.search_console = create_search_console_client(
        settings.gsc_credentials, settings.gsc_property, settings.domain
    )
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
            if request.method not in {"GET", "HEAD"}:
                return Response(
                    content="Method Not Allowed",
                    status_code=405,
                    headers={"Allow": "GET, HEAD"},
                    media_type="text/plain",
                )
            return await serve_static(request, subdomain, request.url.path, settings)

        if not is_control_host(host, settings.domain):
            site_name = custom_domains.activated_site(request.url.hostname)
            if site_name:
                if request.method not in {"GET", "HEAD"}:
                    return Response(
                        content="Method Not Allowed",
                        status_code=405,
                        headers={"Allow": "GET, HEAD"},
                        media_type="text/plain",
                    )
                return await serve_static(request, site_name, request.url.path, settings)
            return Response(
                content="Misdirected Request",
                status_code=421,
                media_type="text/plain",
            )

        request_origin = request.headers.get("origin") or request.headers.get("referer")
        control_scheme = "https" if settings.domain else request.url.scheme
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.cookies.get(COOKIE_NAME)
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

        return await call_next(request)

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
    app.include_router(dashboard.router)
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
        domain = settings.domain or "localhost:8080"

        if identity:
            return templates.TemplateResponse(request, "dashboard.html", {
                "user": identity.user,
                "domain": domain,
            })

        return templates.TemplateResponse(request, "login.html", {
            "domain": domain,
        })

    @app.get("/{path:path}", include_in_schema=False)
    async def catch_all(request: Request, path: str):
        return Response(content="404 Not Found", status_code=404, media_type="text/plain")

    return app


async def serve_static(
    request: Request, subdomain: str, path: str, settings: Settings
) -> Response:
    try:
        filepath = resolve_site_file(settings.sites_dir, subdomain, path)
    except InvalidSubdomain:
        return Response(content="Site not found", status_code=404, media_type="text/plain")

    if filepath:
        content_type = CONTENT_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
        record_analytics(request, subdomain, path, 200, filepath.stat().st_size, content_type, settings)
        return FileResponse(filepath, media_type=content_type)

    site_dir = (settings.sites_dir / subdomain).resolve()
    custom_404 = site_dir / "404.html"
    if site_dir.is_dir() and custom_404.is_file():
        record_analytics(request, subdomain, path, 404, custom_404.stat().st_size, "text/html", settings)
        return FileResponse(custom_404, status_code=404, media_type="text/html")

    content = b"404 Not Found"
    record_analytics(request, subdomain, path, 404, len(content), "text/plain", settings)
    return Response(content=content, status_code=404, media_type="text/plain")


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
