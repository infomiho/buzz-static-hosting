#!/usr/bin/env python3
"""Buzz static site hosting server."""
from __future__ import annotations

import argparse
import os

import uvicorn

from . import config
from .config import SITES_DIR, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
from .db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Static site hosting server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BUZZ_PORT", 8080)))
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--domain", help="Domain for hosted sites (env: BUZZ_DOMAIN)")
    parser.add_argument("--dev", action="store_true", help="Dev mode: bypass authentication")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    # Args override env vars
    if args.domain:
        config.DOMAIN = args.domain
    if args.dev:
        config.DEV_MODE = True

    SITES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

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


if __name__ == "__main__":
    main()
