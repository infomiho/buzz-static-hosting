"""FastAPI dependencies for authentication."""
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from . import config
from .config import SESSION_TOKEN_PREFIX, DEPLOY_TOKEN_PREFIX
from .db import db
from .auth import hash_token


class AuthContext:
    """Authentication context."""
    def __init__(
        self,
        authenticated: bool,
        user_id: int | None,
        site_name: str | None,
        token_type: str | None,
    ):
        self.authenticated = authenticated
        self.user_id = user_id
        self.site_name = site_name  # For deploy tokens - restricts to this site
        self.token_type = token_type  # 'session' | 'deploy' | None


def get_auth_context(authorization: str | None = Header(default=None)) -> AuthContext:
    """Get authentication context from request."""
    # Dev mode: bypass auth, use user_id=1
    if config.DEV_MODE:
        return AuthContext(authenticated=True, user_id=1, site_name=None, token_type="session")

    if not authorization:
        return AuthContext(authenticated=False, user_id=None, site_name=None, token_type=None)

    token = authorization
    if token.startswith("Bearer "):
        token = token[7:]
    if not token:
        return AuthContext(authenticated=False, user_id=None, site_name=None, token_type=None)

    token_hash = hash_token(token)

    # Check if it's a session token
    if token.startswith(SESSION_TOKEN_PREFIX):
        with db() as conn:
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE id = ? AND expires_at > ?",
                (token_hash, datetime.now().isoformat())
            ).fetchone()
        if row:
            return AuthContext(authenticated=True, user_id=row["user_id"], site_name=None, token_type="session")
        return AuthContext(authenticated=False, user_id=None, site_name=None, token_type=None)

    # Check if it's a deploy token
    if token.startswith(DEPLOY_TOKEN_PREFIX):
        with db() as conn:
            row = conn.execute(
                """SELECT user_id, site_name FROM deployment_tokens
                   WHERE id = ? AND (expires_at IS NULL OR expires_at > ?)""",
                (token_hash, datetime.now().isoformat())
            ).fetchone()
            if row:
                # Update last_used_at
                conn.execute(
                    "UPDATE deployment_tokens SET last_used_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), token_hash)
                )
                return AuthContext(authenticated=True, user_id=row["user_id"], site_name=row["site_name"], token_type="deploy")
        return AuthContext(authenticated=False, user_id=None, site_name=None, token_type=None)

    return AuthContext(authenticated=False, user_id=None, site_name=None, token_type=None)


def require_auth(ctx: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
    """Require authentication (session token only)."""
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if ctx.token_type == "deploy":
        raise HTTPException(status_code=403, detail="Deploy tokens cannot perform this operation")
    return ctx


def require_auth_or_deploy(ctx: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
    """Require authentication (session or deploy token)."""
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx
