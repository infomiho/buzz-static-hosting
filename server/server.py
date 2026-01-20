#!/usr/bin/env python3
"""Minimal static site hosting server."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import secrets
import shutil
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Generator
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def parse_multipart(body: bytes, boundary: bytes) -> dict[str, bytes]:
    """Parse multipart form data and return dict of {name: file_content}."""
    parts = body.split(b"--" + boundary)
    files = {}
    for part in parts:
        if not part or part == b"--" or part == b"--\r\n":
            continue
        # Split headers from content
        if b"\r\n\r\n" not in part:
            continue
        header_section, content = part.split(b"\r\n\r\n", 1)
        # Remove trailing \r\n from content
        if content.endswith(b"\r\n"):
            content = content[:-2]
        # Parse Content-Disposition header
        header_text = header_section.decode("utf-8", errors="ignore")
        name_match = re.search(r'name="([^"]+)"', header_text)
        if name_match:
            name = name_match.group(1)
            files[name] = content
    return files

ADJECTIVES = ["cool", "fast", "blue", "red", "green", "happy", "swift", "bright", "calm", "bold"]
NOUNS = ["site", "page", "app", "web", "hub", "box", "lab", "dev", "net", "cloud"]

# Config - set via args or env vars
DATA_DIR = Path(os.environ.get("BUZZ_DATA_DIR", Path(__file__).parent.resolve()))
SITES_DIR = DATA_DIR / "sites"
DB_PATH = DATA_DIR / "data.db"
DOMAIN = os.environ.get("BUZZ_DOMAIN")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
DEV_MODE = False  # Bypasses auth when True
JSON_LOGS = False  # Output logs in JSON format

# Token prefixes
SESSION_TOKEN_PREFIX = "buzz_sess_"
DEPLOY_TOKEN_PREFIX = "buzz_deploy_"

# Store pending device flow codes in memory (device_code -> {user_code, expires_at, access_token, user})
pending_device_codes = {}


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    # Sites table (with owner_id added)
    conn.execute("""CREATE TABLE IF NOT EXISTS sites (
        name TEXT PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, size_bytes INTEGER, owner_id INTEGER)""")
    # Users table
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        github_id INTEGER UNIQUE NOT NULL,
        github_login TEXT NOT NULL,
        github_name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    # Sessions table
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    # Deployment tokens table
    conn.execute("""CREATE TABLE IF NOT EXISTS deployment_tokens (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        site_name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME,
        last_used_at DATETIME,
        FOREIGN KEY (site_name) REFERENCES sites(name) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    # Add owner_id column to sites if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE sites ADD COLUMN owner_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    conn.close()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def generate_subdomain() -> str:
    return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{random.randint(1000, 9999)}"


def get_dir_size(path: str | Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def extract_subdomain(host: str | None) -> str | None:
    if not host:
        return None
    host = host.split(":")[0]
    # Handle configured domain (e.g., *.static.example.com)
    if DOMAIN and host.endswith("." + DOMAIN):
        sub = host[: -(len(DOMAIN) + 1)]
        return sub if sub else None
    # Handle localhost for local development
    parts = host.split(".")
    if len(parts) >= 2 and parts[-1] == "localhost" and parts[0] != "localhost":
        return parts[0]
    return None


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


def hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_session_token() -> str:
    """Generate a new session token."""
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def generate_deploy_token() -> str:
    """Generate a new deployment token."""
    return DEPLOY_TOKEN_PREFIX + secrets.token_urlsafe(32)


def github_request(url: str, data: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
    """Make a request to GitHub API."""
    headers = {"Accept": "application/json"}
    if data:
        data = urlencode(data).encode()
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        return json.loads(e.read().decode())


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        if JSON_LOGS:
            print(json.dumps({"time": datetime.now().isoformat(), "message": args[0]}), flush=True)
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Log API requests."""
        if JSON_LOGS:
            print(json.dumps({
                "time": datetime.now().isoformat(),
                "method": self.command,
                "path": self.path,
                "status": code,
            }), flush=True)
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.command} {self.path} {code}", flush=True)

    def get_auth_context(self):
        """
        Check authentication and return context.
        Returns dict with:
        - authenticated: bool
        - user_id: int or None (for session tokens)
        - site_name: str or None (for deploy tokens - restricts to this site only)
        - token_type: 'session' | 'deploy' | None
        """
        # Dev mode: bypass auth, use user_id=1
        if DEV_MODE:
            return {"authenticated": True, "user_id": 1, "site_name": None, "token_type": "session"}

        token = self.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            token = token[7:]
        if not token:
            return {"authenticated": False, "user_id": None, "site_name": None, "token_type": None}

        token_hash = hash_token(token)

        # Check if it's a session token
        if token.startswith(SESSION_TOKEN_PREFIX):
            with db() as conn:
                row = conn.execute(
                    "SELECT user_id FROM sessions WHERE id = ? AND expires_at > ?",
                    (token_hash, datetime.now().isoformat())
                ).fetchone()
            if row:
                return {"authenticated": True, "user_id": row["user_id"], "site_name": None, "token_type": "session"}
            return {"authenticated": False, "user_id": None, "site_name": None, "token_type": None}

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
                    return {"authenticated": True, "user_id": row["user_id"], "site_name": row["site_name"], "token_type": "deploy"}
            return {"authenticated": False, "user_id": None, "site_name": None, "token_type": None}

        return {"authenticated": False, "user_id": None, "site_name": None, "token_type": None}

    def require_auth(self, allow_deploy_token=False):
        """
        Require authentication. Returns auth context or sends error response.
        If allow_deploy_token is False, only session tokens are accepted.
        """
        ctx = self.get_auth_context()
        if not ctx["authenticated"]:
            self.send_json({"error": "Unauthorized"}, 401)
            return None
        if not allow_deploy_token and ctx["token_type"] == "deploy":
            self.send_json({"error": "Deploy tokens cannot perform this operation"}, 403)
            return None
        return ctx

    def send_unauthorized(self):
        self.send_json({"error": "Unauthorized"}, 401)

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, status=200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, filepath):
        try:
            content = filepath.read_bytes()
            ct = CONTENT_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_text("404 Not Found", 404)

    def send_404(self, site_dir=None):
        if site_dir and (custom := site_dir / "404.html").exists():
            return self.send_file(custom)
        self.send_text("404 Not Found", 404)

    def send_landing_page(self):
        domain = DOMAIN or "localhost:8080"
        template_path = Path(__file__).parent / "landing.html"
        html = template_path.read_text().replace("{{DOMAIN}}", domain)
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        """Read and parse JSON body from request."""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode())
        except json.JSONDecodeError:
            return None

    def do_GET(self):
        subdomain = extract_subdomain(self.headers.get("Host", ""))
        if subdomain:
            return self.serve_static(subdomain)
        # Auth endpoints
        if self.path == "/auth/me":
            return self.handle_auth_me()
        # Site endpoints
        if self.path == "/sites":
            return self.handle_list_sites()
        # Token endpoints
        if self.path == "/tokens":
            return self.handle_list_tokens()
        if self.path == "/":
            return self.send_landing_page()
        if self.path == "/health":
            return self.send_json({"status": "ok"})
        self.send_text("404 Not Found", 404)

    def do_POST(self):
        # Auth endpoints (no auth required)
        if self.path == "/auth/device":
            return self.handle_device_start()
        if self.path == "/auth/device/poll":
            return self.handle_device_poll()
        # Auth endpoints (auth required)
        if self.path == "/auth/logout":
            return self.handle_logout()
        # Site endpoints
        if self.path == "/deploy":
            return self.handle_deploy()
        # Token endpoints
        if self.path == "/tokens":
            return self.handle_create_token()
        self.send_text("404 Not Found", 404)

    def do_DELETE(self):
        if self.path.startswith("/sites/"):
            return self.handle_delete_site(self.path[7:])
        if self.path.startswith("/tokens/"):
            return self.handle_delete_token(self.path[8:])
        self.send_text("404 Not Found", 404)

    def serve_static(self, subdomain):
        site_dir = SITES_DIR / subdomain
        if not site_dir.exists():
            return self.send_text("Site not found", 404)
        path = self.path.split("?")[0]
        if path.endswith("/"):
            path += "index.html"
        filepath = site_dir / path.lstrip("/")
        if filepath.is_file():
            return self.send_file(filepath)
        if not path.endswith(".html"):
            for p in [
                site_dir / (path.lstrip("/") + ".html"),
                site_dir / path.lstrip("/") / "index.html",
            ]:
                if p.is_file():
                    return self.send_file(p)
        self.send_404(site_dir)

    def handle_deploy(self):
        # Require authentication (allow both session and deploy tokens)
        ctx = self.require_auth(allow_deploy_token=True)
        if not ctx:
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return self.send_json({"error": "Expected multipart/form-data"}, 400)

        # Extract boundary from content-type
        boundary_match = re.search(r"boundary=([^\s;]+)", content_type)
        if not boundary_match:
            return self.send_json({"error": "Missing boundary in Content-Type"}, 400)
        boundary = boundary_match.group(1).encode()

        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        subdomain = self.headers.get("x-subdomain", "").strip() or generate_subdomain()
        if not subdomain.replace("-", "").replace("_", "").isalnum():
            return self.send_json({"error": "Invalid subdomain"}, 400)

        # Check deploy token scope
        if ctx["token_type"] == "deploy" and ctx["site_name"] != subdomain:
            return self.send_json({
                "error": f"Deploy token is scoped to site '{ctx['site_name']}', cannot deploy to '{subdomain}'"
            }, 403)

        # Check ownership
        with db() as conn:
            existing = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (subdomain,)).fetchone()
        if existing and existing["owner_id"] is not None and existing["owner_id"] != ctx["user_id"]:
            return self.send_json({
                "error": f"Site '{subdomain}' is owned by another user"
            }, 403)

        # Parse multipart form data
        files = parse_multipart(body, boundary)
        if "file" not in files:
            return self.send_json({"error": "No file uploaded"}, 400)
        site_dir = SITES_DIR / subdomain
        site_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(files["file"])) as zf:
                zf.extractall(site_dir)
        except zipfile.BadZipFile:
            return self.send_json({"error": "Invalid ZIP file"}, 400)

        # Insert or update site with owner
        with db() as conn:
            if existing:
                # Update existing site, claim ownership if unclaimed
                owner_id = existing["owner_id"] if existing["owner_id"] is not None else ctx["user_id"]
                conn.execute(
                    "UPDATE sites SET size_bytes = ?, created_at = ?, owner_id = ? WHERE name = ?",
                    (get_dir_size(site_dir), datetime.now().isoformat(), owner_id, subdomain),
                )
            else:
                # New site, set owner
                conn.execute(
                    "INSERT INTO sites (name, size_bytes, created_at, owner_id) VALUES (?, ?, ?, ?)",
                    (subdomain, get_dir_size(site_dir), datetime.now().isoformat(), ctx["user_id"]),
                )
        if DOMAIN:
            url = f"https://{subdomain}.{DOMAIN}"
        else:
            url = f"http://{subdomain}.localhost:{self.server.server_port}"
        self.send_json({"url": url})

    def handle_list_sites(self):
        # Require session token (deploy tokens can't list sites)
        ctx = self.require_auth(allow_deploy_token=False)
        if not ctx:
            return

        with db() as conn:
            rows = conn.execute(
                "SELECT name, created_at, size_bytes FROM sites WHERE owner_id = ? ORDER BY created_at DESC",
                (ctx["user_id"],)
            ).fetchall()
        self.send_json(
            [
                {
                    "name": r["name"],
                    "created": r["created_at"],
                    "size_bytes": r["size_bytes"],
                }
                for r in rows
            ]
        )

    def handle_delete_site(self, name):
        # Require session token (deploy tokens can't delete sites)
        ctx = self.require_auth(allow_deploy_token=False)
        if not ctx:
            return

        # Check ownership
        with db() as conn:
            site = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (name,)).fetchone()
        if not site:
            return self.send_json({"error": "Site not found"}, 404)
        if site["owner_id"] is not None and site["owner_id"] != ctx["user_id"]:
            return self.send_json({"error": "You don't own this site"}, 403)

        site_dir = SITES_DIR / name
        if site_dir.exists():
            shutil.rmtree(site_dir)
        with db() as conn:
            conn.execute("DELETE FROM sites WHERE name = ?", (name,))
        self.send_response(204)
        self.end_headers()

    # --- Auth handlers ---

    def handle_device_start(self):
        """Start GitHub device flow authentication."""
        if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
            return self.send_json({"error": "GitHub OAuth not configured"}, 500)

        # Request device code from GitHub
        result = github_request(
            "https://github.com/login/device/code",
            {"client_id": GITHUB_CLIENT_ID, "scope": "read:user"}
        )

        if "device_code" not in result:
            return self.send_json({"error": "Failed to start device flow"}, 500)

        # Store pending device code
        pending_device_codes[result["device_code"]] = {
            "user_code": result["user_code"],
            "expires_at": datetime.now() + timedelta(seconds=result.get("expires_in", 900)),
            "interval": result.get("interval", 5),
            "access_token": None,
            "user": None,
        }

        self.send_json({
            "device_code": result["device_code"],
            "user_code": result["user_code"],
            "verification_uri": result.get("verification_uri", "https://github.com/login/device"),
            "interval": result.get("interval", 5),
            "expires_in": result.get("expires_in", 900),
        })

    def handle_device_poll(self):
        """Poll for device flow completion."""
        if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
            return self.send_json({"error": "GitHub OAuth not configured"}, 500)

        data = self.read_json_body()
        if data is None:
            return self.send_json({"error": "Invalid JSON"}, 400)

        device_code = data.get("device_code")
        if not device_code:
            return self.send_json({"error": "device_code required"}, 400)

        # Check if we have this device code
        if device_code not in pending_device_codes:
            return self.send_json({"error": "Invalid or expired device code"}, 400)

        pending = pending_device_codes[device_code]
        if datetime.now() > pending["expires_at"]:
            del pending_device_codes[device_code]
            return self.send_json({"error": "Device code expired"}, 400)

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
                return self.send_json({"status": "pending"})
            elif error == "slow_down":
                return self.send_json({"status": "pending", "interval": result.get("interval", 10)})
            elif error == "expired_token":
                del pending_device_codes[device_code]
                return self.send_json({"error": "Device code expired"}, 400)
            elif error == "access_denied":
                del pending_device_codes[device_code]
                return self.send_json({"error": "User denied access"}, 400)
            else:
                return self.send_json({"error": result.get("error_description", error)}, 400)

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

        self.send_json({
            "status": "complete",
            "token": token,
            "user": {
                "login": github_user["login"],
                "name": github_user.get("name"),
            }
        })

    def handle_auth_me(self):
        """Get current user info."""
        ctx = self.require_auth(allow_deploy_token=False)
        if not ctx:
            return

        with db() as conn:
            user = conn.execute(
                "SELECT github_login, github_name FROM users WHERE id = ?",
                (ctx["user_id"],)
            ).fetchone()

        if not user:
            return self.send_json({"error": "User not found"}, 404)

        self.send_json({
            "login": user["github_login"],
            "name": user["github_name"],
        })

    def handle_logout(self):
        """Invalidate current session."""
        token = self.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            token = token[7:]
        if not token or not token.startswith(SESSION_TOKEN_PREFIX):
            return self.send_json({"error": "No valid session"}, 400)

        token_hash = hash_token(token)
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (token_hash,))
        self.send_json({"success": True})

    # --- Token management handlers ---

    def handle_list_tokens(self):
        """List user's deployment tokens."""
        ctx = self.require_auth(allow_deploy_token=False)
        if not ctx:
            return

        with db() as conn:
            rows = conn.execute(
                """SELECT id, name, site_name, created_at, expires_at, last_used_at
                   FROM deployment_tokens WHERE user_id = ? ORDER BY created_at DESC""",
                (ctx["user_id"],)
            ).fetchall()

        self.send_json([
            {
                "id": r["id"][:16],  # Return truncated hash as ID
                "name": r["name"],
                "site_name": r["site_name"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "last_used_at": r["last_used_at"],
            }
            for r in rows
        ])

    def handle_create_token(self):
        """Create a new deployment token."""
        ctx = self.require_auth(allow_deploy_token=False)
        if not ctx:
            return

        data = self.read_json_body()
        if data is None:
            return self.send_json({"error": "Invalid JSON"}, 400)

        site_name = data.get("site_name")
        name = data.get("name", "Deployment token")
        if not site_name:
            return self.send_json({"error": "site_name required"}, 400)

        # Check user owns the site
        with db() as conn:
            site = conn.execute(
                "SELECT owner_id FROM sites WHERE name = ?", (site_name,)
            ).fetchone()
        if not site:
            return self.send_json({"error": "Site not found"}, 404)
        if site["owner_id"] != ctx["user_id"]:
            return self.send_json({"error": "You don't own this site"}, 403)

        # Create token
        token = generate_deploy_token()
        token_hash = hash_token(token)
        with db() as conn:
            conn.execute(
                "INSERT INTO deployment_tokens (id, name, site_name, user_id) VALUES (?, ?, ?, ?)",
                (token_hash, name, site_name, ctx["user_id"])
            )

        self.send_json({
            "id": token_hash[:16],
            "token": token,  # Only shown once!
            "name": name,
            "site_name": site_name,
        })

    def handle_delete_token(self, token_id):
        """Delete a deployment token."""
        ctx = self.require_auth(allow_deploy_token=False)
        if not ctx:
            return

        with db() as conn:
            # Find token by prefix match (we return truncated ID)
            row = conn.execute(
                "SELECT id FROM deployment_tokens WHERE id LIKE ? AND user_id = ?",
                (token_id + "%", ctx["user_id"])
            ).fetchone()

            if not row:
                return self.send_json({"error": "Token not found"}, 404)

            conn.execute("DELETE FROM deployment_tokens WHERE id = ?", (row["id"],))
        self.send_response(204)
        self.end_headers()


def main() -> None:
    global DOMAIN, DEV_MODE, JSON_LOGS
    parser = argparse.ArgumentParser(description="Static site hosting server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BUZZ_PORT", 8080)))
    parser.add_argument("--domain", help="Domain for hosted sites (env: BUZZ_DOMAIN)")
    parser.add_argument("--dev", action="store_true", help="Dev mode: bypass authentication")
    parser.add_argument("--json-logs", action="store_true", help="Output logs in JSON format")
    args = parser.parse_args()
    # Args override env vars
    if args.domain:
        DOMAIN = args.domain
    if args.dev:
        DEV_MODE = True
    if args.json_logs:
        JSON_LOGS = True
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    server = HTTPServer(("", args.port), RequestHandler)
    print(f"Server running on http://localhost:{args.port}")
    if DOMAIN:
        print(f"Serving sites on *.{DOMAIN}")
    else:
        print(f"Serving sites on *.localhost:{args.port}")
    if DEV_MODE:
        print("DEV MODE: Authentication bypassed")
    elif GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
        print("GitHub OAuth enabled")
    else:
        print("ERROR: GitHub OAuth not configured")
        print("Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables")
        print("Or use --dev flag for local development")
        exit(1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
