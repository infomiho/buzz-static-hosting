from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from . import config
from .auth_service import AuthService, Identity, User


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_identity(
    auth: Annotated[AuthService, Depends(get_auth_service)],
    authorization: str | None = Header(default=None),
) -> Identity | None:
    if config.DEV_MODE:
        return Identity(user=User(id=1, github_login="dev", github_name="Dev User"), token_type="session")
    return auth.authenticate(authorization)


def require_user(identity: Annotated[Identity | None, Depends(get_identity)]) -> Identity:
    if not identity:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if identity.token_type == "deploy":
        raise HTTPException(status_code=403, detail="Deploy tokens cannot perform this operation")
    return identity


def require_identity(identity: Annotated[Identity | None, Depends(get_identity)]) -> Identity:
    if not identity:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return identity
