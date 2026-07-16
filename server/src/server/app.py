import asyncio
import logging
import random
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
from .config import (
    ALLOW_REGISTRATION,
    ALLOWED_GITHUB_USERS,
    CONTENT_TYPES,
    CUSTOM_DOMAIN_INGRESS_IPS,
    CUSTOM_DOMAIN_ORIGIN_HOST,
    CUSTOM_DOMAIN_RECONCILE_SECONDS,
    CUSTOM_DOMAIN_ROUTING_ENABLED,
    CUSTOM_DOMAINS_ENABLED,
    DOMAIN,
    GITHUB_CLIENT_ID,
    MAX_ARCHIVE_BYTES,
    SITES_DIR,
    TRAEFIK_API_AUTHORIZATION,
    TRAEFIK_API_URL,
    TRAEFIK_CERT_RESOLVER,
    TRAEFIK_CONTROL_PORT,
    TRAEFIK_CONTROL_TOKEN,
    TRAEFIK_HTTPS_ENTRYPOINT,
    TRAEFIK_SERVICE,
)
from .cookies import COOKIE_NAME
from .custom_domains import DnsTxtResolver, DomainClaimStore
from .domain_activation import DomainActivator
from .domain_routing import DomainRouteReconciler, build_traefik_snapshot
from .site_path import InvalidSubdomain, resolve_site_file
from .db import db
from .dependencies import get_identity
from .exceptions import BadRequest, Conflict, Forbidden, NotFound, PayloadTooLarge
from .github import HttpGitHubClient
from .routes import auth, dashboard, domains, sites, tokens
from .search_console import create_search_console_client
from .utils import extract_subdomain, is_control_host
from .traefik_control import TraefikControlServer, TraefikRuntimeClient

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
MAX_DEPLOY_BODY_BYTES = MAX_ARCHIVE_BYTES + 1024 * 1024
DOMAIN_CHECK_PREFIX = "/.well-known/buzz-domain-check/"
logger = logging.getLogger(__name__)


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


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        control_server = None
        reconciler_task = None
        analytics_started = False
        stop_reconciler = asyncio.Event()
        try:
            if not CUSTOM_DOMAINS_ENABLED:
                with db() as conn:
                    routed_claim = conn.execute(
                        """SELECT 1 FROM custom_domain_claims
                        WHERE route_status IN ('publishing', 'routed', 'removing') LIMIT 1"""
                    ).fetchone()
                if routed_claim:
                    raise RuntimeError(
                        "Withdraw all custom-domain routers before disabling custom domains"
                    )
            if CUSTOM_DOMAINS_ENABLED and TRAEFIK_CONTROL_TOKEN:
                with db() as conn:
                    DomainClaimStore(conn).prepare_routes(CUSTOM_DOMAIN_ROUTING_ENABLED)
                runtime_client = None
                if TRAEFIK_API_URL:
                    runtime_client = TraefikRuntimeClient(
                        TRAEFIK_API_URL,
                        TRAEFIK_API_AUTHORIZATION,
                        TRAEFIK_HTTPS_ENTRYPOINT,
                        TRAEFIK_SERVICE,
                    )
                control_server = TraefikControlServer(
                    TRAEFIK_CONTROL_TOKEN,
                    TRAEFIK_CONTROL_PORT,
                    runtime_client,
                    snapshot_provider=lambda: build_traefik_snapshot(
                        TRAEFIK_HTTPS_ENTRYPOINT,
                        TRAEFIK_SERVICE,
                        TRAEFIK_CERT_RESOLVER,
                    ),
                )
                app.state.traefik_control = control_server
                control_server.start()
                if runtime_client:
                    activator = DomainActivator(
                        CUSTOM_DOMAIN_INGRESS_IPS,
                        CUSTOM_DOMAIN_ORIGIN_HOST,
                    )
                    reconciler = DomainRouteReconciler(
                        runtime_client,
                        TRAEFIK_HTTPS_ENTRYPOINT,
                        TRAEFIK_SERVICE,
                        TRAEFIK_CERT_RESOLVER,
                        routing_enabled=lambda: CUSTOM_DOMAIN_ROUTING_ENABLED,
                        withdrawal_snapshot_acknowledged=(
                            control_server.withdrawal_snapshot_acknowledged
                        ),
                    )

                    async def reconcile_routes() -> None:
                        while not stop_reconciler.is_set():
                            try:
                                await asyncio.to_thread(control_server.refresh_readiness)
                                await asyncio.to_thread(reconciler.run_once)
                                await asyncio.to_thread(activator.run_once)
                            except Exception:
                                logger.exception("Custom domain reconciliation failed")
                            try:
                                await asyncio.wait_for(
                                    stop_reconciler.wait(),
                                    timeout=random.uniform(0.8, 1.2)
                                    * CUSTOM_DOMAIN_RECONCILE_SECONDS,
                                )
                            except TimeoutError:
                                pass

                    reconciler_task = asyncio.create_task(reconcile_routes())
            app.state.analytics.start()
            analytics_started = True
            yield
        finally:
            if analytics_started:
                try:
                    await app.state.analytics.stop()
                except Exception:
                    logger.exception("Analytics shutdown failed")
            if reconciler_task:
                stop_reconciler.set()
                try:
                    await reconciler_task
                except Exception:
                    logger.exception("Custom domain reconciler shutdown failed")
            if control_server:
                try:
                    control_server.stop()
                finally:
                    app.state.traefik_control = None

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
    app.state.github_client = github_client
    app.state.auth_service = AuthService(
        db=db,
        github=github_client,
        github_client_id=GITHUB_CLIENT_ID,
        allow_registration=ALLOW_REGISTRATION,
        allowed_github_users=ALLOWED_GITHUB_USERS,
    )
    app.state.analytics = AnalyticsRecorder(db)
    app.state.search_console = create_search_console_client()
    app.state.domain_txt_resolver = DnsTxtResolver()
    app.state.traefik_control = None

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

    @app.exception_handler(PayloadTooLarge)
    async def payload_too_large_handler(request: Request, exc: PayloadTooLarge):
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    app.add_middleware(
        DeploymentBodyLimitMiddleware,
        max_body_bytes=MAX_DEPLOY_BODY_BYTES,
    )

    @app.middleware("http")
    async def dispatch_by_host(request: Request, call_next):
        host = request.headers.get("host")
        challenge = resolve_custom_domain_challenge(request.url.hostname, request.url.path)
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
        subdomain = extract_subdomain(host)
        if subdomain:
            if request.method not in {"GET", "HEAD"}:
                return Response(
                    content="Method Not Allowed",
                    status_code=405,
                    headers={"Allow": "GET, HEAD"},
                    media_type="text/plain",
                )
            return await serve_static(request, subdomain, request.url.path)

        if not is_control_host(host):
            site_name = resolve_activated_custom_domain(request.url.hostname)
            if site_name:
                if request.method not in {"GET", "HEAD"}:
                    return Response(
                        content="Method Not Allowed",
                        status_code=405,
                        headers={"Allow": "GET, HEAD"},
                        media_type="text/plain",
                    )
                return await serve_static(request, site_name, request.url.path)
            return Response(
                content="Misdirected Request",
                status_code=421,
                media_type="text/plain",
            )

        request_origin = request.headers.get("origin") or request.headers.get("referer")
        control_scheme = "https" if DOMAIN else request.url.scheme
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
        domain = DOMAIN or "localhost:8080"

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


def resolve_custom_domain_challenge(
    hostname: str | None,
    path: str,
) -> tuple[int, str, str] | None:
    if (
        not CUSTOM_DOMAINS_ENABLED
        or not CUSTOM_DOMAIN_ROUTING_ENABLED
        or not hostname
        or not path.startswith(DOMAIN_CHECK_PREFIX)
    ):
        return None
    token = path.removeprefix(DOMAIN_CHECK_PREFIX)
    if not token or "/" in token:
        return None
    with db() as conn:
        store = DomainClaimStore(conn)
        claim = store.find_challenge(hostname.lower().rstrip("."), token)
        if claim:
            store.mark_challenge_seen(claim.id, claim.route_generation)
    if not claim or not claim.site_name:
        return None
    return claim.id, claim.site_name, token


def resolve_activated_custom_domain(hostname: str | None) -> str | None:
    if not CUSTOM_DOMAINS_ENABLED or not CUSTOM_DOMAIN_ROUTING_ENABLED or not hostname:
        return None
    with db() as conn:
        claim = DomainClaimStore(conn).find_activated(hostname.lower().rstrip("."))
    return claim.site_name if claim else None


async def serve_static(request: Request, subdomain: str, path: str) -> Response:
    try:
        filepath = resolve_site_file(SITES_DIR, subdomain, path)
    except InvalidSubdomain:
        return Response(content="Site not found", status_code=404, media_type="text/plain")

    if filepath:
        content_type = CONTENT_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
        record_analytics(request, subdomain, path, 200, filepath.stat().st_size, content_type)
        return FileResponse(filepath, media_type=content_type)

    site_dir = (SITES_DIR / subdomain).resolve()
    custom_404 = site_dir / "404.html"
    if site_dir.is_dir() and custom_404.is_file():
        record_analytics(request, subdomain, path, 404, custom_404.stat().st_size, "text/html")
        return FileResponse(custom_404, status_code=404, media_type="text/html")

    content = b"404 Not Found"
    record_analytics(request, subdomain, path, 404, len(content), "text/plain")
    return Response(content=content, status_code=404, media_type="text/plain")


def record_analytics(
    request: Request,
    subdomain: str,
    path: str,
    status_code: int,
    bytes_sent: int,
    content_type: str,
) -> None:
    internal_hosts = {f"{subdomain}.{DOMAIN.split(':', 1)[0]}"} if DOMAIN else set()
    event = build_analytics_event(
        request,
        subdomain,
        path,
        status_code,
        bytes_sent,
        content_type,
        internal_hosts,
    )
    if not event:
        return
    if CUSTOM_DOMAINS_ENABLED and event.referrer:
        try:
            with db() as conn:
                internal_hosts.update(
                    DomainClaimStore(conn).activated_hostnames_for_site(subdomain)
                )
            event = build_analytics_event(
                request,
                subdomain,
                path,
                status_code,
                bytes_sent,
                content_type,
                internal_hosts,
            )
        except Exception:
            logger.warning(
                "Failed to resolve internal custom-domain referrers", exc_info=True
            )
    request.app.state.analytics.record(event)
