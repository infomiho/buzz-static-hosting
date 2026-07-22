from server.pending_store import PendingStore

from tests.passkey_helpers import FakeClock


def make_store():
    clock = FakeClock()
    return PendingStore(clock=clock), clock


def test_get_returns_stored_value_without_removing_it():
    store, _ = make_store()
    store.put("key", {"state": 1}, ttl_seconds=60)
    assert store.get("key") == {"state": 1}
    assert store.get("key") == {"state": 1}


def test_consume_removes_the_entry():
    store, _ = make_store()
    store.put("key", "value", ttl_seconds=60)
    assert store.consume("key") == "value"
    assert store.consume("key") is None


def test_unknown_key_returns_none():
    store, _ = make_store()
    assert store.get("missing") is None
    assert store.consume("missing") is None


def test_entries_expire_after_ttl():
    store, clock = make_store()
    store.put("key", "value", ttl_seconds=60)
    clock.advance(59)
    assert store.get("key") == "value"
    clock.advance(1)
    assert store.get("key") is None


def test_put_refreshes_value_and_ttl():
    store, clock = make_store()
    store.put("key", "old", ttl_seconds=60)
    clock.advance(30)
    store.put("key", "new", ttl_seconds=60)
    clock.advance(59)
    assert store.get("key") == "new"


def test_expired_entries_are_purged_on_put():
    store, clock = make_store()
    store.put("stale", "value", ttl_seconds=10)
    clock.advance(11)
    store.put("fresh", "value", ttl_seconds=10)
    assert "stale" not in store._entries


def test_values_are_shared_by_reference():
    store, _ = make_store()
    entry = {"user_id": None}
    store.put("a", entry, ttl_seconds=60)
    store.put("b", entry, ttl_seconds=60)
    store.get("a")["user_id"] = 7
    assert store.get("b")["user_id"] == 7
