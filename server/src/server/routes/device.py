from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth_service import Identity
from ..dependencies import get_device_authorization, get_identity, require_user
from ..device_authorization import DeviceAuthorizationService

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

router = APIRouter(include_in_schema=False)


@router.get("/device", response_class=HTMLResponse)
async def device_page(
    request: Request,
    identity: Annotated[Identity | None, Depends(get_identity)] = None,
):
    # The code is deliberately not pre-filled: the approver must copy it from
    # the terminal that started the sign-in, so a phishing link cannot get a
    # bystander to approve a grant they never initiated.
    if not identity:
        return RedirectResponse(url="/?next=/device", status_code=303)
    return templates.TemplateResponse(request, "device.html", {
        "user": identity.user,
        "outcome": None,
    })


@router.post("/device", response_class=HTMLResponse)
async def device_approve(
    request: Request,
    identity: Annotated[Identity, Depends(require_user)],
    device_auth: Annotated[DeviceAuthorizationService, Depends(get_device_authorization)],
    user_code: str = Form(""),
):
    approved = device_auth.approve(user_code, identity.user.id)
    return templates.TemplateResponse(request, "device.html", {
        "user": identity.user,
        "outcome": "approved" if approved else "invalid",
    })
