from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Callable, Generator

from .analytics import init_analytics_schema
from .config import DB_PATH
from .custom_domains.schema import (
    _custom_domain_claims,
    _custom_domain_routing,
    _custom_domain_activation,
    _multiple_custom_domains,
    _cloudflare_diagnostics,
    _cloudflare_activation,
    _automatic_domain_transitions,
    _transition_target_ttl,
    _domain_path_evidence,
    _automatic_transition_retarget,
)

Migration = Callable[[sqlite3.Connection], None]


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")


def _base_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS sites (
        name TEXT PRIMARY KEY,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        size_bytes INTEGER,
        owner_id INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        github_id INTEGER UNIQUE NOT NULL,
        github_login TEXT NOT NULL,
        github_name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS deployment_tokens (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        site_name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME,
        last_used_at DATETIME,
        FOREIGN KEY (site_name) REFERENCES sites(name) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sites)")}
    if "owner_id" not in columns:
        conn.execute("ALTER TABLE sites ADD COLUMN owner_id INTEGER")
    init_analytics_schema(conn)


MIGRATIONS: tuple[Migration, ...] = (
    _base_schema,
    _custom_domain_claims,
    _custom_domain_routing,
    _custom_domain_activation,
    _multiple_custom_domains,
    _cloudflare_diagnostics,
    _cloudflare_activation,
    _automatic_domain_transitions,
    _transition_target_ttl,
    _domain_path_evidence,
    _automatic_transition_retarget,
)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        _configure_connection(conn)
        conn.execute("BEGIN IMMEDIATE")
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version > len(MIGRATIONS):
            raise RuntimeError(
                f"Database schema version {current_version} is newer than supported version {len(MIGRATIONS)}"
            )
        for version, migration in enumerate(MIGRATIONS, start=1):
            if version <= current_version:
                continue
            migration(conn)
            conn.execute(f"PRAGMA user_version = {version}")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                "Database contains foreign-key violations; restore or repair it before starting Buzz"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    _configure_connection(conn)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
