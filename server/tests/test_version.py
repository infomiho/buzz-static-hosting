import tomllib
from pathlib import Path

from server import __version__
from server.app import MIN_CLI_VERSION


def test_version_reports_server_and_min_cli_versions(client):
    res = client.get("/version")

    assert res.status_code == 200
    assert res.json() == {
        "version": __version__,
        "min_cli_version": MIN_CLI_VERSION,
    }


def test_version_constant_matches_pyproject():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    with pyproject.open("rb") as f:
        project_version = tomllib.load(f)["project"]["version"]

    assert __version__ == project_version
