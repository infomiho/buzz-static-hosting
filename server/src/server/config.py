"""Configuration and constants."""
from __future__ import annotations

import os
from pathlib import Path

# Directory configuration
DATA_DIR = Path(os.environ.get("BUZZ_DATA_DIR", Path(__file__).parent.resolve()))
SITES_DIR = DATA_DIR / "sites"
DB_PATH = DATA_DIR / "data.db"

# Server configuration
DOMAIN = os.environ.get("BUZZ_DOMAIN")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")

# Runtime flags (set via command line args)
DEV_MODE = False
JSON_LOGS = False

# Token prefixes
SESSION_TOKEN_PREFIX = "buzz_sess_"
DEPLOY_TOKEN_PREFIX = "buzz_deploy_"

# Random subdomain generation
ADJECTIVES = ["cool", "fast", "blue", "red", "green", "happy", "swift", "bright", "calm", "bold"]
NOUNS = ["site", "page", "app", "web", "hub", "box", "lab", "dev", "net", "cloud"]

# Content types for static file serving
CONTENT_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
    ".xml": "application/xml",
}

# In-memory storage for pending device flow codes
# device_code -> {user_code, expires_at, access_token, user}
pending_device_codes: dict = {}
