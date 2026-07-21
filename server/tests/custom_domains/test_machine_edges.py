import re

from server.custom_domains.machine_edges import (
    ACTIVE_STATES,
    EDGES,
    TransitionState,
)
from server.custom_domains.transitions import DomainClaimStateMachine


def _state_sets(sql):
    return [
        frozenset(re.findall(r"'([a-z_]+)'", body))
        for body in re.findall(r"state IN \(([^)]*)\)", sql)
    ]


def _schema_sql(conn, kind):
    return [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND sql IS NOT NULL", (kind,)
        )
    ]


def test_active_states_match_transition_triggers(database):
    with database.connect() as conn:
        triggers = [
            sql
            for sql in _schema_sql(conn, "trigger")
            if "custom_domain_transition" in sql
        ]

    assert triggers
    for trigger in triggers:
        for states in _state_sets(trigger):
            assert states == ACTIVE_STATES


def test_transition_state_check_lists_every_declared_state(database):
    with database.connect() as conn:
        table = next(
            sql
            for sql in _schema_sql(conn, "table")
            if "CREATE TABLE custom_domain_mode_transitions" in sql
        )

    declared = frozenset(state.value for state in TransitionState)
    assert _state_sets(table) == [declared]


def test_active_states_alias_is_shared():
    assert DomainClaimStateMachine.ACTIVE_STATES is ACTIVE_STATES
    assert ACTIVE_STATES == frozenset(
        {"observing", "validating", "action_needed", "deadline_evaluation"}
    )


def test_edges_reference_only_declared_states():
    for edge in EDGES:
        for state in edge.from_states:
            assert isinstance(state, TransitionState)
        assert edge.to_state is None or isinstance(edge.to_state, TransitionState)


def test_terminal_edges_free_the_lease():
    terminal = {
        TransitionState.COMPLETED,
        TransitionState.CANCELLED,
        TransitionState.FAILED,
    }
    for edge in EDGES:
        if edge.to_state in terminal:
            assert edge.clears_lease


def test_active_producing_edges_require_a_routed_claim():
    for edge in EDGES:
        entered = (edge.to_state.value if edge.to_state else "") in ACTIVE_STATES
        if edge.event in {"observe", "confirm"} or entered:
            assert "claim_routed" in edge.guards
