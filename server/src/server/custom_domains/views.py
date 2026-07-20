"""The claim view: one read interface over a claim, assembling its connection,
task, diagnostic, and transition so callers render a claim without joining
stores themselves."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .claims import DomainClaim, DomainClaimStore
from .cloudflare import CloudflareDiagnostic, CloudflareDiagnosticStore
from .status import (
    DomainConnection,
    DomainTask,
    project_domain_connection,
    project_domain_task,
)
from .transitions import DomainClaimStateMachine, DomainModeTransition


@dataclass(frozen=True)
class ClaimView:
    claim: DomainClaim
    connection: DomainConnection
    task: DomainTask
    diagnostic: CloudflareDiagnostic | None
    transition: DomainModeTransition | None


def _has_cloudflare_path(claim: DomainClaim, connection: DomainConnection) -> bool:
    return connection.has_cloudflare_path or claim.claim_mode == "cloudflare"


def build_claim_view(conn: sqlite3.Connection, claim: DomainClaim) -> ClaimView:
    transition = DomainClaimStateMachine(conn).get(claim.id)
    connection = project_domain_connection(claim, transition)
    task = project_domain_task(claim, connection)
    diagnostic = None
    if _has_cloudflare_path(claim, connection):
        diagnostic = CloudflareDiagnosticStore(conn).get(
            claim.id, claim.route_generation, claim.mode_generation
        )
    return ClaimView(claim, connection, task, diagnostic, transition)


def claim_views_for_site(
    conn: sqlite3.Connection,
    site_name: str,
    *,
    statuses: frozenset[str] | None = None,
) -> list[ClaimView]:
    claims = DomainClaimStore(conn).list_for_site(site_name)
    if statuses is not None:
        claims = [claim for claim in claims if claim.status in statuses]
    return [build_claim_view(conn, claim) for claim in claims]
