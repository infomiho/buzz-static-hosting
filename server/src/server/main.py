from __future__ import annotations

import argparse
import uvicorn

from . import config
from .config import (
    ALLOW_REGISTRATION,
    ALLOWED_GITHUB_USERS,
    SITES_DIR,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
)
from .db import db, init_db
from .environment import environment_value
from .site_store import SiteStore


def access_control_warning(
    allow_registration: bool, allowed_users: frozenset[str] | None, user_count: int
) -> str | None:
    if allow_registration or allowed_users or user_count:
        return None
    return (
        "WARNING: BUZZ_ALLOW_REGISTRATION is false, BUZZ_ALLOWED_GITHUB_USERS is empty, "
        "and no users exist. Nobody can log in to this server."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Static site hosting server")
    parser.add_argument("--port", type=int, default=environment_value("BUZZ_PORT"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--domain", help="Domain for hosted sites (env: BUZZ_DOMAIN)")
    parser.add_argument("--dev", action="store_true", help="Bypass authentication")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    if args.domain:
        config.DOMAIN = args.domain
    if args.dev:
        config.DEV_MODE = True

    SITES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    with db() as conn:
        SiteStore(conn, SITES_DIR).reconcile()
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    if not config.DEV_MODE:
        warning = access_control_warning(ALLOW_REGISTRATION, ALLOWED_GITHUB_USERS, user_count)
        if warning:
            print(warning)

    print(f"Server running on http://localhost:{args.port}")
    if config.DOMAIN:
        print(f"Serving sites on *.{config.DOMAIN}")
    else:
        print(f"Serving sites on *.localhost:{args.port}")
    if config.DEV_MODE:
        print("DEV MODE: Authentication bypassed")
    elif GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
        print("GitHub OAuth enabled")
    else:
        print("ERROR: GitHub OAuth not configured")
        print("Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables")
        print("Or use --dev flag for local development")
        exit(1)

    uvicorn.run(
        "server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
