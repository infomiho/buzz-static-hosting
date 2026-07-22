"""HTTP-level tests for the account page, passkey login, and the device grant."""
import hashlib
import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from soft_webauthn import SoftWebauthnDevice

from server.cookies import COOKIE_NAME

from tests.passkey_helpers import create_credential, get_assertion

# The app under test runs without a configured domain, so the passkey service
# pins this dev origin (see create_app).
ORIGIN = "http://localhost:8080"
CSRF = {"origin": "http://testserver"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def session(database):
    def _make(github_id=42, login="alice", name="Alice"):
        with database.connect() as conn:
            user_id = conn.execute(
                "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
                (github_id, login, name),
            ).lastrowid
            token = "buzz_sess_" + secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
                (_hash(token), user_id, (datetime.now() + timedelta(days=30)).isoformat()),
            )
        return user_id, token

    return _make


def add_passkey(client, device, name=None):
    options = client.post("/account/passkeys/options", headers=CSRF)
    assert options.status_code == 200
    credential = create_credential(device, options.text, ORIGIN)
    created = client.post(
        "/account/passkeys",
        json={"credential": credential, "name": name},
        headers=CSRF,
    )
    assert created.status_code == 200
    return created.json()


class TestAccountPage:
    def test_requires_a_session(self, client):
        assert client.get("/account/").status_code == 401

    def test_rejects_deploy_tokens(self, client, database, session):
        user_id, _ = session()
        with database.connect() as conn:
            conn.execute("INSERT INTO sites (name, owner_id) VALUES (?, ?)", ("blog", user_id))
            token = "buzz_deploy_" + secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO deployment_tokens (id, name, site_name, user_id) VALUES (?, ?, ?, ?)",
                (_hash(token), "ci", "blog", user_id),
            )
        res = client.get("/account/", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 403

    def test_lists_passkeys(self, client, session):
        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        add_passkey(client, SoftWebauthnDevice(), name="MacBook")
        page = client.get("/account/")
        assert page.status_code == 200
        assert "MacBook" in page.text

    def test_shows_empty_state(self, client, session):
        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        page = client.get("/account/")
        assert "No passkeys yet" in page.text


class TestPasskeyManagement:
    def test_register_and_delete(self, client, session):
        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        created = add_passkey(client, SoftWebauthnDevice(), name="MacBook")
        assert created["name"] == "MacBook"

        deleted = client.post(
            f"/account/passkeys/{created['id']}/delete",
            headers=CSRF,
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        assert deleted.headers["location"] == "/account/"

    def test_register_without_challenge_fails(self, client, session):
        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        options = client.post("/account/passkeys/options", headers=CSRF)
        credential = create_credential(SoftWebauthnDevice(), options.text, ORIGIN)
        first = client.post(
            "/account/passkeys", json={"credential": credential}, headers=CSRF
        )
        assert first.status_code == 200
        replay = client.post(
            "/account/passkeys", json={"credential": credential}, headers=CSRF
        )
        assert replay.status_code == 400

    def test_delete_unknown_passkey(self, client, session):
        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        res = client.post("/account/passkeys/missing/delete", headers=CSRF)
        assert res.status_code == 404


class TestPasskeyLogin:
    def _register(self, client, session):
        user_id, token = session()
        device = SoftWebauthnDevice()
        client.cookies.set(COOKIE_NAME, token)
        add_passkey(client, device)
        client.cookies.delete(COOKIE_NAME)
        return user_id, device

    def test_happy_path_sets_session_cookie(self, client, session):
        _, device = self._register(client, session)
        options = client.post("/dashboard/login/passkey/start")
        assert options.status_code == 200
        assertion = get_assertion(device, options.text, ORIGIN)
        finish = client.post("/dashboard/login/passkey/finish", json={"credential": assertion})
        assert finish.status_code == 200
        assert finish.json() == {"status": "complete"}
        assert COOKIE_NAME in finish.cookies

        client.cookies.set(COOKIE_NAME, finish.cookies[COOKIE_NAME])
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["login"] == "alice"

    def test_unknown_credential_fails(self, client, session):
        self._register(client, session)
        options = client.post("/dashboard/login/passkey/start")
        stranger = SoftWebauthnDevice()
        stranger.cred_init("localhost", b"stranger-handle")
        assertion = get_assertion(stranger, options.text, ORIGIN)
        finish = client.post("/dashboard/login/passkey/finish", json={"credential": assertion})
        assert finish.status_code == 400

    def test_disallowed_user_is_rejected(self, client, session, make_app):
        _, device = self._register(client, session)
        gated = TestClient(make_app(allowed_github_users=frozenset({"someone-else"})))
        options = gated.post("/dashboard/login/passkey/start")
        assertion = get_assertion(device, options.text, ORIGIN)
        finish = gated.post("/dashboard/login/passkey/finish", json={"credential": assertion})
        assert finish.status_code == 403


class TestDeviceGrant:
    def test_full_grant_round_trip(self, client, session):
        start = client.post("/auth/device")
        assert start.status_code == 200
        grant = start.json()
        assert grant["verification_uri"].endswith("/device")
        assert "verification_uri_complete" not in grant

        pending = client.post("/auth/device/poll", json={"device_code": grant["device_code"]})
        assert pending.json() == {"status": "pending"}

        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        approved = client.post(
            "/device", data={"user_code": grant["user_code"]}, headers=CSRF
        )
        assert approved.status_code == 200
        assert "Device connected" in approved.text
        client.cookies.delete(COOKIE_NAME)

        complete = client.post("/auth/device/poll", json={"device_code": grant["device_code"]})
        assert complete.status_code == 200
        body = complete.json()
        assert body["status"] == "complete"
        assert body["user"]["login"] == "alice"

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
        assert me.status_code == 200

    def test_unknown_device_code_is_expired(self, client):
        res = client.post("/auth/device/poll", json={"device_code": "unknown"})
        assert res.status_code == 400

    def test_disallowed_approver_gets_403_on_poll(self, app, client, database, session):
        from server.auth_service import AuthService

        _, token = session()
        grant = client.post("/auth/device").json()
        client.cookies.set(COOKIE_NAME, token)
        approved = client.post("/device", data={"user_code": grant["user_code"]}, headers=CSRF)
        assert approved.status_code == 200
        client.cookies.delete(COOKIE_NAME)
        # The approving user is removed from the allowlist before the CLI polls.
        app.state.auth_service = AuthService(
            db=database.connect,
            allowed_github_users=frozenset({"someone-else"}),
        )
        poll = client.post("/auth/device/poll", json={"device_code": grant["device_code"]})
        assert poll.status_code == 403

    def test_bad_user_code_shows_error(self, client, session):
        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        res = client.post("/device", data={"user_code": "BBBB-BBBB"}, headers=CSRF)
        assert res.status_code == 200
        assert "not valid or has expired" in res.text

    def test_device_page_redirects_anonymous_to_login(self, client):
        res = client.get("/device", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/?next=/device"

    def test_device_page_shows_blank_form_when_signed_in(self, client, session):
        _, token = session()
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/device?code=BCDF-GHJK")
        assert res.status_code == 200
        assert "Approve CLI sign-in" in res.text
        # The code from the query string must not be pre-filled into the input.
        assert "BCDF-GHJK" not in res.text

    def test_approval_requires_a_session(self, client):
        res = client.post("/device", data={"user_code": "BBBB-BBBB"})
        assert res.status_code == 401
