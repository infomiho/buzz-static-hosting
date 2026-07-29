from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from ..db import ReadConnection

logger = logging.getLogger(__name__)

ACCESS_CODE_PREFIX = "buzz_access_code_"
ACCESS_GRANT_PREFIX = "buzz_access_"
ACCESS_CODE_LIFETIME = timedelta(minutes=1)
ACCESS_GRANT_LIFETIME = timedelta(hours=8)


class AccessPolicyNotFound(Exception):
    pass


class AccessSiteNotFound(Exception):
    pass


class AccessNotSiteOwner(Exception):
    pass


class InvalidAccessCode(Exception):
    pass


class AccessReaderIsOwner(Exception):
    pass


@dataclass(frozen=True)
class AccessPolicy:
    id: int
    site_name: str
    # Fenced against a policy row ever being reused for a later privacy period.
    # Today going public deletes the row and cascades its codes and grants, so
    # generation never advances; the checks below keep holding if that changes.
    generation: int


@dataclass(frozen=True)
class AccessDecision:
    protected: bool
    authorized: bool


@dataclass(frozen=True)
class AccessGrant:
    token: str
    return_path: str


@dataclass(frozen=True)
class AccessReader:
    user_id: int
    github_login: str
    github_name: str | None
    avatar_url: str | None


def hash_access_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hold_publication_guard(conn, site_name: str) -> None:
    """Write the publication guard row. Callers must have announced the name
    to Access first (begin_private_publication): files become servable
    mid-deploy, so the visibility cache has to treat the name as protected
    before this row commits."""
    conn.execute(
        "INSERT INTO site_access_publication_guards (site_name) VALUES (?) "
        "ON CONFLICT(site_name) DO UPDATE SET created_at = CURRENT_TIMESTAMP",
        (site_name,),
    )


def release_publication_guard(conn, site_name: str) -> None:
    """Remove the publication guard row. The visibility cache is deliberately
    left alone: a stale entry is fail-safe, and the serving path discards it
    on its next check."""
    conn.execute(
        "DELETE FROM site_access_publication_guards WHERE site_name = ?",
        (site_name,),
    )


class _VisibilityCache:
    """Site names that may be protected: a private policy or publication guard
    exists, or is about to. Absent from a loaded cache means definitely
    public, so the serving hot path can skip the database.

    Reads take no lock; the frozenset is rebound, never mutated. Writes keep
    the fail-safe direction: a name is added before its protecting write
    commits and removed only through the version fence, so a stale entry costs
    an extra database check but can never hide a private site. Correct only
    with a single process and a single AccessService instance funnelling every
    visibility write; the site store's in-process locks codify the same
    single-process assumption.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._names: frozenset[str] | None = None
        self._version = 0

    def load(self, conn) -> None:
        rows = conn.execute(
            "SELECT site_name FROM site_access_policies "
            "UNION SELECT site_name FROM site_access_publication_guards"
        ).fetchall()
        with self._lock:
            self._version += 1
            self._names = frozenset(row["site_name"] for row in rows)

    def may_be_protected(self, site_name: str) -> bool:
        names = self._names
        return names is None or site_name in names

    def version(self) -> int:
        return self._version

    def add(self, site_name: str) -> None:
        with self._lock:
            self._version += 1
            if self._names is not None:
                self._names = self._names | {site_name}

    def discard(self, site_name: str, seen_version: int) -> None:
        """Remove a name unless any write landed after seen_version.

        The fence closes the race where a discard for a committed delete fires
        after a concurrent add for a committed insert: the add moved the
        version, the discard becomes a no-op, and the site keeps its checks.
        """
        with self._lock:
            if self._version != seen_version or self._names is None:
                return
            self._names = self._names - {site_name}


class AccessService:
    def __init__(
        self,
        db: Callable,
        reader: ReadConnection,
    ):
        self._db = db
        self._reader = reader
        self._visibility = _VisibilityCache()

    def load_visibility(self) -> None:
        """Load the visibility cache at application startup. On failure the
        cache stays unloaded and every site gets the full database check."""
        try:
            with self._db() as conn:
                self._visibility.load(conn)
        except sqlite3.Error:
            logger.warning(
                "Could not load the visibility cache; every request will "
                "check the database",
                exc_info=True,
            )

    def close(self) -> None:
        self._reader.close()

    def get_policy(self, site_name: str, user_id: int) -> AccessPolicy | None:
        with self._db() as conn:
            self._require_owner(conn, site_name, user_id)
            return self._policy(conn, site_name)

    def set_policy(self, site_name: str, user_id: int) -> AccessPolicy:
        with self._db() as conn:
            return self.set_policy_on_connection(conn, site_name, user_id)

    def set_policy_on_connection(
        self, conn, site_name: str, user_id: int
    ) -> AccessPolicy:
        """Make a site private. Idempotent: an already-private site keeps its
        policy, so redeploying with --private does not invalidate the owner's
        existing Access session. Concurrent callers race on the site_name
        unique index, so insert-or-ignore and then read back the winner."""
        self._require_owner(conn, site_name, user_id)
        self._visibility.add(site_name)
        conn.execute(
            "INSERT INTO site_access_policies (site_name) VALUES (?) "
            "ON CONFLICT(site_name) DO NOTHING",
            (site_name,),
        )
        policy = self._policy(conn, site_name)
        if policy is None:
            raise AccessPolicyNotFound(site_name)
        return policy

    def begin_private_publication(
        self, site_name: str, user_id: int
    ) -> Callable[[sqlite3.Connection], None]:
        """Announce a private publication before its guard row is written.

        Files become servable mid-deploy, so the visibility cache must treat
        the name as protected before the guard commits. Returns the configure
        step that makes the site private inside the publish transaction.
        """
        self._visibility.add(site_name)

        def configure(conn: sqlite3.Connection) -> None:
            self.set_policy_on_connection(conn, site_name, user_id)

        return configure

    @staticmethod
    def private_site_names_on_connection(conn, site_names: list[str]) -> set[str]:
        if not site_names:
            return set()
        placeholders = ",".join("?" * len(site_names))
        rows = conn.execute(
            f"SELECT site_name FROM site_access_policies WHERE site_name IN ({placeholders})",
            site_names,
        ).fetchall()
        return {row["site_name"] for row in rows}

    def delete_policy(self, site_name: str, user_id: int) -> bool:
        # The version is captured before the transaction so a concurrent
        # set_policy fences out the discard below.
        seen = self._visibility.version()
        with self._db() as conn:
            self._require_owner(conn, site_name, user_id)
            cursor = conn.execute(
                "DELETE FROM site_access_policies WHERE site_name = ?", (site_name,)
            )
            deleted = cursor.rowcount > 0
        self._visibility.discard(site_name, seen)
        return deleted

    def list_readers(self, site_name: str, owner_id: int) -> list[AccessReader]:
        with self._db() as conn:
            self._require_owner(conn, site_name, owner_id)
            policy = self._policy(conn, site_name)
            if not policy:
                return []
            rows = conn.execute(
                "SELECT r.user_id, u.github_login, u.github_name, "
                "i.avatar_url_snapshot "
                "FROM site_access_readers r "
                "JOIN users u ON u.id = r.user_id "
                "LEFT JOIN principal_identities i "
                "ON i.user_id = u.id AND i.provider = 'github' "
                "WHERE r.policy_id = ? ORDER BY r.created_at, r.user_id",
                (policy.id,),
            ).fetchall()
        return [
            AccessReader(
                user_id=row["user_id"],
                github_login=row["github_login"],
                github_name=row["github_name"],
                avatar_url=row["avatar_url_snapshot"],
            )
            for row in rows
        ]

    def add_reader(
        self, site_name: str, owner_id: int, reader_id: int, login_snapshot: str
    ) -> None:
        with self._db() as conn:
            self._require_owner(conn, site_name, owner_id)
            policy = self._policy(conn, site_name)
            if not policy:
                raise AccessPolicyNotFound()
            if reader_id == owner_id:
                raise AccessReaderIsOwner()
            conn.execute(
                "INSERT INTO site_access_readers "
                "(policy_id, user_id, added_by_user_id, added_login_snapshot) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(policy_id, user_id) DO NOTHING",
                (policy.id, reader_id, owner_id, login_snapshot),
            )

    def remove_reader(self, site_name: str, owner_id: int, reader_id: int) -> bool:
        with self._db() as conn:
            self._require_owner(conn, site_name, owner_id)
            policy = self._policy(conn, site_name)
            if not policy:
                raise AccessPolicyNotFound()
            cursor = conn.execute(
                "DELETE FROM site_access_readers WHERE policy_id = ? AND user_id = ?",
                (policy.id, reader_id),
            )
            conn.execute(
                "DELETE FROM site_access_codes WHERE policy_id = ? AND user_id = ?",
                (policy.id, reader_id),
            )
            conn.execute(
                "DELETE FROM site_access_grants WHERE policy_id = ? AND user_id = ?",
                (policy.id, reader_id),
            )
            return cursor.rowcount > 0

    def reader_policy(self, site_name: str, user_id: int) -> AccessPolicy | None:
        with self._db() as conn:
            if not conn.execute(
                "SELECT 1 FROM sites WHERE name = ?", (site_name,)
            ).fetchone():
                raise AccessSiteNotFound()
            policy = self._policy(conn, site_name)
            if not policy:
                return None
            if not self._may_read(conn, policy.id, site_name, user_id):
                raise AccessNotSiteOwner()
            return policy

    async def check_request(
        self, site_name: str, hostname: str, raw_grant: str | None
    ) -> AccessDecision:
        """Decide access for a whole site.

        Deliberately takes no request path. The static resolver serves one file
        under several URLs, so any decision derived from the path can disagree
        with the file that is ultimately served. Keeping the path out of this
        signature makes that disagreement unrepresentable.

        The common case never touches the database: a loaded visibility cache
        proves most sites public inline. Names that may be protected pay the
        full check on the long-lived reader, in the threadpool so the event
        loop never blocks on SQLite.
        """
        if not self._visibility.may_be_protected(site_name):
            return AccessDecision(protected=False, authorized=True)
        seen = self._visibility.version()
        decision = await run_in_threadpool(
            self._check_database, site_name, hostname, raw_grant
        )
        if not decision.protected:
            # Self-healing: an entry that turned out public stops paying the
            # database check, unless a concurrent write fences the discard.
            self._visibility.discard(site_name, seen)
        return decision

    def _check_database(
        self, site_name: str, hostname: str, raw_grant: str | None
    ) -> AccessDecision:
        with self._reader.borrow() as conn:
            guarded = conn.execute(
                "SELECT 1 FROM site_access_publication_guards WHERE site_name = ?",
                (site_name,),
            ).fetchone()
            if guarded:
                return AccessDecision(protected=True, authorized=False)
            policy = self._policy(conn, site_name)
            if not policy:
                return AccessDecision(protected=False, authorized=True)
            if not raw_grant or not raw_grant.startswith(ACCESS_GRANT_PREFIX):
                return AccessDecision(protected=True, authorized=False)

            now = datetime.now().isoformat()
            row = conn.execute(
                "SELECT g.user_id FROM site_access_grants g "
                "JOIN site_access_policies p ON p.id = g.policy_id "
                "JOIN sites s ON s.name = p.site_name "
                "JOIN sessions ss ON ss.id = g.session_id "
                "WHERE g.id = ? AND g.policy_id = ? AND g.generation = p.generation "
                "AND g.hostname = ? "
                "AND (g.user_id = s.owner_id OR EXISTS ("
                "SELECT 1 FROM site_access_readers r "
                "WHERE r.policy_id = p.id AND r.user_id = g.user_id)) "
                "AND g.expires_at > ? AND ss.expires_at > ?",
                (
                    hash_access_token(raw_grant),
                    policy.id,
                    hostname,
                    now,
                    now,
                ),
            ).fetchone()
            user_id = row["user_id"] if row else None
        return AccessDecision(protected=True, authorized=user_id is not None)

    def authorize(
        self,
        site_name: str,
        hostname: str,
        return_path: str,
        user_id: int,
        session_id: str,
    ) -> str:
        now = datetime.now()
        with self._db() as conn:
            policy = self._policy(conn, site_name)
            if not policy:
                raise AccessPolicyNotFound()
            if not self._may_read(conn, policy.id, site_name, user_id):
                raise AccessNotSiteOwner()
            conn.execute(
                "DELETE FROM site_access_codes WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            conn.execute(
                "DELETE FROM site_access_grants WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            raw_code = ACCESS_CODE_PREFIX + secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO site_access_codes "
                "(id, policy_id, generation, user_id, session_id, hostname, return_path, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hash_access_token(raw_code),
                    policy.id,
                    policy.generation,
                    user_id,
                    session_id,
                    hostname,
                    return_path,
                    (now + ACCESS_CODE_LIFETIME).isoformat(),
                ),
            )
            return raw_code

    def exchange_code(
        self, raw_code: str, site_name: str, hostname: str
    ) -> AccessGrant:
        now = datetime.now()
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT c.policy_id, c.generation, c.user_id, c.session_id, "
                "c.hostname, c.return_path "
                "FROM site_access_codes c "
                "JOIN site_access_policies p ON p.id = c.policy_id "
                "JOIN sites s ON s.name = p.site_name "
                "JOIN sessions ss ON ss.id = c.session_id "
                "WHERE c.id = ? AND p.site_name = ? AND c.hostname = ? "
                "AND c.generation = p.generation "
                "AND (c.user_id = s.owner_id OR EXISTS ("
                "SELECT 1 FROM site_access_readers r "
                "WHERE r.policy_id = p.id AND r.user_id = c.user_id)) "
                "AND c.expires_at > ? AND ss.expires_at > ?",
                (
                    hash_access_token(raw_code),
                    site_name,
                    hostname,
                    now.isoformat(),
                    now.isoformat(),
                ),
            ).fetchone()
            if not row:
                raise InvalidAccessCode()
            conn.execute(
                "DELETE FROM site_access_codes WHERE id = ?",
                (hash_access_token(raw_code),),
            )
            raw_grant = ACCESS_GRANT_PREFIX + secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO site_access_grants "
                "(id, policy_id, generation, user_id, session_id, hostname, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    hash_access_token(raw_grant),
                    row["policy_id"],
                    row["generation"],
                    row["user_id"],
                    row["session_id"],
                    hostname,
                    (now + ACCESS_GRANT_LIFETIME).isoformat(),
                ),
            )
            return AccessGrant(raw_grant, row["return_path"])

    @staticmethod
    def _may_read(conn, policy_id: int, site_name: str, user_id: int) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM sites s WHERE s.name = ? AND "
                "(s.owner_id = ? OR EXISTS ("
                "SELECT 1 FROM site_access_readers r "
                "WHERE r.policy_id = ? AND r.user_id = ?))",
                (site_name, user_id, policy_id, user_id),
            ).fetchone()
        )

    @staticmethod
    def _require_owner(conn, site_name: str, user_id: int) -> None:
        row = conn.execute(
            "SELECT owner_id FROM sites WHERE name = ?", (site_name,)
        ).fetchone()
        if not row:
            raise AccessSiteNotFound()
        if row["owner_id"] is None or row["owner_id"] != user_id:
            raise AccessNotSiteOwner()

    @staticmethod
    def _policy(conn, site_name: str) -> AccessPolicy | None:
        row = conn.execute(
            "SELECT id, site_name, generation FROM site_access_policies WHERE site_name = ?",
            (site_name,),
        ).fetchone()
        if not row:
            return None
        return AccessPolicy(row["id"], row["site_name"], row["generation"])
