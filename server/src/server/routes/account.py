from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..auth_service import Identity
from ..dependencies import get_passkey_service, require_user
from ..passkeys import (
    ChallengeExpired,
    PasskeyNotFound,
    PasskeyService,
    RegistrationFailed,
)

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

router = APIRouter(prefix="/account", include_in_schema=False)


class RegisterPasskeyRequest(BaseModel):
    credential: dict[str, Any]
    name: str | None = None


@router.get("/", response_class=HTMLResponse)
async def account_page(
    request: Request,
    identity: Annotated[Identity, Depends(require_user)],
    passkeys: Annotated[PasskeyService, Depends(get_passkey_service)],
):
    return templates.TemplateResponse(request, "account.html", {
        "user": identity.user,
        "passkeys": passkeys.list(identity.user.id),
    })


@router.post("/passkeys/options")
async def passkey_registration_options(
    identity: Annotated[Identity, Depends(require_user)],
    passkeys: Annotated[PasskeyService, Depends(get_passkey_service)],
):
    return Response(
        content=passkeys.registration_options(identity.user.id),
        media_type="application/json",
    )


@router.post("/passkeys")
async def register_passkey(
    data: RegisterPasskeyRequest,
    identity: Annotated[Identity, Depends(require_user)],
    passkeys: Annotated[PasskeyService, Depends(get_passkey_service)],
):
    try:
        created = passkeys.register(identity.user.id, data.credential, data.name)
    except ChallengeExpired:
        raise HTTPException(status_code=400, detail="Registration expired, try again")
    except RegistrationFailed:
        raise HTTPException(status_code=400, detail="Passkey registration failed")
    return {"id": created.id, "name": created.name}


@router.post("/passkeys/{credential_id}/delete")
async def delete_passkey(
    credential_id: str,
    identity: Annotated[Identity, Depends(require_user)],
    passkeys: Annotated[PasskeyService, Depends(get_passkey_service)],
):
    try:
        passkeys.delete(identity.user.id, credential_id)
    except PasskeyNotFound:
        raise HTTPException(status_code=404, detail="Passkey not found")
    return RedirectResponse(url="/account/", status_code=303)
