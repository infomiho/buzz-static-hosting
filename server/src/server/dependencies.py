from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth_service import DEV_SESSION_ID, AccessDenied, AuthService, Identity, User
from .cookies import session_cookie_name
from .db import Database
from .device_authorization import DeviceAuthorizationService
from .github_login import GitHubOAuth
from .passkeys import PasskeyService
from .settings import Settings

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description=(
        "A Buzz session or deployment token. Most operations require a session. "
        "Deployment tokens are accepted only for deployment to their assigned site."
    ),
)


def document_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ],
) -> None:
    pass


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_passkey_service(request: Request) -> PasskeyService:
    return request.app.state.passkeys


def get_device_authorization(request: Request) -> DeviceAuthorizationService:
    return request.app.state.device_authorization


def get_github_oauth(request: Request) -> GitHubOAuth:
    return request.app.state.github_oauth


def get_identity(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    _credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ],
    authorization: str | None = Header(default=None, include_in_schema=False),
) -> Identity | None:
    if settings.dev_mode:
        return Identity(
            user=User(id=1, github_login="dev", github_name="Dev User"),
            token_type="session",
            session_id=DEV_SESSION_ID,
        )

    if authorization:
        return auth.authenticate(authorization)

    cookie_token = request.cookies.get(session_cookie_name(not settings.dev_mode))
    if cookie_token:
        try:
            return auth.authenticate(f"Bearer {cookie_token}")
        except AccessDenied:
            return None

    return None


def require_authenticated_user(
    identity: Annotated[Identity | None, Depends(get_identity)],
) -> Identity:
    if not identity:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if identity.token_type == "deploy":
        raise HTTPException(status_code=403, detail="Deploy tokens cannot perform this operation")
    return identity


def require_control_user(
    identity: Annotated[Identity, Depends(require_authenticated_user)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> Identity:
    if not auth.user_is_allowed(identity.user.id):
        raise HTTPException(
            status_code=403,
            detail=(
                f"GitHub account '{identity.user.github_login}' cannot manage "
                "this Buzz server"
            ),
        )
    return identity


# Existing management routes import this name. Its meaning is intentionally
# control-plane admission; hosted Access uses require_authenticated_user.
require_user = require_control_user


def require_identity(identity: Annotated[Identity | None, Depends(get_identity)]) -> Identity:
    if not identity:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return identity


def require_deploy_identity(
    identity: Annotated[Identity, Depends(require_identity)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> Identity:
    if identity.token_type == "session" and not auth.user_is_allowed(identity.user.id):
        raise HTTPException(
            status_code=403,
            detail=(
                f"GitHub account '{identity.user.github_login}' cannot manage "
                "this Buzz server"
            ),
        )
    return identity


def require_custom_domain_control_ready(request: Request) -> None:
    capability = request.app.state.custom_domains.capabilities()
    if not capability.control_ready:
        raise HTTPException(status_code=503, detail=capability.detail)
