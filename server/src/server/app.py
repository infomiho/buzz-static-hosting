from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from .config import DOMAIN, SITES_DIR, CONTENT_TYPES
from .routes import auth, sites, tokens
from .utils import extract_subdomain


def create_app() -> FastAPI:
    app = FastAPI(title="Buzz", description="Self-hosted static site hosting", version="0.1.0")

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(sites.router, tags=["sites"])
    app.include_router(tokens.router, prefix="/tokens", tags=["tokens"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request):
        subdomain = extract_subdomain(request.headers.get("host", ""))
        if subdomain:
            return await serve_static(subdomain, "/")

        domain = DOMAIN or "localhost:8080"
        template_path = Path(__file__).parent / "landing.html"
        html = template_path.read_text().replace("{{DOMAIN}}", domain)
        return HTMLResponse(content=html)

    @app.get("/{path:path}")
    async def catch_all(request: Request, path: str):
        subdomain = extract_subdomain(request.headers.get("host", ""))
        if subdomain:
            return await serve_static(subdomain, f"/{path}")
        return Response(content="404 Not Found", status_code=404, media_type="text/plain")

    return app


async def serve_static(subdomain: str, path: str) -> Response:
    site_dir = SITES_DIR / subdomain
    if not site_dir.exists():
        return Response(content="Site not found", status_code=404, media_type="text/plain")

    path = path.split("?")[0]
    if path.endswith("/"):
        path += "index.html"

    filepath = site_dir / path.lstrip("/")

    if filepath.is_file():
        return _file_response(filepath)

    if not path.endswith(".html"):
        for candidate in [
            site_dir / (path.lstrip("/") + ".html"),
            site_dir / path.lstrip("/") / "index.html",
        ]:
            if candidate.is_file():
                return _file_response(candidate)

    # SPA fallback - serve 200.html for client-side routing
    spa_fallback = site_dir / "200.html"
    if spa_fallback.is_file():
        return _file_response(spa_fallback)

    custom_404 = site_dir / "404.html"
    if custom_404.exists():
        content = custom_404.read_bytes()
        return Response(content=content, status_code=404, media_type="text/html")

    return Response(content="404 Not Found", status_code=404, media_type="text/plain")


def _file_response(filepath: Path) -> Response:
    content_type = CONTENT_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
    return FileResponse(filepath, media_type=content_type)
