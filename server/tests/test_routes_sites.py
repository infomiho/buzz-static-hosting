import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.exceptions import BadRequest
from server.routes.sites import validate_subdomain, build_site_url
from server.site_store import SiteRecord


class TestValidateSubdomain:
    def test_accepts_alphanumeric(self):
        assert validate_subdomain("my-site") == "my-site"
        assert validate_subdomain("site_123") == "site_123"
        assert validate_subdomain("simple") == "simple"

    def test_strips_whitespace(self):
        assert validate_subdomain("  my-site  ") == "my-site"

    def test_rejects_special_characters(self):
        with pytest.raises(BadRequest):
            validate_subdomain("my site!")

        with pytest.raises(BadRequest):
            validate_subdomain("../escape")

        with pytest.raises(BadRequest):
            validate_subdomain("")


class TestBuildSiteUrl:
    def test_with_domain(self):
        assert build_site_url("my-site", "example.com", 8080) == "https://my-site.example.com"

    def test_without_domain(self):
        assert build_site_url("my-site", None, 8080) == "http://my-site.localhost:8080"

    def test_without_domain_custom_port(self):
        assert build_site_url("my-site", None, 3000) == "http://my-site.localhost:3000"


def test_deploy_returns_explicit_site_name(monkeypatch):
    monkeypatch.setattr("server.config.DEV_MODE", True)
    monkeypatch.setattr(
        "server.routes.sites._deploy_site",
        lambda subdomain, archive, owner_id: SiteRecord(
            name=subdomain,
            owner_id=owner_id,
            size_bytes=0,
            created_at="2026-07-16T00:00:00Z",
        ),
    )
    client = TestClient(create_app())

    response = client.post(
        "/deploy",
        headers={"x-subdomain": "my-site"},
        files={"file": ("site.zip", b"zip", "application/zip")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "name": "my-site",
        "url": "http://my-site.localhost:8080",
    }


def test_deploy_rejects_compressed_upload_over_limit(monkeypatch):
    monkeypatch.setattr("server.config.DEV_MODE", True)
    monkeypatch.setattr("server.routes.sites.MAX_ARCHIVE_BYTES", 4)
    client = TestClient(create_app())

    response = client.post(
        "/deploy",
        files={"file": ("site.zip", b"12345", "application/zip")},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "ZIP exceeds the 4-byte compressed upload limit"}


def test_deploy_rejects_request_body_before_multipart_parsing(monkeypatch):
    monkeypatch.setattr("server.config.DEV_MODE", True)
    monkeypatch.setattr("server.app.MAX_DEPLOY_BODY_BYTES", 100)
    client = TestClient(create_app())

    response = client.post(
        "/deploy",
        files={"file": ("site.zip", b"12345", "application/zip")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Request body exceeds the configured deployment limit"
    }


def test_deploy_rejects_chunked_request_body_over_limit(monkeypatch):
    monkeypatch.setattr("server.config.DEV_MODE", True)
    monkeypatch.setattr("server.app.MAX_DEPLOY_BODY_BYTES", 100)
    client = TestClient(create_app())
    body = (
        b"--buzz\r\n"
        b'Content-Disposition: form-data; name="file"; filename="site.zip"\r\n'
        b"Content-Type: application/zip\r\n\r\n"
        + b"a" * 120
        + b"\r\n--buzz--\r\n"
    )

    response = client.post(
        "/deploy",
        content=iter((body[:80], body[80:])),
        headers={"content-type": "multipart/form-data; boundary=buzz"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Request body exceeds the configured deployment limit"
    }


def test_deploy_authenticates_before_parsing_multipart():
    client = TestClient(create_app())

    response = client.post(
        "/deploy",
        content=b"not multipart",
        headers={"content-type": "multipart/form-data; boundary=missing"},
    )

    assert response.status_code == 401
