from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from .analytics import init_analytics_schema
from .config import DB_PATH


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
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
        try:
            conn.execute("ALTER TABLE sites ADD COLUMN owner_id INTEGER")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        init_analytics_schema(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
