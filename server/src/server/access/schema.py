import sqlite3


def _buzz_access(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE site_access_publication_guards (
        site_name TEXT PRIMARY KEY,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE site_access_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_name TEXT UNIQUE NOT NULL,
        generation INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (site_name) REFERENCES sites(name) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE site_access_patterns (
        policy_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        pattern TEXT NOT NULL,
        PRIMARY KEY (policy_id, position),
        UNIQUE (policy_id, pattern),
        FOREIGN KEY (policy_id) REFERENCES site_access_policies(id) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE site_access_codes (
        id TEXT PRIMARY KEY,
        policy_id INTEGER NOT NULL,
        generation INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        hostname TEXT NOT NULL,
        return_path TEXT NOT NULL,
        expires_at DATETIME NOT NULL,
        FOREIGN KEY (policy_id) REFERENCES site_access_policies(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE site_access_grants (
        id TEXT PRIMARY KEY,
        policy_id INTEGER NOT NULL,
        generation INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        hostname TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        FOREIGN KEY (policy_id) REFERENCES site_access_policies(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX idx_site_access_grants_policy ON site_access_grants(policy_id)"
    )
    conn.execute(
        "CREATE INDEX idx_site_access_grants_expiry ON site_access_grants(expires_at)"
    )
