import pytest

from server.environment import (
    ENVIRONMENT_BY_NAME,
    ENVIRONMENT_VARIABLES,
    parse_bool,
    parse_github_logins,
    parse_positive_int,
    parse_public_ips,
)


def test_environment_variable_names_are_unique():
    names = [variable.name for variable in ENVIRONMENT_VARIABLES]
    assert len(names) == len(set(names))


def test_environment_registry_covers_server_and_deployment_settings():
    assert set(ENVIRONMENT_BY_NAME) == {
        "BUZZ_PORT",
        "BUZZ_DATA_DIR",
        "BUZZ_DOMAIN",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
        "BUZZ_ANALYTICS_SECRET",
        "BUZZ_MAX_ARCHIVE_BYTES",
        "BUZZ_MAX_SITE_BYTES",
        "BUZZ_MAX_SITE_FILES",
        "BUZZ_MAX_ARCHIVE_PATH_BYTES",
        "BUZZ_GSC_CREDENTIALS",
        "BUZZ_GSC_PROPERTY",
        "BUZZ_ALLOW_REGISTRATION",
        "BUZZ_ALLOWED_GITHUB_USERS",
        "BUZZ_CUSTOM_DOMAINS_ENABLED",
        "BUZZ_TRAEFIK_CONTROL_TOKEN",
        "BUZZ_TRAEFIK_CONTROL_PORT",
        "BUZZ_TRAEFIK_API_URL",
        "BUZZ_TRAEFIK_API_AUTHORIZATION",
        "BUZZ_TRAEFIK_HTTPS_ENTRYPOINT",
        "BUZZ_TRAEFIK_SERVICE",
        "BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED",
        "BUZZ_CUSTOM_DOMAIN_ADMISSION_ENABLED",
        "BUZZ_CLOUDFLARE_DIAGNOSTICS_ENABLED",
        "BUZZ_MAX_CUSTOM_DOMAINS_PER_SITE",
        "BUZZ_MAX_CUSTOM_DOMAINS_PER_USER",
        "BUZZ_MAX_CUSTOM_DOMAINS_SERVER_WIDE",
        "BUZZ_CUSTOM_DOMAIN_INGRESS_IPS",
        "BUZZ_CUSTOM_DOMAIN_ORIGIN_HOST",
        "BUZZ_TRAEFIK_CERT_RESOLVER",
        "BUZZ_CUSTOM_DOMAIN_RECONCILE_SECONDS",
        "BUZZ_CUSTOM_DOMAIN_ACME_CA_SERVER",
        "CF_API_TOKEN",
        "ACME_EMAIL",
    }


def test_numeric_setting_preserves_invalid_value_failure(monkeypatch):
    monkeypatch.setenv("BUZZ_MAX_SITE_FILES", "not-a-number")
    with pytest.raises(ValueError):
        ENVIRONMENT_BY_NAME["BUZZ_MAX_SITE_FILES"].read()


def test_proxy_settings_are_not_server_settings():
    assert ENVIRONMENT_BY_NAME["CF_API_TOKEN"].scope == "standalone"
    assert ENVIRONMENT_BY_NAME["ACME_EMAIL"].scope == "standalone"


@pytest.mark.parametrize("value", ["1", "true", "True", " yes ", "on"])
def test_parse_bool_truthy(value):
    assert parse_bool(value) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", " no ", "off"])
def test_parse_bool_falsy(value):
    assert parse_bool(value) is False


def test_parse_bool_rejects_unknown_value():
    with pytest.raises(ValueError):
        parse_bool("maybe")


def test_parse_positive_int_rejects_zero_and_negative_values():
    assert parse_positive_int("5") == 5
    with pytest.raises(ValueError, match="positive integer"):
        parse_positive_int("0")
    with pytest.raises(ValueError, match="positive integer"):
        parse_positive_int("-1")


def test_parse_github_logins_normalizes_and_skips_blanks():
    assert parse_github_logins(" Alice , BOB ,,") == frozenset({"alice", "bob"})
    assert parse_github_logins("") == frozenset()


def test_allowed_github_users_defaults_to_not_set(monkeypatch):
    monkeypatch.delenv("BUZZ_ALLOWED_GITHUB_USERS", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_ALLOWED_GITHUB_USERS"].read() is None


def test_allow_registration_defaults_to_true(monkeypatch):
    monkeypatch.delenv("BUZZ_ALLOW_REGISTRATION", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_ALLOW_REGISTRATION"].read() is True


def test_custom_domains_default_to_disabled(monkeypatch):
    monkeypatch.delenv("BUZZ_CUSTOM_DOMAINS_ENABLED", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_CUSTOM_DOMAINS_ENABLED"].read() is False


def test_custom_domain_routing_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED"].read() is False


def test_custom_domain_admission_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("BUZZ_CUSTOM_DOMAIN_ADMISSION_ENABLED", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_CUSTOM_DOMAIN_ADMISSION_ENABLED"].read() is False


def test_cloudflare_diagnostics_default_to_disabled(monkeypatch):
    monkeypatch.delenv("BUZZ_CLOUDFLARE_DIAGNOSTICS_ENABLED", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_CLOUDFLARE_DIAGNOSTICS_ENABLED"].read() is False


def test_public_ingress_ips_are_normalized_and_must_be_global():
    assert parse_public_ips(" 8.8.8.8,2001:4860:4860::8888 ") == frozenset(
        {"8.8.8.8", "2001:4860:4860::8888"}
    )
    with pytest.raises(ValueError, match="public ingress"):
        parse_public_ips("127.0.0.1")


def test_sensitive_settings_are_marked():
    sensitive = {
        variable.name for variable in ENVIRONMENT_VARIABLES if variable.sensitive
    }
    assert sensitive == {
        "GITHUB_CLIENT_SECRET",
        "BUZZ_ANALYTICS_SECRET",
        "BUZZ_GSC_CREDENTIALS",
        "BUZZ_TRAEFIK_CONTROL_TOKEN",
        "BUZZ_TRAEFIK_API_AUTHORIZATION",
        "CF_API_TOKEN",
    }
