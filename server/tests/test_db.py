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
