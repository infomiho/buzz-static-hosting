import json

import pytest
from soft_webauthn import SoftWebauthnDevice

from server.passkeys import (
    AuthenticationFailed,
    CHALLENGE_TTL_SECONDS,
    ChallengeExpired,
    PasskeyNotFound,
    PasskeyService,
    RegistrationFailed,
)
from server.pending_store import PendingStore

from tests.passkey_helpers import FakeClock, create_credential, get_assertion

ORIGIN = "http://localhost:8080"
RP_ID = "localhost"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(database, clock):
    return PasskeyService(
        db=database.connect,
        store=PendingStore(clock=clock),
        rp_id=RP_ID,
        rp_name="Buzz",
        expected_origin=ORIGIN,
    )


@pytest.fixture
def user_id(database):
    with database.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
            (42, "alice", "Alice"),
        )
        return cursor.lastrowid


def register(service, user_id, device, name=None, origin=ORIGIN):
    options = service.registration_options(user_id)
    credential = create_credential(device, options, origin)
    return service.register(user_id, credential, name)


def authenticate(service, device, origin=ORIGIN):
    options = service.authentication_options()
    return service.authenticate(get_assertion(device, options, origin))


class TestRegistration:
    def test_round_trip_stores_the_credential(self, service, user_id):
        info = register(service, user_id, SoftWebauthnDevice(), name="MacBook")
        assert info.name == "MacBook"
        stored = service.list(user_id)
        assert [p.id for p in stored] == [info.id]
        assert stored[0].last_used_at is None

    def test_name_defaults_when_blank(self, service, user_id):
        info = register(service, user_id, SoftWebauthnDevice(), name="   ")
        assert info.name == "Passkey"

    def test_name_is_truncated(self, service, user_id):
        info = register(service, user_id, SoftWebauthnDevice(), name="x" * 100)
        assert len(info.name) == 60

    def test_wrong_origin_is_rejected(self, service, user_id):
        options = service.registration_options(user_id)
        credential = create_credential(
            SoftWebauthnDevice(), options, "https://evil.localhost:8080"
        )
        with pytest.raises(RegistrationFailed):
            service.register(user_id, credential, None)

    def test_challenge_is_single_use(self, service, user_id):
        options = service.registration_options(user_id)
        credential = create_credential(SoftWebauthnDevice(), options, ORIGIN)
        service.register(user_id, credential, None)
        with pytest.raises(ChallengeExpired):
            service.register(user_id, credential, None)

    def test_challenge_expires(self, service, user_id, clock):
        options = service.registration_options(user_id)
        credential = create_credential(SoftWebauthnDevice(), options, ORIGIN)
        clock.advance(CHALLENGE_TTL_SECONDS + 1)
        with pytest.raises(ChallengeExpired):
            service.register(user_id, credential, None)

    def test_user_handle_is_minted_once(self, service, user_id, database):
        service.registration_options(user_id)
        with database.connect() as conn:
            first = conn.execute(
                "SELECT webauthn_user_handle FROM users WHERE id = ?", (user_id,)
            ).fetchone()[0]
        service.registration_options(user_id)
        with database.connect() as conn:
            second = conn.execute(
                "SELECT webauthn_user_handle FROM users WHERE id = ?", (user_id,)
            ).fetchone()[0]
        assert first == second
        assert len(first) == 32

    def test_existing_credentials_are_excluded(self, service, user_id):
        device = SoftWebauthnDevice()
        info = register(service, user_id, device)
        options = json.loads(service.registration_options(user_id))
        assert [c["id"] for c in options["excludeCredentials"]] == [info.id]


class TestAuthentication:
    def test_round_trip_returns_the_owner(self, service, user_id):
        device = SoftWebauthnDevice()
        register(service, user_id, device)
        assert authenticate(service, device) == user_id

    def test_updates_sign_count_and_last_used(self, service, user_id):
        device = SoftWebauthnDevice()
        info = register(service, user_id, device)
        authenticate(service, device)
        stored = service.list(user_id)[0]
        assert stored.id == info.id
        assert stored.last_used_at is not None

    def test_wrong_origin_is_rejected(self, service, user_id):
        device = SoftWebauthnDevice()
        register(service, user_id, device)
        options = service.authentication_options()
        assertion = get_assertion(device, options, "https://evil.localhost:8080")
        with pytest.raises(AuthenticationFailed):
            service.authenticate(assertion)

    def test_assertion_cannot_be_replayed(self, service, user_id):
        device = SoftWebauthnDevice()
        register(service, user_id, device)
        options = service.authentication_options()
        assertion = get_assertion(device, options, ORIGIN)
        service.authenticate(assertion)
        with pytest.raises(ChallengeExpired):
            service.authenticate(assertion)

    def test_unknown_credential_is_rejected(self, service, user_id):
        device = SoftWebauthnDevice()
        register(service, user_id, device)
        service.delete(user_id, service.list(user_id)[0].id)
        with pytest.raises(AuthenticationFailed):
            authenticate(service, device)

    def test_unissued_challenge_is_rejected(self, service, user_id):
        device = SoftWebauthnDevice()
        register(service, user_id, device)
        options = json.dumps(
            {"challenge": "c29tZS1mb3JnZWQtY2hhbGxlbmdl", "rpId": RP_ID, "timeout": 60000}
        )
        with pytest.raises(ChallengeExpired):
            service.authenticate(get_assertion(device, options, ORIGIN))


class TestManagement:
    def test_delete_requires_ownership(self, service, user_id, database):
        device = SoftWebauthnDevice()
        info = register(service, user_id, device)
        with database.connect() as conn:
            other_id = conn.execute(
                "INSERT INTO users (github_id, github_login) VALUES (?, ?)",
                (43, "mallory"),
            ).lastrowid
        with pytest.raises(PasskeyNotFound):
            service.delete(other_id, info.id)
        service.delete(user_id, info.id)
        assert service.list(user_id) == []

    def test_delete_unknown_credential(self, service, user_id):
        with pytest.raises(PasskeyNotFound):
            service.delete(user_id, "missing")
