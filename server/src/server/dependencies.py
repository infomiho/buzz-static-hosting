from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from . import config
from .config import SESSION_TOKEN_PREFIX, DEPLOY_TOKEN_PREFIX
from .db import db
from .auth import hash_token


class AuthContext:
    def __init__(self, authenticated: bool, user_id: int | None, site_name: str | None, token_type: str | None):
        self.authenticated = authenticated
        self.user_id = user_id
        self.site_name = site_name
        self.token_type = token_type


def get_auth_context(authorization: str | None = Header(default=None)) -> AuthContext:
    if config.DEV_MODE:
        return AuthContext(True, 1, None, "session")

    if not authorization:
        return AuthContext(False, None, None, None)

    token = authorization.removeprefix("Bearer ")
    if not token:
        return AuthContext(False, None, None, None)

    token_hash = hash_token(token)

    if token.startswith(SESSION_TOKEN_PREFIX):
        with db() as conn:
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE id = ? AND expires_at > ?",
                (token_hash, datetime.now().isoformat())
            ).fetchone()
        if row:
            return AuthContext(True, row["user_id"], None, "session")
        return AuthContext(False, None, None, None)

    if token.startswith(DEPLOY_TOKEN_PREFIX):
        with db() as conn:
            row = conn.execute(
                "SELECT user_id, site_name FROM deployment_tokens WHERE id = ? AND (expires_at IS NULL OR expires_at > ?)",
                (token_hash, datetime.now().isoformat())
            ).fetchone()
            if row:
                conn.execute("UPDATE deployment_tokens SET last_used_at = ? WHERE id = ?", (datetime.now().isoformat(), token_hash))
                return AuthContext(True, row["user_id"], row["site_name"], "deploy")
        return AuthContext(False, None, None, None)

    return AuthContext(False, None, None, None)


def require_auth(ctx: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if ctx.token_type == "deploy":
        raise HTTPException(status_code=403, detail="Deploy tokens cannot perform this operation")
    return ctx


def require_auth_or_deploy(ctx: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx
