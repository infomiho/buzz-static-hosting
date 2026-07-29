from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from .access.schema import _buzz_access, _buzz_access_readers, _buzz_access_site_level
from .analytics import init_analytics_schema
from .custom_domains.schema import (
    _automatic_domain_transitions,
    _automatic_transition_retarget,
    _cloudflare_activation,
    _cloudflare_diagnostics,
    _custom_domain_activation,
    _custom_domain_claims,
    _custom_domain_routing,
    _domain_path_evidence,
    _multiple_custom_domains,
    _transition_target_ttl,
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


def _webauthn_credentials(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE users ADD COLUMN webauthn_user_handle BLOB")
    conn.execute(
        """CREATE UNIQUE INDEX idx_users_webauthn_user_handle
        ON users(webauthn_user_handle) WHERE webauthn_user_handle IS NOT NULL"""
    )
    conn.execute("""CREATE TABLE webauthn_credentials (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        public_key BLOB NOT NULL,
        sign_count INTEGER NOT NULL,
        transports TEXT,
        backed_up INTEGER NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_used_at DATETIME,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX idx_webauthn_credentials_user_id ON webauthn_credentials(user_id)"
    )


def _principal_identities(conn: sqlite3.Connection) -> None:
    """Separate authentication identity from control-plane admission.

    The existing users table remains the stable Principal record so installed
    databases keep all ownership, session, token, and Access foreign keys.
    """
    conn.execute(
        "ALTER TABLE users ADD COLUMN control_admitted INTEGER NOT NULL DEFAULT 1"
    )
    conn.execute("""CREATE TABLE principal_identities (
        user_id INTEGER NOT NULL,
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        login_snapshot TEXT NOT NULL,
        name_snapshot TEXT,
        avatar_url_snapshot TEXT,
        last_authenticated_at DATETIME,
        PRIMARY KEY (provider, subject),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX idx_principal_identities_user ON principal_identities(user_id)"
    )
    user_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if {"github_id", "github_login", "github_name"} <= user_columns:
        conn.execute(
            "INSERT INTO principal_identities "
            "(user_id, provider, subject, login_snapshot, name_snapshot) "
            "SELECT id, 'github', CAST(github_id AS TEXT), github_login, github_name FROM users"
        )


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
    _webauthn_credentials,
    _buzz_access,
    _buzz_access_site_level,
    _principal_identities,
    _buzz_access_readers,
)


class ReadConnection:
    """A long-lived connection for hot-path reads, shared across threads.

    Opened on first borrow and reused for the process lifetime, so callers
    skip the per-connect schema parse that dominates a fresh connection.
    borrow() serializes access; hold it only for the duration of the queries.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    @contextmanager
    def borrow(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(self._path, check_same_thread=False)
                _configure_connection(conn)
                conn.row_factory = sqlite3.Row
                self._conn = conn
            yield self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


class Database:
    def __init__(self, path: Path):
        self._path = path

    def reader(self) -> ReadConnection:
        return ReadConnection(self._path)

    def init(self) -> None:
        conn = sqlite3.connect(self._path)
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
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path)
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
