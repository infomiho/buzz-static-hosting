"""Custom-domain schema migrations. The package ships its DDL; db.py owns the
migration ordering and version bookkeeping."""
from __future__ import annotations

import sqlite3


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


def _automatic_domain_transitions(conn: sqlite3.Connection) -> None:
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN mode_generation INTEGER NOT NULL DEFAULT 0""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN automatic_mode INTEGER NOT NULL DEFAULT 0
        CHECK (automatic_mode IN (0, 1))""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN health_failure_count INTEGER NOT NULL DEFAULT 0""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN health_checked_at TEXT""")
    conn.execute("""ALTER TABLE custom_domain_claims
        ADD COLUMN common_failure_count INTEGER NOT NULL DEFAULT 0""")
    conn.execute("""UPDATE custom_domain_claims
        SET health_checked_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
        WHERE activated_at IS NOT NULL""")
    conn.execute("""ALTER TABLE custom_domain_cloudflare_diagnostics
        RENAME TO custom_domain_cloudflare_diagnostics_old""")
    conn.execute("""CREATE TABLE custom_domain_cloudflare_diagnostics (
        claim_id INTEGER NOT NULL,
        route_generation INTEGER NOT NULL,
        mode_generation INTEGER NOT NULL DEFAULT 0,
        probe_generation INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT NOT NULL,
        ranges_version TEXT,
        answer_fingerprint TEXT,
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
        ownership_status TEXT NOT NULL DEFAULT 'not_checked',
        ownership_error TEXT,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (claim_id, route_generation, mode_generation, probe_generation),
        FOREIGN KEY (claim_id) REFERENCES custom_domain_claims(id) ON DELETE CASCADE)""")
    conn.execute("""INSERT INTO custom_domain_cloudflare_diagnostics
        (claim_id, route_generation, mode_generation, probe_generation, checked_at,
         ranges_version, answer_fingerprint, dns_status, dns_error, edge_tls_status,
         edge_tls_error, edge_http_status, edge_http_error, edge_http_status_code,
         edge_address, cf_ray, cf_cache_status, redirect_location, http_forward_status,
         http_forward_error, http_forward_status_code, origin_status, origin_error,
         ownership_status, ownership_error, consecutive_failures)
        SELECT claim_id, route_generation, 0, 0, checked_at, ranges_version, NULL,
               dns_status, dns_error, edge_tls_status, edge_tls_error, edge_http_status,
               edge_http_error, edge_http_status_code, edge_address, cf_ray,
               cf_cache_status, redirect_location, http_forward_status,
               http_forward_error, http_forward_status_code, origin_status, origin_error,
               ownership_status, ownership_error, consecutive_failures
        FROM custom_domain_cloudflare_diagnostics_old""")
    conn.execute("DROP TABLE custom_domain_cloudflare_diagnostics_old")
    conn.execute("""CREATE TABLE custom_domain_mode_transitions (
        claim_id INTEGER PRIMARY KEY,
        mode_generation INTEGER NOT NULL,
        probe_generation INTEGER NOT NULL DEFAULT 0,
        source_mode TEXT CHECK (source_mode IN ('direct', 'cloudflare')),
        target_mode TEXT NOT NULL CHECK (target_mode IN ('direct', 'cloudflare')),
        state TEXT NOT NULL CHECK (state IN (
            'observing', 'validating', 'action_needed', 'deadline_evaluation',
            'completed', 'cancelled', 'failed')),
        started_at TEXT NOT NULL,
        deadline_at TEXT,
        checked_at TEXT,
        completed_at TEXT,
        answer_fingerprint TEXT,
        confirmed_fingerprint TEXT,
        confirmed_at TEXT,
        stable_observation_count INTEGER NOT NULL DEFAULT 0,
        first_target_observed_at TEXT,
        last_target_observed_at TEXT,
        observed_mode TEXT CHECK (observed_mode IN (
            'direct', 'cloudflare', 'mixed', 'unsupported', 'unavailable')),
        observed_ttl INTEGER,
        error TEXT,
        lease_owner TEXT,
        lease_expires_at TEXT,
        FOREIGN KEY (claim_id) REFERENCES custom_domain_claims(id) ON DELETE CASCADE,
        CHECK (source_mode IS NULL OR source_mode <> target_mode))""")
    for event in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER custom_domain_transition_{event.lower()}_guard
            BEFORE {event} ON custom_domain_mode_transitions
            BEGIN
              SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM custom_domain_claims
                WHERE id = NEW.claim_id AND mode_generation = NEW.mode_generation
              ) THEN RAISE(ABORT, 'transition mode generation mismatch') END;
              SELECT CASE WHEN NEW.state IN
                ('observing', 'validating', 'action_needed', 'deadline_evaluation')
                AND NOT EXISTS (
                  SELECT 1 FROM custom_domain_claims
                  WHERE id = NEW.claim_id AND status = 'verified'
                    AND route_status = 'routed' AND site_name IS NOT NULL
                    AND removal_requested_at IS NULL
                ) THEN RAISE(ABORT, 'claim cannot transition') END;
              SELECT CASE WHEN NEW.source_mode IS NULL AND NEW.state IN
                ('observing', 'validating', 'action_needed', 'deadline_evaluation')
                AND EXISTS (
                SELECT 1 FROM custom_domain_claims
                WHERE id = NEW.claim_id AND activated_at IS NOT NULL
              ) THEN RAISE(ABORT, 'onboarding transition cannot be active') END;
              SELECT CASE WHEN NEW.source_mode IS NULL AND NEW.deadline_at IS NOT NULL
                THEN RAISE(ABORT, 'onboarding transition cannot have deadline') END;
              SELECT CASE WHEN NEW.source_mode IS NOT NULL AND NEW.deadline_at IS NULL
                THEN RAISE(ABORT, 'active transition requires deadline') END;
              SELECT CASE WHEN NEW.source_mode IS NOT NULL AND NEW.state IN
                ('observing', 'validating', 'action_needed', 'deadline_evaluation')
                AND NOT EXISTS (
                  SELECT 1 FROM custom_domain_claims WHERE id = NEW.claim_id
                    AND activated_at IS NOT NULL AND claim_mode = NEW.source_mode
                ) THEN RAISE(ABORT, 'active transition source mismatch') END;
             END""")


def _transition_target_ttl(conn: sqlite3.Connection) -> None:
    conn.execute("""ALTER TABLE custom_domain_mode_transitions
        ADD COLUMN max_target_ttl INTEGER NOT NULL DEFAULT 0""")
    conn.execute("""UPDATE custom_domain_mode_transitions
        SET max_target_ttl = COALESCE(observed_ttl, 0)
        WHERE answer_fingerprint IS NOT NULL""")


def _domain_path_evidence(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE custom_domain_path_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim_id INTEGER NOT NULL,
        route_generation INTEGER NOT NULL,
        mode_generation INTEGER NOT NULL,
        probe_generation INTEGER NOT NULL,
        checked_at TEXT NOT NULL,
        path_mode TEXT CHECK (path_mode IN ('direct', 'cloudflare')),
        observed_mode TEXT NOT NULL CHECK (observed_mode IN (
            'direct', 'cloudflare', 'mixed', 'unsupported', 'unavailable')),
        observed_addresses TEXT NOT NULL,
        answer_fingerprint TEXT,
        confirmation_fingerprint TEXT,
        common_result TEXT NOT NULL,
        path_result TEXT NOT NULL,
        FOREIGN KEY (claim_id) REFERENCES custom_domain_claims(id) ON DELETE CASCADE)""")
    conn.execute("""CREATE INDEX custom_domain_path_evidence_current
        ON custom_domain_path_evidence (
            claim_id, route_generation, mode_generation, probe_generation, id DESC)""")


def _automatic_transition_retarget(conn: sqlite3.Connection) -> None:
    conn.execute("""ALTER TABLE custom_domain_mode_transitions
        ADD COLUMN automatic_retarget INTEGER NOT NULL DEFAULT 0
        CHECK (automatic_retarget IN (0, 1))""")
    conn.execute("""UPDATE custom_domain_mode_transitions AS transitions
        SET automatic_retarget = 1
        WHERE source_mode IS NULL
          AND state IN ('observing', 'validating', 'action_needed')
          AND EXISTS (SELECT 1 FROM custom_domain_claims AS claims
            WHERE claims.id = transitions.claim_id
              AND claims.automatic_mode = 1 AND claims.activated_at IS NULL
              AND claims.mode_generation = transitions.mode_generation)""")
