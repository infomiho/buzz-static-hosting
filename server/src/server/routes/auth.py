from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from ..api_models import (
    ApiUser,
    DeviceAuthorizationResponse,
    DevicePollCompleteResponse,
    DevicePollPendingResponse,
    DevicePollRequest,
    ErrorResponse,
    LogoutResponse,
)
from ..auth_service import AuthService, InvalidSession
from ..dependencies import (
    Identity,
    document_bearer_token,
    get_auth_service,
    get_device_authorization,
    require_user,
)
from ..device_authorization import DeviceAuthorizationService, DeviceCodeExpired

router = APIRouter()


@router.post(
    "/device",
    response_model=DeviceAuthorizationResponse,
    operation_id="startDeviceAuthorization",
    summary="Start device authorization",
)
async def device_start(
    device_auth: Annotated[DeviceAuthorizationService, Depends(get_device_authorization)],
):
    return device_auth.start()


@router.post(
    "/device/poll",
    response_model=DevicePollPendingResponse | DevicePollCompleteResponse,
    response_model_exclude_none=True,
    operation_id="pollDeviceAuthorization",
    summary="Poll device authorization",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "The device code expired or was already used.",
        },
        403: {
            "model": ErrorResponse,
            "description": "The approving account is not allowed on this server.",
        },
    },
)
async def device_poll(
    data: DevicePollRequest,
    device_auth: Annotated[DeviceAuthorizationService, Depends(get_device_authorization)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    try:
        user_id = device_auth.poll(data.device_code)
    except DeviceCodeExpired:
        raise HTTPException(status_code=400, detail="Device code expired")

    if user_id is None:
        return {"status": "pending"}

    try:
        result = auth.login_by_user_id(user_id)
    except InvalidSession:
        raise HTTPException(status_code=400, detail="Device code expired")

    return {
        "status": "complete",
        "token": result.token,
        "user": {"login": result.user.github_login, "name": result.user.github_name},
    }


@router.get(
    "/me",
    response_model=ApiUser,
    operation_id="getCurrentUser",
    summary="Get the signed-in user",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "A session token is required."},
    },
)
async def me(identity: Annotated[Identity, Depends(require_user)]):
    return {"login": identity.user.github_login, "name": identity.user.github_name}


@router.post(
    "/logout",
    response_model=LogoutResponse,
    operation_id="logout",
    summary="Revoke the current session",
    dependencies=[Depends(document_bearer_token)],
    responses={
        400: {
            "model": ErrorResponse,
            "description": "No valid session was supplied.",
        }
    },
)
async def logout(
    auth: Annotated[AuthService, Depends(get_auth_service)],
    authorization: str | None = Header(default=None, include_in_schema=False),
):
    if not authorization:
        raise HTTPException(status_code=400, detail="No valid session")
    try:
        auth.logout(authorization)
    except InvalidSession:
        raise HTTPException(status_code=400, detail="No valid session")
    return {"success": True}
