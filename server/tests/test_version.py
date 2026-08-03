from fastapi.testclient import TestClient

from server import __version__
from server.app import MIN_CLI_VERSION


def test_version_reports_server_and_min_cli_versions(make_app):
    res = TestClient(make_app(dev_mode=True)).get("/version")

    assert res.status_code == 200
    assert res.json() == {
        "version": __version__,
        "min_cli_version": MIN_CLI_VERSION,
    }
