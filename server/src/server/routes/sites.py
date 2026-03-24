from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile, Response

from ..config import DOMAIN, SITES_DIR
from ..db import db
from ..dependencies import AuthContext, require_auth, require_auth_or_deploy
from ..exceptions import BadRequest, Forbidden
from ..site_store import SiteStore
from ..utils import generate_subdomain

router = APIRouter()


def validate_subdomain(subdomain: str) -> str:
    subdomain = subdomain.strip()
    if not subdomain.replace("-", "").replace("_", "").isalnum():
        raise BadRequest("Invalid subdomain")
    return subdomain


def enforce_deploy_token_scope(ctx: AuthContext, subdomain: str) -> None:
    if ctx.token_type == "deploy" and ctx.site_name != subdomain:
        raise Forbidden(
            f"Deploy token is scoped to site '{ctx.site_name}', cannot deploy to '{subdomain}'"
        )


def build_site_url(subdomain: str, domain: str | None, fallback_port: int) -> str:
    if domain:
        return f"https://{subdomain}.{domain}"
    return f"http://{subdomain}.localhost:{fallback_port}"


@router.post("/deploy")
async def deploy(
    request: Request,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_auth_or_deploy),
    x_subdomain: str | None = Header(default=None),
):
    subdomain = validate_subdomain(x_subdomain) if x_subdomain else generate_subdomain()
    enforce_deploy_token_scope(ctx, subdomain)

    with db() as conn:
        store = SiteStore(conn, SITES_DIR)
        record = store.deploy(subdomain, await file.read(), ctx.user_id)

    return {"url": build_site_url(record.name, DOMAIN, request.url.port or 8080)}


@router.get("/sites")
async def list_sites(ctx: Annotated[AuthContext, Depends(require_auth)]):
    with db() as conn:
        store = SiteStore(conn, SITES_DIR)
        sites = store.list_for_owner(ctx.user_id)
    return [{"name": s.name, "created": s.created_at, "size_bytes": s.size_bytes} for s in sites]


@router.delete("/sites/{name}")
async def delete_site(name: str, ctx: Annotated[AuthContext, Depends(require_auth)]):
    with db() as conn:
        SiteStore(conn, SITES_DIR).delete(name, ctx.user_id)
    return Response(status_code=204)
