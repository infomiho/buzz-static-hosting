from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

Scope = Literal["server", "standalone"]


@dataclass(frozen=True)
class EnvironmentVariable:
    name: str
    description: str
    default: str | int | bool | None = None
    required: str | None = None
    sensitive: bool = False
    scope: Scope = "server"
    example: str | None = None
    parser: Callable[[str], Any] = str

    def read(self) -> Any:
        value = os.environ.get(self.name)
        if value is None:
            return self.default
        return self.parser(value)


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}")


def parse_github_logins(value: str) -> frozenset[str]:
    return frozenset(login.strip().lower() for login in value.split(",") if login.strip())


PACKAGE_DIR = Path(__file__).parent.resolve()

ENVIRONMENT_VARIABLES = (
    EnvironmentVariable(
        "BUZZ_PORT",
        "Port used by the Buzz server. The `--port` option takes precedence.",
        default=8080,
        example="8080",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_DATA_DIR",
        "Directory containing deployed sites and the SQLite database.",
        default=str(PACKAGE_DIR),
        example="/data",
    ),
    EnvironmentVariable(
        "BUZZ_DOMAIN",
        "Base hostname for the dashboard and site subdomains.",
        required="Required for production hosting.",
        example="buzz.example.com",
    ),
    EnvironmentVariable(
        "GITHUB_CLIENT_ID",
        "Client ID for the GitHub OAuth app with Device Flow enabled.",
        required="Required unless the server runs with --dev.",
        example="Iv1.example",
    ),
    EnvironmentVariable(
        "GITHUB_CLIENT_SECRET",
        "Client secret for the GitHub OAuth app.",
        required="Required unless the server runs with --dev.",
        sensitive=True,
        example="your-github-client-secret",
    ),
    EnvironmentVariable(
        "BUZZ_ANALYTICS_SECRET",
        "Secret used to hash analytics visitors. Falls back to `GITHUB_CLIENT_SECRET`, then a process-local random value.",
        sensitive=True,
        example="replace-with-a-random-secret",
    ),
    EnvironmentVariable(
        "BUZZ_MAX_ARCHIVE_BYTES",
        "Maximum compressed deployment archive size in bytes.",
        default=500 * 1024 * 1024,
        example="524288000",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_SITE_BYTES",
        "Maximum extracted size of one deployed site in bytes.",
        default=500 * 1024 * 1024,
        example="524288000",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_SITE_FILES",
        "Maximum number of files and directories in one deployed site.",
        default=10_000,
        example="10000",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_ARCHIVE_PATH_BYTES",
        "Maximum UTF-8 byte length of a path in a deployment archive.",
        default=1024,
        example="1024",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_GSC_CREDENTIALS",
        "Google service-account JSON or a path to a readable credentials file.",
        sensitive=True,
        example='{"type":"service_account",...}',
    ),
    EnvironmentVariable(
        "BUZZ_GSC_PROPERTY",
        "Google Search Console property. Defaults to `sc-domain:<BUZZ_DOMAIN>`.",
        example="sc-domain:buzz.example.com",
    ),
    EnvironmentVariable(
        "BUZZ_ALLOW_REGISTRATION",
        "Whether new GitHub users can sign up. Existing users keep access when disabled.",
        default=True,
        example="false",
        parser=parse_bool,
    ),
    EnvironmentVariable(
        "BUZZ_ALLOWED_GITHUB_USERS",
        "Comma-separated GitHub usernames allowed to sign in. When set, unlisted users are denied on every request and listed users may sign up even when registration is disabled.",
        example="alice,bob",
        parser=parse_github_logins,
    ),
    EnvironmentVariable(
        "CF_API_TOKEN",
        "Cloudflare API token used by Traefik for DNS-01 validation.",
        required="Required by the standalone Docker Compose deployment.",
        sensitive=True,
        scope="standalone",
        example="your-cloudflare-api-token",
    ),
    EnvironmentVariable(
        "ACME_EMAIL",
        "Email address used for Let's Encrypt certificate notifications.",
        required="Required by the standalone Docker Compose deployment.",
        scope="standalone",
        example="admin@example.com",
    ),
)

ENVIRONMENT_BY_NAME = {variable.name: variable for variable in ENVIRONMENT_VARIABLES}


def environment_value(name: str) -> Any:
    return ENVIRONMENT_BY_NAME[name].read()
