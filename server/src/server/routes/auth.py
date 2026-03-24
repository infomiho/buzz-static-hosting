from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..auth_service import (
    AuthService, DeviceFlowDenied, DeviceFlowExpired,
    DeviceFlowFailed, DeviceFlowPending, DeviceFlowSlowDown,
    InvalidSession,
)
from ..dependencies import Identity, get_auth_service, require_user

router = APIRouter()


class DevicePollRequest(BaseModel):
    device_code: str


@router.post("/device")
async def device_start(auth: Annotated[AuthService, Depends(get_auth_service)]):
    try:
        return auth.start_device_flow()
    except DeviceFlowFailed as e:
        raise HTTPException(status_code=500, detail=e.detail)


@router.post("/device/poll")
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


@router.get("/me")
async def me(identity: Annotated[Identity, Depends(require_user)]):
    return {"login": identity.user.github_login, "name": identity.user.github_name}


@router.post("/logout")
async def logout(
    auth: Annotated[AuthService, Depends(get_auth_service)],
    authorization: str | None = Header(default=None),
):
    if not authorization:
        raise HTTPException(status_code=400, detail="No valid session")
    try:
        auth.logout(authorization)
    except InvalidSession:
        raise HTTPException(status_code=400, detail="No valid session")
    return {"success": True}
