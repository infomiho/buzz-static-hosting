import re
from pathlib import Path


class InvalidSubdomain(ValueError):
    pass


_SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def validated_subdomain(raw: str) -> str:
    subdomain = raw.strip().lower()
    if not _SUBDOMAIN_RE.match(subdomain):
        raise InvalidSubdomain(f"Invalid subdomain: {subdomain!r}")
    return subdomain


def resolve_site_file(sites_dir: Path, subdomain: str, url_path: str) -> Path | None:
    subdomain = validated_subdomain(subdomain)

    site_root = (sites_dir / subdomain).resolve()
    if not site_root.is_relative_to(sites_dir.resolve()):
        return None
    if not site_root.is_dir():
        return None

    url_path = url_path.split("?")[0]
    if url_path.endswith("/"):
        url_path += "index.html"

    def safe_candidate(relative: str) -> Path | None:
        candidate = (site_root / relative.lstrip("/")).resolve()
        if not candidate.is_relative_to(site_root):
            return None
        if candidate.is_file():
            return candidate
        return None

    result = safe_candidate(url_path)
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
