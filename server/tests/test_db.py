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
        assert {"activated_at", "activation_checked_at", "activation_error"} <= columns


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
