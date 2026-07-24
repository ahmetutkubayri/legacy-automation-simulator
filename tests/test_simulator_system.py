"""LegacySystem behavior: screens, navigation, tables, busy states, popups."""

from __future__ import annotations

from pathlib import Path

import pytest

from las.simulator import (
    ControlNotFoundError,
    LegacySystem,
    NavigationError,
    PopupActiveError,
    RecordStore,
    ScreenId,
    SimulatedClock,
    SystemBusyError,
    SystemClock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

KNOWN_REF = "EXT-10000"  # first mapping row; six ledger records
KNOWN_PERIOD = "2026-01"


@pytest.fixture(scope="module")
def store_template() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


@pytest.fixture()
def store() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


def make_system(store: RecordStore, **kwargs) -> LegacySystem:
    kwargs.setdefault("clock", SimulatedClock())
    return LegacySystem(store, **kwargs)


def goto_results(system: LegacySystem, external_ref: str = KNOWN_REF, period: str = "") -> None:
    system.press("open_query")
    system.set_field("external_ref_input", external_ref)
    system.set_field("period_input", period)
    system.press("run_query")


def goto_detail(system: LegacySystem, row: int = 0) -> None:
    goto_results(system)
    system.set_field("row_selector", str(row))
    system.press("open_detail")


# --- initial state and screen transitions ------------------------------------


def test_initial_state_is_main_menu(store: RecordStore) -> None:
    system = make_system(store)
    assert system.current_screen == ScreenId.MAIN_MENU
    assert not system.is_busy
    assert system.popup_message is None


def test_every_valid_button_transition(store: RecordStore) -> None:
    system = make_system(store)
    system.press("open_query")
    assert system.current_screen == ScreenId.QUERY
    system.press("back_to_menu")
    assert system.current_screen == ScreenId.MAIN_MENU

    goto_results(system)
    assert system.current_screen == ScreenId.RESULTS
    system.press("back_to_query")
    assert system.current_screen == ScreenId.QUERY

    system.press("run_query")  # fields still populated
    system.set_field("row_selector", "0")
    system.press("open_detail")
    assert system.current_screen == ScreenId.DETAIL
    system.press("back_to_results")
    assert system.current_screen == ScreenId.RESULTS


def test_navigate_supports_structural_transitions(store: RecordStore) -> None:
    system = make_system(store)
    system.navigate(ScreenId.QUERY)
    assert system.current_screen == ScreenId.QUERY
    system.navigate(ScreenId.MAIN_MENU)
    assert system.current_screen == ScreenId.MAIN_MENU

    goto_results(system)
    system.navigate(ScreenId.QUERY)
    assert system.current_screen == ScreenId.QUERY


@pytest.mark.parametrize(
    "target", [ScreenId.RESULTS, ScreenId.DETAIL, ScreenId.MAIN_MENU]
)
def test_invalid_navigation_raises(store: RecordStore, target: ScreenId) -> None:
    system = make_system(store)  # on MAIN_MENU; only QUERY is reachable
    with pytest.raises(NavigationError, match="cannot navigate"):
        system.navigate(target)


def test_controls_are_screen_specific(store: RecordStore) -> None:
    system = make_system(store)
    menu_ids = {control.control_id for control in system.visible_controls()}
    assert menu_ids == {"open_query", "menu_status"}

    with pytest.raises(ControlNotFoundError, match="run_query"):
        system.press("run_query")
    with pytest.raises(ControlNotFoundError):
        system.set_field("external_ref_input", "x")
    with pytest.raises(ControlNotFoundError):
        system.read_table_rows()

    system.press("open_query")
    query_ids = {control.control_id for control in system.visible_controls()}
    assert "run_query" in query_ids
    assert "results_table" not in query_ids


# --- querying ----------------------------------------------------------------


def test_query_with_no_matches_pops_up_and_stays(store: RecordStore) -> None:
    system = make_system(store)
    system.press("open_query")
    system.set_field("external_ref_input", "EXT-90000")  # orphan; not in ledger
    system.press("run_query")
    assert system.current_screen == ScreenId.QUERY
    assert system.popup_message == "No matching records found."
    system.press("dismiss")
    assert system.popup_message is None
    assert system.get_field("query_status") == "0 records found"


def test_query_with_one_match(store: RecordStore) -> None:
    system = make_system(store)
    goto_results(system, period=KNOWN_PERIOD)
    assert system.current_screen == ScreenId.RESULTS
    rows = system.read_table_rows()
    real = [row for row in rows if row.record_id]
    assert len(real) == 1
    assert real[0].external_ref == KNOWN_REF
    assert real[0].period == KNOWN_PERIOD


def test_query_with_multiple_matches(store: RecordStore) -> None:
    system = make_system(store)
    goto_results(system)  # all periods for one reference
    assert system.get_field("results_status") == "Showing 6 records"
    real = [row for row in system.read_table_rows() if row.record_id]
    assert len(real) == 6
    assert [row.period for row in real] == [f"2026-0{i}" for i in range(1, 7)]


# --- pagination and buffer rows ----------------------------------------------


def test_window_with_fewer_rows_than_visible(store: RecordStore) -> None:
    system = make_system(store, visible_rows=12, buffer_rows=3)
    goto_results(system)  # 6 real rows
    assert system.table_row_count() == 9  # inflated by buffer
    rows = system.read_table_rows()
    assert len(rows) == 9
    assert sum(1 for row in rows if row.record_id) == 6
    assert all(row.record_id == "" for row in rows[6:])


def test_window_exactly_full(store: RecordStore) -> None:
    system = make_system(store, visible_rows=6, buffer_rows=2)
    goto_results(system)
    rows = system.read_table_rows()
    assert len(rows) == 6
    assert all(row.record_id for row in rows)


def test_one_more_row_than_window(store: RecordStore) -> None:
    system = make_system(store, visible_rows=5, buffer_rows=2)
    goto_results(system)  # 6 real rows, window 5
    first = system.read_table_rows()
    assert len(first) == 5
    assert all(row.record_id for row in first)

    position = system.scroll_table(5)
    assert position == 3  # clamped: reported 8 - visible 5
    window = system.read_table_rows()
    real = [row for row in window if row.record_id]
    assert {row.record_id for row in real} <= {row.record_id for row in first} | {
        row.record_id for row in window
    }
    assert window[-2:] == (window[-1], window[-1])  # trailing empties equal


def test_multi_page_scrolling_covers_all_rows(store: RecordStore) -> None:
    system = make_system(store, visible_rows=4, buffer_rows=2)
    goto_results(system)  # 6 real rows across two windows
    seen: dict[int, str] = {}
    position = 0
    while True:
        for offset, row in enumerate(system.read_table_rows()):
            if row.record_id:
                seen[position + offset] = row.record_id
        next_position = system.scroll_table(position + 4)
        if next_position == position:
            break
        position = next_position
    assert len(seen) == 6
    assert sorted(seen) == list(range(6))


def test_single_row_window(store: RecordStore) -> None:
    system = make_system(store, visible_rows=1, buffer_rows=1)
    goto_results(system, period=KNOWN_PERIOD)  # exactly 1 real row
    assert system.table_row_count() == 2
    (only_row,) = system.read_table_rows()
    assert only_row.record_id
    system.scroll_table(1)
    (buffer_row,) = system.read_table_rows()
    assert buffer_row.record_id == ""


def test_scroll_clamps_to_valid_range(store: RecordStore) -> None:
    system = make_system(store, visible_rows=4, buffer_rows=2)
    goto_results(system)
    assert system.scroll_table(-10) == 0
    assert system.scroll_table(999) == 4  # reported 8 - visible 4


# --- detail screen -----------------------------------------------------------


def test_detail_read_and_status_update(store: RecordStore) -> None:
    system = make_system(store)
    goto_detail(system, row=0)
    summary = system.get_field("record_summary")
    assert "OPR-88000000" in summary
    assert "PENDING" in summary
    assert system.get_field("status_input") == "PENDING"

    system.set_field("status_input", "READY")
    system.press("apply_status")
    assert system.get_field("detail_status") == "Status updated to READY"
    assert "READY" in system.get_field("record_summary")
    assert store.get_record("OPR-88000000").status.value == "READY"


def test_invalid_status_update_pops_up(store: RecordStore) -> None:
    system = make_system(store)
    goto_detail(system, row=0)  # PENDING record
    system.set_field("status_input", "COMPLETE")
    system.press("apply_status")
    assert system.popup_message is not None
    assert "Status change rejected" in system.popup_message
    assert store.get_record("OPR-88000000").status.value == "PENDING"


@pytest.mark.parametrize("selector", ["", "abc", "99", "-1"])
def test_invalid_row_selection_pops_up(store: RecordStore, selector: str) -> None:
    system = make_system(store)
    goto_results(system)
    system.set_field("row_selector", selector)
    system.press("open_detail")
    assert system.current_screen == ScreenId.RESULTS
    assert system.popup_message is not None


# --- busy behavior and simulated time ----------------------------------------


def test_busy_after_action_blocks_until_time_passes(store: RecordStore) -> None:
    clock = SimulatedClock()
    system = make_system(store, busy_delay_seconds=2.0, clock=clock)
    system.press("open_query")
    system.set_field("external_ref_input", KNOWN_REF)
    system.press("run_query")

    assert system.is_busy
    with pytest.raises(SystemBusyError):
        system.read_table_rows()
    with pytest.raises(SystemBusyError):
        system.set_field("row_selector", "0")
    with pytest.raises(SystemBusyError):
        system.press("back_to_query")

    clock.advance(1.9)
    assert system.is_busy
    clock.advance(0.2)
    assert not system.is_busy
    assert system.read_table_rows()


def test_zero_delay_never_blocks(store: RecordStore) -> None:
    system = make_system(store, busy_delay_seconds=0.0)
    goto_results(system)
    assert not system.is_busy


def test_simulated_clock_sleep_advances_time() -> None:
    clock = SimulatedClock()
    clock.sleep(5.0)
    assert clock.now() == 5.0
    clock.advance(-1.0)  # negative advances are ignored
    assert clock.now() == 5.0


def test_system_clock_is_monotonic_without_material_sleep() -> None:
    clock = SystemClock()
    first = clock.now()
    clock.sleep(0.0)
    assert clock.now() >= first


# --- popups ------------------------------------------------------------------


def test_popup_blocks_all_normal_interaction(store: RecordStore) -> None:
    system = make_system(store)
    system.press("open_query")
    system.set_field("external_ref_input", "EXT-90000")
    system.press("run_query")
    assert system.popup_message is not None

    with pytest.raises(PopupActiveError):
        system.press("run_query")
    with pytest.raises(PopupActiveError):
        system.set_field("external_ref_input", "changed")
    with pytest.raises(PopupActiveError):
        system.navigate(ScreenId.MAIN_MENU)

    assert system.get_field("popup_message") == "No matching records found."
    popup_ids = {control.control_id for control in system.visible_controls()}
    assert popup_ids == {"popup_message", "dismiss"}

    system.press("dismiss")
    assert system.popup_message is None
    system.set_field("external_ref_input", KNOWN_REF)  # interaction restored


def test_dismiss_without_popup_is_not_found(store: RecordStore) -> None:
    system = make_system(store)
    with pytest.raises(ControlNotFoundError):
        system.press("dismiss")


# --- reset -------------------------------------------------------------------


def test_reset_returns_to_pristine_menu_from_every_screen(store: RecordStore) -> None:
    for setup in (
        lambda s: None,
        lambda s: s.press("open_query"),
        goto_results,
        goto_detail,
    ):
        system = make_system(store)
        setup(system)
        system.reset()
        assert system.current_screen == ScreenId.MAIN_MENU
        assert system.popup_message is None
        assert not system.is_busy
        system.press("open_query")
        assert system.get_field("external_ref_input") == ""  # fields cleared


def test_reset_clears_popup_and_busy(store: RecordStore) -> None:
    clock = SimulatedClock()
    system = make_system(store, busy_delay_seconds=5.0, clock=clock)
    system.press("open_query")
    system.set_field("external_ref_input", "EXT-90000")
    system.press("run_query")  # busy + popup
    assert system.is_busy
    assert system.popup_message is not None
    system.reset()
    assert not system.is_busy
    assert system.popup_message is None
    assert system.current_screen == ScreenId.MAIN_MENU


# --- constructor validation --------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"visible_rows": 0},
        {"buffer_rows": -1},
        {"busy_delay_seconds": -0.1},
    ],
)
def test_invalid_constructor_arguments_raise(store: RecordStore, kwargs: dict) -> None:
    with pytest.raises(ValueError):
        LegacySystem(store, **kwargs)
