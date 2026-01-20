#!/usr/bin/env python3
"""Minimal static site hosting server."""

import argparse
import cgi
import hashlib
import io
import json
import os
import random
import secrets
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import HTTPError

ADJECTIVES = ["cool", "fast", "blue", "red", "green", "happy", "swift", "bright", "calm", "bold"]
NOUNS = ["site", "page", "app", "web", "hub", "box", "lab", "dev", "net", "cloud"]

# Config - set via args or env vars
DATA_DIR = Path(os.environ.get("BUZZ_DATA_DIR", Path(__file__).parent.resolve()))
SITES_DIR = DATA_DIR / "sites"
DB_PATH = DATA_DIR / "data.db"
DOMAIN = os.environ.get("BUZZ_DOMAIN")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")

# Token prefixes
SESSION_TOKEN_PREFIX = "buzz_sess_"
DEPLOY_TOKEN_PREFIX = "buzz_deploy_"

# Store pending device flow codes in memory (device_code -> {user_code, expires_at, access_token, user})
pending_device_codes = {}


def init_db():
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


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def generate_subdomain():
    return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{random.randint(1000, 9999)}"


def get_dir_size(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def extract_subdomain(host):
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


def hash_token(token):
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_session_token():
    """Generate a new session token."""
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def generate_deploy_token():
    """Generate a new deployment token."""
    return DEPLOY_TOKEN_PREFIX + secrets.token_urlsafe(32)


def github_request(url, data=None, method="POST"):
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
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def get_auth_context(self):
        """
        Check authentication and return context.
        Returns dict with:
        - authenticated: bool
        - user_id: int or None (for session tokens)
        - site_name: str or None (for deploy tokens - restricts to this site only)
        - token_type: 'session' | 'deploy' | None
        """
        token = self.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            token = token[7:]
        if not token:
            return {"authenticated": False, "user_id": None, "site_name": None, "token_type": None}

        token_hash = hash_token(token)
        conn = get_db()

        # Check if it's a session token
        if token.startswith(SESSION_TOKEN_PREFIX):
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE id = ? AND expires_at > ?",
                (token_hash, datetime.now().isoformat())
            ).fetchone()
            conn.close()
            if row:
                return {"authenticated": True, "user_id": row["user_id"], "site_name": None, "token_type": "session"}
            return {"authenticated": False, "user_id": None, "site_name": None, "token_type": None}

        # Check if it's a deploy token
        if token.startswith(DEPLOY_TOKEN_PREFIX):
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
                conn.commit()
                conn.close()
                return {"authenticated": True, "user_id": row["user_id"], "site_name": row["site_name"], "token_type": "deploy"}
            conn.close()
            return {"authenticated": False, "user_id": None, "site_name": None, "token_type": None}

        conn.close()
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
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Buzz - Static Site Hosting</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; background: #fff; color: #111; min-height: 100vh; padding: 2rem; }}
        .container {{ max-width: 700px; margin: 0 auto; }}
        h1 {{ font-size: 2.5rem; margin-bottom: 0.5rem; color: #000; }}
        .subtitle {{ color: #666; margin-bottom: 2rem; font-size: 1.1rem; }}
        .step {{ background: #fafafa; border: 1px solid #e5e5e5; border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; }}
        .step-header {{ display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }}
        .step-num {{ background: #000; color: #fff; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 0.9rem; }}
        .step h2 {{ font-size: 1.1rem; color: #000; }}
        .step p {{ color: #666; margin-bottom: 0.75rem; font-size: 0.95rem; }}
        pre {{ background: #fff; border: 1px solid #e5e5e5; border-radius: 6px; padding: 1rem; overflow-x: auto; font-size: 0.9rem; }}
        code {{ color: #000; font-family: 'SF Mono', Consolas, monospace; }}
        .highlight {{ font-weight: 600; }}
        a {{ color: #000; text-decoration: underline; }}
        a:hover {{ color: #666; }}
        .footer {{ margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid #e5e5e5; color: #888; font-size: 0.9rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Buzz</h1>
        <p class="subtitle">Fast static site hosting with GitHub authentication</p>

        <div class="step">
            <div class="step-header">
                <span class="step-num">1</span>
                <h2>Install the CLI</h2>
            </div>
            <pre><code>npm install -g @infomiho/buzz-cli</code></pre>
        </div>

        <div class="step">
            <div class="step-header">
                <span class="step-num">2</span>
                <h2>Connect to this server</h2>
            </div>
            <pre><code>buzz config server https://{domain}</code></pre>
        </div>

        <div class="step">
            <div class="step-header">
                <span class="step-num">3</span>
                <h2>Login with GitHub</h2>
            </div>
            <pre><code>buzz login</code></pre>
        </div>

        <div class="step">
            <div class="step-header">
                <span class="step-num">4</span>
                <h2>Deploy your site</h2>
            </div>
            <p>Deploy any directory containing static files:</p>
            <pre><code>buzz deploy ./dist <span class="highlight">--subdomain my-site</span></code></pre>
            <p style="margin-top: 0.75rem;">Your site will be live at <code>https://<span class="highlight">my-site</span>.{domain}</code></p>
        </div>

        <div class="footer">
            <p>View source on <a href="https://github.com/infomiho/buzz-static-hosting" target="_blank">GitHub</a></p>
        </div>
    </div>
</body>
</html>"""
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
        ctype, pdict = cgi.parse_header(content_type)
        pdict["boundary"] = pdict["boundary"].encode()
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
        conn = get_db()
        existing = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (subdomain,)).fetchone()
        if existing and existing["owner_id"] is not None and existing["owner_id"] != ctx["user_id"]:
            conn.close()
            return self.send_json({
                "error": f"Site '{subdomain}' is owned by another user"
            }, 403)

        fs = cgi.FieldStorage(
            fp=io.BytesIO(body),
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )
        if "file" not in fs:
            conn.close()
            return self.send_json({"error": "No file uploaded"}, 400)
        site_dir = SITES_DIR / subdomain
        site_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(fs["file"].file.read())) as zf:
                zf.extractall(site_dir)
        except zipfile.BadZipFile:
            conn.close()
            return self.send_json({"error": "Invalid ZIP file"}, 400)

        # Insert or update site with owner
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
        conn.commit()
        conn.close()
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

        conn = get_db()
        # Only show user's own sites
        rows = conn.execute(
            "SELECT name, created_at, size_bytes FROM sites WHERE owner_id = ? ORDER BY created_at DESC",
            (ctx["user_id"],)
        ).fetchall()
        conn.close()
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
        conn = get_db()
        site = conn.execute("SELECT owner_id FROM sites WHERE name = ?", (name,)).fetchone()
        if not site:
            conn.close()
            return self.send_json({"error": "Site not found"}, 404)
        if site["owner_id"] is not None and site["owner_id"] != ctx["user_id"]:
            conn.close()
            return self.send_json({"error": "You don't own this site"}, 403)

        site_dir = SITES_DIR / name
        if site_dir.exists():
            shutil.rmtree(site_dir)
        conn.execute("DELETE FROM sites WHERE name = ?", (name,))
        conn.commit()
        conn.close()
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
        conn = get_db()
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
        conn.commit()
        conn.close()

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

        conn = get_db()
        user = conn.execute(
            "SELECT github_login, github_name FROM users WHERE id = ?",
            (ctx["user_id"],)
        ).fetchone()
        conn.close()

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
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE id = ?", (token_hash,))
        conn.commit()
        conn.close()
        self.send_json({"success": True})

    # --- Token management handlers ---

    def handle_list_tokens(self):
        """List user's deployment tokens."""
        ctx = self.require_auth(allow_deploy_token=False)
        if not ctx:
            return

        conn = get_db()
        rows = conn.execute(
            """SELECT id, name, site_name, created_at, expires_at, last_used_at
               FROM deployment_tokens WHERE user_id = ? ORDER BY created_at DESC""",
            (ctx["user_id"],)
        ).fetchall()
        conn.close()

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
        conn = get_db()
        site = conn.execute(
            "SELECT owner_id FROM sites WHERE name = ?", (site_name,)
        ).fetchone()
        if not site:
            conn.close()
            return self.send_json({"error": "Site not found"}, 404)
        if site["owner_id"] != ctx["user_id"]:
            conn.close()
            return self.send_json({"error": "You don't own this site"}, 403)

        # Create token
        token = generate_deploy_token()
        token_hash = hash_token(token)
        conn.execute(
            "INSERT INTO deployment_tokens (id, name, site_name, user_id) VALUES (?, ?, ?, ?)",
            (token_hash, name, site_name, ctx["user_id"])
        )
        conn.commit()
        conn.close()

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

        conn = get_db()
        # Find token by prefix match (we return truncated ID)
        row = conn.execute(
            "SELECT id FROM deployment_tokens WHERE id LIKE ? AND user_id = ?",
            (token_id + "%", ctx["user_id"])
        ).fetchone()

        if not row:
            conn.close()
            return self.send_json({"error": "Token not found"}, 404)

        conn.execute("DELETE FROM deployment_tokens WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        self.send_response(204)
        self.end_headers()


def main():
    global DOMAIN
    parser = argparse.ArgumentParser(description="Static site hosting server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BUZZ_PORT", 8080)))
    parser.add_argument("--domain", help="Domain for hosted sites (env: BUZZ_DOMAIN)")
    args = parser.parse_args()
    # Args override env vars
    if args.domain:
        DOMAIN = args.domain
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    server = HTTPServer(("", args.port), RequestHandler)
    print(f"Server running on http://localhost:{args.port}")
    if DOMAIN:
        print(f"Serving sites on *.{DOMAIN}")
    else:
        print(f"Serving sites on *.localhost:{args.port}")
    if GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
        print("GitHub OAuth enabled")
    else:
        print("WARNING: GitHub OAuth not configured (set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
