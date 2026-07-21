"""Resolved runtime settings. A frozen snapshot of the environment plus the
command-line overrides, passed explicitly to the components that need it. This
is the single seam between the process environment and the application; nothing
below reads the environment directly."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from .environment import environment_value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    sites_dir: Path
    db_path: Path
    domain: str | None
    dev_mode: bool
    github_client_id: str | None
    github_client_secret: str | None
    analytics_secret: str
    allow_registration: bool
    allowed_github_users: frozenset[str] | None
    custom_domains_enabled: bool
    traefik_control_token: str | None
    traefik_control_port: int
    traefik_api_url: str | None
    traefik_api_authorization: str | None
    traefik_https_entrypoint: str
    traefik_service: str
    traefik_cert_resolver: str
    custom_domain_operator_token: str | None
    custom_domain_ingress_ips: frozenset[str]
    custom_domain_origin_host: str | None
    custom_domain_reconcile_seconds: float
    max_custom_domains_per_site: int
    max_custom_domains_per_user: int
    max_custom_domains_server_wide: int
    max_archive_bytes: int
    max_site_bytes: int
    max_site_files: int
    max_archive_path_bytes: int
    gsc_credentials: str | None
    gsc_property: str | None

    @classmethod
    def from_environment(cls) -> "Settings":
        data_dir = Path(environment_value("BUZZ_DATA_DIR"))
        analytics_secret = (
            environment_value("BUZZ_ANALYTICS_SECRET")
            or environment_value("GITHUB_CLIENT_SECRET")
            or secrets.token_hex(16)
        )
        return cls(
            data_dir=data_dir,
            sites_dir=data_dir / "sites",
            db_path=data_dir / "data.db",
            domain=environment_value("BUZZ_DOMAIN"),
            dev_mode=False,
            github_client_id=environment_value("GITHUB_CLIENT_ID"),
            github_client_secret=environment_value("GITHUB_CLIENT_SECRET"),
            analytics_secret=analytics_secret,
            allow_registration=environment_value("BUZZ_ALLOW_REGISTRATION"),
            allowed_github_users=environment_value("BUZZ_ALLOWED_GITHUB_USERS"),
            custom_domains_enabled=environment_value("BUZZ_CUSTOM_DOMAINS_ENABLED"),
            traefik_control_token=environment_value("BUZZ_TRAEFIK_CONTROL_TOKEN"),
            traefik_control_port=environment_value("BUZZ_TRAEFIK_CONTROL_PORT"),
            traefik_api_url=environment_value("BUZZ_TRAEFIK_API_URL"),
            traefik_api_authorization=environment_value("BUZZ_TRAEFIK_API_AUTHORIZATION"),
            traefik_https_entrypoint=environment_value("BUZZ_TRAEFIK_HTTPS_ENTRYPOINT"),
            traefik_service=environment_value("BUZZ_TRAEFIK_SERVICE"),
            traefik_cert_resolver=environment_value("BUZZ_TRAEFIK_CERT_RESOLVER"),
            custom_domain_operator_token=environment_value("BUZZ_CUSTOM_DOMAIN_OPERATOR_TOKEN"),
            custom_domain_ingress_ips=environment_value("BUZZ_CUSTOM_DOMAIN_INGRESS_IPS"),
            custom_domain_origin_host=environment_value("BUZZ_CUSTOM_DOMAIN_ORIGIN_HOST"),
            custom_domain_reconcile_seconds=environment_value("BUZZ_CUSTOM_DOMAIN_RECONCILE_SECONDS"),
            max_custom_domains_per_site=environment_value("BUZZ_MAX_CUSTOM_DOMAINS_PER_SITE"),
            max_custom_domains_per_user=environment_value("BUZZ_MAX_CUSTOM_DOMAINS_PER_USER"),
            max_custom_domains_server_wide=environment_value("BUZZ_MAX_CUSTOM_DOMAINS_SERVER_WIDE"),
            max_archive_bytes=environment_value("BUZZ_MAX_ARCHIVE_BYTES"),
            max_site_bytes=environment_value("BUZZ_MAX_SITE_BYTES"),
            max_site_files=environment_value("BUZZ_MAX_SITE_FILES"),
            max_archive_path_bytes=environment_value("BUZZ_MAX_ARCHIVE_PATH_BYTES"),
            gsc_credentials=environment_value("BUZZ_GSC_CREDENTIALS"),
            gsc_property=environment_value("BUZZ_GSC_PROPERTY"),
        )
