import re
from pathlib import Path
from urllib.parse import unquote


class InvalidSubdomain(ValueError):
    pass


class InvalidPath(ValueError):
    pass


_SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_INVALID_ESCAPE_RE = re.compile(r"%(?![0-9a-fA-F]{2})")
_ENCODED_SEPARATOR_RE = re.compile(r"%(?:2f|5c)", re.IGNORECASE)


def validated_subdomain(raw: str) -> str:
    subdomain = raw.strip().lower()
    if not _SUBDOMAIN_RE.match(subdomain):
        raise InvalidSubdomain(f"Invalid subdomain: {subdomain!r}")
    return subdomain


def normalized_url_path(raw: str) -> str:
    path = raw.split("?", 1)[0]
    if not path.startswith("/") or _INVALID_ESCAPE_RE.search(path):
        raise InvalidPath("Invalid URL path")
    if _ENCODED_SEPARATOR_RE.search(path):
        raise InvalidPath("Encoded path separators are not allowed")

    path = unquote(path)
    if "\\" in path or "\0" in path or "//" in path:
        raise InvalidPath("Invalid URL path")
    parts = path.lstrip("/").split("/") if path != "/" else []
    if any(part in {".", ".."} for part in parts):
        raise InvalidPath("Invalid URL path")
    if path != "/":
        path = path.rstrip("/")
    return path


def resolve_site_file(sites_dir: Path, subdomain: str, url_path: str) -> Path | None:
    try:
        url_path = normalized_url_path(url_path)
    except InvalidPath:
        return None
    return resolve_normalized_site_file(sites_dir, subdomain, url_path)


def resolve_normalized_site_file(
    sites_dir: Path, subdomain: str, url_path: str
) -> Path | None:
    subdomain = validated_subdomain(subdomain)

    site_root = (sites_dir / subdomain).resolve()
    if not site_root.is_relative_to(sites_dir.resolve()):
        return None
    if not site_root.is_dir():
        return None

    def safe_candidate(relative: str) -> Path | None:
        candidate = (site_root / relative.lstrip("/")).resolve()
        if not candidate.is_relative_to(site_root):
            return None
        if candidate.is_file():
            return candidate
        return None

    result = safe_candidate(url_path if url_path != "/" else "/index.html")
    if result:
        return result

    if not url_path.endswith(".html"):
        for suffix_path in [url_path.lstrip("/") + ".html", url_path.lstrip("/") + "/index.html"]:
            result = safe_candidate(suffix_path)
            if result:
                return result

    spa = safe_candidate("200.html")
    if spa:
        return spa

    return None
