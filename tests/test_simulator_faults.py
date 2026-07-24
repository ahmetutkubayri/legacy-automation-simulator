"""Fault-injection behavior: profiles, determinism, forcing, integration."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from las.simulator import (
    FAULTS_AT_POINT,
    PROFILES,
    ControlNotFoundError,
    FaultInjector,
    FaultKind,
    FaultProfile,
    InteractionPoint,
    LegacySystem,
    PopupActiveError,
    RecordStore,
    ScreenId,
    SimulatedClock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

KNOWN_REF = "EXT-10000"

#: A representative deterministic sequence of interaction points.
POINT_CYCLE = [
    InteractionPoint.CONTROL_LOOKUP,
    InteractionPoint.BUTTON_PRESS,
    InteractionPoint.QUERY_EXECUTION,
    InteractionPoint.CONTROL_LOOKUP,
    InteractionPoint.PAGE_MOVEMENT,
    InteractionPoint.RECORD_OPENING,
    InteractionPoint.STATUS_APPLICATION,
] * 40


@pytest.fixture()
def store() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


def make_system(store: RecordStore, injector: FaultInjector, **kwargs) -> LegacySystem:
    kwargs.setdefault("clock", SimulatedClock())
    return LegacySystem(store, fault_injector=injector, **kwargs)


def run_sequence(injector: FaultInjector) -> list[tuple[str, float]]:
    return [
        (decision.fault.value if decision.fault else "", round(decision.delay_seconds, 9))
        for decision in (injector.decide(point) for point in POINT_CYCLE)
    ]


# --- profiles ----------------------------------------------------------------


def test_builtin_profiles_have_valid_generic_parameters() -> None:
    assert PROFILES["none"].fault_probability == 0.0
    for name in ("mild", "hostile"):
        profile = PROFILES[name]
        assert 0.0 < profile.fault_probability <= 1.0
        for low, high in (profile.slow_response_seconds, profile.extended_busy_seconds):
            assert 0.0 <= low <= high
    assert PROFILES["mild"].fault_probability < PROFILES["hostile"].fault_probability


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fault_probability": -0.1},
        {"fault_probability": 1.5},
        {"slow_response_seconds": (2.0, 1.0)},
        {"slow_response_seconds": (-1.0, 1.0)},
        {"extended_busy_seconds": (5.0, 4.0)},
        {"name": ""},
    ],
)
def test_invalid_profile_parameters_are_rejected(kwargs: dict) -> None:
    base = {
        "name": "custom",
        "fault_probability": 0.5,
        "slow_response_seconds": (0.1, 0.2),
        "extended_busy_seconds": (1.0, 2.0),
    }
    with pytest.raises(ValueError):
        FaultProfile(**{**base, **kwargs})


def test_unknown_profile_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown fault profile"):
        FaultInjector("chaotic")


def test_profiles_are_immutable() -> None:
    with pytest.raises(AttributeError):
        PROFILES["mild"].fault_probability = 1.0  # type: ignore[misc]


# --- determinism -------------------------------------------------------------


def test_none_profile_never_triggers_over_long_sequence() -> None:
    injector = FaultInjector("none", seed=123)
    decisions = run_sequence(injector)
    assert all(fault == "" for fault, _ in decisions)
    assert injector.records == ()


def test_same_seed_and_profile_produce_identical_sequences() -> None:
    first = run_sequence(FaultInjector("hostile", seed=42))
    second = run_sequence(FaultInjector("hostile", seed=42))
    assert first == second
    assert any(fault for fault, _ in first)  # hostile does trigger


def test_different_seeds_produce_different_sequences() -> None:
    first = run_sequence(FaultInjector("hostile", seed=1))
    second = run_sequence(FaultInjector("hostile", seed=2))
    assert first != second


def test_separate_injectors_do_not_share_rng_state() -> None:
    baseline = run_sequence(FaultInjector("hostile", seed=7))
    exhausted = FaultInjector("hostile", seed=7)
    run_sequence(exhausted)  # consume this instance's RNG heavily
    fresh = FaultInjector("hostile", seed=7)
    assert run_sequence(fresh) == baseline


# --- forcing and records ------------------------------------------------------


def test_every_fault_kind_can_be_forced_deterministically() -> None:
    point_for_kind = {
        FaultKind.TRANSIENT_CONTROL_MISSING: InteractionPoint.CONTROL_LOOKUP,
        FaultKind.SURPRISE_POPUP: InteractionPoint.QUERY_EXECUTION,
        FaultKind.EXTENDED_BUSY: InteractionPoint.QUERY_EXECUTION,
        FaultKind.STALLED_PAGINATION: InteractionPoint.PAGE_MOVEMENT,
        FaultKind.SLOW_RESPONSE: InteractionPoint.BUTTON_PRESS,
    }
    injector = FaultInjector("none", seed=0)
    for kind, point in point_for_kind.items():
        injector.force(kind, point=point, delay_seconds=1.5)
        decision = injector.decide(point)
        assert decision.fault is kind
        assert decision.delay_seconds == 1.5


def test_forced_fault_waits_for_matching_point() -> None:
    injector = FaultInjector("none", seed=0)
    injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.PAGE_MOVEMENT)
    assert not injector.decide(InteractionPoint.CONTROL_LOOKUP).triggered
    assert injector.decide(InteractionPoint.PAGE_MOVEMENT).fault is (
        FaultKind.STALLED_PAGINATION
    )


def test_forcing_ineligible_fault_at_point_is_rejected() -> None:
    injector = FaultInjector("none", seed=0)
    with pytest.raises(ValueError, match="cannot occur"):
        injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.CONTROL_LOOKUP)
    with pytest.raises(ValueError, match="delay_seconds"):
        injector.force(FaultKind.SLOW_RESPONSE, delay_seconds=-1.0)


def test_forced_delayed_kind_defaults_to_profile_midpoint() -> None:
    injector = FaultInjector("mild", seed=0)
    injector.force(FaultKind.EXTENDED_BUSY, point=InteractionPoint.QUERY_EXECUTION)
    decision = injector.decide(InteractionPoint.QUERY_EXECUTION)
    assert decision.delay_seconds == pytest.approx(7.5)  # midpoint of (5, 10)


def test_fault_records_contain_only_safe_documented_fields() -> None:
    injector = FaultInjector("none", seed=0)
    injector.force(
        FaultKind.SLOW_RESPONSE,
        point=InteractionPoint.BUTTON_PRESS,
        delay_seconds=0.4,
    )
    injector.decide(InteractionPoint.BUTTON_PRESS)
    (record,) = injector.records
    assert set(asdict(record)) == {"sequence", "fault", "point", "delay_seconds"}
    assert record.sequence == 1
    assert record.fault is FaultKind.SLOW_RESPONSE
    assert record.point is InteractionPoint.BUTTON_PRESS
    assert record.delay_seconds == 0.4
    assert injector.counts()[FaultKind.SLOW_RESPONSE] == 1


def test_eligibility_map_keeps_kinds_semantically_placed() -> None:
    assert FAULTS_AT_POINT[InteractionPoint.CONTROL_LOOKUP] == (
        FaultKind.TRANSIENT_CONTROL_MISSING,
    )
    assert FaultKind.STALLED_PAGINATION in FAULTS_AT_POINT[InteractionPoint.PAGE_MOVEMENT]
    assert FaultKind.STALLED_PAGINATION not in FAULTS_AT_POINT[InteractionPoint.QUERY_EXECUTION]


# --- integration: each fault's observable behavior ----------------------------


def goto_results(system: LegacySystem, external_ref: str = KNOWN_REF) -> None:
    system.press("open_query")
    system.set_field("external_ref_input", external_ref)
    system.press("run_query")


def test_transient_missing_control_fails_once_then_succeeds(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    system = make_system(store, injector)
    injector.force(
        FaultKind.TRANSIENT_CONTROL_MISSING, point=InteractionPoint.CONTROL_LOOKUP
    )
    with pytest.raises(ControlNotFoundError, match="temporarily unavailable"):
        system.press("open_query")
    assert system.current_screen == ScreenId.MAIN_MENU  # nothing happened

    system.press("open_query")  # fault consumed; identical action now works
    assert system.current_screen == ScreenId.QUERY


def test_surprise_popup_blocks_until_dismissed(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    system = make_system(store, injector)
    system.press("open_query")
    system.set_field("external_ref_input", KNOWN_REF)
    injector.force(FaultKind.SURPRISE_POPUP, point=InteractionPoint.QUERY_EXECUTION)
    system.press("run_query")

    assert system.current_screen == ScreenId.QUERY  # the query did not run
    assert system.popup_message == "Unexpected system notice. Dismiss to continue."
    with pytest.raises(PopupActiveError):
        system.set_field("external_ref_input", "changed")

    system.press("dismiss")
    system.press("run_query")  # now it goes through
    assert system.current_screen == ScreenId.RESULTS


def test_extended_busy_swallows_action_and_outlasts_normal_delay(
    store: RecordStore,
) -> None:
    clock = SimulatedClock()
    injector = FaultInjector("none", seed=0)
    system = make_system(store, injector, busy_delay_seconds=1.0, clock=clock)
    system.press("open_query")
    clock.advance(1.0)
    system.set_field("external_ref_input", KNOWN_REF)
    injector.force(
        FaultKind.EXTENDED_BUSY,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=30.0,
    )
    system.press("run_query")

    clock.advance(1.5)  # a normal action would be idle by now
    assert system.is_busy  # distinguishable prolonged-busy condition
    clock.advance(30.0)
    assert not system.is_busy
    assert system.current_screen == ScreenId.QUERY  # action was swallowed


def test_slow_response_completes_action_using_simulated_time_only(
    store: RecordStore,
) -> None:
    clock = SimulatedClock()
    injector = FaultInjector("none", seed=0)
    system = make_system(store, injector, busy_delay_seconds=0.0, clock=clock)
    system.press("open_query")
    system.set_field("external_ref_input", KNOWN_REF)
    injector.force(
        FaultKind.SLOW_RESPONSE,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=2.0,
    )
    system.press("run_query")

    assert system.current_screen == ScreenId.RESULTS  # the action did complete
    assert system.is_busy  # but the system is still slow
    clock.advance(2.1)  # only simulated time moves
    assert not system.is_busy
    assert system.read_table_rows()


def test_stalled_pagination_keeps_position_and_corrupts_nothing(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    system = make_system(store, injector, visible_rows=4, buffer_rows=2)
    goto_results(system)
    first_window = system.read_table_rows()
    snapshot_before = store.snapshot()

    injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.PAGE_MOVEMENT)
    assert system.scroll_table(4) == 0  # request ignored, position unchanged
    assert system.read_table_rows() == first_window
    assert store.snapshot() == snapshot_before

    assert system.scroll_table(4) == 4  # next attempt scrolls normally
    assert system.read_table_rows() != first_window


def test_slow_response_during_scroll_extends_busy_but_scrolls(store: RecordStore) -> None:
    clock = SimulatedClock()
    injector = FaultInjector("none", seed=0)
    system = make_system(store, injector, visible_rows=4, buffer_rows=2, clock=clock)
    goto_results(system)
    injector.force(
        FaultKind.SLOW_RESPONSE,
        point=InteractionPoint.PAGE_MOVEMENT,
        delay_seconds=3.0,
    )
    assert system.scroll_table(4) == 4  # the scroll itself succeeds
    assert system.is_busy
    clock.advance(3.1)
    assert not system.is_busy


# --- none-profile preservation ------------------------------------------------


def test_none_profile_preserves_full_t5_workflow(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=99)
    system = make_system(store, injector)
    goto_results(system)
    assert system.current_screen == ScreenId.RESULTS
    system.set_field("row_selector", "0")
    system.press("open_detail")
    assert system.current_screen == ScreenId.DETAIL
    system.set_field("status_input", "READY")
    system.press("apply_status")
    assert system.get_field("detail_status") == "Status updated to READY"
    system.press("back_to_results")
    system.press("back_to_query")
    system.press("back_to_menu")
    assert system.current_screen == ScreenId.MAIN_MENU
    system.reset()
    assert injector.records == ()  # not a single fault along the way


def test_default_system_uses_none_profile(store: RecordStore) -> None:
    system = LegacySystem(store, clock=SimulatedClock())
    assert system.fault_injector.profile.name == "none"
