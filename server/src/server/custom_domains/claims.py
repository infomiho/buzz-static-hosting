from __future__ import annotations

import ipaddress
import logging
import math
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import dns.exception
import dns.resolver
import idna

from .errors import (
    ClaimConflict,
    ClaimNotFound,
    DomainCheckUnavailable,
    DomainQuotaExceeded,
    InvalidHostname,
    UnsupportedClaimMode,
)

CLAIM_TTL = timedelta(hours=24)
CHECK_COOLDOWN = timedelta(seconds=60)
HEALTH_FRESHNESS_SECONDS = 10 * 60
ACTIVE_STATUSES = ("pending", "verified")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainClaimLimits:
    per_site: int
    per_user: int
    server_wide: int


@dataclass(frozen=True)
class DomainClaimQuota:
    site_usage: int
    user_usage: int
    server_usage: int
    limits: DomainClaimLimits

    @property
    def error(self) -> str | None:
        if self.site_usage >= self.limits.per_site:
            return (
                f"This site has reached its custom-domain limit of {self.limits.per_site}. "
                "Remove an alias before adding another."
            )
        if self.user_usage >= self.limits.per_user:
            return (
                f"You have reached your custom-domain limit of {self.limits.per_user}. "
                "Remove an alias before adding another."
            )
        if self.server_usage >= self.limits.server_wide:
            return (
                f"This Buzz server has reached its custom-domain limit of "
                f"{self.limits.server_wide}. Contact the server operator."
            )
        return None


class TxtResolver(Protocol):
    def lookup(self, name: str) -> tuple[str, ...]: ...


class DnsTxtResolver:
    def lookup(self, name: str) -> tuple[str, ...]:
        try:
            answer = dns.resolver.resolve(name, "TXT", lifetime=5)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return ()
        except dns.exception.DNSException as exc:
            raise DomainCheckUnavailable("DNS lookup temporarily failed") from exc
        try:
            return tuple(b"".join(record.strings).decode("ascii") for record in answer)
        except (AttributeError, UnicodeDecodeError) as exc:
            raise DomainCheckUnavailable("DNS returned an invalid TXT response") from exc


@dataclass(frozen=True)
class DomainClaim:
    id: int
    hostname: str
    site_name: str | None
    verification_token: str
    status: str
    created_at: str
    expires_at: str
    verified_at: str | None
    last_checked_at: str | None
    last_error: str | None
    challenge_token: str | None
    route_status: str
    route_generation: int
    route_error: str | None
    route_updated_at: str | None
    removal_requested_at: str | None
    withdrawn_at: str | None
    challenge_seen_at: str | None
    activated_at: str | None
    activation_checked_at: str | None
    activation_error: str | None
    claim_mode: str
    mode_generation: int
    automatic_mode: bool
    health_checked_at: str | None
    health_failure_count: int
    common_failure_count: int

    @property
    def verification_name(self) -> str:
        return f"_buzz.{self.hostname}"

    @property
    def verification_value(self) -> str:
        return f"buzz-domain-verification={self.verification_token}"

    def check_retry_after(self, now: datetime | None = None) -> int:
        if not self.last_checked_at:
            return 0
        now = now or datetime.now(timezone.utc)
        available_at = datetime.fromisoformat(self.last_checked_at) + CHECK_COOLDOWN
        return max(0, math.ceil((available_at - now).total_seconds()))

    def has_fresh_health(self, now: datetime | None = None) -> bool:
        if not self.activated_at or not self.health_checked_at:
            return False
        now = now or datetime.now(timezone.utc)
        checked_at = datetime.fromisoformat(self.health_checked_at)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return checked_at >= now - timedelta(seconds=HEALTH_FRESHNESS_SECONDS)

    @property
    def route_name(self) -> str:
        return f"buzz-domain-{self.id}-g{self.route_generation}"

    @property
    def challenge_path(self) -> str | None:
        if not self.challenge_token:
            return None
        return f"/.well-known/buzz-domain-check/{self.challenge_token}"


def normalize_hostname(raw: str, buzz_domain: str | None) -> str:
    hostname = raw.strip()
    if hostname.endswith("."):
        hostname = hostname[:-1]
    if not hostname or any(char in hostname for char in "/:@?#*"):
        raise InvalidHostname("Enter a hostname without a scheme, port, path, or wildcard")
    if any(char.isspace() for char in hostname):
        raise InvalidHostname("Hostnames cannot contain whitespace")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise InvalidHostname("IP addresses cannot be used as custom domains")
    try:
        normalized = idna.encode(hostname, uts46=True, std3_rules=True).decode("ascii").lower()
    except idna.IDNAError as exc:
        raise InvalidHostname("Enter a valid DNS hostname") from exc
    if len(normalized) > 253 or len(normalized.split(".")) < 2:
        raise InvalidHostname("Enter a fully qualified hostname")
    if normalized == "localhost" or normalized.endswith((".localhost", ".local")):
        raise InvalidHostname("Local hostnames cannot be used as custom domains")
    if buzz_domain:
        try:
            managed_domain = idna.encode(
                buzz_domain.strip().rstrip("."), uts46=True, std3_rules=True
            ).decode("ascii").lower()
        except idna.IDNAError as exc:
            raise RuntimeError("BUZZ_DOMAIN is not a valid hostname") from exc
        if normalized == managed_domain or normalized.endswith(f".{managed_domain}"):
            raise InvalidHostname("This hostname is reserved by the Buzz server")
    return normalized


class DomainClaimStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(
        self,
        site_name: str,
        hostname: str,
        now: datetime | None = None,
        limits: DomainClaimLimits | None = None,
        claim_mode: str = "direct",
        automatic_mode: bool = False,
    ) -> DomainClaim:
        now = now or datetime.now(timezone.utc)
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
        self.expire_pending(now)
        duplicate = self._conn.execute(
            """SELECT 1 FROM custom_domain_claims
            WHERE site_name = ? AND hostname = ?
              AND status IN ('pending', 'verified') LIMIT 1""",
            (site_name, hostname),
        ).fetchone()
        if duplicate:
            raise ClaimConflict("This hostname is already attached to this site")
        if limits:
            quota = self.quota(site_name, limits)
            if quota.error:
                raise DomainQuotaExceeded(quota.error)
        token = f"bdv_{secrets.token_urlsafe(32)}"
        if claim_mode not in {"direct", "cloudflare"}:
            raise UnsupportedClaimMode("Unsupported custom-domain mode")
        try:
            cursor = self._conn.execute(
                """INSERT INTO custom_domain_claims
                (hostname, site_name, verification_token, status, created_at, expires_at,
                 claim_mode, automatic_mode)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    hostname,
                    site_name,
                    token,
                    now.isoformat(),
                    (now + CLAIM_TTL).isoformat(),
                    claim_mode,
                    automatic_mode,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ClaimConflict("Could not create this custom domain claim") from exc
        return self.get(cursor.lastrowid, site_name)

    def quota(self, site_name: str, limits: DomainClaimLimits) -> DomainClaimQuota:
        owner = self._conn.execute(
            "SELECT owner_id FROM sites WHERE name = ?", (site_name,)
        ).fetchone()
        if not owner:
            raise ClaimNotFound("Site not found")
        site_usage = self._conn.execute(
            """SELECT COUNT(*) FROM custom_domain_claims
            WHERE site_name = ? AND status IN ('pending', 'verified')""",
            (site_name,),
        ).fetchone()[0]
        user_usage = self._conn.execute(
            """SELECT COUNT(*) FROM custom_domain_claims AS claims
            JOIN sites ON sites.name = claims.site_name
            WHERE sites.owner_id = ? AND claims.status IN ('pending', 'verified')""",
            (owner["owner_id"],),
        ).fetchone()[0]
        server_usage = self._conn.execute(
            """SELECT COUNT(*) FROM custom_domain_claims
            WHERE status IN ('pending', 'verified')"""
        ).fetchone()[0]
        return DomainClaimQuota(site_usage, user_usage, server_usage, limits)

    def list_for_site(self, site_name: str) -> list[DomainClaim]:
        self.expire_pending()
        rows = self._conn.execute(
            """SELECT * FROM custom_domain_claims
            WHERE site_name = ? ORDER BY id DESC""",
            (site_name,),
        ).fetchall()
        return [self.from_row(row) for row in rows]

    def get(self, claim_id: int, site_name: str) -> DomainClaim:
        self.expire_pending()
        row = self._conn.execute(
            "SELECT * FROM custom_domain_claims WHERE id = ? AND site_name = ?",
            (claim_id, site_name),
        ).fetchone()
        if not row:
            raise ClaimNotFound("Custom domain claim not found")
        return self.from_row(row)

    def record_check(
        self,
        claim_id: int,
        site_name: str,
        found_values: tuple[str, ...],
        now: datetime | None = None,
    ) -> DomainClaim:
        now = now or datetime.now(timezone.utc)
        claim = self.get(claim_id, site_name)
        if claim.status == "verified":
            return claim
        if claim.status != "pending":
            raise ClaimConflict("This custom domain claim is no longer pending")
        if claim.verification_value not in found_values:
            self._conn.execute(
                """UPDATE custom_domain_claims
                SET last_checked_at = ?, last_error = 'txt_mismatch' WHERE id = ?""",
                (now.isoformat(), claim_id),
            )
            return self.get(claim_id, site_name)
        try:
            self._conn.execute(
                """UPDATE custom_domain_claims
                SET status = 'verified', verified_at = ?, last_checked_at = ?, last_error = NULL
                WHERE id = ? AND status = 'pending'""",
                (now.isoformat(), now.isoformat(), claim_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ClaimConflict("This hostname is already verified on this Buzz server") from exc
        return self.get(claim_id, site_name)

    def reserve_check(
        self,
        claim_id: int,
        site_name: str,
        now: datetime | None = None,
    ) -> DomainClaim:
        now = now or datetime.now(timezone.utc)
        self.expire_pending(now)
        available_before = (now - CHECK_COOLDOWN).isoformat()
        cursor = self._conn.execute(
            """UPDATE custom_domain_claims SET last_checked_at = ?
            WHERE id = ? AND site_name = ? AND status = 'pending'
              AND (last_checked_at IS NULL OR last_checked_at <= ?)""",
            (now.isoformat(), claim_id, site_name, available_before),
        )
        if cursor.rowcount:
            return self.get(claim_id, site_name)
        claim = self.get(claim_id, site_name)
        if claim.status == "pending" and claim.check_retry_after(now):
            raise ClaimConflict("Wait before checking this custom domain again")
        return claim

    def record_check_error(
        self,
        claim_id: int,
        site_name: str,
        error: str,
        now: datetime | None = None,
    ) -> DomainClaim:
        now = now or datetime.now(timezone.utc)
        claim = self.get(claim_id, site_name)
        if claim.status != "pending":
            return claim
        self._conn.execute(
            """UPDATE custom_domain_claims
            SET last_checked_at = ?, last_error = ? WHERE id = ?""",
            (now.isoformat(), error, claim_id),
        )
        return self.get(claim_id, site_name)

    def set_onboarding_error(
        self, claim_id: int, route_generation: int, error: str | None
    ) -> None:
        # Surface a pre-activation onboarding signal (e.g. Cloudflare detected on
        # a server that cannot validate it) on a verified, routed, not-yet-activated
        # claim, without disturbing claims in any other state.
        self._conn.execute(
            """UPDATE custom_domain_claims SET last_error = ?
            WHERE id = ? AND route_generation = ? AND status = 'verified'
              AND route_status = 'routed' AND activated_at IS NULL""",
            (error, claim_id, route_generation),
        )

    def cancel(
        self,
        claim_id: int,
        site_name: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        claim = self.get(claim_id, site_name)
        if claim.status not in ACTIVE_STATUSES:
            return False
        if claim.route_status == "removing":
            if not claim.removal_requested_at:
                self._conn.execute(
                    """UPDATE custom_domain_claims SET removal_requested_at = ?
                    WHERE id = ? AND route_status = 'removing'
                      AND removal_requested_at IS NULL""",
                    (now.isoformat(), claim_id),
                )
            return True
        if claim.route_status in {"publishing", "routed"}:
            self._invalidate_transition(claim.id)
            self._conn.execute(
                """UPDATE custom_domain_claims
                SET route_status = 'removing', removal_requested_at = ?,
                    route_updated_at = ?, route_error = NULL WHERE id = ?""",
                (now.isoformat(), now.isoformat(), claim_id),
            )
            logger.info(
                "Custom domain claim %d generation %d queued for withdrawal",
                claim.id,
                claim.route_generation,
            )
            return True
        self._invalidate_transition(claim.id)
        self._conn.execute(
            """UPDATE custom_domain_claims
            SET status = 'cancelled', route_status = 'removed',
                removal_requested_at = ?, withdrawn_at = ?, route_updated_at = ?
            WHERE id = ?""",
            (now.isoformat(), now.isoformat(), now.isoformat(), claim_id),
        )
        return False

    def prepare_routes(
        self,
        routing_enabled: bool,
        now: datetime | None = None,
    ) -> list[DomainClaim]:
        now = now or datetime.now(timezone.utc)
        if routing_enabled:
            rows = self._conn.execute(
                """SELECT id FROM custom_domain_claims
                WHERE status = 'verified' AND route_status = 'not_routed'"""
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    """UPDATE custom_domain_claims
                    SET route_status = 'publishing', route_generation = route_generation + 1,
                        challenge_token = ?, challenge_seen_at = NULL,
                        route_updated_at = ?, route_error = NULL, withdrawn_at = NULL
                    WHERE id = ?""",
                    (f"bdc_{secrets.token_urlsafe(32)}", now.isoformat(), row["id"]),
                )
                self._conn.execute(
                    """UPDATE custom_domain_claims
                    SET activated_at = NULL, activation_checked_at = NULL,
                        activation_error = NULL, health_checked_at = NULL,
                        health_failure_count = 0, common_failure_count = 0
                    WHERE id = ?""",
                    (row["id"],),
                )
                logger.info("Custom domain claim %d queued for publication", row["id"])
        else:
            transition_claims = self._conn.execute(
                """SELECT id FROM custom_domain_claims
                WHERE status = 'verified' AND route_status IN ('publishing', 'routed')"""
            ).fetchall()
            for row in transition_claims:
                self._invalidate_transition(row["id"])
            cursor = self._conn.execute(
                """UPDATE custom_domain_claims
                SET route_status = 'removing', route_updated_at = ?, route_error = NULL
                WHERE status = 'verified' AND route_status IN ('publishing', 'routed')""",
                (now.isoformat(),),
            )
            if cursor.rowcount:
                logger.info(
                    "%d custom domain routes queued for operator withdrawal",
                    cursor.rowcount,
                )
        rows = self._conn.execute(
            """SELECT * FROM custom_domain_claims
            WHERE status = 'verified' AND route_status IN ('publishing', 'routed', 'removing')
            ORDER BY id"""
        ).fetchall()
        return [self.from_row(row) for row in rows]

    def routable_claims(self) -> list[DomainClaim]:
        rows = self._conn.execute(
            """SELECT * FROM custom_domain_claims
            WHERE status = 'verified' AND route_status IN ('publishing', 'routed')
            ORDER BY id"""
        ).fetchall()
        return [self.from_row(row) for row in rows]

    def activation_candidates(self) -> list[DomainClaim]:
        rows = self._conn.execute(
            """SELECT * FROM custom_domain_claims
            WHERE status = 'verified' AND route_status = 'routed'
              AND activated_at IS NULL AND claim_mode = 'direct'
              AND automatic_mode = 0 AND health_checked_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM custom_domain_mode_transitions
                WHERE claim_id = custom_domain_claims.id
                  AND mode_generation = custom_domain_claims.mode_generation)
            ORDER BY activation_checked_at IS NOT NULL, activation_checked_at, id"""
        ).fetchall()
        return [self.from_row(row) for row in rows]

    def find_activated(self, hostname: str) -> DomainClaim | None:
        row = self._conn.execute(
            """SELECT * FROM custom_domain_claims
            WHERE hostname = ? AND status = 'verified' AND route_status = 'routed'
              AND route_error IS NULL AND activated_at IS NOT NULL
              AND site_name IS NOT NULL
              AND julianday(health_checked_at) >= julianday('now', ?)""",
            (hostname, f"-{HEALTH_FRESHNESS_SECONDS} seconds"),
        ).fetchone()
        return self.from_row(row) if row else None

    def activated_hostnames_for_site(self, site_name: str) -> frozenset[str]:
        rows = self._conn.execute(
            """SELECT hostname FROM custom_domain_claims
            WHERE site_name = ? AND status = 'verified' AND route_status = 'routed'
              AND route_error IS NULL AND activated_at IS NOT NULL
              AND julianday(health_checked_at) >= julianday('now', ?)""",
            (site_name, f"-{HEALTH_FRESHNESS_SECONDS} seconds"),
        ).fetchall()
        return frozenset(row["hostname"] for row in rows)

    def mark_routed(
        self,
        claim_id: int,
        generation: int,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        self._conn.execute(
            """UPDATE custom_domain_claims
            SET route_status = 'routed', route_error = NULL, route_updated_at = ?
            WHERE id = ? AND route_generation = ? AND route_status = 'publishing'""",
            (now.isoformat(), claim_id, generation),
        )

    def record_route_error(
        self,
        claim_id: int,
        generation: int,
        error: str,
    ) -> bool:
        cursor = self._conn.execute(
            """UPDATE custom_domain_claims
            SET route_error = ?
            WHERE id = ? AND route_generation = ?
              AND route_status IN ('publishing', 'removing') AND route_error IS NOT ?""",
            (error, claim_id, generation, error),
        )
        return cursor.rowcount > 0

    def finish_withdrawal(
        self,
        claim_id: int,
        generation: int,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        claim = self._conn.execute(
            """SELECT removal_requested_at FROM custom_domain_claims
            WHERE id = ? AND route_generation = ? AND route_status = 'removing'""",
            (claim_id, generation),
        ).fetchone()
        if not claim:
            return
        status = "cancelled" if claim["removal_requested_at"] else "verified"
        route_status = "removed" if claim["removal_requested_at"] else "not_routed"
        self._conn.execute(
            """UPDATE custom_domain_claims
            SET status = ?, route_status = ?, withdrawn_at = ?,
                route_updated_at = ?, route_error = NULL
            WHERE id = ? AND route_generation = ?""",
            (status, route_status, now.isoformat(), now.isoformat(), claim_id, generation),
        )

    def find_challenge(self, hostname: str, token: str) -> DomainClaim | None:
        row = self._conn.execute(
            """SELECT * FROM custom_domain_claims
            WHERE hostname = ? AND challenge_token = ? AND status = 'verified'
              AND route_status IN ('publishing', 'routed')""",
            (hostname, token),
        ).fetchone()
        return self.from_row(row) if row else None

    def mark_challenge_seen(
        self,
        claim_id: int,
        generation: int,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        self._conn.execute(
            """UPDATE custom_domain_claims SET challenge_seen_at = ?
            WHERE id = ? AND route_generation = ? AND status = 'verified'
              AND route_status IN ('publishing', 'routed')""",
            (now.isoformat(), claim_id, generation),
        )

    def has_active_claim(self, site_name: str) -> bool:
        self.expire_pending()
        row = self._conn.execute(
            """SELECT 1 FROM custom_domain_claims
            WHERE site_name = ? AND status IN ('pending', 'verified') LIMIT 1""",
            (site_name,),
        ).fetchone()
        return row is not None

    def has_active_cloudflare_claim(self) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM custom_domain_claims
            WHERE claim_mode = 'cloudflare' AND activated_at IS NOT NULL
              AND route_status IN ('publishing', 'routed', 'removing') LIMIT 1"""
        ).fetchone()
        return row is not None

    def has_routed_cloudflare_claim(self) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM custom_domain_claims
            WHERE claim_mode = 'cloudflare' AND activated_at IS NOT NULL
              AND route_status = 'routed' LIMIT 1"""
        ).fetchone()
        return row is not None

    def has_routed_claim(self) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM custom_domain_claims
            WHERE route_status IN ('publishing', 'routed', 'removing') LIMIT 1"""
        ).fetchone()
        return row is not None

    def site_name_for(self, claim_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT site_name FROM custom_domain_claims WHERE id = ?",
            (claim_id,),
        ).fetchone()
        return row["site_name"] if row else None

    def expire_pending(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._conn.execute(
            """UPDATE custom_domain_claims SET status = 'expired'
            WHERE status = 'pending' AND expires_at <= ?""",
            (now.isoformat(),),
        )

    def _invalidate_transition(self, claim_id: int) -> None:
        transition = self._conn.execute(
            "SELECT 1 FROM custom_domain_mode_transitions WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        self._conn.execute(
            "UPDATE custom_domain_claims SET mode_generation = mode_generation + 1 WHERE id = ?",
            (claim_id,),
        )
        if transition:
            self._conn.execute(
                """UPDATE custom_domain_mode_transitions
                SET mode_generation = mode_generation + 1,
                    probe_generation = probe_generation + 1,
                    state = 'cancelled', completed_at = CURRENT_TIMESTAMP,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE claim_id = ?""",
                (claim_id,),
            )

    @staticmethod
    def from_row(row: sqlite3.Row) -> DomainClaim:
        return DomainClaim(
            id=row["id"],
            hostname=row["hostname"],
            site_name=row["site_name"],
            verification_token=row["verification_token"],
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            verified_at=row["verified_at"],
            last_checked_at=row["last_checked_at"],
            last_error=row["last_error"],
            challenge_token=row["challenge_token"],
            route_status=row["route_status"],
            route_generation=row["route_generation"],
            route_error=row["route_error"],
            route_updated_at=row["route_updated_at"],
            removal_requested_at=row["removal_requested_at"],
            withdrawn_at=row["withdrawn_at"],
            challenge_seen_at=row["challenge_seen_at"],
            activated_at=row["activated_at"],
            activation_checked_at=row["activation_checked_at"],
            activation_error=row["activation_error"],
            claim_mode=row["claim_mode"],
            mode_generation=row["mode_generation"],
            automatic_mode=bool(row["automatic_mode"]),
            health_checked_at=row["health_checked_at"],
            health_failure_count=row["health_failure_count"],
            common_failure_count=row["common_failure_count"],
        )
