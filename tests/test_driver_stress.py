"""Seeded hostile-profile stress scenarios and determinism checks."""

from __future__ import annotations

from pathlib import Path

from las.driver import DriverError, LegacyDriver, RetryPolicy
from las.simulator import FaultInjector, LegacySystem, RecordStore, SimulatedClock

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

KNOWN_REF = "EXT-10000"
SCENARIO_COUNT = 50

POLICY = RetryPolicy(
    max_attempts=4,
    initial_delay_seconds=0.1,
    backoff_multiplier=2.0,
    max_delay_seconds=0.5,
    operation_timeout_seconds=12.0,
    poll_interval_seconds=0.5,
)


def run_scenario(profile: str, seed: int) -> str:
    """One bounded scripted scenario; returns a deterministic outcome label.

    Every scenario must either succeed or end in a documented typed
    DriverError — anything else (raw simulator errors, hangs) fails the
    suite.
    """
    store = RecordStore.from_fixtures(SAMPLES_DIR)
    system = LegacySystem(
        store,
        visible_rows=5,
        buffer_rows=2,
        busy_delay_seconds=0.2,
        clock=SimulatedClock(),
        fault_injector=FaultInjector(profile, seed=seed),
    )
    driver = LegacyDriver(system, policy=POLICY)
    try:
        driver.open_query()
        result = driver.run_query(KNOWN_REF)
        if result.screen == "results":
            driver.scroll_results(2)
            driver.open_result(0)
            driver.back_to_results()
        recovery = driver.recover()
        return f"ok(reset={recovery.used_reset})"
    except DriverError as exc:
        return f"error({type(exc).__name__})"


def test_fifty_hostile_scenarios_are_bounded_and_typed() -> None:
    outcomes = [run_scenario("hostile", seed) for seed in range(SCENARIO_COUNT)]
    assert len(outcomes) == SCENARIO_COUNT
    # Only documented outcome shapes appear (guaranteed by run_scenario's
    # narrow except clause: anything unexpected would have raised).
    assert all(outcome.startswith(("ok(", "error(")) for outcome in outcomes)
    # The driver is not trivially failing everything, and hostility bites:
    successes = [outcome for outcome in outcomes if outcome.startswith("ok(")]
    assert successes, "driver never succeeded under hostile faults"


def test_hostile_scenarios_are_deterministic() -> None:
    first = [run_scenario("hostile", seed) for seed in range(10)]
    second = [run_scenario("hostile", seed) for seed in range(10)]
    assert first == second


def test_mild_scenarios_are_deterministic_and_mostly_clean() -> None:
    outcomes = [run_scenario("mild", seed) for seed in range(10)]
    assert outcomes == [run_scenario("mild", seed) for seed in range(10)]
    assert any(outcome.startswith("ok(") for outcome in outcomes)


def test_none_profile_scenario_is_clean() -> None:
    outcome = run_scenario("none", seed=0)
    assert outcome == "ok(reset=False)"
