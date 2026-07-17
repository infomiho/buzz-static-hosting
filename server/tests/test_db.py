import sqlite3

import pytest

from server import db as db_module


def test_fresh_database_runs_all_migrations(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)

    db_module.init_db()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db_module.MIGRATIONS)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "sites" in tables
        assert "custom_domain_claims" in tables
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(custom_domain_claims)")
        }
        assert {
            "activated_at",
            "activation_checked_at",
            "activation_error",
            "claim_mode",
        } <= columns
        indexes = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA index_list(custom_domain_claims)")
        }
        assert "custom_domain_claims_active_site" not in indexes
        assert indexes["custom_domain_claims_site_status"] == 0


def test_existing_unversioned_database_upgrades_without_data_loss(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sites (name TEXT PRIMARY KEY, created_at TEXT, size_bytes INTEGER)")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")
    monkeypatch.setattr(db_module, "DB_PATH", path)

    db_module.init_db()

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sites").fetchone()[0] == "existing-site"
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sites)")}
        assert "owner_id" in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db_module.MIGRATIONS)


def test_version_four_database_upgrades_to_multiple_aliases(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        for migration in db_module.MIGRATIONS[:4]:
            migration(conn)
        conn.execute("PRAGMA user_version = 4")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at)
            VALUES ('one.example.com', 'existing-site', 'token-one', 'pending',
                    '2026-07-16T00:00:00+00:00', '2026-07-17T00:00:00+00:00')"""
        )
    monkeypatch.setattr(db_module, "DB_PATH", path)

    db_module.init_db()

    with sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at)
            VALUES ('two.example.com', 'existing-site', 'token-two', 'pending',
                    '2026-07-16T00:00:00+00:00', '2026-07-17T00:00:00+00:00')"""
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM custom_domain_claims WHERE site_name = 'existing-site'"
        ).fetchone()[0] == 2
        assert {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT claim_mode FROM custom_domain_claims"
            )
        } == {"direct"}


def test_cloudflare_diagnostic_data_survives_activation_migration(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        for migration in db_module.MIGRATIONS[:6]:
            migration(conn)
        conn.execute("PRAGMA user_version = 6")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")
        claim_id = conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode)
            VALUES ('one.example.com', 'existing-site', 'token-one', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 1, 'cloudflare')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO custom_domain_cloudflare_diagnostics
            (claim_id, route_generation, checked_at, dns_status, edge_tls_status,
             edge_http_status, http_forward_status, origin_status)
            VALUES (?, 1, '2026-07-16T00:00:00+00:00', 'healthy', 'healthy',
                    'healthy', 'healthy', 'healthy')""",
            (claim_id,),
        )
    monkeypatch.setattr(db_module, "DB_PATH", path)

    db_module.init_db()

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            """SELECT ownership_status, ownership_error, consecutive_failures
            FROM custom_domain_cloudflare_diagnostics WHERE claim_id = ?""",
            (claim_id,),
        ).fetchone()
    assert row == ("not_checked", None, 0)


def test_mode_transition_migration_preserves_claims_and_diagnostics(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        for migration in db_module.MIGRATIONS[:7]:
            migration(conn)
        conn.execute("PRAGMA user_version = 7")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")
        claim_id = conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, activated_at)
            VALUES ('one.example.com', 'existing-site', 'token-one', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 3, 'cloudflare', '2026-07-16T01:00:00+00:00')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, activated_at)
            VALUES ('direct.example.com', 'existing-site', 'token-direct', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 2, 'direct', '2026-07-16T01:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, removal_requested_at)
            VALUES ('removing.example.com', 'existing-site', 'token-removing', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'removing', 4, 'direct', '2026-07-16T03:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode)
            VALUES ('detached.example.com', NULL, 'token-detached', 'cancelled',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'removed', 5, 'direct')"""
        )
        conn.execute(
            """INSERT INTO custom_domain_cloudflare_diagnostics
            (claim_id, route_generation, checked_at, dns_status, edge_tls_status,
             edge_http_status, http_forward_status, origin_status, ownership_status)
            VALUES (?, 3, '2026-07-16T02:00:00+00:00', 'healthy', 'healthy',
                    'healthy', 'healthy', 'healthy', 'healthy')""",
            (claim_id,),
        )
    monkeypatch.setattr(db_module, "DB_PATH", path)

    db_module.init_db()

    with sqlite3.connect(path) as conn:
        claim = conn.execute(
            """SELECT claim_mode, activated_at, mode_generation, health_checked_at
            FROM custom_domain_claims"""
        ).fetchone()
        diagnostic = conn.execute(
            """SELECT route_generation, mode_generation, probe_generation,
                      answer_fingerprint
               FROM custom_domain_cloudflare_diagnostics"""
        ).fetchone()
        transition_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(custom_domain_mode_transitions)")
        }
        preserved = conn.execute(
            """SELECT hostname, site_name, route_status, claim_mode, activated_at
            FROM custom_domain_claims ORDER BY id"""
        ).fetchall()

    assert claim[:3] == ("cloudflare", "2026-07-16T01:00:00+00:00", 0)
    assert claim[3] is not None
    assert diagnostic == (3, 0, 0, None)
    assert preserved == [
        (
            "one.example.com",
            "existing-site",
            "routed",
            "cloudflare",
            "2026-07-16T01:00:00+00:00",
        ),
        (
            "direct.example.com",
            "existing-site",
            "routed",
            "direct",
            "2026-07-16T01:00:00+00:00",
        ),
        ("removing.example.com", "existing-site", "removing", "direct", None),
        ("detached.example.com", None, "removed", "direct", None),
    ]
    assert {
        "claim_id",
        "mode_generation",
        "probe_generation",
        "source_mode",
        "target_mode",
        "state",
        "answer_fingerprint",
        "confirmed_fingerprint",
        "confirmed_at",
        "lease_owner",
        "lease_expires_at",
    } <= transition_columns


def test_transition_schema_rejects_stale_mode_generation(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('site')")
        claim_id = conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation)
            VALUES ('one.example.com', 'site', 'token', 'verified', 'now', 'later',
                    'routed', 1)"""
        ).lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="mode generation"):
            conn.execute(
                """INSERT INTO custom_domain_mode_transitions
                (claim_id, mode_generation, target_mode, state, started_at)
                VALUES (?, 99, 'direct', 'observing', 'now')""",
                (claim_id,),
            )


def test_migrations_are_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)

    db_module.init_db()
    db_module.init_db()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db_module.MIGRATIONS)


def test_normal_connections_enforce_foreign_keys(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()

    with pytest.raises(sqlite3.IntegrityError):
        with db_module.db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, expires_at) VALUES ('session', 999, '2099-01-01')"
            )


def test_existing_foreign_key_violation_blocks_startup(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            INSERT INTO sessions (id, user_id, expires_at) VALUES ('orphan', 999, '2099-01-01');
        """)
    monkeypatch.setattr(db_module, "DB_PATH", path)

    with pytest.raises(RuntimeError, match="foreign-key violations"):
        db_module.init_db()
