"""Authentication routes."""
import json
from datetime import datetime, timedelta
from typing import Annotated
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, SESSION_TOKEN_PREFIX, pending_device_codes
from ..db import db
from ..auth import hash_token, generate_session_token, github_request
from ..dependencies import AuthContext, require_auth

router = APIRouter()


class DevicePollRequest(BaseModel):
    device_code: str


@router.post("/device")
async def device_start():
    """Start GitHub device flow authentication."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub OAuth not configured")

    # Request device code from GitHub
    result = github_request(
        "https://github.com/login/device/code",
        {"client_id": GITHUB_CLIENT_ID, "scope": "read:user"}
    )

    if "device_code" not in result:
        raise HTTPException(status_code=500, detail="Failed to start device flow")

    # Store pending device code
    pending_device_codes[result["device_code"]] = {
        "user_code": result["user_code"],
        "expires_at": datetime.now() + timedelta(seconds=result.get("expires_in", 900)),
        "interval": result.get("interval", 5),
        "access_token": None,
        "user": None,
    }

    return {
        "device_code": result["device_code"],
        "user_code": result["user_code"],
        "verification_uri": result.get("verification_uri", "https://github.com/login/device"),
        "interval": result.get("interval", 5),
        "expires_in": result.get("expires_in", 900),
    }


@router.post("/device/poll")
async def device_poll(data: DevicePollRequest):
    """Poll for device flow completion."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub OAuth not configured")

    device_code = data.device_code

    # Check if we have this device code
    if device_code not in pending_device_codes:
        raise HTTPException(status_code=400, detail="Invalid or expired device code")

    pending = pending_device_codes[device_code]
    if datetime.now() > pending["expires_at"]:
        del pending_device_codes[device_code]
        raise HTTPException(status_code=400, detail="Device code expired")

    # Try to exchange device code for access token
    result = github_request(
        "https://github.com/login/oauth/access_token",
        {
            "client_id": GITHUB_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
    )

    if "error" in result:
        error = result["error"]
        if error == "authorization_pending":
            return {"status": "pending"}
        elif error == "slow_down":
            return {"status": "pending", "interval": result.get("interval", 10)}
        elif error == "expired_token":
            del pending_device_codes[device_code]
            raise HTTPException(status_code=400, detail="Device code expired")
        elif error == "access_denied":
            del pending_device_codes[device_code]
            raise HTTPException(status_code=400, detail="User denied access")
        else:
            raise HTTPException(status_code=400, detail=result.get("error_description", error))

    # Got access token! Fetch user info
    access_token = result["access_token"]
    req = Request("https://api.github.com/user")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Buzz-Static-Hosting")
    with urlopen(req) as resp:
        github_user = json.loads(resp.read().decode())

    # Create or update user in database
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE github_id = ?", (github_user["id"],)
        ).fetchone()

        if existing:
            user_id = existing["id"]
            conn.execute(
                "UPDATE users SET github_login = ?, github_name = ? WHERE id = ?",
                (github_user["login"], github_user.get("name"), user_id)
            )
        else:
            cursor = conn.execute(
                "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
                (github_user["id"], github_user["login"], github_user.get("name"))
            )
            user_id = cursor.lastrowid

        # Create session token
        token = generate_session_token()
        token_hash = hash_token(token)
        expires_at = datetime.now() + timedelta(days=30)
        conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at.isoformat())
        )

    # Clean up device code
    del pending_device_codes[device_code]

    return {
        "status": "complete",
        "token": token,
        "user": {
            "login": github_user["login"],
            "name": github_user.get("name"),
        }
    }


@router.get("/me")
async def me(ctx: Annotated[AuthContext, Depends(require_auth)]):
    """Get current user info."""
    with db() as conn:
        user = conn.execute(
            "SELECT github_login, github_name FROM users WHERE id = ?",
            (ctx.user_id,)
        ).fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "login": user["github_login"],
        "name": user["github_name"],
    }


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)):
    """Invalidate current session."""
    if not authorization:
        raise HTTPException(status_code=400, detail="No valid session")

    token = authorization
    if token.startswith("Bearer "):
        token = token[7:]
    if not token or not token.startswith(SESSION_TOKEN_PREFIX):
        raise HTTPException(status_code=400, detail="No valid session")

    token_hash = hash_token(token)
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (token_hash,))
    return {"success": True}
