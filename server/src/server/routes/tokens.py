from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth_service import AuthService, NotSiteOwner, SiteNotFound, TokenNotFound
from ..dependencies import Identity, get_auth_service, require_user

router = APIRouter()


class CreateTokenRequest(BaseModel):
    site_name: str
    name: str = "Deployment token"


@router.get("")
async def list_tokens(
    identity: Annotated[Identity, Depends(require_user)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    return [
        {
            "id": t.id_prefix,
            "name": t.name,
            "site_name": t.site_name,
            "created_at": t.created_at,
            "expires_at": t.expires_at,
            "last_used_at": t.last_used_at,
        }
        for t in auth.list_deploy_tokens(identity.user.id)
    ]


@router.post("")
async def create_token(
    data: CreateTokenRequest,
    identity: Annotated[Identity, Depends(require_user)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    try:
        result = auth.create_deploy_token(identity.user.id, data.site_name, data.name)
    except SiteNotFound:
        raise HTTPException(status_code=404, detail="Site not found")
    except NotSiteOwner:
        raise HTTPException(status_code=403, detail="You don't own this site")

    return {"id": result.id_prefix, "token": result.raw_token, "name": result.name, "site_name": result.site_name}


@router.delete("/{token_id}")
async def delete_token(
    token_id: str,
    identity: Annotated[Identity, Depends(require_user)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    try:
        auth.delete_deploy_token(identity.user.id, token_id)
    except TokenNotFound:
        raise HTTPException(status_code=404, detail="Token not found")
    return Response(status_code=204)
