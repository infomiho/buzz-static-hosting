"""Utility functions."""
from __future__ import annotations

import random
import re
from pathlib import Path

from .config import ADJECTIVES, NOUNS, DOMAIN


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


def generate_subdomain() -> str:
    """Generate a random subdomain."""
    return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{random.randint(1000, 9999)}"


def get_dir_size(path: str | Path) -> int:
    """Get total size of all files in a directory."""
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def extract_subdomain(host: str | None) -> str | None:
    """Extract subdomain from host header."""
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
