"""Pure DNS observation tracking for custom-domain mode transitions.

Owns the decision of how a fresh DNS observation advances a transition's
stability run: when to record a new answer, increment the stable-observation
count, or reset a target run. Imports nothing from the rest of the package so
it stays a dependency-free value layer; ``evidence.py`` re-exports
``DnsObservation`` for the historical import site.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MIN_SEPARATION_SECONDS = 60
STABLE_OBSERVATIONS_REQUIRED = 2

TRACKABLE_MODES = frozenset({"direct", "cloudflare"})


@dataclass(frozen=True)
class DnsObservation:
    mode: str
    addresses: tuple[str, ...] = ()
    ttl: int = 0
    fingerprint: str | None = None
    error: str | None = None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class TrackedObservation:
    target_mode: str
    automatic_retarget: bool
    observed_mode: str | None
    answer_fingerprint: str | None
    stable_observation_count: int
    max_target_ttl: int
    first_target_observed_at: str | None
    last_target_observed_at: str | None


@dataclass(frozen=True)
class ObservationDecision:
    state: str
    record_answer: bool
    stable_observation_count: int
    max_target_ttl: int
    start_target_run: bool
    accept_target_sample: bool


def advance(
    tracked: TrackedObservation, observation: DnsObservation, now: datetime
) -> ObservationDecision:
    target_observed = (
        observation.mode == tracked.target_mode and observation.fingerprint is not None
    )
    tracked_observation = target_observed or bool(
        tracked.automatic_retarget
        and observation.mode in TRACKABLE_MODES
        and observation.fingerprint is not None
    )
    same_answer = bool(
        tracked_observation
        and observation.mode == tracked.observed_mode
        and observation.fingerprint == tracked.answer_fingerprint
    )
    separated = False
    if same_answer and tracked.last_target_observed_at:
        last_observed = parse_timestamp(tracked.last_target_observed_at)
        window = max(MIN_SEPARATION_SECONDS, observation.ttl, tracked.max_target_ttl)
        separated = now - last_observed >= timedelta(seconds=window)
    stable_increment = same_answer and separated
    accept_target_sample = tracked_observation and (not same_answer or stable_increment)
    start_target_run = tracked_observation and (
        not same_answer or tracked.first_target_observed_at is None
    )
    if stable_increment:
        stable_observation_count = tracked.stable_observation_count + 1
    elif tracked_observation:
        stable_observation_count = 1
    else:
        stable_observation_count = tracked.stable_observation_count
    if tracked_observation and same_answer:
        max_target_ttl = max(tracked.max_target_ttl, observation.ttl)
    elif tracked_observation:
        max_target_ttl = observation.ttl
    else:
        max_target_ttl = tracked.max_target_ttl
    return ObservationDecision(
        state="validating" if target_observed else "observing",
        record_answer=tracked_observation,
        stable_observation_count=stable_observation_count,
        max_target_ttl=max_target_ttl,
        start_target_run=start_target_run,
        accept_target_sample=accept_target_sample,
    )
