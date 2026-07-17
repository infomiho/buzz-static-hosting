from dataclasses import dataclass

from .custom_domains import DomainClaim
from .domain_transitions import DomainClaimStateMachine, DomainModeTransition


@dataclass(frozen=True)
class DomainConnection:
    status: str
    effective_mode: str | None
    observed_mode: str | None
    target_mode: str | None
    transition_started_at: str | None
    transition_deadline_at: str | None
    transition_error: str | None
    transition_state: str | None

    @property
    def status_label(self) -> str:
        return {
            "waiting_for_dns": "Waiting for DNS",
            "securing": "Securing connection",
            "connected": "Connected",
            "updating": "Updating connection",
            "action_needed": "Action needed",
        }[self.status]

    @property
    def has_cloudflare_path(self) -> bool:
        return "cloudflare" in {
            self.effective_mode,
            self.observed_mode,
            self.target_mode,
        }

    @property
    def can_retry(self) -> bool:
        return self.transition_state == "failed"

    @property
    def can_cancel(self) -> bool:
        return self.transition_state in DomainClaimStateMachine.ACTIVE_STATES

    @property
    def show_paths(self) -> bool:
        return bool(
            self.transition_state in DomainClaimStateMachine.ACTIVE_STATES
            or self.transition_state == "failed"
        )


def project_domain_connection(
    claim: DomainClaim,
    transition: DomainModeTransition | None = None,
) -> DomainConnection:
    active_lifecycle = bool(
        claim.status == "verified"
        and claim.route_status == "routed"
        and not claim.removal_requested_at
    )
    effective_mode = (
        claim.claim_mode if active_lifecycle and claim.has_fresh_health() else None
    )
    if transition and transition.state == "failed":
        status = "action_needed"
    elif transition and transition.state == "cancelled" and not transition.source_mode:
        status = "waiting_for_dns"
    elif transition and transition.state in DomainClaimStateMachine.ACTIVE_STATES:
        if transition.state == "action_needed":
            status = "action_needed"
        elif effective_mode:
            status = "updating"
        elif claim.activated_at:
            status = "action_needed"
        else:
            status = "securing"
    elif effective_mode:
        status = "connected"
    elif active_lifecycle and claim.activated_at:
        status = "action_needed"
    elif claim.status == "verified" and claim.route_status in {"publishing", "routed"}:
        status = "securing"
    else:
        status = "waiting_for_dns"
    exposes_transition_path = bool(
        transition
        and (
            transition.state in DomainClaimStateMachine.ACTIVE_STATES
            or transition.state == "failed"
        )
    )
    return DomainConnection(
        status=status,
        effective_mode=effective_mode,
        observed_mode=transition.observed_mode if exposes_transition_path else None,
        target_mode=transition.target_mode if exposes_transition_path else None,
        transition_started_at=transition.started_at if exposes_transition_path else None,
        transition_deadline_at=transition.deadline_at if exposes_transition_path else None,
        transition_error=transition.error if exposes_transition_path else None,
        transition_state=transition.state if exposes_transition_path else None,
    )
