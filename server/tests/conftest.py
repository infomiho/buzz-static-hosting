import dataclasses
import os

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Database
from server.settings import Settings


@pytest.fixture(autouse=True)
def scrub_environment(monkeypatch):
    """Keep Settings.from_environment() deterministic: a developer's exported
    BUZZ_* or GitHub OAuth variables must not leak into test settings."""
    for name in list(os.environ):
        if name.startswith("BUZZ_") or name.startswith("GITHUB_CLIENT"):
            monkeypatch.delenv(name)


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "data.db")
    db.init()
    return db


@pytest.fixture
def make_settings(tmp_path):
    def _make(**overrides):
        values = {
            "sites_dir": tmp_path,
            "db_path": tmp_path / "data.db",
            "domain": None,
            "analytics_secret": "test-secret",
            **overrides,
        }
        return dataclasses.replace(Settings.from_environment(), **values)

    return _make


@pytest.fixture
def make_app(database, make_settings):
    def _make(**overrides):
        return create_app(settings=make_settings(**overrides), database=database)

    return _make


@pytest.fixture
def client(make_app):
    return TestClient(make_app())
