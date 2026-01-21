from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..db import db
from ..auth import hash_token, generate_deploy_token
from ..dependencies import AuthContext, require_auth

router = APIRouter()


class CreateTokenRequest(BaseModel):
    site_name: str
    name: str = "Deployment token"


@router.get("")
async def list_tokens(ctx: Annotated[AuthContext, Depends(require_auth)]):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, site_name, created_at, expires_at, last_used_at FROM deployment_tokens WHERE user_id = ? ORDER BY created_at DESC",
            (ctx.user_id,)
        ).fetchall()

    return [
        {
            "id": r["id"][:16],
            "name": r["name"],
            "site_name": r["site_name"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "last_used_at": r["last_used_at"],
        }
        for r in rows
    ]


@router.post("")
async def create_token(data: CreateTokenRequest, ctx: Annotated[AuthContext, Depends(require_auth)]):
    with db() as conn:
        site = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (data.site_name,)).fetchone()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if site["owner_id"] != ctx.user_id:
        raise HTTPException(status_code=403, detail="You don't own this site")

    token = generate_deploy_token()
    token_hash = hash_token(token)
    with db() as conn:
        conn.execute(
            "INSERT INTO deployment_tokens (id, name, site_name, user_id) VALUES (?, ?, ?, ?)",
            (token_hash, data.name, data.site_name, ctx.user_id)
        )

    return {"id": token_hash[:16], "token": token, "name": data.name, "site_name": data.site_name}


@router.delete("/{token_id}")
async def delete_token(token_id: str, ctx: Annotated[AuthContext, Depends(require_auth)]):
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM deployment_tokens WHERE id LIKE ? AND user_id = ?",
            (token_id + "%", ctx.user_id)
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Token not found")

        conn.execute("DELETE FROM deployment_tokens WHERE id = ?", (row["id"],))
    return Response(status_code=204)
