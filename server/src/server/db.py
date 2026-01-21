"""Database utilities."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from .config import DB_PATH


def init_db() -> None:
    """Initialize database schema."""
    conn = sqlite3.connect(DB_PATH)
    # Sites table (with owner_id added)
    conn.execute("""CREATE TABLE IF NOT EXISTS sites (
        name TEXT PRIMARY KEY, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, size_bytes INTEGER, owner_id INTEGER)""")
    # Users table
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        github_id INTEGER UNIQUE NOT NULL,
        github_login TEXT NOT NULL,
        github_name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    # Sessions table
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    # Deployment tokens table
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
    # Add owner_id column to sites if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE sites ADD COLUMN owner_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    conn.close()


def get_db() -> sqlite3.Connection:
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
