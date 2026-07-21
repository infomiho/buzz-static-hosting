"""Transition legality expressed once: the states, the guard fragments, and the
edge table that together describe how a custom-domain mode transition may move.

The scattered guard literals of the state machine all reduce to a small set of
reusable fragments here. Two orientations exist because a transition step may
update either table: transition rows guarded by an EXISTS over the owning claim,
or claim rows guarded by an EXISTS over the reserved transition. The schema
triggers remain the database-level backstop; the consistency test asserts this
table and those triggers agree on the active states.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class TransitionState(StrEnum):
    OBSERVING = "observing"
    VALIDATING = "validating"
    ACTION_NEEDED = "action_needed"
    DEADLINE_EVALUATION = "deadline_evaluation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# The states an owner may still act within. A transition leaves these only for a
# terminal state (completed, cancelled, failed).
ACTIVE_STATE_ORDER: tuple[TransitionState, ...] = (
    TransitionState.OBSERVING,
    TransitionState.VALIDATING,
    TransitionState.ACTION_NEEDED,
    TransitionState.DEADLINE_EVALUATION,
)
# Active states before the 24h deadline is evaluated. Entering deadline
# evaluation and automatic retargeting both read from this subset, so a claim
# already past the deadline cannot be retargeted or re-evaluated.
PRE_DEADLINE_STATE_ORDER: tuple[TransitionState, ...] = ACTIVE_STATE_ORDER[:3]
ACTIVE_STATES: frozenset[str] = frozenset(state.value for state in ACTIVE_STATE_ORDER)


def state_in(states: Iterable[TransitionState], column: str = "state") -> str:
    joined = ", ".join(f"'{state.value}'" for state in states)
    return f"{column} IN ({joined})"


ACTIVE_STATES_SQL = state_in(ACTIVE_STATE_ORDER)
PRE_DEADLINE_STATES_SQL = state_in(PRE_DEADLINE_STATE_ORDER)

def lease_held(prefix: str = "") -> str:
    """This owner still holds the probe and its lease has not expired."""
    column = f"{prefix}." if prefix else ""
    return f"{column}lease_owner = ? AND {column}lease_expires_at > datetime('now')"


# A held lease in the common, unaliased orientation.
LEASE_HELD_SQL = lease_held()
# A lease that may be claimed: none exists yet or the prior one lapsed.
LEASE_AVAILABLE_SQL = "(lease_expires_at IS NULL OR lease_expires_at <= datetime('now'))"

# Identify a transition row within its generation. reserve() claims by
# (claim_id, mode_generation) since it mints the probe_generation; every later
# step keys on the reserved probe_generation too.
TRANSITION_GENERATION_KEY_SQL = "claim_id = ? AND mode_generation = ?"
RESERVED_KEY_SQL = f"{TRANSITION_GENERATION_KEY_SQL} AND probe_generation = ?"


def claim_routed_exists(*, route_generation: bool = True) -> str:
    """EXISTS gate proving the owning claim is a routed, verified, live domain.

    Used when a transition-row UPDATE must confirm its claim still hosts. The
    renewal path omits route_generation because it re-checks an already reserved
    probe rather than re-establishing the route identity.
    """
    identity = (
        "id = ? AND route_generation = ? AND mode_generation = ?"
        if route_generation
        else "id = ? AND mode_generation = ?"
    )
    return (
        "EXISTS (SELECT 1 FROM custom_domain_claims"
        f" WHERE {identity} AND status = 'verified'"
        " AND route_status = 'routed' AND removal_requested_at IS NULL)"
    )


def reserved_transition_exists(
    *, extra: str = "", states: Iterable[TransitionState] = ACTIVE_STATE_ORDER
) -> str:
    """EXISTS gate proving a claim-row UPDATE still owns its reserved probe.

    ``extra`` carries any per-edge transition predicate (a source_mode match)
    whose placeholder slots between the key and the lease owner.
    """
    predicates = RESERVED_KEY_SQL
    if extra:
        predicates += f" AND {extra}"
    return (
        "EXISTS (SELECT 1 FROM custom_domain_mode_transitions"
        f" WHERE {predicates} AND {LEASE_HELD_SQL}"
        f" AND {state_in(states)})"
    )


def claim_scope(
    *,
    table: str = "",
    activation: str | None = None,
    claim_mode: bool = False,
    automatic: bool = False,
    include_removal: bool = True,
) -> str:
    """Claim-row predicates identifying and gating the owning claim.

    Only ``id``, ``route_generation``, ``mode_generation`` and (when requested)
    ``claim_mode`` bind placeholders, in that order. The failure edges pass
    ``include_removal=False`` so deactivation still works once removal begins.
    ``table`` prefixes the columns for the INSERT...SELECT store guards.
    """
    prefix = f"{table}." if table else ""
    parts = [
        f"{prefix}id = ? AND {prefix}route_generation = ? AND {prefix}mode_generation = ?"
    ]
    if claim_mode:
        parts.append(f"{prefix}claim_mode = ?")
    if automatic:
        parts.append(f"{prefix}automatic_mode = 1")
    if activation == "activated":
        parts.append(f"{prefix}activated_at IS NOT NULL")
    elif activation == "not_activated":
        parts.append(f"{prefix}activated_at IS NULL")
    parts.append(f"{prefix}status = 'verified'")
    parts.append(f"{prefix}route_status = 'routed'")
    if include_removal:
        parts.append(f"{prefix}removal_requested_at IS NULL")
    return " AND ".join(parts)


@dataclass(frozen=True)
class Edge:
    """One legal move of the transition state machine.

    ``guards`` names the legality predicates the applier enforces; the SQL is
    emitted by the fragment builders above. The flags record the side effects
    that fence generations and free the lease.
    """

    event: str
    from_states: tuple[TransitionState, ...]
    to_state: TransitionState | None
    guards: tuple[str, ...] = ()
    bumps_mode_generation: bool = False
    clears_lease: bool = False


_ROUTED = "claim_routed"
_LEASE = "lease_held"
_ONBOARDING = "claim_onboarding"
_HANDOFF = "claim_handoff"
_NOT_ACTIVATED = "claim_not_activated"
_MATCHES_ACTIVATION = "activation_matches_source"
_AUTOMATIC = "automatic_retarget"
_STABLE = "stable_target"
_CONFIRMED = "confirmed_answer"
_DIAGNOSED = "cloudflare_diagnosed_for_cloudflare_target"
_ALLOW_REMOVAL = "allow_removal"
_DEADLINE_DUE = "deadline_due"

EDGES: tuple[Edge, ...] = (
    Edge(
        "start",
        (),
        TransitionState.OBSERVING,
        guards=(_ROUTED,),
    ),
    Edge(
        "observe",
        ACTIVE_STATE_ORDER,
        None,
        guards=(_ROUTED, _LEASE),
    ),
    Edge(
        "confirm",
        ACTIVE_STATE_ORDER,
        None,
        guards=(_ROUTED, _LEASE, _STABLE),
    ),
    Edge(
        "action_needed",
        ACTIVE_STATE_ORDER,
        TransitionState.ACTION_NEEDED,
        guards=(_ROUTED, _LEASE),
        clears_lease=True,
    ),
    Edge(
        "complete",
        ACTIVE_STATE_ORDER,
        TransitionState.COMPLETED,
        guards=(_ROUTED, _LEASE, _STABLE, _CONFIRMED, _DIAGNOSED),
        clears_lease=True,
    ),
    Edge(
        "cancel",
        ACTIVE_STATE_ORDER,
        TransitionState.CANCELLED,
        guards=(_ROUTED, _LEASE, _MATCHES_ACTIVATION),
        bumps_mode_generation=True,
        clears_lease=True,
    ),
    Edge(
        "fail",
        ACTIVE_STATE_ORDER,
        TransitionState.FAILED,
        guards=(_ROUTED, _LEASE, _ALLOW_REMOVAL),
        clears_lease=True,
    ),
    Edge(
        "preserve_target",
        ACTIVE_STATE_ORDER,
        TransitionState.OBSERVING,
        guards=(_ROUTED, _LEASE, _HANDOFF, _NOT_ACTIVATED),
        bumps_mode_generation=True,
        clears_lease=True,
    ),
    Edge(
        "retarget",
        PRE_DEADLINE_STATE_ORDER,
        TransitionState.OBSERVING,
        guards=(_ROUTED, _LEASE, _ONBOARDING, _NOT_ACTIVATED, _AUTOMATIC, _STABLE),
        bumps_mode_generation=True,
        clears_lease=True,
    ),
    Edge(
        "enter_deadline_evaluation",
        PRE_DEADLINE_STATE_ORDER,
        TransitionState.DEADLINE_EVALUATION,
        guards=(_ROUTED, _LEASE, _DEADLINE_DUE),
    ),
)

EDGES_BY_EVENT: dict[str, Edge] = {edge.event: edge for edge in EDGES}
