from dataclasses import dataclass

from .claims import DomainClaim
from .transitions import DomainClaimStateMachine, DomainModeTransition

TRANSIENT_DNS_ERRORS = {
    "dns_answer_changed",
    "dns_confirmation_missing",
    "dns_timeout",
    "dns_unavailable",
}


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


@dataclass(frozen=True)
class DomainTask:
    phase: str
    label: str
    summary: str
    next_action: str
    open_by_default: bool


def project_domain_task(claim: DomainClaim, connection: DomainConnection) -> DomainTask:
    if claim.route_status == "removing":
        return DomainTask(
            "removing",
            "Removing",
            "Buzz is safely withdrawing this domain.",
            "wait",
            True,
        )
    if claim.status == "pending":
        return DomainTask(
            "verify_ownership",
            "Verify ownership",
            "Add the DNS records below to prove ownership and point the domain to Buzz.",
            "check_ownership",
            True,
        )
    if claim.last_error == "cloudflare_unsupported":
        return DomainTask(
            "configure_dns",
            "Point the domain directly to Buzz",
            "This server can't connect Cloudflare-proxied domains right now. "
            "Point DNS directly to Buzz using the records below.",
            "configure_dns",
            True,
        )
    if connection.status == "waiting_for_dns" and claim.route_status == "routed":
        return DomainTask(
            "configure_dns",
            "Update DNS",
            "Point this domain to Buzz using the records below.",
            "configure_dns",
            True,
        )
    if connection.status == "connected":
        return DomainTask(
            "connected",
            "Connected",
            "Buzz is serving your site on this domain.",
            "visit",
            False,
        )
    if connection.status == "action_needed":
        if claim.activation_error in TRANSIENT_DNS_ERRORS:
            return DomainTask(
                "action_needed",
                "Action needed",
                "Buzz could not check DNS right now. It will retry automatically.",
                "wait",
                True,
            )
        return DomainTask(
            "action_needed",
            "Action needed",
            "Buzz could not validate this domain. Check its DNS settings.",
            "retry" if connection.can_retry else "fix_configuration",
            True,
        )
    if (
        connection.target_mode
        and connection.observed_mode
        and connection.observed_mode != connection.target_mode
    ) or (
        (claim.activation_error or "").startswith("dns_")
        and claim.activation_error not in TRANSIENT_DNS_ERRORS
    ):
        return DomainTask(
            "configure_dns",
            "Update DNS",
            "Buzz detected DNS settings that do not match the required connection.",
            "configure_dns",
            True,
        )
    if connection.status == "updating":
        return DomainTask(
            "updating",
            "Updating",
            "DNS change detected. Buzz is validating the new connection.",
            "wait",
            False,
        )
    if claim.activation_error in TRANSIENT_DNS_ERRORS:
        return DomainTask(
            "action_needed",
            "Action needed",
            "Buzz could not check DNS right now. It will retry automatically.",
            "wait",
            True,
        )
    if claim.activation_error:
        return DomainTask(
            "action_needed",
            "Action needed",
            "Buzz could not validate this domain. Check its DNS settings.",
            "fix_configuration",
            True,
        )
    if connection.status in {"waiting_for_dns", "securing"}:
        return DomainTask(
            "connecting",
            "Connecting",
            "Buzz is preparing the secure connection.",
            "wait",
            False,
        )
    return DomainTask(
        "action_needed",
        "Action needed",
        "Buzz could not validate this domain. Check its DNS settings.",
        "retry" if connection.can_retry else "fix_configuration",
        True,
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
