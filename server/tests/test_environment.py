import pytest

from server.environment import ENVIRONMENT_BY_NAME, ENVIRONMENT_VARIABLES


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


def test_sensitive_settings_are_marked():
    sensitive = {
        variable.name for variable in ENVIRONMENT_VARIABLES if variable.sensitive
    }
    assert sensitive == {
        "GITHUB_CLIENT_SECRET",
        "BUZZ_ANALYTICS_SECRET",
        "BUZZ_GSC_CREDENTIALS",
        "CF_API_TOKEN",
    }
