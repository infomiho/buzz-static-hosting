#!/usr/bin/env python3
"""Minimal static site hosting server."""

import argparse
import cgi
import io
import json
import os
import random
import shutil
import sqlite3
import zipfile
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ADJECTIVES = ["cool", "fast", "blue", "red", "green", "happy", "swift", "bright", "calm", "bold"]
NOUNS = ["site", "page", "app", "web", "hub", "box", "lab", "dev", "net", "cloud"]

# Config - set via args or env vars
DATA_DIR = Path(os.environ.get("BUZZ_DATA_DIR", Path(__file__).parent.resolve()))
SITES_DIR = DATA_DIR / "sites"
DB_PATH = DATA_DIR / "data.db"
DOMAIN = os.environ.get("BUZZ_DOMAIN")
AUTH_TOKEN = os.environ.get("BUZZ_TOKEN")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS sites (
        name TEXT PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, size_bytes INTEGER)""")
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


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def check_auth(self):
        """Check if request has valid auth token. Returns True if authorized."""
        if not AUTH_TOKEN:
            return True  # No token configured, allow all
        token = self.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            token = token[7:]
        return token == AUTH_TOKEN

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

    def do_GET(self):
        subdomain = extract_subdomain(self.headers.get("Host", ""))
        if subdomain:
            return self.serve_static(subdomain)
        if self.path == "/sites":
            if not self.check_auth():
                return self.send_unauthorized()
            return self.handle_list_sites()
        if self.path == "/":
            return self.send_text(
                "Static Site Hosting Server\n\nPOST /deploy\nGET /sites\nDELETE /sites/{name}"
            )
        self.send_text("404 Not Found", 404)

    def do_POST(self):
        if self.path == "/deploy":
            if not self.check_auth():
                return self.send_unauthorized()
            return self.handle_deploy()
        self.send_text("404 Not Found", 404)

    def do_DELETE(self):
        if self.path.startswith("/sites/"):
            if not self.check_auth():
                return self.send_unauthorized()
            return self.handle_delete_site(self.path[7:])
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
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return self.send_json({"error": "Expected multipart/form-data"}, 400)
        ctype, pdict = cgi.parse_header(content_type)
        pdict["boundary"] = pdict["boundary"].encode()
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        subdomain = self.headers.get("x-subdomain", "").strip() or generate_subdomain()
        if not subdomain.replace("-", "").replace("_", "").isalnum():
            return self.send_json({"error": "Invalid subdomain"}, 400)
        fs = cgi.FieldStorage(
            fp=io.BytesIO(body),
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )
        if "file" not in fs:
            return self.send_json({"error": "No file uploaded"}, 400)
        site_dir = SITES_DIR / subdomain
        site_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(fs["file"].file.read())) as zf:
                zf.extractall(site_dir)
        except zipfile.BadZipFile:
            return self.send_json({"error": "Invalid ZIP file"}, 400)
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO sites (name, size_bytes, created_at) VALUES (?, ?, ?)",
            (subdomain, get_dir_size(site_dir), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        if DOMAIN:
            url = f"https://{subdomain}.{DOMAIN}"
        else:
            url = f"http://{subdomain}.localhost:{self.server.server_port}"
        self.send_json({"url": url})

    def handle_list_sites(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT name, created_at, size_bytes FROM sites ORDER BY created_at DESC"
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
        site_dir = SITES_DIR / name
        if not site_dir.exists():
            return self.send_json({"error": "Site not found"}, 404)
        shutil.rmtree(site_dir)
        conn = get_db()
        conn.execute("DELETE FROM sites WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        self.send_response(204)
        self.end_headers()


def main():
    global DOMAIN, AUTH_TOKEN
    parser = argparse.ArgumentParser(description="Static site hosting server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BUZZ_PORT", 8080)))
    parser.add_argument("--domain", help="Domain for hosted sites (env: BUZZ_DOMAIN)")
    parser.add_argument("--token", help="Auth token for API access (env: BUZZ_TOKEN)")
    args = parser.parse_args()
    # Args override env vars
    if args.domain:
        DOMAIN = args.domain
    if args.token:
        AUTH_TOKEN = args.token
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    server = HTTPServer(("", args.port), RequestHandler)
    print(f"Server running on http://localhost:{args.port}")
    if DOMAIN:
        print(f"Serving sites on *.{DOMAIN}")
    else:
        print(f"Serving sites on *.localhost:{args.port}")
    if AUTH_TOKEN:
        print("Authentication enabled")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
