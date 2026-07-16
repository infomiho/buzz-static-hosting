"""Configuration and constants."""
from __future__ import annotations

from pathlib import Path

from .environment import environment_value

# Directory configuration
DATA_DIR = Path(environment_value("BUZZ_DATA_DIR"))
SITES_DIR = DATA_DIR / "sites"
DB_PATH = DATA_DIR / "data.db"

# Server configuration
DOMAIN = environment_value("BUZZ_DOMAIN")
GITHUB_CLIENT_ID = environment_value("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = environment_value("GITHUB_CLIENT_SECRET")
ANALYTICS_SECRET = environment_value("BUZZ_ANALYTICS_SECRET")

# Access control
ALLOW_REGISTRATION = environment_value("BUZZ_ALLOW_REGISTRATION")
ALLOWED_GITHUB_USERS = environment_value("BUZZ_ALLOWED_GITHUB_USERS")

# Private Traefik custom-domain control plane
CUSTOM_DOMAINS_ENABLED = environment_value("BUZZ_CUSTOM_DOMAINS_ENABLED")
TRAEFIK_CONTROL_TOKEN = environment_value("BUZZ_TRAEFIK_CONTROL_TOKEN")
TRAEFIK_CONTROL_PORT = environment_value("BUZZ_TRAEFIK_CONTROL_PORT")
TRAEFIK_API_URL = environment_value("BUZZ_TRAEFIK_API_URL")
TRAEFIK_API_AUTHORIZATION = environment_value("BUZZ_TRAEFIK_API_AUTHORIZATION")
TRAEFIK_HTTPS_ENTRYPOINT = environment_value("BUZZ_TRAEFIK_HTTPS_ENTRYPOINT")
TRAEFIK_SERVICE = environment_value("BUZZ_TRAEFIK_SERVICE")
CUSTOM_DOMAIN_ROUTING_ENABLED = environment_value("BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED")
CUSTOM_DOMAIN_ADMISSION_ENABLED = environment_value("BUZZ_CUSTOM_DOMAIN_ADMISSION_ENABLED")
CLOUDFLARE_DIAGNOSTICS_ENABLED = environment_value("BUZZ_CLOUDFLARE_DIAGNOSTICS_ENABLED")
MAX_CUSTOM_DOMAINS_PER_SITE = environment_value("BUZZ_MAX_CUSTOM_DOMAINS_PER_SITE")
MAX_CUSTOM_DOMAINS_PER_USER = environment_value("BUZZ_MAX_CUSTOM_DOMAINS_PER_USER")
MAX_CUSTOM_DOMAINS_SERVER_WIDE = environment_value("BUZZ_MAX_CUSTOM_DOMAINS_SERVER_WIDE")
CUSTOM_DOMAIN_INGRESS_IPS = environment_value("BUZZ_CUSTOM_DOMAIN_INGRESS_IPS")
CUSTOM_DOMAIN_ORIGIN_HOST = environment_value("BUZZ_CUSTOM_DOMAIN_ORIGIN_HOST")
TRAEFIK_CERT_RESOLVER = environment_value("BUZZ_TRAEFIK_CERT_RESOLVER")
CUSTOM_DOMAIN_RECONCILE_SECONDS = environment_value("BUZZ_CUSTOM_DOMAIN_RECONCILE_SECONDS")

# Deployment limits
MAX_ARCHIVE_BYTES = environment_value("BUZZ_MAX_ARCHIVE_BYTES")
MAX_SITE_BYTES = environment_value("BUZZ_MAX_SITE_BYTES")
MAX_SITE_FILES = environment_value("BUZZ_MAX_SITE_FILES")
MAX_ARCHIVE_PATH_BYTES = environment_value("BUZZ_MAX_ARCHIVE_PATH_BYTES")

# Google Search Console (optional): service account key JSON or path to it,
# and the property to query (defaults to sc-domain:<BUZZ_DOMAIN>)
GSC_CREDENTIALS = environment_value("BUZZ_GSC_CREDENTIALS")
GSC_PROPERTY = environment_value("BUZZ_GSC_PROPERTY")

# Runtime flags (set via command line args)
DEV_MODE = False

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
