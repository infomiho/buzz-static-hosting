from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from ..api_models import (
    CreateTokenRequest,
    CreatedDeploymentTokenResponse,
    DeploymentTokenResponse,
    ErrorResponse,
)
from ..auth_service import AuthService, NotSiteOwner, SiteNotFound, TokenNotFound
from ..dependencies import Identity, get_auth_service, require_user

router = APIRouter()


@router.get(
    "",
    response_model=list[DeploymentTokenResponse],
    operation_id="listDeploymentTokens",
    summary="List deployment tokens",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "A session token is required."},
    },
)
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


@router.post(
    "",
    response_model=CreatedDeploymentTokenResponse,
    operation_id="createDeploymentToken",
    summary="Create a deployment token",
    description="The token value is returned once and cannot be retrieved later.",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {
            "model": ErrorResponse,
            "description": "A session token and site ownership are required.",
        },
        404: {"model": ErrorResponse, "description": "The site does not exist."},
    },
)
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


@router.delete(
    "/{token_id}",
    status_code=204,
    operation_id="deleteDeploymentToken",
    summary="Revoke a deployment token",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "A session token is required."},
        404: {
            "model": ErrorResponse,
            "description": "The deployment token does not exist.",
        },
    },
)
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
