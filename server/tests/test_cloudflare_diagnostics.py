import errno
import ipaddress
import json
import ssl
from datetime import datetime, timedelta, timezone

import pytest

from server import db as db_module
from server.cloudflare_diagnostics import (
    CloudflareDiagnostician,
    CloudflareDiagnosticStore,
    HttpForwardProbeResult,
    probe_cloudflare_http_forwarding,
)
from server.custom_domains import DomainClaimStore
from server.domain_evidence import AddressAnswer, DomainDnsObserver, DomainEvidenceCollector
from server.domain_probes import (
    MAX_RANGE_AGE,
    ActivationFailed,
    CloudflareRangeError,
    CloudflareRangeState,
    CloudflareRanges,
    EdgeProbeResult,
    load_cloudflare_ranges,
    probe_cloudflare_edge,
)


def range_file(tmp_path, **overrides):
    path = tmp_path / "ranges.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "test",
                "published_at": "2026-07-16T00:00:00+00:00",
                "ipv4": ["8.8.8.0/24"],
                "ipv6": ["2001:4860::/32"],
                **overrides,
            }
        )
    )
    return path


def test_range_loader_fails_closed_for_missing_invalid_and_stale_data(tmp_path):
    with pytest.raises(CloudflareRangeError, match="range_data_missing"):
        load_cloudflare_ranges(tmp_path / "missing.json")
    with pytest.raises(CloudflareRangeError, match="range_data_invalid"):
        load_cloudflare_ranges(range_file(tmp_path, ipv4=["invalid"]))
    now = datetime(2026, 7, 16, tzinfo=timezone.utc) + MAX_RANGE_AGE + timedelta(seconds=1)
    with pytest.raises(CloudflareRangeError, match="range_data_stale"):
        load_cloudflare_ranges(range_file(tmp_path), now=now)


@pytest.fixture
def diagnostic_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com", claim_mode="cloudflare")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
    return db_module.db


def diagnostician(diagnostic_db, addresses=("8.8.8.8",), **overrides):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    range_state = overrides.pop(
        "range_state",
        CloudflareRangeState(
            CloudflareRanges(
                "test",
                datetime.now(timezone.utc),
                (
                    ipaddress.ip_network("8.8.8.0/24"),
                    ipaddress.ip_network("2001:4860::/32"),
                ),
            )
        ),
    )
    edge_probe = overrides.pop(
        "edge_probe",
        lambda address, _claim: EdgeProbeResult(
            "healthy", None, "healthy", None, address=address
        ),
    )

    def lookup(_name, family):
        version = 4 if family == "A" else 6
        family_addresses = tuple(
            address
            for address in addresses
            if ipaddress.ip_address(address).version == version
        )
        if not family_addresses:
            return AddressAnswer.no_answer()
        return AddressAnswer.addresses(family_addresses, 60)

    collector = overrides.pop("evidence_collector", None) or DomainEvidenceCollector(
        DomainDnsObserver(lookup, frozenset(), cloudflare_range_state=range_state),
        "origin",
        router_validator=overrides.pop("router_validator", lambda _claim: None),
        ownership_resolver=overrides.pop(
            "ownership_resolver", lambda _name: (claim.verification_value,)
        ),
        origin_probe=overrides.pop("origin_probe", lambda _origin, _claim: None),
        edge_probe=edge_probe,
        cloudflare_range_state=range_state,
    )
    return CloudflareDiagnostician(
        collector,
        http_probe=overrides.pop(
            "http_probe", lambda *_args: HttpForwardProbeResult("healthy", None, 200)
        ),
        range_state=range_state,
        activation_enabled=overrides.pop("activation_enabled", False),
        **overrides,
    )


def test_explicit_unactivated_cloudflare_diagnostics_are_persisted(diagnostic_db):
    diagnostician(diagnostic_db).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        diagnostic = CloudflareDiagnosticStore(conn).get(
            claim.id, claim.route_generation, claim.mode_generation
        )
    assert diagnostic.dns_status == "healthy"
    assert diagnostic.answer_fingerprint
    assert diagnostic.edge_http_status == "healthy"
    assert diagnostic.http_forward_status == "healthy"
    assert claim.activated_at is None


def test_range_state_is_shared_by_collection_policy_and_diagnostics(diagnostic_db):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    state = CloudflareRangeState(
        CloudflareRanges(
            "old",
            datetime.now(timezone.utc) - MAX_RANGE_AGE - timedelta(seconds=1),
            (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
        )
    )
    collector = DomainEvidenceCollector(
        DomainDnsObserver(
            lambda _name, family: AddressAnswer.addresses(("8.8.8.8",), 60)
            if family == "A"
            else AddressAnswer.no_answer(),
            cloudflare_range_state=state,
        ),
        "origin",
        router_validator=lambda _claim: None,
        ownership_resolver=lambda _name: (claim.verification_value,),
        origin_probe=lambda *_args: None,
        cloudflare_range_state=state,
    )
    evidence = collector.collect(claim, "cloudflare")
    checker = CloudflareDiagnostician(collector, range_state=state)

    assert evidence.target_error("cloudflare").error == "range_data_stale"
    assert checker.range_error == "range_data_stale"
    assert checker._diagnose_evidence(evidence).dns_error == "range_data_stale"


def test_coordinator_health_recording_skips_http_forwarding(diagnostic_db):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )
    state = CloudflareRangeState(ranges)
    collector = DomainEvidenceCollector(
        DomainDnsObserver(
            lambda _name, family: AddressAnswer.addresses(("8.8.8.8",), 60)
            if family == "A"
            else AddressAnswer.no_answer(),
            cloudflare_range_state=state,
        ),
        "origin",
        router_validator=lambda _claim: None,
        ownership_resolver=lambda _name: (claim.verification_value,),
        origin_probe=lambda *_args: None,
        edge_probe=lambda address, _claim: EdgeProbeResult(
            "healthy", None, "healthy", None, address=address
        ),
        cloudflare_range_state=state,
    )
    evidence = collector.collect(claim, "cloudflare")
    checker = CloudflareDiagnostician(
        collector,
        http_probe=lambda *_args: pytest.fail("coordinator recording must not probe HTTP"),
        range_state=state,
    )

    assert checker.record_health(claim, evidence, evidence)
    with diagnostic_db() as conn:
        diagnostic = CloudflareDiagnosticStore(conn).get(
            claim.id, claim.route_generation, claim.mode_generation, 0
        )

    assert diagnostic.http_forward_status == "not_checked"


def test_activation_uses_confirmed_evidence(diagnostic_db):
    diagnostician(diagnostic_db, activation_enabled=True).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        resolved = DomainClaimStore(conn).find_activated(claim.hostname)
    assert claim.activated_at is not None
    assert resolved.id == claim.id


def test_all_cloudflare_addresses_are_probed(diagnostic_db):
    checked = []
    diagnostician(
        diagnostic_db,
        addresses=("8.8.8.8", "8.8.8.9"),
        edge_probe=lambda address, _claim: checked.append(address)
        or EdgeProbeResult("healthy", None, "healthy", None, address=address),
    ).run_once()

    assert set(checked) == {"8.8.8.8", "8.8.8.9"}


def test_unroutable_ipv6_is_probed_but_diagnostics_present_healthy_ipv4(
    diagnostic_db,
):
    checked = []

    def edge_probe(address, _claim):
        checked.append(address)
        if ipaddress.ip_address(address).version == 6:
            return EdgeProbeResult(
                "failed",
                "edge_address_family_unavailable",
                "not_checked",
                None,
                address=address,
            )
        return EdgeProbeResult("healthy", None, "healthy", None, address=address)

    diagnostician(
        diagnostic_db,
        addresses=("8.8.8.8", "8.8.8.9", "2001:4860::1", "2001:4860::2"),
        edge_probe=edge_probe,
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        diagnostic = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)

    assert set(checked) == {
        "8.8.8.8",
        "8.8.8.9",
        "2001:4860::1",
        "2001:4860::2",
    }
    assert diagnostic.activation_error is None
    assert diagnostic.edge_address in {"8.8.8.8", "8.8.8.9"}


def test_mixed_addresses_fail_without_edge_probe(diagnostic_db):
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )
    diagnostician(
        diagnostic_db,
        addresses=("8.8.8.8", "1.1.1.1"),
        range_state=CloudflareRangeState(ranges),
        edge_probe=lambda *_args: pytest.fail("mixed DNS must not probe the edge"),
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        diagnostic = CloudflareDiagnosticStore(conn).get(claim.id, claim.route_generation)
    assert diagnostic.dns_error == "dns_non_cloudflare_address"
    assert diagnostic.edge_tls_status == "not_checked"


def test_common_failure_prevents_activation(diagnostic_db):
    diagnostician(
        diagnostic_db,
        activation_enabled=True,
        ownership_resolver=lambda _name: (),
    ).run_once()

    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.activation_error == "ownership_txt_mismatch"


def test_activated_cloudflare_is_not_owned_by_explicit_diagnostician(diagnostic_db):
    diagnostician(diagnostic_db, activation_enabled=True).run_once()
    with diagnostic_db() as conn:
        conn.execute(
            "UPDATE custom_domain_cloudflare_diagnostics SET checked_at = datetime('now', '-2 minutes')"
        )
    diagnostician(
        diagnostic_db,
        edge_probe=lambda *_args: pytest.fail("activated claims belong to coordinator"),
    ).run_once()


class FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
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

    def wrap_socket(self, _connection, server_hostname):
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


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(302, headers={"Location": "https://example.com"}), "edge_redirect"),
        (FakeResponse(403, headers={"CF-Ray": "ray"}), "edge_waf_denied"),
        (FakeResponse(403, headers={"cf-mitigated": "challenge"}), "edge_challenge_present"),
        (FakeResponse(530, b"Error code: 1014"), "cloudflare_1014"),
        (FakeResponse(525), "cloudflare_525"),
        (FakeResponse(526), "cloudflare_526"),
        (FakeResponse(200, b"stale", {"CF-Cache-Status": "HIT"}), "edge_cached_challenge"),
        (FakeResponse(200, b"x" * (16 * 1024 + 1)), "edge_response_too_large"),
        (FakeResponse(200, b"wrong"), "edge_challenge_mismatch"),
    ],
)
def test_edge_probe_classification_matrix(
    diagnostic_db, monkeypatch, response, error
):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    monkeypatch.setattr("socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr("ssl.create_default_context", lambda: FakeContext(FakeTls()))
    monkeypatch.setattr("http.client.HTTPResponse", lambda _socket: response)

    result = probe_cloudflare_edge("8.8.8.8", claim)

    assert result.http_error == error


def test_edge_probe_rejects_invalid_tls(diagnostic_db, monkeypatch):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    monkeypatch.setattr("socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(
        "ssl.create_default_context",
        lambda: FakeContext(FakeTls(), ssl.SSLCertVerificationError()),
    )

    result = probe_cloudflare_edge("8.8.8.8", claim)

    assert (result.tls_error, result.http_status) == ("edge_tls_invalid", "not_checked")


@pytest.mark.parametrize(
    "error_number", [errno.ENETUNREACH, errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL]
)
def test_edge_probe_classifies_ipv6_routing_limitations(
    diagnostic_db, monkeypatch, error_number
):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    monkeypatch.setattr(
        "socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(error_number, "ignored")),
    )

    ipv6 = probe_cloudflare_edge("2001:4860::1", claim)
    ipv4 = probe_cloudflare_edge("8.8.8.8", claim)

    assert ipv6.tls_error == "edge_address_family_unavailable"
    assert ipv4.tls_error == "edge_unavailable"


@pytest.mark.parametrize("error_number", [errno.ETIMEDOUT, errno.ECONNRESET])
def test_edge_probe_keeps_ipv6_transport_failures_as_edge_unavailable(
    diagnostic_db, monkeypatch, error_number
):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    monkeypatch.setattr(
        "socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(error_number, "ignored")),
    )

    result = probe_cloudflare_edge("2001:4860::1", claim)

    assert result.tls_error == "edge_unavailable"


def test_http_forwarding_redirect_is_observed(diagnostic_db, monkeypatch):
    with diagnostic_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    connection = FakeTls()
    monkeypatch.setattr("socket.create_connection", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr("http.client.HTTPResponse", lambda _socket: FakeResponse(301))

    result = probe_cloudflare_http_forwarding("8.8.8.8", claim)

    assert (result.status, result.error) == ("observed", "http_forward_redirect")
