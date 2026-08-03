import tomllib
from pathlib import Path

import pytest

from server import __version__
from server.app import MIN_CLI_VERSION

SERVER_DIR = Path(__file__).parents[1]


def test_version_reports_server_and_min_cli_versions(client):
    res = client.get("/version")

    assert res.status_code == 200
    assert res.json() == {
        "version": __version__,
        "min_cli_version": MIN_CLI_VERSION,
    }


def test_version_constant_matches_pyproject():
    with (SERVER_DIR / "pyproject.toml").open("rb") as f:
        project_version = tomllib.load(f)["project"]["version"]

    assert __version__ == project_version


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.coolify.yml"])
def test_compose_files_pin_the_current_version(name):
    compose = (SERVER_DIR / name).read_text()

    assert f"ghcr.io/infomiho/buzzstatic:{__version__}" in compose
