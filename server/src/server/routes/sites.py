import io
import shutil
import zipfile
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, Response

from ..config import DOMAIN, SITES_DIR
from ..db import db
from ..utils import generate_subdomain, get_dir_size
from ..dependencies import AuthContext, require_auth, require_auth_or_deploy

router = APIRouter()


@router.post("/deploy")
async def deploy(
    request: Request,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_auth_or_deploy),
    x_subdomain: str | None = Header(default=None),
):
    subdomain = x_subdomain.strip() if x_subdomain else generate_subdomain()
    if not subdomain.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid subdomain")

    if ctx.token_type == "deploy" and ctx.site_name != subdomain:
        raise HTTPException(
            status_code=403,
            detail=f"Deploy token is scoped to site '{ctx.site_name}', cannot deploy to '{subdomain}'"
        )

    with db() as conn:
        existing = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (subdomain,)).fetchone()
    if existing and existing["owner_id"] is not None and existing["owner_id"] != ctx.user_id:
        raise HTTPException(status_code=403, detail=f"Site '{subdomain}' is owned by another user")

    content = await file.read()
    site_dir = SITES_DIR / subdomain
    site_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            zf.extractall(site_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")

    with db() as conn:
        if existing:
            owner_id = existing["owner_id"] if existing["owner_id"] is not None else ctx.user_id
            conn.execute(
                "UPDATE sites SET size_bytes = ?, created_at = ?, owner_id = ? WHERE name = ?",
                (get_dir_size(site_dir), datetime.now().isoformat(), owner_id, subdomain),
            )
        else:
            conn.execute(
                "INSERT INTO sites (name, size_bytes, created_at, owner_id) VALUES (?, ?, ?, ?)",
                (subdomain, get_dir_size(site_dir), datetime.now().isoformat(), ctx.user_id),
            )

    if DOMAIN:
        url = f"https://{subdomain}.{DOMAIN}"
    else:
        port = request.url.port or 8080
        url = f"http://{subdomain}.localhost:{port}"
    return {"url": url}


@router.get("/sites")
async def list_sites(ctx: Annotated[AuthContext, Depends(require_auth)]):
    with db() as conn:
        rows = conn.execute(
            "SELECT name, created_at, size_bytes FROM sites WHERE owner_id = ? ORDER BY created_at DESC",
            (ctx.user_id,)
        ).fetchall()
    return [{"name": r["name"], "created": r["created_at"], "size_bytes": r["size_bytes"]} for r in rows]


@router.delete("/sites/{name}")
async def delete_site(name: str, ctx: Annotated[AuthContext, Depends(require_auth)]):
    with db() as conn:
        site = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (name,)).fetchone()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if site["owner_id"] is not None and site["owner_id"] != ctx.user_id:
        raise HTTPException(status_code=403, detail="You don't own this site")

    site_dir = SITES_DIR / name
    if site_dir.exists():
        shutil.rmtree(site_dir)
    with db() as conn:
        conn.execute("DELETE FROM sites WHERE name = ?", (name,))
    return Response(status_code=204)
