from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config
from .auth_service import AccessDenied, AuthService, Identity, User
from .cookies import COOKIE_NAME

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


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_identity(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    _credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ],
    authorization: str | None = Header(default=None, include_in_schema=False),
) -> Identity | None:
    if config.DEV_MODE:
        return Identity(user=User(id=1, github_login="dev", github_name="Dev User"), token_type="session")

    if authorization:
        return auth.authenticate(authorization)

    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            return auth.authenticate(f"Bearer {cookie_token}")
        except AccessDenied:
            return None

    return None


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


def require_custom_domain_control_ready(request: Request) -> None:
    if not config.CUSTOM_DOMAINS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Custom domains are not enabled on this Buzz server",
        )
    control = request.app.state.custom_domains.control
    if not config.TRAEFIK_CONTROL_TOKEN or control is None:
        raise HTTPException(
            status_code=503,
            detail="Custom domains are enabled but the control plane is not configured",
        )
    if not control.is_ready():
        raise HTTPException(
            status_code=503,
            detail="Custom domain control plane is not ready",
        )


def require_custom_domain_admission_enabled() -> None:
    if not config.CUSTOM_DOMAIN_ADMISSION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="New custom domain claims are not enabled on this Buzz server",
        )
    if not config.CUSTOM_DOMAIN_ROUTING_ENABLED or not config.CUSTOM_DOMAIN_INGRESS_IPS:
        raise HTTPException(
            status_code=503,
            detail="Custom domain production routing is not configured",
        )
