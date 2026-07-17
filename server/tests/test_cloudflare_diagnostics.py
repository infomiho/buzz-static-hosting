import ipaddress
import json
import ssl
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from server import db as db_module
from server.cloudflare_diagnostics import (
    MAX_RANGE_AGE,
    CloudflareDiagnostician,
    CloudflareDiagnosticStore,
    CloudflareRangeError,
    CloudflareRanges,
    EdgeProbeResult,
    HttpForwardProbeResult,
    load_cloudflare_ranges,
    probe_cloudflare_edge,
    probe_cloudflare_http_forwarding,
)
from server.custom_domains import DomainClaimStore
from server.domain_activation import ActivationFailed


def range_file(tmp_path, **overrides):
    data = {
        "schema_version": 1,
        "version": "test",
        "published_at": "2026-07-16T00:00:00+00:00",
        "ipv4": ["8.8.8.0/24"],
        "ipv6": ["2001:4860::/32"],
        **overrides,
    }
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(data))
    return path


def test_range_loader_rejects_missing_malformed_and_stale_data(tmp_path):
    with pytest.raises(CloudflareRangeError, match="range_data_missing"):
        load_cloudflare_ranges(tmp_path / "missing.json")
    with pytest.raises(CloudflareRangeError, match="range_data_invalid"):
        load_cloudflare_ranges(range_file(tmp_path, ipv4=["not-a-network"]))
    now = datetime(2026, 7, 16, tzinfo=timezone.utc) + MAX_RANGE_AGE + timedelta(seconds=1)
    with pytest.raises(CloudflareRangeError, match="range_data_stale"):
        load_cloudflare_ranges(range_file(tmp_path), now=now)
    future = datetime(2026, 7, 14, tzinfo=timezone.utc)
    with pytest.raises(CloudflareRangeError, match="range_data_invalid"):
        load_cloudflare_ranges(range_file(tmp_path), now=future)


def test_bundled_ranges_are_valid_and_current():
    ranges = load_cloudflare_ranges()

    assert ranges.version == "2026-07-16"
    assert ranges.contains(ipaddress.ip_address("104.16.0.1"))
    assert ranges.contains(ipaddress.ip_address("2606:4700::1"))


@pytest.fixture
def diagnostic_db(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com", claim_mode="cloudflare")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
    return db_module.db


def test_diagnostics_pin_validated_cloudflare_address_without_activation(diagnostic_db):
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )
    dialed = []

    def edge_probe(address, _claim):
        dialed.append(address)
        return EdgeProbeResult("healthy", None, "healthy", None, address=address)

    CloudflareDiagnostician(
        "origin",
        resolver=lambda _hostname: ("8.8.8.8",),
        edge_probe=edge_probe,
        http_probe=lambda _address, _claim: HttpForwardProbeResult(
            "healthy", None, 200
        ),
        origin_probe=lambda _origin, _claim: None,
        ranges=ranges,
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)
    assert dialed == ["8.8.8.8"]
    assert evidence.dns_status == "healthy"
    assert evidence.edge_http_status == "healthy"
    assert evidence.origin_status == "healthy"
    assert claim.activated_at is None


def test_mixed_dns_answers_fail_before_any_public_dial(diagnostic_db):
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )
    edge_probe = lambda _address, _claim: pytest.fail("edge must not be dialed")

    CloudflareDiagnostician(
        "origin",
        resolver=lambda _hostname: ("8.8.8.8", "1.1.1.1"),
        edge_probe=edge_probe,
        origin_probe=lambda _origin, _claim: None,
        ranges=ranges,
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)
    assert evidence.dns_error == "dns_mixed_cloudflare_addresses"
    assert evidence.edge_tls_status == "not_checked"
    assert evidence.origin_status == "healthy"


def test_stale_range_error_skips_public_dial_but_checks_origin(diagnostic_db):
    origins = []
    CloudflareDiagnostician(
        "origin",
        edge_probe=lambda _address, _claim: pytest.fail("edge must not be dialed"),
        origin_probe=lambda origin, _claim: origins.append(origin),
        range_error="range_data_stale",
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)
    assert evidence.dns_error == "range_data_stale"
    assert origins == ["origin"]


def test_ranges_that_age_out_after_construction_fail_closed(diagnostic_db):
    ranges = CloudflareRanges(
        "old",
        datetime.now(timezone.utc) - MAX_RANGE_AGE - timedelta(seconds=1),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )
    CloudflareDiagnostician(
        "origin",
        edge_probe=lambda _address, _claim: pytest.fail("edge must not be dialed"),
        origin_probe=lambda _origin, _claim: None,
        ranges=ranges,
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)
    assert evidence.dns_error == "range_data_stale"


def test_removal_race_discards_completed_diagnostic(diagnostic_db):
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )

    def remove_during_probe(_address, claim):
        with diagnostic_db() as conn:
            DomainClaimStore(conn).cancel(claim.id, "my-site")
        return EdgeProbeResult("healthy", None, "healthy", None)

    CloudflareDiagnostician(
        "origin",
        resolver=lambda _hostname: ("8.8.8.8",),
        edge_probe=remove_during_probe,
        http_probe=lambda _address, _claim: HttpForwardProbeResult(
            "healthy", None, 200
        ),
        origin_probe=lambda _origin, _claim: None,
        ranges=ranges,
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)
    assert claim.route_status == "removing"
    assert evidence is None


def test_older_same_generation_evidence_cannot_overwrite_newer_result(
    diagnostic_db
):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        store = CloudflareDiagnosticStore(conn)
        base = CloudflareDiagnostician(
            "origin", range_error="range_data_stale"
        )._diagnose(claim)
        newer = replace(
            base,
            checked_at="2099-01-01T00:00:00+00:00",
            dns_error="newer_result",
        )
        older = replace(
            base,
            checked_at="2000-01-01T00:00:00+00:00",
            dns_error="older_result",
        )
        assert store.record(newer) is True
        assert store.record(older) is False
        stored = store.get(claim.id, claim.route_generation)

    assert stored.dns_error == "newer_result"


class FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FakeTls(FakeSocket):
    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data


class FakeContext:
    def __init__(self, tls, error=None):
        self.tls = tls
        self.error = error
        self.server_hostname = None

    def wrap_socket(self, _connection, server_hostname):
        self.server_hostname = server_hostname
        if self.error:
            raise self.error
        return self.tls


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def begin(self):
        pass

    def read(self, _size):
        return self.body

    def getheader(self, name):
        return self.headers.get(name)


def routed_claim(diagnostic_db):
    with diagnostic_db() as conn:
        return DomainClaimStore(conn).list_for_site("my-site")[0]


def activation_diagnostician(diagnostic_db, **overrides):
    claim = routed_claim(diagnostic_db)
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )
    options = {
        "resolver": lambda _hostname: ("8.8.8.8",),
        "edge_probe": lambda address, _claim: EdgeProbeResult(
            "healthy", None, "healthy", None, address=address
        ),
        "http_probe": lambda _address, _claim: HttpForwardProbeResult(
            "healthy", None, 200
        ),
        "origin_probe": lambda _origin, _claim: None,
        "ownership_resolver": lambda _name: (claim.verification_value,),
        "ranges": ranges,
        "activation_enabled": True,
        **overrides,
    }
    return CloudflareDiagnostician("origin", **options)


def make_diagnostic_due(diagnostic_db):
    with diagnostic_db() as conn:
        conn.execute(
            "UPDATE custom_domain_cloudflare_diagnostics SET checked_at = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),),
        )


def test_healthy_cloudflare_evidence_activates_and_serves_claim(diagnostic_db):
    activation_diagnostician(diagnostic_db).run_once()

    with diagnostic_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        resolved = store.find_activated(claim.hostname)

    assert claim.activated_at is not None
    assert claim.activation_error is None
    assert resolved.id == claim.id


def test_ownership_loss_deactivates_immediately(diagnostic_db):
    activation_diagnostician(diagnostic_db).run_once()
    make_diagnostic_due(diagnostic_db)

    activation_diagnostician(
        diagnostic_db, ownership_resolver=lambda _name: ()
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.activation_error == "ownership_txt_mismatch"


def test_challenge_identity_failure_deactivates_immediately(diagnostic_db):
    activation_diagnostician(diagnostic_db).run_once()
    make_diagnostic_due(diagnostic_db)
    mismatch = lambda address, _claim: EdgeProbeResult(
        "healthy", None, "failed", "edge_challenge_mismatch", address=address
    )

    activation_diagnostician(diagnostic_db, edge_probe=mismatch).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.activation_error == "edge_challenge_mismatch"


@pytest.mark.parametrize(
    "edge",
    [
        EdgeProbeResult("failed", "edge_tls_invalid", "not_checked", None),
        EdgeProbeResult("healthy", None, "failed", "cloudflare_526"),
    ],
)
def test_tls_validation_failures_deactivate_immediately(diagnostic_db, edge):
    activation_diagnostician(diagnostic_db).run_once()
    make_diagnostic_due(diagnostic_db)

    activation_diagnostician(
        diagnostic_db, edge_probe=lambda _address, _claim: edge
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None


def test_invalid_range_policy_deactivates_immediately(diagnostic_db):
    activation_diagnostician(diagnostic_db).run_once()
    make_diagnostic_due(diagnostic_db)

    activation_diagnostician(
        diagnostic_db,
        ranges=None,
        range_error="range_data_stale",
        edge_probe=lambda _address, _claim: pytest.fail("edge must not be dialed"),
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.activation_error == "range_data_stale"


@pytest.mark.parametrize("dns_error", ["dns_unavailable", "dns_no_addresses"])
def test_dns_failures_deactivate_immediately(diagnostic_db, dns_error):
    activation_diagnostician(diagnostic_db).run_once()
    make_diagnostic_due(diagnostic_db)

    def resolver(_hostname):
        if dns_error == "dns_unavailable":
            raise ActivationFailed(dns_error)
        return ()

    activation_diagnostician(diagnostic_db, resolver=resolver).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.activation_error == dns_error


def test_router_failure_during_probe_cannot_reactivate_claim(diagnostic_db):
    activation_diagnostician(diagnostic_db).run_once()
    make_diagnostic_due(diagnostic_db)

    def fail_router_during_probe(address, claim):
        with diagnostic_db() as conn:
            DomainClaimStore(conn).record_cloudflare_route_health(
                claim.id, claim.route_generation, "router_not_observed"
            )
        return EdgeProbeResult("healthy", None, "healthy", None, address=address)

    activation_diagnostician(
        diagnostic_db, edge_probe=fail_router_during_probe
    ).run_once()

    with diagnostic_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        resolved = store.find_activated(claim.hostname)
    assert claim.activated_at is None
    assert claim.route_error == "router_not_observed"
    assert resolved is None


def test_transient_edge_failure_has_three_attempt_grace_period(diagnostic_db):
    activation_diagnostician(diagnostic_db).run_once()
    unavailable = lambda address, _claim: EdgeProbeResult(
        "failed", "edge_unavailable", "not_checked", None, address=address
    )

    for expected_failures in (1, 2):
        make_diagnostic_due(diagnostic_db)
        activation_diagnostician(diagnostic_db, edge_probe=unavailable).run_once()
        with diagnostic_db() as conn:
            claim = DomainClaimStore(conn).list_for_site("my-site")[0]
            evidence = CloudflareDiagnosticStore(conn).get(
                claim.id, claim.route_generation
            )
        assert evidence.consecutive_failures == expected_failures
        assert claim.activated_at is not None
        assert claim.activation_error == "edge_unavailable"

    make_diagnostic_due(diagnostic_db)
    activation_diagnostician(diagnostic_db, edge_probe=unavailable).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.activation_error == "edge_unavailable"


def test_healthy_evidence_reactivates_after_transient_shutdown(diagnostic_db):
    activation_diagnostician(diagnostic_db).run_once()
    unavailable = lambda address, _claim: EdgeProbeResult(
        "failed", "edge_unavailable", "not_checked", None, address=address
    )
    for _ in range(3):
        make_diagnostic_due(diagnostic_db)
        activation_diagnostician(diagnostic_db, edge_probe=unavailable).run_once()
    make_diagnostic_due(diagnostic_db)

    activation_diagnostician(diagnostic_db).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)
    assert claim.activated_at is not None
    assert claim.activation_error is None
    assert evidence.consecutive_failures == 0


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(302, headers={"Location": "https://example.com/"}), "edge_redirect"),
        (FakeResponse(403, headers={"CF-Ray": "ray"}), "edge_waf_denied"),
        (FakeResponse(403, headers={"cf-mitigated": "challenge"}), "edge_challenge_present"),
        (FakeResponse(530, b"Error code: 1014"), "cloudflare_1014"),
        (FakeResponse(525), "cloudflare_525"),
        (FakeResponse(526), "cloudflare_526"),
        (FakeResponse(200, b"stale", {"CF-Cache-Status": "HIT"}), "edge_cached_challenge"),
    ],
)
def test_edge_probe_classifies_cloudflare_interference(
    diagnostic_db, monkeypatch, response, error
):
    claim = routed_claim(diagnostic_db)
    tls = FakeTls()
    context = FakeContext(tls)
    monkeypatch.setattr("socket.create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr("ssl.create_default_context", lambda: context)
    monkeypatch.setattr("http.client.HTTPResponse", lambda _socket: response)

    result = probe_cloudflare_edge("8.8.8.8", claim)

    assert result.http_error == error
    assert context.server_hostname == claim.hostname
    assert f"Host: {claim.hostname}\r\n".encode() in tls.sent


def test_edge_probe_reports_invalid_universal_ssl(diagnostic_db, monkeypatch):
    claim = routed_claim(diagnostic_db)
    monkeypatch.setattr("socket.create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr(
        "ssl.create_default_context",
        lambda: FakeContext(FakeTls(), ssl.SSLCertVerificationError()),
    )

    result = probe_cloudflare_edge("8.8.8.8", claim)

    assert result.tls_error == "edge_tls_invalid"
    assert result.http_status == "not_checked"


def test_http_forwarding_probe_rejects_redirects(diagnostic_db, monkeypatch):
    claim = routed_claim(diagnostic_db)
    connection = FakeTls()
    monkeypatch.setattr(
        "socket.create_connection", lambda address, timeout: connection
    )
    monkeypatch.setattr(
        "http.client.HTTPResponse", lambda _socket: FakeResponse(301)
    )

    result = probe_cloudflare_http_forwarding("8.8.8.8", claim)

    assert result.error == "http_forward_redirect"
    assert result.status == "observed"
    assert f"Host: {claim.hostname}\r\n".encode() in connection.sent
