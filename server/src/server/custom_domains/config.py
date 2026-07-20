"""Startup configuration for the custom-domains runtime. Captures the wiring
knobs the runtime reads once when it starts; admission and capability checks
read the live config module so operators can flip flags without a restart."""
from __future__ import annotations

from dataclasses import dataclass

from .. import config as server_config


@dataclass(frozen=True)
class CustomDomainsConfig:
    custom_domains_enabled: bool
    custom_domain_ingress_ips: frozenset[str]
    custom_domain_operator_token: str | None
    custom_domain_origin_host: str | None
    custom_domain_reconcile_seconds: float
    traefik_api_url: str | None
    traefik_api_authorization: str | None
    traefik_cert_resolver: str | None
    traefik_control_port: int
    traefik_control_token: str | None
    traefik_https_entrypoint: str
    traefik_service: str

    @classmethod
    def from_config(cls) -> "CustomDomainsConfig":
        return cls(
            custom_domains_enabled=server_config.CUSTOM_DOMAINS_ENABLED,
            custom_domain_ingress_ips=server_config.CUSTOM_DOMAIN_INGRESS_IPS,
            custom_domain_operator_token=server_config.CUSTOM_DOMAIN_OPERATOR_TOKEN,
            custom_domain_origin_host=server_config.CUSTOM_DOMAIN_ORIGIN_HOST,
            custom_domain_reconcile_seconds=server_config.CUSTOM_DOMAIN_RECONCILE_SECONDS,
            traefik_api_url=server_config.TRAEFIK_API_URL,
            traefik_api_authorization=server_config.TRAEFIK_API_AUTHORIZATION,
            traefik_cert_resolver=server_config.TRAEFIK_CERT_RESOLVER,
            traefik_control_port=server_config.TRAEFIK_CONTROL_PORT,
            traefik_control_token=server_config.TRAEFIK_CONTROL_TOKEN,
            traefik_https_entrypoint=server_config.TRAEFIK_HTTPS_ENTRYPOINT,
            traefik_service=server_config.TRAEFIK_SERVICE,
        )
