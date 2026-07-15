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
from ..auth_service import (
    AuthService, DeviceFlowDenied, DeviceFlowExpired,
    DeviceFlowFailed, DeviceFlowPending, DeviceFlowSlowDown,
    InvalidSession,
)
from ..dependencies import Identity, document_bearer_token, get_auth_service, require_user

router = APIRouter()


@router.post(
    "/device",
    response_model=DeviceAuthorizationResponse,
    operation_id="startDeviceAuthorization",
    summary="Start GitHub device authorization",
    responses={
        500: {
            "model": ErrorResponse,
            "description": "GitHub authentication is unavailable.",
        }
    },
)
async def device_start(auth: Annotated[AuthService, Depends(get_auth_service)]):
    try:
        return auth.start_device_flow()
    except DeviceFlowFailed as e:
        raise HTTPException(status_code=500, detail=e.detail)


@router.post(
    "/device/poll",
    response_model=DevicePollPendingResponse | DevicePollCompleteResponse,
    response_model_exclude_none=True,
    operation_id="pollDeviceAuthorization",
    summary="Poll GitHub device authorization",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "The device flow failed or expired.",
        },
        403: {
            "model": ErrorResponse,
            "description": "The GitHub account is not allowed on this server.",
        },
    },
)
async def device_poll(data: DevicePollRequest, auth: Annotated[AuthService, Depends(get_auth_service)]):
    try:
        result = auth.poll_device_flow(data.device_code)
    except DeviceFlowPending:
        return {"status": "pending"}
    except DeviceFlowSlowDown as e:
        return {"status": "pending", "interval": e.interval}
    except DeviceFlowExpired:
        raise HTTPException(status_code=400, detail="Device code expired")
    except DeviceFlowDenied:
        raise HTTPException(status_code=400, detail="User denied access")
    except DeviceFlowFailed as e:
        raise HTTPException(status_code=400, detail=e.detail)

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
