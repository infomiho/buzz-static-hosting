import ssl

import pytest

from server import db as db_module
from server.custom_domains.claims import DomainClaimStore
from server.custom_domains.evidence import AddressAnswer, DomainDnsObserver, DomainEvidenceCollector
from server.custom_domains.activation import DomainActivator
from server.custom_domains.probes import MAX_RESPONSE_BYTES, ActivationFailed, probe_origin


@pytest.fixture
def activation_db(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
    return db_module.db


def activator(resolver, probe=lambda _origin, _claim: None):
    def lookup(name, family):
        if family == "AAAA":
            return AddressAnswer.no_answer()
        try:
            return AddressAnswer.addresses(resolver(name), 0)
        except ActivationFailed as exc:
            return AddressAnswer(
                "timeout" if exc.code == "dns_unavailable" else "invalid"
            )

    def ownership(name):
        with db_module.db() as conn:
            claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        return (claim.verification_value,) if claim.verification_name == name else ()

    collector = DomainEvidenceCollector(
        DomainDnsObserver(lookup, frozenset({"8.8.8.8"})),
        "traefik",
        router_validator=lambda _claim: None,
        ownership_resolver=ownership,
        origin_probe=probe,
    )
    return DomainActivator(
        evidence_collector=collector,
    )


def test_activation_requires_allowed_dns_and_exact_origin_challenge(activation_db):
    probes = []
    route_activator = activator(
        lambda _hostname: ("8.8.8.8",),
        lambda origin, claim: probes.append((origin, claim.hostname)),
    )

    route_activator.run_once()

    with activation_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        found = DomainClaimStore(conn).find_activated("www.example.com")
        evidence = conn.execute(
            "SELECT * FROM custom_domain_path_evidence WHERE claim_id = ?",
            (claim.id,),
        ).fetchone()
    assert claim.activated_at is not None
    assert claim.activation_error is None
    assert found is not None
    assert probes == [("traefik", "www.example.com")]
    assert evidence["path_mode"] == "direct"
    assert evidence["common_result"] == evidence["path_result"] == "healthy"
    assert evidence["confirmation_fingerprint"] == evidence["answer_fingerprint"]


@pytest.mark.parametrize(
    ("addresses", "error"),
    [
        ((), "dns_no_addresses"),
        (("1.1.1.1",), "dns_unexpected_address"),
        (("8.8.8.8", "127.0.0.1"), "dns_non_public_address"),
    ],
)
def test_activation_rejects_unexpected_dns_answers(activation_db, addresses, error):
    route_activator = activator(lambda _hostname: addresses)

    route_activator.run_once()

    with activation_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    assert claim.activated_at is None
    assert claim.activation_error == error


def test_activation_records_probe_failure(activation_db):
    def fail_probe(_origin, _claim):
        raise ActivationFailed("tls_invalid")

    activator(lambda _hostname: ("8.8.8.8",), fail_probe).run_once()

    with activation_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence = conn.execute(
            "SELECT path_result FROM custom_domain_path_evidence WHERE claim_id = ?",
            (claim.id,),
        ).fetchone()
    assert claim.activated_at is None
    assert claim.activation_error == "tls_invalid"
    assert claim.activation_checked_at is not None
    assert evidence["path_result"] == "tls_invalid"


def test_activation_isolates_alias_failures(activation_db):
    with activation_db() as conn:
        store = DomainClaimStore(conn)
        second = store.create("my-site", "two.example.com")
        store.record_check(second.id, "my-site", (second.verification_value,))
        second = [
            claim for claim in store.prepare_routes(True) if claim.id == second.id
        ][0]
        store.mark_routed(second.id, second.route_generation)

    def resolve(hostname):
        if hostname == "www.example.com":
            raise RuntimeError("unexpected failure")
        return ("8.8.8.8",)

    activator(resolve).run_once()

    with activation_db() as conn:
        claims = DomainClaimStore(conn).list_for_site("my-site")
    assert {claim.hostname: claim.activated_at is not None for claim in claims} == {
        "two.example.com": True,
        "www.example.com": False,
    }
    failed = next(claim for claim in claims if claim.hostname == "www.example.com")
    assert failed.activation_error == "activation_check_failed"
    assert failed.activation_checked_at is not None


def test_removal_race_prevents_activation(activation_db):
    def remove_during_probe(_origin, claim):
        with activation_db() as conn:
            DomainClaimStore(conn).cancel(claim.id, "my-site")

    activator(lambda _hostname: ("8.8.8.8",), remove_during_probe).run_once()

    with activation_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM custom_domain_path_evidence WHERE claim_id = ?",
            (claim.id,),
        ).fetchone()[0]
    assert claim.route_status == "removing"
    assert claim.activated_at is None
    assert evidence_count == 0


def test_new_route_generation_clears_activation(activation_db):
    route_activator = activator(lambda _hostname: ("8.8.8.8",))
    route_activator.run_once()
    with activation_db() as conn:
        store = DomainClaimStore(conn)
        first = store.list_for_site("my-site")[0]
        store.prepare_routes(False)
        store.finish_withdrawal(first.id, first.route_generation)
        second = store.prepare_routes(True)[0]

    assert first.activated_at is not None
    assert second.activated_at is None
    assert second.activation_checked_at is None
    assert second.activation_error is None


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


class FakeTlsContext:
    def __init__(self, tls, error=None):
        self.tls = tls
        self.error = error
        self.server_hostname = None

    def wrap_socket(self, connection, server_hostname):
        self.server_hostname = server_hostname
        if self.error:
            raise self.error
        return self.tls


class FakeHttpResponse:
    def __init__(self, status=200, body=b"", claim_id="1"):
        self.status = status
        self.body = body
        self.claim_id = claim_id

    def begin(self):
        pass

    def read(self, size):
        return self.body

    def getheader(self, name):
        return self.claim_id if name == "X-Buzz-Domain-Claim" else None


def routed_claim(activation_db):
    with activation_db() as conn:
        return DomainClaimStore(conn).list_for_site("my-site")[0]


def test_origin_probe_uses_internal_origin_with_custom_sni_and_host(
    activation_db, monkeypatch
):
    claim = routed_claim(activation_db)
    expected = f"buzz-domain-check={claim.challenge_token};site=my-site".encode()
    tls = FakeTls()
    context = FakeTlsContext(tls)
    monkeypatch.setattr("socket.create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr("ssl.create_default_context", lambda: context)
    monkeypatch.setattr(
        "http.client.HTTPResponse",
        lambda socket: FakeHttpResponse(body=expected, claim_id=str(claim.id)),
    )

    probe_origin("traefik", claim)

    assert context.server_hostname == "www.example.com"
    assert tls.sent.startswith(f"GET {claim.challenge_path} HTTP/1.1\r\n".encode())
    assert b"Host: www.example.com\r\n" in tls.sent


@pytest.mark.parametrize(
    "response",
    [
        FakeHttpResponse(status=302),
        FakeHttpResponse(status=200, body=b"wrong"),
        FakeHttpResponse(status=200, body=b"x" * (MAX_RESPONSE_BYTES + 1)),
        FakeHttpResponse(status=200, claim_id="wrong"),
    ],
)
def test_origin_probe_rejects_non_exact_response(activation_db, monkeypatch, response):
    claim = routed_claim(activation_db)
    monkeypatch.setattr("socket.create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr("ssl.create_default_context", lambda: FakeTlsContext(FakeTls()))
    monkeypatch.setattr("http.client.HTTPResponse", lambda socket: response)

    with pytest.raises(ActivationFailed, match="challenge_mismatch"):
        probe_origin("traefik", claim)


def test_origin_probe_rejects_untrusted_tls(activation_db, monkeypatch):
    claim = routed_claim(activation_db)
    monkeypatch.setattr("socket.create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr(
        "ssl.create_default_context",
        lambda: FakeTlsContext(FakeTls(), ssl.SSLCertVerificationError()),
    )

    with pytest.raises(ActivationFailed, match="tls_invalid"):
        probe_origin("traefik", claim)


def test_origin_probe_records_connection_failure(activation_db, monkeypatch):
    claim = routed_claim(activation_db)

    def unavailable(address, timeout):
        raise TimeoutError

    monkeypatch.setattr("socket.create_connection", unavailable)

    with pytest.raises(ActivationFailed, match="origin_unavailable"):
        probe_origin("traefik", claim)
