import ipaddress
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from server import db as db_module
from server.custom_domains.cloudflare import (
    CloudflareDiagnostician,
    CloudflareDiagnosticStore,
)
from server.custom_domains.probes import (
    MAX_CONCURRENT_CLAIM_CHECKS,
    MAX_RESOLVED_ADDRESSES,
    CloudflareRanges,
    CloudflareRangeState,
    EdgeProbeResult,
    ProbeExecutor,
)
from server.custom_domains.claims import DomainClaimStore
from server.custom_domains.evidence import (
    AddressAnswer,
    ClaimEvidence,
    DnsObservation,
    DomainDnsObserver,
    DomainEvidenceCollector,
    EvidenceResult,
)
from server.custom_domains.transitions import DomainClaimStateMachine, DomainTransitionCoordinator
from server.custom_domains.errors import ClaimConflict


@pytest.fixture
def transition_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "data.db")
    db_module.init_db()
    with db_module.db() as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('my-site')")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
    return db_module.db


def activate(conn, claim, now=None):
    current = DomainClaimStore(conn).get(claim.id, "my-site")
    assert DomainClaimStateMachine(conn).apply_activation_decision(current, None, now=now)
    return DomainClaimStore(conn).get(claim.id, "my-site")


class DiagnosticRecorder:
    def __init__(self):
        self.transitions = []

    def record_transition(self, claim, reservation, evidence, confirmed):
        self.transitions.append((claim.id, reservation.probe_generation, evidence, confirmed))
        return True

    def record_health(self, *_args):
        return True


class Collector:
    def __init__(self, observation, delay=0, target_error=None, seen=None):
        self.observation = observation
        self.delay = delay
        self.error = target_error
        self.seen = seen

    def collect(self, claim, target_mode=None):
        if self.delay:
            time.sleep(self.delay)
        if self.seen is not None:
            self.seen.add(claim.id)
        healthy = EvidenceResult("healthy")
        modes = (target_mode,) if isinstance(target_mode, str) else target_mode or ()
        if self.observation.mode not in modes:
            return ClaimEvidence(claim, healthy, self.observation, healthy, healthy)
        return ClaimEvidence(
            claim,
            healthy,
            self.observation,
            healthy,
            healthy,
            edge=(
                tuple(
                    EdgeProbeResult("healthy", None, "healthy", None, address=address)
                    for address in self.observation.addresses
                )
                if self.observation.mode == "cloudflare"
                else ()
            ),
            confirmed_dns=self.observation,
        )


class CloudflareSourceCollector:
    def __init__(self, error=None):
        self.error = error

    def collect(self, claim, _target_modes=None):
        healthy = EvidenceResult("healthy")
        ownership = healthy
        router = healthy
        origin = healthy
        ranges = healthy
        edge = EdgeProbeResult("healthy", None, "healthy", None, address="104.16.0.1")
        if self.error == "ownership_txt_mismatch":
            ownership = EvidenceResult("failed", self.error)
        elif self.error in {"router_not_observed", "router_configuration_mismatch"}:
            router = EvidenceResult("failed", self.error)
        elif self.error in {"tls_invalid", "challenge_mismatch"}:
            origin = EvidenceResult("failed", self.error)
        elif self.error == "origin_unavailable":
            origin = EvidenceResult("failed", self.error, transient=True)
        elif self.error == "range_data_stale":
            ranges = EvidenceResult("failed", self.error)
        elif self.error == "edge_tls_invalid":
            edge = EdgeProbeResult("failed", self.error, "not_checked", None)
        elif self.error:
            edge = EdgeProbeResult("healthy", None, "failed", self.error)
        observation = DnsObservation(
            "cloudflare", ("104.16.0.1",), 60, "cloudflare-source"
        )
        return ClaimEvidence(
            claim,
            ownership,
            observation,
            router,
            origin,
            ranges,
            edge=(edge,),
            confirmed_dns=observation,
        )


def coordinator(transition_db, observation, **kwargs):
    recorder = kwargs.pop("recorder", DiagnosticRecorder())
    admission = kwargs.pop("admission", True)
    cloudflare_target = kwargs.pop("cloudflare_target", True)
    return DomainTransitionCoordinator(
        kwargs.pop("collector", Collector(observation)),
        recorder,
        admission_enabled=lambda: admission,
        cloudflare_target_enabled=lambda: cloudflare_target,
        database=transition_db,
        lease_owner="test",
    )


def age_target_evidence(transition_db, claim_id):
    with transition_db() as conn:
        conn.execute(
            """UPDATE custom_domain_mode_transitions
            SET last_target_observed_at = datetime('now', '-61 seconds')
            WHERE claim_id = ?""",
            (claim_id,),
        )


def start_cloudflare_to_direct_handoff(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET claim_mode = 'cloudflare' WHERE id = ?",
            (claim.id,),
        )
        claim = activate(conn, claim)
        DomainClaimStateMachine(conn).start(claim.id, claim.route_generation, "direct")
    return claim


def test_dns_observation_keeps_families_independent_and_probes_every_address():
    answers = {
        ("proxy.example.com", "A"): AddressAnswer.addresses(("104.16.0.1",), 60),
        ("proxy.example.com", "AAAA"): AddressAnswer.addresses(("2606:4700::1",), 90),
    }
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("104.16.0.0/12"), ipaddress.ip_network("2606:4700::/32")),
    )
    observation = DomainDnsObserver(lambda name, family: answers[(name, family)], frozenset(), ranges).observe(
        "proxy.example.com"
    )

    assert observation.mode == "cloudflare"
    assert observation.addresses == ("104.16.0.1", "2606:4700::1")
    assert observation.ttl == 90
    assert observation.fingerprint


def test_dns_observation_fails_closed_if_either_family_is_untrustworthy():
    observer = DomainDnsObserver(
        lambda _name, family: AddressAnswer.addresses(("8.8.8.8",), 60)
        if family == "A"
        else AddressAnswer("timeout"),
        frozenset({"8.8.8.8"}),
    )

    observation = observer.observe("www.example.com")

    assert observation.mode == "unavailable"
    assert observation.fingerprint is None


@pytest.mark.parametrize(
    ("lookup", "expected_mode", "expected_error"),
    [
        (
            lambda name, family: AddressAnswer.cname(name, 60),
            "unavailable",
            "dns_invalid",
        ),
        (
            lambda _name, family: AddressAnswer.addresses(
                tuple(f"8.8.8.{index}" for index in range(1, 18)), 60
            )
            if family == "A"
            else AddressAnswer.no_answer(),
            "unavailable",
            "dns_too_many_addresses",
        ),
        (
            lambda _name, family: AddressAnswer.addresses(("127.0.0.1",), 60)
            if family == "A"
            else AddressAnswer.no_answer(),
            "unsupported",
            "dns_non_public_address",
        ),
        (
            lambda _name, family: AddressAnswer.addresses(("1.1.1.1",), 60)
            if family == "A"
            else AddressAnswer.no_answer(),
            "unsupported",
            None,
        ),
        (
            lambda _name, family: AddressAnswer.addresses(("8.8.8.8",), 60)
            if family == "A"
            else AddressAnswer("timeout"),
            "unavailable",
            "dns_timeout",
        ),
    ],
)
def test_dns_observer_rejects_bounded_and_unsupported_answers(
    lookup, expected_mode, expected_error
):
    observation = DomainDnsObserver(
        lookup, ingress_addresses=frozenset({"8.8.8.8"})
    ).observe("www.example.com")

    assert (observation.mode, observation.error) == (expected_mode, expected_error)


def test_evidence_collection_enforces_its_phase_deadline(
    transition_db, monkeypatch
):
    monkeypatch.setattr("server.custom_domains.evidence.PROBE_PHASE_SECONDS", 0.05)
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]

    def delayed_lookup(_name, _family):
        time.sleep(0.2)
        return AddressAnswer.no_answer()

    collector = DomainEvidenceCollector(
        DomainDnsObserver(delayed_lookup),
        "origin",
        router_validator=lambda _claim: time.sleep(0.2),
        ownership_resolver=lambda _name: time.sleep(0.2) or (),
        origin_probe=lambda *_args: time.sleep(0.2),
    )

    started = time.monotonic()
    evidence = collector.collect(claim)

    assert time.monotonic() - started < 0.15
    assert evidence.dns.error == "dns_timeout"
    assert evidence.common_error.transient


def test_complete_evidence_sample_shares_one_deadline(transition_db, monkeypatch):
    monkeypatch.setattr("server.custom_domains.evidence.PROBE_PHASE_SECONDS", 0.08)
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]

    def lookup(_name, family):
        time.sleep(0.06)
        return (
            AddressAnswer.addresses(("8.8.8.8",), 60)
            if family == "A"
            else AddressAnswer.no_answer()
        )

    collector = DomainEvidenceCollector(
        DomainDnsObserver(lookup, frozenset({"8.8.8.8"})),
        "origin",
        router_validator=lambda _claim: None,
        ownership_resolver=lambda _name: (claim.verification_value,),
        origin_probe=lambda *_args: None,
    )

    started = time.monotonic()
    evidence = collector.collect(claim, "direct")

    assert time.monotonic() - started < 0.14
    assert evidence.target_error("direct") is not None


def test_common_error_prioritizes_non_transient_invariant_failure(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    evidence = ClaimEvidence(
        claim,
        EvidenceResult("failed", "ownership_dns_unavailable", transient=True),
        DnsObservation("unavailable", error="dns_timeout"),
        EvidenceResult("failed", "router_configuration_mismatch"),
        EvidenceResult("failed", "origin_unavailable", transient=True),
    )

    assert evidence.common_error.error == "router_configuration_mismatch"


def test_network_executor_never_exceeds_twenty_concurrent_calls():
    executor = ProbeExecutor(max_workers=MAX_CONCURRENT_CLAIM_CHECKS)
    lock = threading.Lock()
    active = 0
    maximum = 0

    def work():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    futures = [executor.submit(work) for _ in range(40)]
    for future in futures:
        future.result()

    assert maximum == MAX_CONCURRENT_CLAIM_CHECKS


def test_probe_capacity_starts_all_base_operations_for_twenty_claims(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    probe_executor = ProbeExecutor()
    release = threading.Event()
    started = 0
    lock = threading.Lock()

    def block(result):
        nonlocal started
        with lock:
            started += 1
        release.wait(2)
        return result

    observer = DomainDnsObserver(
        lambda _name, _family: block(AddressAnswer.no_answer()),
        executor=probe_executor,
    )
    collector = DomainEvidenceCollector(
        observer,
        "origin",
        router_validator=lambda _claim: block(None),
        ownership_resolver=lambda _name: block((claim.verification_value,)),
        origin_probe=lambda *_args: block(None),
        executor=probe_executor,
    )
    expected = MAX_CONCURRENT_CLAIM_CHECKS * 5

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CLAIM_CHECKS) as callers:
        futures = [
            callers.submit(collector.collect, claim)
            for _ in range(MAX_CONCURRENT_CLAIM_CHECKS)
        ]
        limit = time.monotonic() + 1
        while started < expected and time.monotonic() < limit:
            time.sleep(0.005)
        release.set()
        for future in futures:
            future.result()

    assert started == expected


def test_transition_completion_requires_reserved_stable_confirmed_evidence(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        state = DomainClaimStateMachine(conn)
        transition = state.start(claim.id, claim.route_generation, "direct")
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "worker"
        )
        assert reservation
        observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
        assert state.record_reserved_observation(claim, reservation, observation)
        assert not state.complete_reserved(claim, reservation)
        assert state.release_reservation(reservation)

        conn.execute(
            """UPDATE custom_domain_mode_transitions
            SET last_target_observed_at = datetime('now', '-61 seconds') WHERE claim_id = ?""",
            (claim.id,),
        )
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "worker"
        )
        assert reservation
        assert state.record_reserved_observation(claim, reservation, observation)
        assert not state.complete_reserved(claim, reservation)
        assert state.record_reserved_confirmation(claim, reservation, observation)
        assert state.complete_reserved(claim, reservation)

        activated = DomainClaimStore(conn).get(claim.id, "my-site")
    assert activated.activated_at is not None


def test_expired_reservation_cannot_confirm_or_complete(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        state = DomainClaimStateMachine(conn)
        transition = state.start(claim.id, claim.route_generation, "direct")
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "worker"
        )
        conn.execute(
            """UPDATE custom_domain_mode_transitions
            SET observed_mode = 'direct', answer_fingerprint = 'stable',
                stable_observation_count = 2, lease_expires_at = datetime('now', '-1 second')
            WHERE claim_id = ?""",
            (claim.id,),
        )
        observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")

        assert not state.record_reserved_confirmation(claim, reservation, observation)
        assert not state.complete_reserved(claim, reservation)


@pytest.mark.parametrize(
    ("target_healthy", "effective_healthy", "expected"),
    [
        (True, False, "completed"),
        (False, True, "cancelled"),
        (True, True, "completed"),
        (False, False, "failed"),
    ],
)
def test_deadline_resolution_matrix(
    transition_db, target_healthy, effective_healthy, expected
):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET claim_mode = 'cloudflare' WHERE id = ?",
            (claim.id,),
        )
        claim = activate(conn, claim)
        state = DomainClaimStateMachine(conn)
        transition = state.start(
            claim.id,
            claim.route_generation,
            "direct",
            now=datetime.now(timezone.utc) - timedelta(days=2),
        )
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "worker"
        )
        observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
        assert state.record_reserved_observation(claim, reservation, observation)
        conn.execute(
            "UPDATE custom_domain_mode_transitions SET stable_observation_count = 2 WHERE claim_id = ?",
            (claim.id,),
        )
        assert state.record_reserved_confirmation(claim, reservation, observation)

        result = state.resolve_reserved_deadline(
            claim, reservation, target_healthy, effective_healthy
        )

        assert result == expected
        assert state.get(claim.id).state == expected


def test_probe_lease_exclusion_and_expiry_recovery(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        state = DomainClaimStateMachine(conn)
        transition = state.start(claim.id, claim.route_generation, "direct")
        first = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "first"
        )
        assert first
        assert not state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "second"
        )
        conn.execute(
            "UPDATE custom_domain_mode_transitions SET lease_expires_at = datetime('now', '-1 second')"
        )
        second = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "second"
        )

        assert second
        assert second.probe_generation == first.probe_generation + 1


def test_changed_confirmation_and_one_bad_address_fail_closed(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    healthy = EvidenceResult("healthy")
    changed = ClaimEvidence(
        claim,
        healthy,
        DnsObservation("direct", ("8.8.8.8",), 60, "first"),
        healthy,
        healthy,
        confirmed_dns=DnsObservation("direct", ("8.8.4.4",), 60, "second"),
    )
    one_bad = ClaimEvidence(
        claim,
        healthy,
        DnsObservation("cloudflare", ("104.16.0.1", "104.16.0.2"), 60, "cf"),
        healthy,
        healthy,
        edge=(
            EdgeProbeResult("healthy", None, "healthy", None),
            EdgeProbeResult("healthy", None, "failed", "edge_challenge_mismatch"),
        ),
        confirmed_dns=DnsObservation(
            "cloudflare", ("104.16.0.1", "104.16.0.2"), 60, "cf"
        ),
    )

    assert changed.target_error("direct").error == "dns_answer_changed"
    assert one_bad.target_error("cloudflare").error == "edge_challenge_mismatch"


HEALTHY_EDGE = EdgeProbeResult("healthy", None, "healthy", None)
FAMILY_UNAVAILABLE = EdgeProbeResult(
    "failed", "edge_address_family_unavailable", "not_checked", None
)


def cloudflare_evidence(claim, addresses, edge, fingerprint):
    healthy = EvidenceResult("healthy")
    observation = DnsObservation("cloudflare", addresses, 60, fingerprint)
    return ClaimEvidence(
        claim,
        healthy,
        observation,
        healthy,
        healthy,
        edge=edge,
        confirmed_dns=observation,
    )


def test_cloudflare_accepts_wholly_unroutable_ipv6_when_ipv4_fully_validates(
    transition_db,
):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    addresses = ("104.16.0.1", "104.16.0.2", "2606:4700::1", "2606:4700::2")
    evidence = cloudflare_evidence(
        claim,
        addresses,
        (HEALTHY_EDGE, HEALTHY_EDGE, FAMILY_UNAVAILABLE, FAMILY_UNAVAILABLE),
        "all-answers",
    )

    assert evidence.target_error("cloudflare") is None
    assert evidence.dns.addresses == addresses
    assert evidence.confirmed_dns.fingerprint == "all-answers"


def test_cloudflare_fails_when_all_address_families_are_unroutable(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    addresses = ("104.16.0.1", "2606:4700::1")
    evidence = cloudflare_evidence(
        claim,
        addresses,
        (FAMILY_UNAVAILABLE, FAMILY_UNAVAILABLE),
        "all-unroutable",
    )

    error = evidence.target_error("cloudflare")

    assert (error.error, error.transient) == ("edge_unavailable", True)


def test_cloudflare_does_not_skip_partial_family_transport_failure(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    addresses = ("104.16.0.1", "2606:4700::1", "2606:4700::2")
    evidence = cloudflare_evidence(
        claim,
        addresses,
        (
            HEALTHY_EDGE,
            FAMILY_UNAVAILABLE,
            EdgeProbeResult(
                "failed", "edge_unavailable", "not_checked", None
            ),
        ),
        "partial-failure",
    )

    assert evidence.target_error("cloudflare").error == "edge_unavailable"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            EdgeProbeResult("failed", "edge_tls_invalid", "not_checked", None),
            "edge_tls_invalid",
        ),
        (
            EdgeProbeResult("healthy", None, "failed", "edge_challenge_present"),
            "edge_challenge_present",
        ),
    ],
)
def test_cloudflare_does_not_hide_invalid_result_in_reachable_family(
    transition_db, result, expected
):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
    addresses = ("104.16.0.1", "104.16.0.2", "2606:4700::1")
    evidence = cloudflare_evidence(
        claim,
        addresses,
        (HEALTHY_EDGE, result, FAMILY_UNAVAILABLE),
        "reachable-failure",
    )

    assert evidence.target_error("cloudflare").error == expected


@pytest.mark.parametrize("source,target", [("direct", "cloudflare"), ("cloudflare", "direct")])
def test_active_transition_completes_in_both_directions(
    transition_db, source, target
):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET claim_mode = ? WHERE id = ?",
            (source, claim.id),
        )
        claim = activate(conn, claim)
        DomainClaimStateMachine(conn).start(claim.id, claim.route_generation, target)
    address = "104.16.0.1" if target == "cloudflare" else "8.8.8.8"
    observation = DnsObservation(target, (address,), 60, f"{target}-stable")
    collector = Collector(observation)
    range_state = CloudflareRangeState(
        CloudflareRanges(
            "test",
            datetime.now(timezone.utc),
            (
                ipaddress.ip_network("104.16.0.0/12"),
                ipaddress.ip_network("2606:4700::/32"),
            ),
        )
    )
    recorder = (
        CloudflareDiagnostician(collector, range_state=range_state)
        if target == "cloudflare"
        else DiagnosticRecorder()
    )
    worker = coordinator(
        transition_db, observation, collector=collector, recorder=recorder
    )

    worker.run_once()
    age_target_evidence(transition_db, claim.id)
    worker.run_once()

    with transition_db() as conn:
        updated = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
    assert (updated.claim_mode, transition.state) == (target, "completed")


@pytest.mark.parametrize(
    "error",
    [
        "range_data_stale",
        "ownership_txt_mismatch",
        "router_not_observed",
        "tls_invalid",
        "challenge_mismatch",
        "edge_tls_invalid",
        "edge_challenge_mismatch",
        "edge_waf_denied",
        "cloudflare_526",
    ],
)
def test_active_cloudflare_source_immediate_failure_preserves_target_on_first_proof(
    transition_db, error
):
    claim = start_cloudflare_to_direct_handoff(transition_db)
    collector = CloudflareSourceCollector(error)
    worker = coordinator(
        transition_db,
        collector.collect(claim).dns,
        collector=collector,
    )

    worker.run_once()

    with transition_db() as conn:
        failed = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
    assert failed.activated_at is None
    assert failed.activation_error == error
    assert transition.source_mode is None
    assert transition.target_mode == "direct"
    assert transition.state == "observing"


@pytest.mark.parametrize("error", ["edge_unavailable", "origin_unavailable", "cloudflare_525"])
def test_active_cloudflare_source_transient_failure_preserves_target_on_third_attempt(
    transition_db, error
):
    claim = start_cloudflare_to_direct_handoff(transition_db)
    collector = CloudflareSourceCollector(error)
    worker = coordinator(
        transition_db,
        collector.collect(claim).dns,
        collector=collector,
    )

    for expected_count in (1, 2):
        worker.run_once()
        with transition_db() as conn:
            current = DomainClaimStore(conn).get(claim.id, "my-site")
        assert current.activated_at is not None
        assert current.health_failure_count == expected_count
    worker.run_once()

    with transition_db() as conn:
        failed = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
    assert failed.activated_at is None
    assert failed.health_failure_count == 3
    assert transition.source_mode is None
    assert transition.target_mode == "direct"
    assert transition.state == "observing"


def test_source_failure_preserves_direct_handoff_after_cached_cloudflare_observation(
    transition_db,
):
    claim = start_cloudflare_to_direct_handoff(transition_db)
    collector = CloudflareSourceCollector("edge_unavailable")
    worker = coordinator(
        transition_db,
        collector.collect(claim).dns,
        collector=collector,
    )

    worker.run_once()
    worker.run_once()
    worker.run_once()
    collector.error = None
    worker.run_once()

    with transition_db() as conn:
        deactivated = DomainClaimStore(conn).get(claim.id, "my-site")
        preserved = DomainClaimStateMachine(conn).get(claim.id)
    assert deactivated.activated_at is None
    assert preserved.source_mode is None
    assert preserved.target_mode == "direct"
    assert preserved.state in DomainClaimStateMachine.ACTIVE_STATES

    direct = DnsObservation("direct", ("8.8.8.8",), 60, "direct-stable")
    worker._evidence_collector = Collector(direct)
    worker.run_once()
    age_target_evidence(transition_db, claim.id)
    worker.run_once()

    with transition_db() as conn:
        activated = DomainClaimStore(conn).get(claim.id, "my-site")
        completed = DomainClaimStateMachine(conn).get(claim.id)
    assert activated.activated_at is not None
    assert activated.claim_mode == "direct"
    assert completed.state == "completed"


def test_active_cloudflare_source_recovery_resets_failure_counter(transition_db):
    claim = start_cloudflare_to_direct_handoff(transition_db)
    collector = CloudflareSourceCollector("edge_unavailable")
    worker = coordinator(
        transition_db,
        collector.collect(claim).dns,
        collector=collector,
    )

    worker.run_once()
    collector.error = None
    worker.run_once()
    with transition_db() as conn:
        recovered = DomainClaimStore(conn).get(claim.id, "my-site")
    assert recovered.activated_at is not None
    assert recovered.health_failure_count == 0
    assert recovered.activation_error is None

    collector.error = "edge_unavailable"
    worker.run_once()
    with transition_db() as conn:
        retried = DomainClaimStore(conn).get(claim.id, "my-site")
    assert retried.activated_at is not None
    assert retried.health_failure_count == 1


def test_pending_target_failure_stays_on_transition_record(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        DomainClaimStateMachine(conn).start(
            claim.id, claim.route_generation, "cloudflare"
        )
    collector = CloudflareSourceCollector("edge_challenge_mismatch")
    worker = coordinator(
        transition_db,
        collector.collect(claim).dns,
        collector=collector,
    )

    worker.run_once()
    age_target_evidence(transition_db, claim.id)
    worker.run_once()

    with transition_db() as conn:
        unchanged = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
    assert unchanged.activation_error is None
    assert transition.state == "action_needed"
    assert transition.error == "edge_challenge_mismatch"


def test_target_observation_does_not_clear_unevaluated_source_error(transition_db):
    claim = start_cloudflare_to_direct_handoff(transition_db)
    with transition_db() as conn:
        conn.execute(
            """UPDATE custom_domain_claims
            SET activation_error = 'edge_unavailable', health_failure_count = 1
            WHERE id = ?""",
            (claim.id,),
        )
        before = DomainClaimStore(conn).get(claim.id, "my-site").health_checked_at
    observation = DnsObservation("direct", ("8.8.8.8",), 60, "direct-target")

    coordinator(transition_db, observation).run_once()

    with transition_db() as conn:
        pending = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
    assert pending.activation_error == "edge_unavailable"
    assert pending.health_failure_count == 1
    assert pending.health_checked_at == before
    assert transition.state == "validating"


def test_automatic_onboarding_completes_after_ttl_separated_observations(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute("UPDATE custom_domain_claims SET automatic_mode = 1 WHERE id = ?", (claim.id,))
    observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
    worker = coordinator(transition_db, observation)

    worker.run_once()
    worker.run_once()
    age_target_evidence(transition_db, claim.id)
    worker.run_once()

    with transition_db() as conn:
        claim = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
        path = conn.execute(
            """SELECT * FROM custom_domain_path_evidence
            WHERE claim_id = ? ORDER BY id DESC LIMIT 1""",
            (claim.id,),
        ).fetchone()
    assert claim.activated_at is not None
    assert transition.state == "completed"
    assert path["route_generation"] == claim.route_generation
    assert path["mode_generation"] == claim.mode_generation
    assert path["probe_generation"] == transition.probe_generation
    assert path["confirmation_fingerprint"] == "stable"


def test_onboarding_flags_cloudflare_when_unsupported_and_clears_on_repoint(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET automatic_mode = 1 WHERE id = ?", (claim.id,)
        )
    cloudflare = DnsObservation("cloudflare", ("104.16.0.1",), 60, "cf")
    coordinator(transition_db, cloudflare, cloudflare_target=False).run_once()

    with transition_db() as conn:
        stuck = DomainClaimStore(conn).get(claim.id, "my-site")
    assert stuck.activated_at is None
    assert stuck.last_error == "cloudflare_unsupported"

    direct = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
    coordinator(transition_db, direct).run_once()

    with transition_db() as conn:
        cleared = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
    assert cleared.last_error is None
    assert transition is not None and transition.target_mode == "direct"


def test_automatic_onboarding_retargets_after_stable_supported_mismatch(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET automatic_mode = 1 WHERE id = ?",
            (claim.id,),
        )
    cloudflare = DnsObservation(
        "cloudflare", ("104.16.0.1",), 60, "cloudflare-cached"
    )
    worker = coordinator(transition_db, cloudflare)
    worker.run_once()

    with transition_db() as conn:
        original = DomainClaimStateMachine(conn).get(claim.id)
    assert original.target_mode == "cloudflare"

    direct = DnsObservation("direct", ("8.8.8.8",), 60, "direct-stable")
    worker._evidence_collector = Collector(direct)
    worker.run_once()
    age_target_evidence(transition_db, claim.id)
    worker.run_once()

    with transition_db() as conn:
        retargeted = DomainClaimStateMachine(conn).get(claim.id)
    assert retargeted.mode_generation > original.mode_generation
    assert retargeted.source_mode is None
    assert retargeted.target_mode == "direct"
    assert retargeted.state == "observing"
    assert retargeted.stable_observation_count == 0


def test_retarget_rejects_stale_prior_reservation(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET automatic_mode = 1 WHERE id = ?",
            (claim.id,),
        )
    cloudflare = DnsObservation(
        "cloudflare", ("104.16.0.1",), 60, "cloudflare-cached"
    )
    coordinator(transition_db, cloudflare).run_once()
    direct = DnsObservation("direct", ("8.8.8.8",), 60, "direct-stable")

    with transition_db() as conn:
        state = DomainClaimStateMachine(conn)
        transition = state.get(claim.id)
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "worker"
        )
        current = DomainClaimStore(conn).get(claim.id, "my-site")
        assert state.record_reserved_observation(current, reservation, direct)
        assert state.release_reservation(reservation)
        conn.execute(
            """UPDATE custom_domain_mode_transitions
            SET last_target_observed_at = datetime('now', '-61 seconds')
            WHERE claim_id = ?""",
            (claim.id,),
        )
        reservation = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "worker"
        )
        assert state.record_reserved_observation(current, reservation, direct)
        assert state.retarget_reserved_automatic_onboarding(
            current, reservation, "direct"
        )

        retargeted = state.get(claim.id)
        assert not state.record_reserved_observation(current, reservation, direct)
    assert retargeted.mode_generation > reservation.mode_generation
    assert retargeted.probe_generation > reservation.probe_generation


def test_automatic_onboarding_does_not_retarget_on_unstable_observations(
    transition_db,
):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET automatic_mode = 1 WHERE id = ?",
            (claim.id,),
        )
    cloudflare = DnsObservation(
        "cloudflare", ("104.16.0.1",), 60, "cloudflare-cached"
    )
    worker = coordinator(transition_db, cloudflare)
    worker.run_once()
    with transition_db() as conn:
        original = DomainClaimStateMachine(conn).get(claim.id)

    observations = (
        DnsObservation("direct", ("8.8.8.8",), 60, "direct-stable"),
        cloudflare,
        DnsObservation("direct", ("8.8.8.8",), 60, "direct-stable"),
        DnsObservation("mixed", ("8.8.8.8", "104.16.0.1"), 60, "mixed"),
        DnsObservation("direct", ("8.8.8.8",), 60, "direct-stable"),
        DnsObservation("unsupported", ("1.1.1.1",), 60, "unsupported"),
        DnsObservation("direct", ("8.8.8.8",), 60, "direct-stable"),
    )
    for observation in observations:
        worker._evidence_collector = Collector(observation)
        worker.run_once()
        age_target_evidence(transition_db, claim.id)

    with transition_db() as conn:
        unchanged = DomainClaimStateMachine(conn).get(claim.id)
    assert unchanged.mode_generation == original.mode_generation
    assert unchanged.target_mode == "cloudflare"
    assert unchanged.stable_observation_count == 1


def test_transient_health_failure_deactivates_on_third_attempt(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        activate(conn, claim)
    unavailable = DnsObservation("unavailable", error="dns_timeout")
    worker = coordinator(transition_db, unavailable, admission=False)

    worker.run_once()
    worker.run_once()
    with transition_db() as conn:
        retained = DomainClaimStore(conn).get(claim.id, "my-site")
    worker.run_once()
    with transition_db() as conn:
        failed = DomainClaimStore(conn).get(claim.id, "my-site")

    assert retained.activated_at is not None
    assert failed.activated_at is None


def test_coordinator_owns_activated_legacy_cloudflare_claims(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute("UPDATE custom_domain_claims SET claim_mode = 'cloudflare' WHERE id = ?", (claim.id,))
        claim = activate(conn, claim)
    seen = set()
    observation = DnsObservation("cloudflare", ("104.16.0.1",), 60, "cf")
    worker = coordinator(
        transition_db,
        observation,
        collector=Collector(observation, seen=seen),
    )

    worker.run_once()

    assert seen == {claim.id}


def test_coordinator_persists_current_cloudflare_evidence_before_cutover(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute("UPDATE custom_domain_claims SET automatic_mode = 1 WHERE id = ?", (claim.id,))
    observation = DnsObservation("cloudflare", ("104.16.0.1",), 60, "cf-stable")
    collector = Collector(observation)
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("104.16.0.0/12"), ipaddress.ip_network("2606:4700::/32")),
    )
    range_state = CloudflareRangeState(ranges)
    diagnostician = CloudflareDiagnostician(collector, range_state=range_state)
    worker = coordinator(
        transition_db,
        observation,
        collector=collector,
        recorder=diagnostician,
    )

    worker.run_once()
    worker.run_once()
    age_target_evidence(transition_db, claim.id)
    worker.run_once()

    with transition_db() as conn:
        claim = DomainClaimStore(conn).get(claim.id, "my-site")
        transition = DomainClaimStateMachine(conn).get(claim.id)
        diagnostic = CloudflareDiagnosticStore(conn).get(
            claim.id,
            claim.route_generation,
            claim.mode_generation,
            transition.probe_generation,
        )
    assert transition.state == "completed"
    assert diagnostic.answer_fingerprint == "cf-stable"
    assert diagnostic.dns_status == "healthy"


def test_transition_probes_all_sixteen_cloudflare_addresses(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        claim = activate(conn, claim)
        DomainClaimStateMachine(conn).start(
            claim.id, claim.route_generation, "cloudflare"
        )
        claim = DomainClaimStore(conn).get(claim.id, "my-site")
    addresses = tuple(f"8.8.8.{index}" for index in range(1, MAX_RESOLVED_ADDRESSES + 1))
    ranges = CloudflareRanges(
        "test",
        datetime.now(timezone.utc),
        (ipaddress.ip_network("8.8.8.0/24"), ipaddress.ip_network("2001:4860::/32")),
    )
    range_state = CloudflareRangeState(ranges)
    checked = []

    def lookup(_name, family):
        return (
            AddressAnswer.addresses(addresses, 60)
            if family == "A"
            else AddressAnswer.no_answer()
        )

    collector = DomainEvidenceCollector(
        DomainDnsObserver(
            lookup,
            ingress_addresses=frozenset({"1.1.1.1"}),
            cloudflare_range_state=range_state,
        ),
        "origin",
        router_validator=lambda _claim: None,
        ownership_resolver=lambda _name: (claim.verification_value,),
        origin_probe=lambda *_args: None,
        edge_probe=lambda address, _claim: checked.append(address)
        or EdgeProbeResult("healthy", None, "healthy", None, address=address),
        cloudflare_range_state=range_state,
    )
    diagnostician = CloudflareDiagnostician(collector, range_state=range_state)
    worker = coordinator(
        transition_db,
        DnsObservation("cloudflare", addresses),
        collector=collector,
        recorder=diagnostician,
    )

    worker.run_once()

    with transition_db() as conn:
        transition = DomainClaimStateMachine(conn).get(claim.id)
    assert set(checked) == set(addresses)
    assert transition.state == "validating"


def test_retry_of_active_transition_is_conflict(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        state = DomainClaimStateMachine(conn)
        state.start(claim.id, claim.route_generation, "direct")

        with pytest.raises(ClaimConflict, match="active transition"):
            state.retry(claim.id, claim.route_generation)


def test_failed_retry_rejects_stale_prior_generation_evidence(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        state = DomainClaimStateMachine(conn)
        transition = state.start(claim.id, claim.route_generation, "direct")
        stale = state.reserve_probe(
            claim.id, claim.route_generation, transition.mode_generation, "worker"
        )
        assert state.fail_reserved(claim, stale, "target_check_failed")
        retried = state.retry(claim.id, claim.route_generation)
        observation = DnsObservation("direct", ("8.8.8.8",), 60, "stale")

        assert not state.record_reserved_observation(claim, stale, observation)
        assert retried.mode_generation > stale.mode_generation
        assert retried.probe_generation > stale.probe_generation


def test_onboarding_cancellation_is_idempotent(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        transition = DomainClaimStateMachine(conn).start(
            claim.id, claim.route_generation, "direct"
        )
    class NoCollection:
        def collect(self, *_args):
            pytest.fail("onboarding cancellation must not collect evidence")

    worker = coordinator(
        transition_db,
        DnsObservation("unavailable", error="dns_timeout"),
        collector=NoCollection(),
    )

    assert worker.cancel(claim.id, "my-site")
    assert worker.cancel(claim.id, "my-site")

    with transition_db() as conn:
        updated = DomainClaimStore(conn).get(claim.id, "my-site")
        cancelled = DomainClaimStateMachine(conn).get(claim.id)
    assert cancelled.state == "cancelled"
    assert cancelled.mode_generation > transition.mode_generation
    assert not updated.automatic_mode


def test_active_cancellation_rejects_unhealthy_effective_path(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        claim = activate(conn, claim)
        DomainClaimStateMachine(conn).start(
            claim.id, claim.route_generation, "cloudflare"
        )

    class UnhealthyCollector:
        def collect(self, current, _mode=None):
            healthy = EvidenceResult("healthy")
            return ClaimEvidence(
                current,
                EvidenceResult("failed", "ownership_txt_mismatch"),
                DnsObservation("direct", ("8.8.8.8",), 60, "direct"),
                healthy,
                healthy,
            )

    worker = coordinator(
        transition_db,
        DnsObservation("direct"),
        collector=UnhealthyCollector(),
    )

    with pytest.raises(ClaimConflict, match="effective domain path is not healthy"):
        worker.cancel(claim.id, "my-site")

    with transition_db() as conn:
        assert DomainClaimStateMachine(conn).get(claim.id).state == "observing"


def test_stale_health_is_not_served(transition_db):
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        activate(conn, claim)
        conn.execute(
            "UPDATE custom_domain_claims SET health_checked_at = datetime('now', '-601 seconds') WHERE id = ?",
            (claim.id,),
        )
        assert DomainClaimStore(conn).find_activated(claim.hostname) is None


def test_scheduler_runs_at_most_twenty_claims_concurrently(transition_db):
    with transition_db() as conn:
        claims = DomainClaimStore(conn)
        first = claims.list_for_site("my-site")[0]
        activate(conn, first)
        for index in range(39):
            claim = claims.create("my-site", f"alias-{index}.example.com")
            claims.record_check(claim.id, "my-site", (claim.verification_value,))
        for claim in claims.prepare_routes(True):
            claims.mark_routed(claim.id, claim.route_generation)
            activate(conn, claim)

    lock = threading.Lock()
    active = 0
    maximum = 0

    class DelayedCollector(Collector):
        def collect(self, claim, target_mode=None):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            try:
                return super().collect(claim, target_mode)
            finally:
                with lock:
                    active -= 1

    observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
    worker = coordinator(transition_db, observation, collector=DelayedCollector(observation))
    started = time.monotonic()
    worker.run_once()

    assert maximum == 20
    assert time.monotonic() - started < 1


def test_scheduler_returns_at_deadline_when_collection_blocks(
    transition_db, monkeypatch
):
    monkeypatch.setattr("server.custom_domains.transitions.COORDINATOR_PASS_SECONDS", 0.05)
    with transition_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        activate(conn, claim)
        before = DomainClaimStore(conn).get(claim.id, "my-site").health_checked_at
    observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
    worker = coordinator(
        transition_db,
        observation,
        collector=Collector(observation, delay=0.2),
    )

    started = time.monotonic()
    worker.run_once()
    elapsed = time.monotonic() - started
    time.sleep(0.25)

    with transition_db() as conn:
        after = DomainClaimStore(conn).get(claim.id, "my-site").health_checked_at
    assert elapsed < 0.15
    assert after == before


def test_scheduler_orders_due_deadline_before_oldest_health_evidence(transition_db):
    with transition_db() as conn:
        claims = DomainClaimStore(conn)
        oldest = claims.list_for_site("my-site")[0]
        activate(conn, oldest)
        due = claims.create("my-site", "due.example.com")
        claims.record_check(due.id, "my-site", (due.verification_value,))
        for prepared in claims.prepare_routes(True):
            claims.mark_routed(prepared.id, prepared.route_generation)
        due = activate(conn, claims.get(due.id, "my-site"))
        DomainClaimStateMachine(conn).start(
            due.id,
            due.route_generation,
            "cloudflare",
            now=datetime.now(timezone.utc) - timedelta(days=2),
        )
        conn.execute(
            "UPDATE custom_domain_claims SET health_checked_at = datetime('now', '-1 hour') WHERE id = ?",
            (oldest.id,),
        )
        conn.execute(
            "UPDATE custom_domain_claims SET health_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (due.id,),
        )

        ordered = DomainClaimStateMachine(conn).managed_candidates()

    assert [claim.id for claim in ordered[:2]] == [due.id, oldest.id]


def test_scheduler_covers_1000_oldest_first_with_real_work(transition_db):
    with transition_db() as conn:
        claims = DomainClaimStore(conn)
        first = claims.list_for_site("my-site")[0]
        activate(conn, first)
        rows = [
            (f"capacity-{index}.example.com", f"token-{index}", f"challenge-{index}")
            for index in range(999)
        ]
        conn.executemany(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, challenge_token, activated_at,
             activation_checked_at, health_checked_at)
            VALUES (?, 'my-site', ?, 'verified', CURRENT_TIMESTAMP, '2099-01-01',
                    'routed', 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                    datetime('now', '-9 minutes'))""",
            rows,
        )
    seen = set()
    observation = DnsObservation("direct", ("8.8.8.8",), 60, "stable")
    worker = coordinator(
        transition_db,
        observation,
        collector=Collector(observation, delay=0.001, seen=seen),
    )
    started = time.monotonic()
    for _ in range(25):
        worker.run_once()

    assert len(seen) == 1000
    assert time.monotonic() - started < 10
