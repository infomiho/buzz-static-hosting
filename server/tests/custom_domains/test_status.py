from types import SimpleNamespace

import pytest

from server.custom_domains.status import DomainConnection, project_domain_task


def connection(status: str, *, can_retry: bool = False) -> DomainConnection:
    transition_state = "failed" if can_retry else None
    return DomainConnection(
        status=status,
        effective_mode=None,
        observed_mode=None,
        target_mode=None,
        transition_started_at=None,
        transition_deadline_at=None,
        transition_error=None,
        transition_state=transition_state,
    )


@pytest.mark.parametrize(
    ("claim_status", "route_status", "connection_status", "can_retry", "phase", "next_action", "is_open"),
    [
        ("pending", "not_routed", "waiting_for_dns", False, "verify_ownership", "check_ownership", True),
        ("verified", "routed", "waiting_for_dns", False, "configure_dns", "configure_dns", True),
        ("verified", "publishing", "securing", False, "connecting", "wait", False),
        ("verified", "routed", "connected", False, "connected", "visit", False),
        ("verified", "routed", "updating", False, "updating", "wait", False),
        ("verified", "routed", "action_needed", True, "action_needed", "retry", True),
        ("verified", "removing", "connected", False, "removing", "wait", True),
    ],
)
def test_domain_task_projects_one_user_phase_and_next_action(
    claim_status,
    route_status,
    connection_status,
    can_retry,
    phase,
    next_action,
    is_open,
):
    claim = SimpleNamespace(
        status=claim_status,
        route_status=route_status,
        activation_error=None,
    )

    task = project_domain_task(
        claim,
        connection(connection_status, can_retry=can_retry),
    )

    assert task.phase == phase
    assert task.next_action == next_action
    assert task.open_by_default is is_open


def test_domain_task_requires_dns_update_when_observed_path_misses_target():
    claim = SimpleNamespace(
        status="verified",
        route_status="routed",
        activation_error=None,
    )
    current = DomainConnection(
        status="securing",
        effective_mode=None,
        observed_mode="direct",
        target_mode="cloudflare",
        transition_started_at=None,
        transition_deadline_at=None,
        transition_error=None,
        transition_state="observing",
    )

    task = project_domain_task(claim, current)

    assert task.phase == "configure_dns"
    assert task.next_action == "configure_dns"
    assert task.open_by_default


def test_domain_task_does_not_blame_configuration_for_transient_dns_failure():
    claim = SimpleNamespace(
        status="verified",
        route_status="routed",
        activation_error="dns_unavailable",
    )

    task = project_domain_task(claim, connection("securing"))

    assert task.phase == "action_needed"
    assert task.next_action == "wait"
    assert task.summary == "Buzz could not check DNS right now. It will retry automatically."
