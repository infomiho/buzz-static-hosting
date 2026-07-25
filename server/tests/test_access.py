import hashlib
import io
import re
import threading
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.access import (
    AccessService,
    InvalidAccessCode,
    InvalidAccessPattern,
    matches_pattern,
    validate_patterns,
)
from server.site_store import SiteStore


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
    (site_dir / "index.html").write_text("public home")
    (site_dir / "admin" / "index.html").write_text("private admin")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _site_archive() -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("index.html", "private from first publish")
    return archive.getvalue()


class TestAccessPatterns:
    @pytest.mark.parametrize(
        ("pattern", "path", "expected"),
        [
            ("/admin/**", "/admin", True),
            ("/admin/**", "/admin/users/1", True),
            ("/admin/**", "/administrator", False),
            ("/teams/*/settings", "/teams/red/settings", True),
            ("/teams/*/settings", "/teams/red/internal/settings", False),
            ("/reports/*", "/reports/july", True),
            ("/reports/*", "/reports/2026/july", False),
            ("/", "/anything", True),
        ],
    )
    def test_matching(self, pattern, path, expected):
        assert matches_pattern(pattern, path) is expected

    def test_validation_deduplicates_patterns(self):
        assert validate_patterns([" /admin/** ", "/admin/**", "/reports/*/"]) == (
            "/admin/**",
            "/reports/*",
        )

    @pytest.mark.parametrize(
        "pattern",
        [
            "admin/**",
            "/admin*",
            "/admin//users",
            "/../admin",
            "/admin?draft=1",
            "/private%20docs/**",
        ],
    )
    def test_validation_rejects_ambiguous_patterns(self, pattern):
        with pytest.raises(InvalidAccessPattern):
            validate_patterns([pattern])


def test_access_api_configures_and_disables_policy(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    client = TestClient(make_app())

    disabled = client.get("/sites/private-site/access", headers=_auth(token))
    enabled = client.put(
        "/sites/private-site/access",
        headers=_auth(token),
        json={"patterns": ["/admin/**", "/reports/*"]},
    )
    deleted = client.delete("/sites/private-site/access", headers=_auth(token))

    assert disabled.json() == {"enabled": False, "patterns": []}
    assert enabled.json() == {
        "enabled": True,
        "patterns": ["/admin/**", "/reports/*"],
    }
    assert deleted.status_code == 204


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
        "/sites/private-site/access",
        headers=_auth(deploy_token),
        json={"patterns": ["/"]},
    )

    assert response.status_code == 403

    deploy_response = client.post(
        "/deploy",
        headers={
            **_auth(deploy_token),
            "x-subdomain": "private-site",
            "x-buzz-access-patterns": '["/"]',
        },
        files={"file": ("site.zip", b"not-read", "application/zip")},
    )
    assert deploy_response.status_code == 403
    assert (
        deploy_response.json()["detail"]
        == "Deployment tokens cannot manage Buzz Access"
    )


def test_first_deployment_can_enable_access_atomically(make_app, database):
    token = _session_token(database)
    client = TestClient(make_app())

    deployed = client.post(
        "/deploy",
        headers={
            **_auth(token),
            "x-subdomain": "private-site",
            "x-buzz-access-patterns": '["/"]',
        },
        files={"file": ("site.zip", _site_archive(), "application/zip")},
    )

    assert deployed.status_code == 200
    response = client.get("/", headers={"host": "private-site.localhost:8080"})
    assert response.status_code == 401
    assert "private from first publish" not in response.text


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
                        publish_conn, "private-site", owner_id, ("/",)
                    ),
                )
        except Exception as error:  # noqa: BLE001 - propagate worker failures to pytest
            errors.append(error)

    worker = threading.Thread(target=deploy)
    worker.start()
    assert published.wait(timeout=5)
    try:
        response = TestClient(make_app()).get(
            "/", headers={"host": "private-site.localhost:8080"}
        )
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
    allowed_app.state.access.set_policy("private-site", 1, ["/"])
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
            headers={
                "host": "private-site.localhost:8080",
                "cookie": f"__Host-buzz_access={grant.token}",
            },
        )

    assert response.status_code == 401


def test_protected_paths_require_owner_handoff(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    client = TestClient(make_app())
    client.put(
        "/sites/private-site/access",
        headers=_auth(token),
        json={"patterns": ["/admin/**"]},
    )

    public = client.get("/", headers={"host": "private-site.localhost:8080"})
    gated = client.get("/admin", headers={"host": "private-site.localhost:8080"})

    assert public.status_code == 200
    assert public.text == "public home"
    assert gated.status_code == 401
    assert "Buzz Access" in gated.text
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
    assert "Open private-site.localhost?" in authorize.text

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
        headers={
            "host": "private-site.localhost:8080",
            "origin": "http://private-site.localhost:8080",
        },
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
        headers={
            "host": "private-site.localhost:8080",
            "cookie": f"__Host-buzz_access={access_cookie}",
        },
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
        headers={
            "host": "private-site.localhost:8080",
            "cookie": f"__Host-buzz_access={access_cookie}",
        },
    )
    assert expired.status_code == 401


def test_encoded_path_cannot_bypass_access(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app()
    app.state.access.set_policy("private-site", 1, ["/admin/**"])
    client = TestClient(app)

    response = client.get(
        "/%2561dmin",
        headers={"host": "private-site.localhost:8080"},
    )

    assert response.status_code != 200
    assert response.text != "private admin"


def test_policy_update_invalidates_existing_grant(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app()
    policy = app.state.access.set_policy("private-site", 1, ["/admin/**"])
    session_id = hashlib.sha256(token.encode()).hexdigest()
    code = app.state.access.authorize_owner(
        "private-site", "private-site.localhost", "/admin", 1, session_id
    )
    grant = app.state.access.exchange_code(
        code, "private-site", "private-site.localhost"
    )

    assert app.state.access.check_request(
        "private-site", "private-site.localhost", "/admin", grant.token
    ).authorized

    updated = app.state.access.set_policy("private-site", 1, ["/admin/**"])

    assert updated.generation == policy.generation + 1
    assert not app.state.access.check_request(
        "private-site", "private-site.localhost", "/admin", grant.token
    ).authorized


def test_dev_mode_exercises_access_handoff_without_github_login(
    make_app, database, tmp_path
):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app(dev_mode=True)
    app.state.access.set_policy("private-site", 1, ["/"])
    client = TestClient(app)

    gated = client.get("/admin", headers={"host": "private-site.localhost:8080"})
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
        headers={
            "host": "private-site.localhost:8080",
            "origin": "http://localhost:8080",
        },
        data={"code": code.group(1)},
        follow_redirects=False,
    )
    access_cookie = callback.cookies.get("buzz_access")
    assert access_cookie

    response = client.get(
        "/admin",
        headers={
            "host": "private-site.localhost:8080",
            "cookie": f"buzz_access={access_cookie}",
        },
    )

    assert response.status_code == 200
    assert response.text == "private admin"
    assert response.headers["cache-control"] == "private, no-store"


def test_access_code_is_single_use(make_app, database, tmp_path):
    token = _session_token(database)
    _create_site(database, tmp_path, token)
    app = make_app()
    app.state.access.set_policy("private-site", 1, ["/"])
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
