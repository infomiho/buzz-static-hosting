import pytest

from server.exceptions import BadRequest
from server.routes.sites import validate_subdomain, build_site_url


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
