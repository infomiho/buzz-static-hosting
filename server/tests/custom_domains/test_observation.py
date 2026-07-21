from datetime import datetime, timedelta, timezone

from server.custom_domains.observation import (
    STABLE_OBSERVATIONS_REQUIRED,
    DnsObservation,
    TrackedObservation,
    advance,
    parse_timestamp,
)

START = datetime(2026, 7, 21, 10, 0, 0, tzinfo=timezone.utc)


def make_tracked(**overrides) -> TrackedObservation:
    base = dict(
        target_mode="direct",
        automatic_retarget=False,
        observed_mode=None,
        answer_fingerprint=None,
        stable_observation_count=0,
        max_target_ttl=0,
        first_target_observed_at=None,
        last_target_observed_at=None,
    )
    base.update(overrides)
    return TrackedObservation(**base)


def test_first_target_answer_starts_tracking_run():
    decision = advance(
        make_tracked(target_mode="direct"),
        DnsObservation("direct", ttl=60, fingerprint="abc"),
        START,
    )
    assert decision.state == "validating"
    assert decision.record_answer is True
    assert decision.stable_observation_count == 1
    assert decision.max_target_ttl == 60
    assert decision.start_target_run is True
    assert decision.accept_target_sample is True


def test_same_answer_before_separation_holds_count_at_one():
    tracked = make_tracked(
        observed_mode="direct",
        answer_fingerprint="abc",
        stable_observation_count=5,
        max_target_ttl=60,
        first_target_observed_at=START.isoformat(),
        last_target_observed_at=START.isoformat(),
    )
    decision = advance(
        tracked,
        DnsObservation("direct", ttl=60, fingerprint="abc"),
        START + timedelta(seconds=10),
    )
    assert decision.stable_observation_count == 1
    assert decision.start_target_run is False
    assert decision.accept_target_sample is False


def test_ttl_separated_same_answer_increments_stability():
    tracked = make_tracked(
        observed_mode="direct",
        answer_fingerprint="abc",
        stable_observation_count=1,
        max_target_ttl=60,
        first_target_observed_at=START.isoformat(),
        last_target_observed_at=START.isoformat(),
    )
    decision = advance(
        tracked,
        DnsObservation("direct", ttl=60, fingerprint="abc"),
        START + timedelta(seconds=61),
    )
    assert decision.stable_observation_count == 2
    assert decision.accept_target_sample is True
    assert decision.start_target_run is False


def test_separation_window_is_largest_of_floor_answer_ttl_and_history():
    def increments(ttl: int, max_target_ttl: int, gap_seconds: int) -> bool:
        tracked = make_tracked(
            observed_mode="direct",
            answer_fingerprint="abc",
            stable_observation_count=1,
            max_target_ttl=max_target_ttl,
            first_target_observed_at=START.isoformat(),
            last_target_observed_at=START.isoformat(),
        )
        decision = advance(
            tracked,
            DnsObservation("direct", ttl=ttl, fingerprint="abc"),
            START + timedelta(seconds=gap_seconds),
        )
        return decision.stable_observation_count == 2

    # Floor of 60 seconds dominates a small ttl and history.
    assert increments(5, 5, 60) is True
    assert increments(5, 5, 59) is False
    # Answer ttl dominates.
    assert increments(120, 5, 120) is True
    assert increments(120, 5, 119) is False
    # Recorded history (max_target_ttl) dominates.
    assert increments(5, 200, 200) is True
    assert increments(5, 200, 199) is False


def test_answer_change_resets_run():
    tracked = make_tracked(
        observed_mode="direct",
        answer_fingerprint="abc",
        stable_observation_count=2,
        max_target_ttl=300,
        first_target_observed_at=START.isoformat(),
        last_target_observed_at=START.isoformat(),
    )
    decision = advance(
        tracked,
        DnsObservation("direct", ttl=30, fingerprint="xyz"),
        START + timedelta(seconds=120),
    )
    assert decision.stable_observation_count == 1
    assert decision.max_target_ttl == 30
    assert decision.start_target_run is True
    assert decision.accept_target_sample is True


def test_mode_change_resets_run():
    tracked = make_tracked(
        target_mode="cloudflare",
        observed_mode="direct",
        answer_fingerprint="abc",
        stable_observation_count=2,
        max_target_ttl=60,
        first_target_observed_at=START.isoformat(),
        last_target_observed_at=START.isoformat(),
    )
    decision = advance(
        tracked,
        DnsObservation("cloudflare", ttl=30, fingerprint="abc"),
        START + timedelta(seconds=120),
    )
    assert decision.stable_observation_count == 1
    assert decision.max_target_ttl == 30
    assert decision.state == "validating"


def test_non_target_mode_untracked_without_automatic_retarget():
    tracked = make_tracked(target_mode="direct", automatic_retarget=False)
    decision = advance(
        tracked,
        DnsObservation("cloudflare", ttl=60, fingerprint="cf"),
        START,
    )
    assert decision.record_answer is False
    assert decision.state == "observing"
    assert decision.stable_observation_count == 0
    assert decision.max_target_ttl == 0
    assert decision.start_target_run is False
    assert decision.accept_target_sample is False


def test_automatic_retarget_tracks_non_target_answers():
    tracked = make_tracked(target_mode="direct", automatic_retarget=True)
    decision = advance(
        tracked,
        DnsObservation("cloudflare", ttl=60, fingerprint="cf"),
        START,
    )
    assert decision.record_answer is True
    assert decision.state == "observing"
    assert decision.stable_observation_count == 1
    assert decision.max_target_ttl == 60


def test_missing_fingerprint_never_tracks():
    tracked = make_tracked(
        target_mode="direct",
        automatic_retarget=True,
        stable_observation_count=3,
        max_target_ttl=99,
    )
    decision = advance(
        tracked,
        DnsObservation("direct", ttl=60, fingerprint=None),
        START,
    )
    assert decision.record_answer is False
    assert decision.state == "observing"
    assert decision.stable_observation_count == 3
    assert decision.max_target_ttl == 99


def test_untrackable_modes_never_track():
    for mode in ("mixed", "unavailable", "unsupported"):
        tracked = make_tracked(
            target_mode="direct",
            automatic_retarget=True,
            stable_observation_count=4,
            max_target_ttl=77,
        )
        decision = advance(
            tracked,
            DnsObservation(mode, ttl=60, fingerprint="x"),
            START,
        )
        assert decision.record_answer is False, mode
        assert decision.state == "observing", mode
        assert decision.stable_observation_count == 4, mode
        assert decision.max_target_ttl == 77, mode


def test_naive_timestamps_treated_as_utc():
    assert parse_timestamp("2026-07-21 10:00:00") == datetime(
        2026, 7, 21, 10, 0, 0, tzinfo=timezone.utc
    )
    tracked = make_tracked(
        observed_mode="direct",
        answer_fingerprint="abc",
        stable_observation_count=1,
        max_target_ttl=60,
        first_target_observed_at="2026-07-21 10:00:00",
        last_target_observed_at="2026-07-21 10:00:00",
    )
    decision = advance(
        tracked,
        DnsObservation("direct", ttl=60, fingerprint="abc"),
        START + timedelta(seconds=61),
    )
    assert decision.stable_observation_count == 2


def test_stable_threshold_constant_is_two():
    assert STABLE_OBSERVATIONS_REQUIRED == 2
