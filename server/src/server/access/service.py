from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

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


def hash_access_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccessService:
    def __init__(self, db: Callable, user_allowed: Callable[[int], bool] | None = None):
        self._db = db
        self._user_allowed = user_allowed or (lambda user_id: True)

    def get_policy(self, site_name: str, user_id: int) -> AccessPolicy | None:
        with self._db() as conn:
            self._require_owner(conn, site_name, user_id)
            return self._policy(conn, site_name)

    def set_policy(self, site_name: str, user_id: int) -> AccessPolicy:
        with self._db() as conn:
            return self.set_policy_on_connection(conn, site_name, user_id)

    @classmethod
    def set_policy_on_connection(
        cls, conn, site_name: str, user_id: int
    ) -> AccessPolicy:
        """Make a site private. Idempotent: an already-private site keeps its
        policy, so redeploying with --private does not invalidate the owner's
        existing Access session. Concurrent callers race on the site_name
        unique index, so insert-or-ignore and then read back the winner."""
        cls._require_owner(conn, site_name, user_id)
        conn.execute(
            "INSERT INTO site_access_policies (site_name) VALUES (?) "
            "ON CONFLICT(site_name) DO NOTHING",
            (site_name,),
        )
        policy = cls._policy(conn, site_name)
        if policy is None:
            raise AccessPolicyNotFound(site_name)
        return policy

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
        with self._db() as conn:
            self._require_owner(conn, site_name, user_id)
            cursor = conn.execute(
                "DELETE FROM site_access_policies WHERE site_name = ?", (site_name,)
            )
            return cursor.rowcount > 0

    def check_request(
        self, site_name: str, hostname: str, raw_grant: str | None
    ) -> AccessDecision:
        """Decide access for a whole site.

        Deliberately takes no request path. The static resolver serves one file
        under several URLs, so any decision derived from the path can disagree
        with the file that is ultimately served. Keeping the path out of this
        signature makes that disagreement unrepresentable.
        """
        with self._db() as conn:
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
                "AND g.hostname = ? AND g.user_id = s.owner_id "
                "AND g.expires_at > ? AND ss.expires_at > ?",
                (
                    hash_access_token(raw_grant),
                    policy.id,
                    hostname,
                    now,
                    now,
                ),
            ).fetchone()
            authorized = row is not None and self._user_allowed(row["user_id"])
            return AccessDecision(protected=True, authorized=authorized)

    def authorize_owner(
        self,
        site_name: str,
        hostname: str,
        return_path: str,
        user_id: int,
        session_id: str,
    ) -> str:
        now = datetime.now()
        with self._db() as conn:
            self._require_owner(conn, site_name, user_id)
            policy = self._policy(conn, site_name)
            if not policy:
                raise AccessPolicyNotFound()
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
                "AND c.generation = p.generation AND c.user_id = s.owner_id "
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
