import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..auth_service import (
    AuthService,
    DeviceFlowDenied,
    DeviceFlowExpired,
    DeviceFlowFailed,
    DeviceFlowPending,
    DeviceFlowSlowDown,
)
from ..cookies import COOKIE_NAME, set_session_cookie, clear_session_cookie
from ..dependencies import get_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard")


class PollRequest(BaseModel):
    device_code: str


@router.post("/login/start")
async def login_start(auth: Annotated[AuthService, Depends(get_auth_service)]):
    try:
        return auth.start_device_flow()
    except DeviceFlowFailed as e:
        raise HTTPException(status_code=500, detail=e.detail)


@router.post("/login/poll")
async def login_poll(
    data: PollRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
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

    response = JSONResponse(content={"status": "complete"})
    set_session_cookie(response, result.token)
    return response


@router.post("/logout")
async def logout(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
):
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            auth.logout(f"Bearer {cookie_token}")
        except Exception:
            logger.warning("Failed to revoke session on logout", exc_info=True)

    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    return response
