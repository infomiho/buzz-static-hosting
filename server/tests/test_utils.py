from server.utils import extract_subdomain, generate_subdomain, is_control_host


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


def test_control_host_with_domain(monkeypatch):
    monkeypatch.setattr("server.utils.DOMAIN", "example.com")
    assert is_control_host("example.com:8080")
    assert not is_control_host("mysite.example.com")
    assert not is_control_host("attacker.example")


def test_control_host_for_local_development():
    assert is_control_host("localhost:8080")
    assert is_control_host("127.0.0.1:8080")
    assert is_control_host("[::1]:8080")
    assert is_control_host("testserver")


def test_rejects_malformed_control_authorities():
    assert not is_control_host("localhost:not-a-port")
    assert not is_control_host("[localhost]junk")
    assert not is_control_host("user@localhost")


def test_production_domain_disables_localhost_tenants(monkeypatch):
    monkeypatch.setattr("server.utils.DOMAIN", "example.com")
    assert extract_subdomain("site.localhost") is None
