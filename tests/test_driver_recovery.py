"""Recovery behavior: every start state, bounded fallbacks, data safety."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from las.driver import DriverRecoveryError, LegacyDriver, RetryPolicy
from las.simulator import (
    FaultInjector,
    FaultKind,
    InteractionPoint,
    LegacySystem,
    RecordStore,
    SimulatedClock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

KNOWN_REF = "EXT-10000"

POLICY = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.1,
    backoff_multiplier=2.0,
    max_delay_seconds=0.5,
    operation_timeout_seconds=10.0,
    poll_interval_seconds=0.5,
)


@pytest.fixture()
def store() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


def make_driver(
    store: RecordStore, injector: FaultInjector | None = None, **system_kwargs
) -> tuple[LegacyDriver, SimulatedClock]:
    clock = SimulatedClock()
    system_kwargs.setdefault("clock", clock)
    if injector is not None:
        system_kwargs["fault_injector"] = injector
    return LegacyDriver(LegacySystem(store, **system_kwargs), policy=POLICY), clock


def to_query(driver: LegacyDriver) -> None:
    driver.open_query()


def to_results(driver: LegacyDriver) -> None:
    driver.run_query(KNOWN_REF)


def to_detail(driver: LegacyDriver) -> None:
    driver.run_query(KNOWN_REF)
    driver.open_result(0)


@pytest.mark.parametrize(
    ("setup", "expected_steps"),
    [
        (lambda d: None, ()),
        (to_query, ("press_back_to_menu",)),
        (to_results, ("press_back_to_query", "press_back_to_menu")),
        (
            to_detail,
            ("press_back_to_results", "press_back_to_query", "press_back_to_menu"),
        ),
    ],
    ids=["main_menu", "query", "results", "detail"],
)
def test_recovery_from_every_screen_without_reset(
    store: RecordStore, setup, expected_steps: tuple[str, ...]
) -> None:
    driver, _ = make_driver(store)
    setup(driver)
    snapshot_before = store.snapshot()

    result = driver.recover()

    assert result.final_screen == "main_menu"
    assert driver.screen_name == "main_menu"
    assert result.used_reset is False
    assert result.steps == expected_steps
    assert store.snapshot() == snapshot_before  # no data mutated


def test_recovery_dismisses_blocking_popup(store: RecordStore) -> None:
    driver, _ = make_driver(store)
    driver.run_query("EXT-90000")  # consumed popup; make a fresh one manually
    driver.system.set_field("external_ref_input", "EXT-90001")
    driver.system.press("run_query")
    assert driver.system.popup_message is not None

    result = driver.recover()
    assert result.used_reset is False
    assert "dismiss_popup" in result.steps
    assert result.final_screen == "main_menu"


def test_recovery_waits_out_normal_busy(store: RecordStore) -> None:
    driver, _ = make_driver(store, busy_delay_seconds=2.0)
    driver.open_query()
    driver.set_field("external_ref_input", KNOWN_REF)
    driver.system.press("run_query")  # raw press: leaves the system busy
    result = driver.recover()
    assert result.steps[0] == "wait_idle"
    assert result.final_screen == "main_menu"
    assert result.used_reset is False


def test_recovery_fails_cleanly_under_extended_busy(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver, _ = make_driver(store, injector)
    driver.open_query()
    driver.set_field("external_ref_input", KNOWN_REF)
    injector.force(
        FaultKind.EXTENDED_BUSY,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=60.0,
    )
    driver.system.press("run_query")

    with pytest.raises(DriverRecoveryError, match="busy beyond the recovery deadline"):
        driver.recover()


def test_reset_is_used_only_as_final_fallback(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver, _ = make_driver(store, injector)
    driver.run_query("EXT-90000")
    driver.system.set_field("external_ref_input", "EXT-90001")
    driver.system.press("run_query")  # popup open again
    # Make every dismiss attempt fail transiently: the normal path exhausts.
    for _ in range(POLICY.max_attempts):
        injector.force(
            FaultKind.TRANSIENT_CONTROL_MISSING, point=InteractionPoint.CONTROL_LOOKUP
        )
    snapshot_before = store.snapshot()

    result = driver.recover()

    assert result.used_reset is True
    assert result.steps[-1] == "reset"
    assert result.final_screen == "main_menu"
    assert store.snapshot() == snapshot_before  # reset touches no data


def test_recovery_result_is_immutable(store: RecordStore) -> None:
    driver, _ = make_driver(store)
    result = driver.recover()
    with pytest.raises(FrozenInstanceError):
        result.used_reset = True  # type: ignore[misc]


def test_unclearable_popup_falls_back_to_reset(
    store: RecordStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If dismissal has no effect, the bounded loop gives up and resets."""
    driver, _ = make_driver(store)
    driver.open_query()
    driver.system.set_field("external_ref_input", "EXT-90000")
    driver.system.press("run_query")  # popup open
    monkeypatch.setattr(driver.system, "press", lambda control_id: None)

    result = driver.recover()
    assert result.used_reset is True
    assert result.steps.count("dismiss_popup") == POLICY.max_attempts


def test_recovery_raises_if_even_reset_cannot_reach_menu(
    store: RecordStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive final verification: a system that cannot be recovered."""
    driver, _ = make_driver(store)
    driver.open_query()
    driver.system.set_field("external_ref_input", "EXT-90000")
    driver.system.press("run_query")  # popup open
    monkeypatch.setattr(driver.system, "press", lambda control_id: None)
    monkeypatch.setattr(driver.system, "reset", lambda: None)

    with pytest.raises(DriverRecoveryError, match="could not reach the main menu"):
        driver.recover()
