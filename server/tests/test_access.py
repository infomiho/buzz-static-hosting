import hashlib
import io
import re
import threading
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.access import AccessService, InvalidAccessCode
from server.site_store import SiteStore

SITE_HOST = {"host": "private-site.localhost:8080"}


def _session_token(database, *, github_id: int = 1, login: str = "owner") -> str:
    raw = f"buzz_sess_{login}"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    with database.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO users (github_id, github_login) VALUES (?, ?)",
            (github_id, login),
        )
        conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (
                token_hash,
                cursor.lastrowid,
                (datetime.now() + timedelta(days=1)).isoformat(),
            ),
        )
    return raw


def _create_site(database, tmp_path, token: str, name: str = "private-site") -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with database.connect() as conn:
        user_id = conn.execute(
            "SELECT user_id FROM sessions WHERE id = ?", (token_hash,)
        ).fetchone()["user_id"]
        conn.execute(
            "INSERT INTO sites (name, owner_id, size_bytes) VALUES (?, ?, 1)",
            (name, user_id),
        )
    site_dir = tmp_path / name
    (site_dir / "admin").mkdir(parents=True)
    (site_dir / "assets").mkdir()
    (site_dir / "index.html").write_text("public home")
    (site_dir / "admin" / "index.html").write_text("private admin")
    # The shapes real generators emit, each reachable under several URLs.
    (site_dir / "reports.html").write_text("private reports")
    (site_dir / "assets" / "app-a1b2c3.js").write_text("const SECRET = 1;")
    (site_dir / "200.html").write_text("spa shell")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _site_archive() -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("index.html", "private from first publish")
    return archive.getvalue()


# What every URL reaches while the fixture site is public, covering the aliases
# the resolver invents (<path>.html, <path>/index.html) and the SPA catch-all.
# The private test walks the same URLs, so the two can never drift apart.
PUBLIC_RESPONSES = [
    ("/", "public home"),
    ("/index.html", "public home"),
    ("/index", "public home"),
    ("/admin", "private admin"),
    ("/admin/", "private admin"),
    ("/admin/index.html", "private admin"),
    ("/admin/index", "private admin"),
    ("/reports", "private reports"),
    ("/reports.html", "private reports"),
    ("/assets/app-a1b2c3.js", "const SECRET = 1;"),
    ("/no/such/route", "spa shell"),
    ("/%2561dmin", "spa shell"),
]
SITE_BODIES = {body for _, body in PUBLIC_RESPONSES}


def test_private_site_gates_every_url(make_app, database, tmp_path):
    """The invariant the whole design exists to guarantee: when a site is
    private, no URL returns its bytes, whatever the resolver would have done."""
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    client = TestClient(make_app())
    client.put("/sites/private-site/access", headers=_auth(token))

    for url, _ in PUBLIC_RESPONSES:
        response = client.get(url, headers=SITE_HOST)
        assert response.status_code == 401, url
        for body in SITE_BODIES:
            assert body not in response.text, f"{url} leaked {body!r}"


@pytest.mark.parametrize(("url", "body"), PUBLIC_RESPONSES)
def test_public_site_serves_every_url(make_app, database, tmp_path, url, body):
    """The counterpart to the privacy invariant: pinning what each URL serves
    while public proves the private case changes visibility and nothing else."""
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    client = TestClient(make_app())

    response = client.get(url, headers=SITE_HOST)

    assert response.status_code == 200, url
    assert response.text == body, url


def test_access_api_reports_and_toggles_visibility(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    client = TestClient(make_app())

    public = client.get("/sites/private-site/access", headers=_auth(token))
    private = client.put("/sites/private-site/access", headers=_auth(token))
    still_private = client.get("/sites/private-site/access", headers=_auth(token))
    deleted = client.delete("/sites/private-site/access", headers=_auth(token))

    assert public.json() == {"private": False}
    assert private.json() == {"private": True}
    assert still_private.json() == {"private": True}
    assert deleted.status_code == 204


def test_making_a_site_private_is_idempotent(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app()

    first = app.state.access.set_policy("private-site", 1)
    again = app.state.access.set_policy("private-site", 1)

    assert (first.id, first.generation) == (again.id, again.generation)


def test_site_listing_reports_visibility(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    client = TestClient(make_app())

    public = client.get("/sites", headers=_auth(token))
    client.put("/sites/private-site/access", headers=_auth(token))
    private = client.get("/sites", headers=_auth(token))

    assert public.json()[0]["private"] is False
    assert private.json()[0]["private"] is True


def test_deploy_token_cannot_manage_access(make_app, database, tmp_path):
    owner_token = _session_token(database)
    _create_site(database, tmp_path, owner_token)
    with database.connect() as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE github_login = 'owner'"
        ).fetchone()["id"]
    deploy_token = "buzz_deploy_private"
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO deployment_tokens (id, name, site_name, user_id) VALUES (?, ?, ?, ?)",
            (
                hashlib.sha256(deploy_token.encode()).hexdigest(),
                "CI",
                "private-site",
                user_id,
            ),
        )
    client = TestClient(make_app())

    response = client.put(
        "/sites/private-site/access", headers=_auth(deploy_token)
    )

    assert response.status_code == 403

    deploy_response = client.post(
        "/deploy",
        headers={
            **_auth(deploy_token),
            "x-buzz-site": "private-site",
            "x-buzz-access": "private",
        },
        files={"file": ("site.zip", b"not-read", "application/zip")},
    )
    assert deploy_response.status_code == 403
    assert deploy_response.json()["detail"] == "Deployment tokens cannot manage access"


def test_first_deployment_can_go_private_atomically(make_app, database):
    token = _session_token(database)
    client = TestClient(make_app())

    deployed = client.post(
        "/deploy",
        headers={
            **_auth(token),
            "x-buzz-site": "private-site",
            "x-buzz-access": "private",
        },
        files={"file": ("site.zip", _site_archive(), "application/zip")},
    )

    assert deployed.status_code == 200
    response = client.get("/", headers=SITE_HOST)
    assert response.status_code == 401
    assert "private from first publish" not in response.text


def test_redeploy_reports_that_a_site_is_still_private(make_app, database):
    """The deploy response must report the site's visibility, not echo the flag:
    a redeploy without --private is still a redeploy of a private site."""
    token = _session_token(database)
    client = TestClient(make_app())
    headers = {**_auth(token), "x-buzz-site": "private-site"}

    first = client.post(
        "/deploy",
        headers={**headers, "x-buzz-access": "private"},
        files={"file": ("site.zip", _site_archive(), "application/zip")},
    )
    redeployed = client.post(
        "/deploy",
        headers=headers,
        files={"file": ("site.zip", _site_archive(), "application/zip")},
    )

    assert first.json()["private"] is True
    assert redeployed.json()["private"] is True


def test_publication_guard_closes_the_file_publish_window(
    make_app, database, tmp_path, monkeypatch
):
    _session_token(database)
    with database.connect() as conn:
        owner_id = conn.execute(
            "SELECT id FROM users WHERE github_login = 'owner'"
        ).fetchone()["id"]
    published = threading.Event()
    release = threading.Event()
    errors: list[Exception] = []
    original_sync = SiteStore._sync_directory

    def pausing_sync(path):
        original_sync(path)
        if path == tmp_path and (tmp_path / "private-site").is_dir():
            published.set()
            assert release.wait(timeout=5)

    monkeypatch.setattr(SiteStore, "_sync_directory", staticmethod(pausing_sync))

    def deploy():
        try:
            with database.connect() as conn:
                SiteStore(conn, tmp_path).deploy(
                    "private-site",
                    io.BytesIO(_site_archive()),
                    owner_id,
                    lambda publish_conn: AccessService.set_policy_on_connection(
                        publish_conn, "private-site", owner_id
                    ),
                )
        except Exception as error:  # noqa: BLE001 - propagate worker failures to pytest
            errors.append(error)

    worker = threading.Thread(target=deploy)
    worker.start()
    assert published.wait(timeout=5)
    try:
        response = TestClient(make_app()).get("/", headers=SITE_HOST)
        assert response.status_code == 401
        assert "private from first publish" not in response.text
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert not errors


def test_access_grant_respects_current_operator_allowlist(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    allowed_app = make_app(allowed_github_users=frozenset({"owner"}))
    allowed_app.state.access.set_policy("private-site", 1)
    session_id = hashlib.sha256(token.encode()).hexdigest()
    code = allowed_app.state.access.authorize_owner(
        "private-site", "private-site.localhost", "/", 1, session_id
    )
    grant = allowed_app.state.access.exchange_code(
        code, "private-site", "private-site.localhost"
    )

    denied_app = make_app(allowed_github_users=frozenset({"someone-else"}))
    with TestClient(denied_app) as client:
        response = client.get(
            "/",
            headers={**SITE_HOST, "cookie": f"__Host-buzz_access={grant.token}"},
        )

    assert response.status_code == 401


def test_private_site_requires_owner_handoff(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    client = TestClient(make_app())
    client.put("/sites/private-site/access", headers=_auth(token))

    gated = client.get("/admin", headers=SITE_HOST)
    assert gated.status_code == 401
    assert "Private site" in gated.text
    assert "private-site.localhost" in gated.text
    assert gated.headers["cache-control"] == "private, no-store"
    assert gated.headers["x-robots-tag"] == "noindex, nofollow"

    authorize = client.get(
        "/access/authorize",
        headers={"host": "localhost:8080", **_auth(token)},
        params={
            "site": "private-site",
            "host": "private-site.localhost",
            "path": "/admin?tab=users",
        },
    )
    assert authorize.status_code == 200
    assert "Opening private-site.localhost" in authorize.text

    handoff = client.post(
        "/access/authorize",
        headers={"host": "localhost:8080", **_auth(token)},
        data={
            "site": "private-site",
            "host": "private-site.localhost",
            "path": "/admin?tab=users",
        },
    )
    code = re.search(r'name="code" value="([^"]+)"', handoff.text)
    assert handoff.status_code == 200
    assert code

    callback = client.post(
        "/.well-known/buzz-access/callback",
        headers={**SITE_HOST, "origin": "http://private-site.localhost:8080"},
        data={"code": code.group(1)},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/admin?tab=users"
    cookie_match = re.search(
        r"__Host-buzz_access=([^;]+)", callback.headers["set-cookie"]
    )
    assert cookie_match
    access_cookie = cookie_match.group(1)

    allowed = client.get(
        "/admin",
        headers={**SITE_HOST, "cookie": f"__Host-buzz_access={access_cookie}"},
    )
    assert allowed.status_code == 200
    assert allowed.text == "private admin"
    assert allowed.headers["cache-control"] == "private, no-store"

    with database.connect() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE id = ?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
    expired = client.get(
        "/admin",
        headers={**SITE_HOST, "cookie": f"__Host-buzz_access={access_cookie}"},
    )
    assert expired.status_code == 401


def test_going_public_then_private_invalidates_an_existing_grant(
    make_app, database, tmp_path
):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app()
    app.state.access.set_policy("private-site", 1)
    session_id = hashlib.sha256(token.encode()).hexdigest()
    code = app.state.access.authorize_owner(
        "private-site", "private-site.localhost", "/", 1, session_id
    )
    grant = app.state.access.exchange_code(
        code, "private-site", "private-site.localhost"
    )

    assert app.state.access.check_request(
        "private-site", "private-site.localhost", grant.token
    ).authorized

    app.state.access.delete_policy("private-site", 1)
    app.state.access.set_policy("private-site", 1)

    assert not app.state.access.check_request(
        "private-site", "private-site.localhost", grant.token
    ).authorized


def test_dev_mode_exercises_access_handoff_without_github_login(
    make_app, database, tmp_path
):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app(dev_mode=True)
    app.state.access.set_policy("private-site", 1)
    client = TestClient(app)

    gated = client.get("/admin", headers=SITE_HOST)
    assert gated.status_code == 401

    authorize = client.get(
        "/access/authorize",
        headers={"host": "localhost:8080"},
        params={
            "site": "private-site",
            "host": "private-site.localhost",
            "path": "/admin",
        },
    )
    assert authorize.status_code == 200

    handoff = client.post(
        "/access/authorize",
        headers={"host": "localhost:8080"},
        data={
            "site": "private-site",
            "host": "private-site.localhost",
            "path": "/admin",
        },
    )
    code = re.search(r'name="code" value="([^"]+)"', handoff.text)
    assert code
    assert "http://private-site.localhost:8080/.well-known/buzz-access/callback" in handoff.text

    callback = client.post(
        "/.well-known/buzz-access/callback",
        headers={**SITE_HOST, "origin": "http://localhost:8080"},
        data={"code": code.group(1)},
        follow_redirects=False,
    )
    access_cookie = callback.cookies.get("buzz_access")
    assert access_cookie

    response = client.get(
        "/admin",
        headers={**SITE_HOST, "cookie": f"buzz_access={access_cookie}"},
    )

    assert response.status_code == 200
    assert response.text == "private admin"
    assert response.headers["cache-control"] == "private, no-store"


def test_access_code_is_single_use(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app()
    app.state.access.set_policy("private-site", 1)
    session_id = hashlib.sha256(token.encode()).hexdigest()
    code = app.state.access.authorize_owner(
        "private-site", "private-site.localhost", "/", 1, session_id
    )

    first = app.state.access.exchange_code(
        code, "private-site", "private-site.localhost"
    )

    assert first.return_path == "/"
    with pytest.raises(InvalidAccessCode):
        app.state.access.exchange_code(code, "private-site", "private-site.localhost")


def test_denied_visitor_is_given_a_way_out(make_app, database, tmp_path):
    owner_token = _session_token(database)
    _create_site(database, tmp_path, owner_token)
    stranger_token = _session_token(database, github_id=2, login="stranger")
    app = make_app()
    app.state.access.set_policy("private-site", 1)
    client = TestClient(app)

    response = client.get(
        "/access/authorize",
        headers={"host": "localhost:8080", **_auth(stranger_token)},
        params={
            "site": "private-site",
            "host": "private-site.localhost",
            "path": "/",
        },
    )

    assert response.status_code == 403
    assert "private-site.localhost" in response.text
    assert "Sign in as someone else" in response.text
