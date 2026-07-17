from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Callable, Generator

from .analytics import init_analytics_schema
from .config import DB_PATH

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


def _custom_domain_claims(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE custom_domain_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hostname TEXT NOT NULL,
        site_name TEXT,
        verification_token TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('pending', 'verified', 'expired', 'cancelled')),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        verified_at TEXT,
        last_checked_at TEXT,
        last_error TEXT,
        FOREIGN KEY (site_name) REFERENCES sites(name) ON DELETE SET NULL)""")
    conn.execute("""CREATE UNIQUE INDEX custom_domain_claims_verified_hostname
        ON custom_domain_claims(hostname) WHERE status = 'verified'""")
    conn.execute("""CREATE UNIQUE INDEX custom_domain_claims_active_site
        ON custom_domain_claims(site_name) WHERE status IN ('pending', 'verified')""")
    conn.execute("""CREATE INDEX custom_domain_claims_expiration
        ON custom_domain_claims(status, expires_at)""")


def _custom_domain_routing(conn: sqlite3.Connection) -> None:
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN challenge_token TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN route_status TEXT NOT NULL DEFAULT 'not_routed'
        CHECK (route_status IN ('not_routed', 'publishing', 'routed', 'removing', 'removed'))""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN route_generation INTEGER NOT NULL DEFAULT 0""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN route_error TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN route_updated_at TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN removal_requested_at TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN withdrawn_at TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN challenge_seen_at TEXT""")
    conn.execute("""CREATE UNIQUE INDEX custom_domain_claims_challenge_token
        ON custom_domain_claims(challenge_token) WHERE challenge_token IS NOT NULL""")


def _custom_domain_activation(conn: sqlite3.Connection) -> None:
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN activated_at TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN activation_checked_at TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN activation_error TEXT""")


def _multiple_custom_domains(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX custom_domain_claims_active_site")
    conn.execute("""CREATE INDEX custom_domain_claims_site_status
        ON custom_domain_claims(site_name, status)""")


def _cloudflare_diagnostics(conn: sqlite3.Connection) -> None:
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN claim_mode TEXT NOT NULL DEFAULT 'direct'
        CHECK (claim_mode IN ('direct', 'cloudflare'))""")
    conn.execute("""CREATE TABLE custom_domain_cloudflare_diagnostics (
        claim_id INTEGER NOT NULL,
        route_generation INTEGER NOT NULL,
        checked_at TEXT NOT NULL,
        ranges_version TEXT,
        dns_status TEXT NOT NULL,
        dns_error TEXT,
        edge_tls_status TEXT NOT NULL,
        edge_tls_error TEXT,
        edge_http_status TEXT NOT NULL,
        edge_http_error TEXT,
        edge_http_status_code INTEGER,
        edge_address TEXT,
        cf_ray TEXT,
        cf_cache_status TEXT,
        redirect_location TEXT,
        http_forward_status TEXT NOT NULL,
        http_forward_error TEXT,
        http_forward_status_code INTEGER,
        origin_status TEXT NOT NULL,
        origin_error TEXT,
        PRIMARY KEY (claim_id, route_generation),
        FOREIGN KEY (claim_id) REFERENCES custom_domain_claims(id) ON DELETE CASCADE)""")


def _cloudflare_activation(conn: sqlite3.Connection) -> None:
    conn.execute("""ALTER TABLE custom_domain_cloudflare_diagnostics
        ADD COLUMN ownership_status TEXT NOT NULL DEFAULT 'not_checked'""")
    conn.execute("""ALTER TABLE custom_domain_cloudflare_diagnostics
        ADD COLUMN ownership_error TEXT""")
    conn.execute("""ALTER TABLE custom_domain_cloudflare_diagnostics
        ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0""")


MIGRATIONS: tuple[Migration, ...] = (
    _base_schema,
    _custom_domain_claims,
    _custom_domain_routing,
    _custom_domain_activation,
    _multiple_custom_domains,
    _cloudflare_diagnostics,
    _cloudflare_activation,
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
