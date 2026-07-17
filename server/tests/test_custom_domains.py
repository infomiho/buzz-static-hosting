from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from server import db as db_module
from server.custom_domains import (
    DnsTxtResolver,
    DomainCheckUnavailable,
    DomainClaimLimits,
    DomainClaimStore,
    DomainQuotaExceeded,
    InvalidHostname,
    normalize_hostname,
)
from server.exceptions import Conflict


def test_dns_resolver_rejects_non_ascii_txt_data(monkeypatch):
    record = type("Record", (), {"strings": [b"\xff"]})()
    monkeypatch.setattr("dns.resolver.resolve", lambda *args, **kwargs: [record])

    with pytest.raises(DomainCheckUnavailable, match="invalid TXT"):
        DnsTxtResolver().lookup("_buzz.example.com")


@pytest.fixture
def claim_db(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute(
            "INSERT INTO users (id, github_id, github_login) VALUES (1, 1, 'alice'), (2, 2, 'bob')"
        )
        conn.execute(
            """INSERT INTO sites (name, owner_id) VALUES
            ('site-one', 1), ('site-two', 2), ('site-three', 1)"""
        )
    return db_module.db


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("WWW.Example.COM", "www.example.com"),
        (" www.example.com. ", "www.example.com"),
        ("münchen.example", "xn--mnchen-3ya.example"),
    ],
)
def test_normalize_hostname(raw, expected):
    assert normalize_hostname(raw, "buzz.example.com") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com",
        "example.com/path",
        "example.com:443",
        "*.example.com",
        "127.0.0.1",
        "localhost",
        "service.local",
        "single-label",
        "my-site.buzz.example.com",
        "buzz.example.com",
        "bad_name.example.com",
    ],
)
def test_normalize_hostname_rejects_invalid_or_reserved_names(raw):
    with pytest.raises(InvalidHostname):
        normalize_hostname(raw, "buzz.example.com")


def test_pending_claims_do_not_reserve_hostname_globally(claim_db):
    with claim_db() as conn:
        first = DomainClaimStore(conn).create("site-one", "www.example.com")
    with claim_db() as conn:
        second = DomainClaimStore(conn).create("site-two", "www.example.com")

    assert first.hostname == second.hostname
    assert first.id != second.id


def test_multiple_active_claims_are_allowed_per_site(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        first = store.create("site-one", "one.example.com")
        second = store.create("site-one", "two.example.com")

    assert first.id != second.id
    assert {first.hostname, second.hostname} == {
        "one.example.com",
        "two.example.com",
    }


def test_cloudflare_claims_cannot_enter_direct_activation(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        direct = store.create("site-one", "direct.example.com")
        cloudflare = store.create(
            "site-one", "proxy.example.com", claim_mode="cloudflare"
        )
        for claim in (direct, cloudflare):
            store.record_check(claim.id, "site-one", (claim.verification_value,))
        routed = store.prepare_routes(True)
        for claim in routed:
            store.mark_routed(claim.id, claim.route_generation)
        candidates = store.activation_candidates()

    assert [claim.hostname for claim in candidates] == ["direct.example.com"]


def test_legacy_direct_activation_excludes_automatic_and_health_managed_claims(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        automatic = store.create(
            "site-one", "automatic.example.com", automatic_mode=True
        )
        recovering = store.create("site-one", "recovering.example.com")
        explicit = store.create("site-one", "explicit.example.com")
        for claim in (automatic, recovering, explicit):
            store.record_check(claim.id, "site-one", (claim.verification_value,))
        for claim in store.prepare_routes(True):
            store.mark_routed(claim.id, claim.route_generation)
        conn.execute(
            """UPDATE custom_domain_claims SET health_checked_at = CURRENT_TIMESTAMP
            WHERE id = ?""",
            (recovering.id,),
        )

        candidates = store.activation_candidates()

    assert [claim.hostname for claim in candidates] == ["explicit.example.com"]


def test_duplicate_active_hostname_is_rejected_for_same_site(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        store.create("site-one", "one.example.com")
        with pytest.raises(Conflict, match="already attached"):
            store.create("site-one", "one.example.com")


@pytest.mark.parametrize(
    ("limits", "site_name", "message"),
    [
        (DomainClaimLimits(per_site=1, per_user=10, server_wide=10), "site-one", "site"),
        (DomainClaimLimits(per_site=10, per_user=1, server_wide=10), "site-three", "You"),
        (DomainClaimLimits(per_site=10, per_user=10, server_wide=1), "site-two", "server"),
    ],
)
def test_claim_quotas_are_enforced_by_scope(claim_db, limits, site_name, message):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        store.create("site-one", "one.example.com", limits=limits)
        with pytest.raises(DomainQuotaExceeded, match=message):
            store.create(site_name, "two.example.com", limits=limits)


def test_cancelled_claim_releases_quota_independently(claim_db):
    limits = DomainClaimLimits(per_site=1, per_user=10, server_wide=10)
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        first = store.create("site-one", "one.example.com", limits=limits)
        store.cancel(first.id, "site-one")
        second = store.create("site-one", "two.example.com", limits=limits)

    assert second.status == "pending"


def test_concurrent_claims_cannot_exceed_site_quota(claim_db):
    limits = DomainClaimLimits(per_site=1, per_user=10, server_wide=10)
    barrier = Barrier(2)

    def create(hostname):
        barrier.wait()
        try:
            with claim_db() as conn:
                DomainClaimStore(conn).create(
                    "site-one", hostname, limits=limits
                )
            return "created"
        except DomainQuotaExceeded:
            return "limited"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(create, ("one.example.com", "two.example.com"))
        )

    assert sorted(outcomes) == ["created", "limited"]


def test_verification_acquires_global_hostname_claim(claim_db):
    with claim_db() as conn:
        first = DomainClaimStore(conn).create("site-one", "www.example.com")
    with claim_db() as conn:
        second = DomainClaimStore(conn).create("site-two", "www.example.com")
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        verified = store.record_check(first.id, "site-one", (first.verification_value,))
        assert verified.status == "verified"
    with claim_db() as conn:
        with pytest.raises(Conflict, match="already verified"):
            DomainClaimStore(conn).record_check(
                second.id, "site-two", (second.verification_value,)
            )


def test_failed_check_keeps_claim_pending(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.create("site-one", "www.example.com")
        checked = store.record_check(claim.id, "site-one", ("wrong-value",))

    assert checked.status == "pending"
    assert checked.last_error == "txt_mismatch"
    assert checked.last_checked_at is not None


def test_pending_claim_expires_and_releases_site(claim_db):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    with claim_db() as conn:
        first = DomainClaimStore(conn).create("site-one", "old.example.com", now)
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        replacement = store.create(
            "site-one", "new.example.com", now + timedelta(hours=25)
        )
        expired = store.get(first.id, "site-one")

    assert expired.status == "expired"
    assert replacement.status == "pending"


def test_cancel_releases_site_but_preserves_claim(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.create("site-one", "old.example.com")
        store.cancel(claim.id, "site-one")
        replacement = store.create("site-one", "new.example.com")
        claims = store.list_for_site("site-one")

    assert replacement.status == "pending"
    assert {claim.status for claim in claims} == {"pending", "cancelled"}


def test_repeated_routed_cancellation_preserves_withdrawal_boundary(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.create("site-one", "www.example.com")
        claim = store.record_check(
            claim.id, "site-one", (claim.verification_value,)
        )
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
        store.cancel(
            claim.id,
            "site-one",
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )
        first = store.get(claim.id, "site-one")
        store.cancel(
            claim.id,
            "site-one",
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        repeated = store.get(claim.id, "site-one")

    assert repeated.removal_requested_at == first.removal_requested_at
    assert repeated.route_updated_at == first.route_updated_at


def test_owner_cancellation_is_preserved_during_operator_withdrawal(claim_db):
    with claim_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.create("site-one", "www.example.com")
        claim = store.record_check(
            claim.id, "site-one", (claim.verification_value,)
        )
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
        removing = store.prepare_routes(
            False, now=datetime(2026, 7, 16, tzinfo=timezone.utc)
        )[0]
        store.cancel(
            claim.id,
            "site-one",
            now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        cancelled = store.get(claim.id, "site-one")
        store.finish_withdrawal(cancelled.id, cancelled.route_generation)
        withdrawn = store.get(claim.id, "site-one")

    assert cancelled.route_updated_at == removing.route_updated_at
    assert cancelled.removal_requested_at == "2026-07-17T00:00:00+00:00"
    assert withdrawn.status == "cancelled"
    assert withdrawn.route_status == "removed"
