from server.utils import extract_subdomain, generate_subdomain


def test_generate_subdomain_format():
    subdomain = generate_subdomain()
    parts = subdomain.split("-")
    assert len(parts) == 3
    assert parts[2].isdigit()


def test_extract_subdomain_with_domain(monkeypatch):
    monkeypatch.setattr("server.utils.DOMAIN", "example.com")
    assert extract_subdomain("mysite.example.com") == "mysite"
    assert extract_subdomain("example.com") is None
    assert extract_subdomain(None) is None


def test_extract_subdomain_localhost():
    assert extract_subdomain("mysite.localhost:8080") == "mysite"
    assert extract_subdomain("localhost:8080") is None
