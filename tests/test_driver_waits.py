"""Retry-policy and wait-helper behavior, all on simulated time."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from las.driver import (
    DriverTimeoutError,
    RetryPolicy,
    wait_for_control,
    wait_for_popup,
    wait_for_screen,
    wait_until_idle,
)
from las.simulator import (
    FaultInjector,
    FaultKind,
    InteractionPoint,
    LegacySystem,
    RecordStore,
    ScreenId,
    SimulatedClock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

KNOWN_REF = "EXT-10000"


@pytest.fixture()
def store() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


def make_system(store: RecordStore, **kwargs) -> tuple[LegacySystem, SimulatedClock]:
    clock = SimulatedClock()
    kwargs.setdefault("clock", clock)
    return LegacySystem(store, **kwargs), clock


# --- retry policy -------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"backoff_multiplier": 0.5},
        {"initial_delay_seconds": -0.1},
        {"max_delay_seconds": -1.0},
        {"operation_timeout_seconds": math.inf},
        {"initial_delay_seconds": math.nan},
        {"poll_interval_seconds": 0.0},
    ],
)
def test_invalid_policy_values_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_delay_progression_and_cap() -> None:
    policy = RetryPolicy(
        initial_delay_seconds=0.1, backoff_multiplier=2.0, max_delay_seconds=0.5
    )
    delays = [policy.delay_for_attempt(attempt) for attempt in range(1, 6)]
    assert delays == pytest.approx([0.1, 0.2, 0.4, 0.5, 0.5])
    with pytest.raises(ValueError):
        policy.delay_for_attempt(0)


def test_policy_is_immutable() -> None:
    with pytest.raises(AttributeError):
        RetryPolicy().max_attempts = 5  # type: ignore[misc]


# --- wait_until_idle ----------------------------------------------------------


def make_busy(system: LegacySystem) -> None:
    """Trigger a busy-inducing action (query execution starts a busy state)."""
    system.press("open_query")
    system.set_field("external_ref_input", KNOWN_REF)
    system.press("run_query")


def test_wait_until_idle_succeeds_after_normal_busy(store: RecordStore) -> None:
    system, clock = make_system(store, busy_delay_seconds=3.0)
    make_busy(system)
    assert system.is_busy
    elapsed = wait_until_idle(system, timeout_seconds=10.0, poll_interval_seconds=0.5)
    assert elapsed == pytest.approx(3.0)  # deadline not overshot
    assert not system.is_busy
    assert clock.now() == pytest.approx(3.0)


def test_wait_until_idle_times_out(store: RecordStore) -> None:
    system, _ = make_system(store, busy_delay_seconds=5.0)
    make_busy(system)
    with pytest.raises(DriverTimeoutError) as excinfo:
        wait_until_idle(system, timeout_seconds=2.0, poll_interval_seconds=0.5)
    assert excinfo.value.timeout_seconds == 2.0
    assert excinfo.value.screen == "results"  # query navigated before busy


def test_slow_response_fault_completes_within_deadline(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    system, _ = make_system(store, fault_injector=injector)
    system.press("open_query")
    system.set_field("external_ref_input", KNOWN_REF)
    injector.force(
        FaultKind.SLOW_RESPONSE,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=2.0,
    )
    system.press("run_query")
    elapsed = wait_until_idle(system, timeout_seconds=10.0, poll_interval_seconds=0.5)
    assert elapsed == pytest.approx(2.0)
    assert system.current_screen == ScreenId.RESULTS


def test_extended_busy_fault_produces_timeout(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    system, _ = make_system(store, fault_injector=injector)
    system.press("open_query")
    system.set_field("external_ref_input", KNOWN_REF)
    injector.force(
        FaultKind.EXTENDED_BUSY,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=30.0,
    )
    system.press("run_query")
    with pytest.raises(DriverTimeoutError):
        wait_until_idle(system, timeout_seconds=10.0, poll_interval_seconds=0.5)


# --- wait_for_screen / popup / control ---------------------------------------


def test_wait_for_screen_success_and_timeout(store: RecordStore) -> None:
    system, _ = make_system(store, busy_delay_seconds=1.0)
    system.press("open_query")  # navigation itself is instant; busy after press
    elapsed = wait_for_screen(
        system, ScreenId.QUERY, timeout_seconds=5.0, poll_interval_seconds=0.5
    )
    assert elapsed == pytest.approx(0.0)
    with pytest.raises(DriverTimeoutError, match="results"):
        wait_for_screen(
            system, ScreenId.RESULTS, timeout_seconds=1.0, poll_interval_seconds=0.5
        )


def test_wait_for_popup_success_and_timeout(store: RecordStore) -> None:
    system, _ = make_system(store)
    system.press("open_query")
    system.set_field("external_ref_input", "EXT-90000")  # matches nothing
    system.press("run_query")
    text = wait_for_popup(system, timeout_seconds=1.0, poll_interval_seconds=0.2)
    assert text == "No matching records found."

    system.press("dismiss")
    with pytest.raises(DriverTimeoutError, match="popup"):
        wait_for_popup(system, timeout_seconds=1.0, poll_interval_seconds=0.2)


def test_wait_for_control_success_and_timeout(store: RecordStore) -> None:
    system, _ = make_system(store, busy_delay_seconds=2.0)
    elapsed = wait_for_control(
        system, "open_query", timeout_seconds=1.0, poll_interval_seconds=0.2
    )
    assert elapsed == 0.0
    with pytest.raises(DriverTimeoutError, match="run_query"):
        wait_for_control(
            system, "run_query", timeout_seconds=1.0, poll_interval_seconds=0.2
        )


# --- argument validation and determinism -------------------------------------


def test_wait_argument_validation(store: RecordStore) -> None:
    system, _ = make_system(store)
    with pytest.raises(ValueError):
        wait_until_idle(system, timeout_seconds=-1.0)
    with pytest.raises(ValueError):
        wait_until_idle(system, timeout_seconds=1.0, poll_interval_seconds=0.0)


def test_waits_advance_only_simulated_time(store: RecordStore) -> None:
    system, clock = make_system(store, busy_delay_seconds=4.0)
    make_busy(system)
    before = clock.now()
    wait_until_idle(system, timeout_seconds=10.0, poll_interval_seconds=1.0)
    assert clock.now() - before == pytest.approx(4.0)  # exact, deterministic
