from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth_service import AuthService, Identity
from .config import DOMAIN, GITHUB_CLIENT_ID, SITES_DIR, CONTENT_TYPES
from .site_path import InvalidSubdomain, resolve_site_file
from .db import db
from .dependencies import get_identity
from .exceptions import BadRequest, Forbidden, NotFound
from .github import HttpGitHubClient
from .routes import auth, dashboard, sites, tokens
from .utils import extract_subdomain

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app() -> FastAPI:
    app = FastAPI(title="Buzz", description="Self-hosted static site hosting", version="0.1.0")
    github_client = HttpGitHubClient()
    app.state.github_client = github_client
    app.state.auth_service = AuthService(db=db, github=github_client, github_client_id=GITHUB_CLIENT_ID)

    @app.exception_handler(BadRequest)
    async def bad_request_handler(request: Request, exc: BadRequest):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(Forbidden)
    async def forbidden_handler(request: Request, exc: Forbidden):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def not_found_handler(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(dashboard.router, tags=["dashboard"])
    app.include_router(sites.router, tags=["sites"])
    app.include_router(tokens.router, prefix="/tokens", tags=["tokens"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request, identity: Identity | None = Depends(get_identity)):
        subdomain = extract_subdomain(request.headers.get("host", ""))
        if subdomain:
            return await serve_static(subdomain, "/")

        domain = DOMAIN or "localhost:8080"

        if identity:
            return templates.TemplateResponse(request, "dashboard.html", {
                "user": identity.user,
                "domain": domain,
            })

        return templates.TemplateResponse(request, "login.html", {
            "domain": domain,
        })

    @app.get("/{path:path}")
    async def catch_all(request: Request, path: str):
        subdomain = extract_subdomain(request.headers.get("host", ""))
        if subdomain:
            return await serve_static(subdomain, f"/{path}")
        return Response(content="404 Not Found", status_code=404, media_type="text/plain")

    return app


async def serve_static(subdomain: str, path: str) -> Response:
    try:
        filepath = resolve_site_file(SITES_DIR, subdomain, path)
    except InvalidSubdomain:
        return Response(content="Site not found", status_code=404, media_type="text/plain")

    if filepath:
        content_type = CONTENT_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
        return FileResponse(filepath, media_type=content_type)

    site_dir = (SITES_DIR / subdomain).resolve()
    custom_404 = site_dir / "404.html"
    if site_dir.is_dir() and custom_404.is_file():
        return Response(content=custom_404.read_bytes(), status_code=404, media_type="text/html")

    return Response(content="404 Not Found", status_code=404, media_type="text/plain")
