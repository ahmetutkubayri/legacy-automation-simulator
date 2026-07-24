"""Driver client behavior: lookup, text scan, operations, popup policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from las.driver import (
    ControlNode,
    DriverControlError,
    DriverNavigationError,
    DriverOperationError,
    DriverTimeoutError,
    LegacyDriver,
    RetryPolicy,
    collect_text,
)
from las.simulator import (
    NO_MATCHES_MESSAGE,
    ControlNotFoundError,
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

POLICY = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.1,
    backoff_multiplier=2.0,
    max_delay_seconds=0.5,
    operation_timeout_seconds=10.0,
    poll_interval_seconds=0.5,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def record(self, stage: str, event: str, payload=None) -> None:
        self.events.append((stage, event, dict(payload or {})))


@pytest.fixture()
def store() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


def make_driver(
    store: RecordStore, injector: FaultInjector | None = None, **system_kwargs
) -> LegacyDriver:
    system_kwargs.setdefault("clock", SimulatedClock())
    if injector is not None:
        system_kwargs["fault_injector"] = injector
    return LegacyDriver(LegacySystem(store, **system_kwargs), policy=POLICY)


# --- find_control -------------------------------------------------------------


def test_fallback_candidates_tried_in_exact_order(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.system.press("open_query")
    found = driver.find_control(("missing_alpha", "run_query", "back_to_menu"))
    assert found == "run_query"  # first *visible* candidate wins


def test_first_valid_candidate_returned_even_if_later_ones_exist(
    store: RecordStore,
) -> None:
    driver = make_driver(store)
    driver.system.press("open_query")
    assert driver.find_control(("back_to_menu", "run_query")) == "back_to_menu"


def test_empty_candidate_list_is_rejected(store: RecordStore) -> None:
    driver = make_driver(store)
    with pytest.raises(DriverControlError, match="no candidate"):
        driver.find_control(())


def test_duplicate_candidates_are_normalized_keeping_first(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.system.press("open_query")
    assert driver.find_control(("run_query", "run_query", "run_query")) == "run_query"


def test_all_candidates_absent_raises_with_candidate_list(store: RecordStore) -> None:
    driver = make_driver(store)
    with pytest.raises(DriverControlError) as excinfo:
        driver.find_control(("ghost_one", "ghost_two"))
    message = str(excinfo.value)
    assert "ghost_one" in message
    assert "ghost_two" in message
    assert excinfo.value.attempts == POLICY.max_attempts
    assert excinfo.value.screen == "main_menu"


def test_transient_control_failure_retries_then_succeeds(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector)
    injector.force(
        FaultKind.TRANSIENT_CONTROL_MISSING, point=InteractionPoint.CONTROL_LOOKUP
    )
    attempts = driver.press(("open_query",), operation="open_query")
    assert attempts == 2  # one transient failure, then success
    assert driver.system.current_screen == ScreenId.QUERY


def test_exhausted_transient_failures_chain_the_simulator_cause(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector)
    for _ in range(POLICY.max_attempts):
        injector.force(
            FaultKind.TRANSIENT_CONTROL_MISSING, point=InteractionPoint.CONTROL_LOOKUP
        )
    with pytest.raises(DriverControlError) as excinfo:
        driver.press(("open_query",), operation="open_query")
    assert isinstance(excinfo.value.__cause__, ControlNotFoundError)
    assert excinfo.value.attempts == POLICY.max_attempts


# --- text scanning ------------------------------------------------------------


def test_collect_text_is_deterministic_preorder() -> None:
    tree = [
        ControlNode("root-a", (ControlNode("child-a1"), ControlNode("child-a2"))),
        ControlNode("root-b", (ControlNode("child-b1", (ControlNode("leaf"),)),)),
    ]
    assert collect_text(tree) == ("root-a", "child-a1", "child-a2", "root-b", "child-b1", "leaf")
    assert collect_text(tree) == collect_text(tree)


def test_collect_text_cycle_protection() -> None:
    class Node:
        def __init__(self, text: str) -> None:
            self.text = text
            self.children: list[Node] = []

    node = Node("looped")
    node.children.append(node)  # a genuine cycle
    assert collect_text([node]) == ("looped",)  # visited once, no hang


def test_collect_text_depth_limit() -> None:
    deep = ControlNode("leaf")
    for level in range(10):
        deep = ControlNode(f"level-{level}", (deep,))
    with pytest.raises(DriverOperationError, match="depth"):
        collect_text([deep], max_depth=3)


def test_collect_text_node_limit() -> None:
    flat = [ControlNode(f"node-{i}") for i in range(50)]
    with pytest.raises(DriverOperationError, match="nodes"):
        collect_text(flat, max_nodes=10)


def test_collect_text_never_calls_repr_or_str() -> None:
    class Guard:
        text = "safe-text"
        children: tuple = ()

        def __repr__(self) -> str:
            raise AssertionError("repr must never be called")

        def __str__(self) -> str:
            raise AssertionError("str must never be called")

    assert collect_text([Guard()]) == ("safe-text",)


def test_visible_texts_covers_only_current_screen(store: RecordStore) -> None:
    driver = make_driver(store)
    menu_texts = driver.visible_texts()
    assert "Open Record Query" in menu_texts
    assert "Run Query" not in menu_texts  # other screens are not exposed

    driver.system.press("open_query")
    query_texts = driver.visible_texts()
    assert "Run Query" in query_texts
    assert "Open Record Query" not in query_texts


def test_visible_texts_discovers_popup_and_status_text(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.system.press("open_query")
    driver.system.set_field("external_ref_input", "EXT-90000")
    driver.system.press("run_query")
    assert NO_MATCHES_MESSAGE in driver.visible_texts()  # popup text discoverable

    driver.system.press("dismiss")
    assert "0 records found" in driver.visible_texts()  # status text discoverable


# --- operations ---------------------------------------------------------------


def test_open_query_exercises_fallback_and_confirms_screen(store: RecordStore) -> None:
    driver = make_driver(store)
    result = driver.open_query()
    assert result.operation == "open_query"
    assert result.screen == "query"
    assert result.attempts == 1
    assert driver.screen_name == "query"


def test_run_query_success(store: RecordStore) -> None:
    driver = make_driver(store)
    result = driver.run_query(KNOWN_REF)
    assert result.screen == "results"
    assert result.popup_text is None
    assert driver.get_field("results_status") == "Showing 6 records"


def test_run_query_no_matches_is_data_not_error(store: RecordStore) -> None:
    driver = make_driver(store)
    result = driver.run_query("EXT-90000")
    assert result.popup_text == NO_MATCHES_MESSAGE
    assert result.screen == "query"  # stayed put, popup consumed
    assert driver.popup_text is None


def test_open_result_reaches_detail(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.run_query(KNOWN_REF)
    result = driver.open_result(0)
    assert result.screen == "detail"
    assert "OPR-88000000" in driver.get_field("record_summary")
    driver.back_to_results()
    assert driver.screen_name == "results"
    driver.back_to_query()
    assert driver.screen_name == "query"


def test_open_result_invalid_row_is_permanent_failure(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.run_query(KNOWN_REF)
    with pytest.raises(DriverOperationError, match="No record at row 99"):
        driver.open_result(99)
    assert driver.popup_text is None  # dismissed to restore interactivity
    assert driver.screen_name == "results"  # but the failure was surfaced


def test_open_result_from_wrong_screen_is_navigation_error(store: RecordStore) -> None:
    driver = make_driver(store)
    with pytest.raises(DriverNavigationError, match="requires the results screen"):
        driver.open_result(0)


def test_surprise_popup_is_dismissed_and_operation_retried(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector)
    driver.open_query()
    injector.force(FaultKind.SURPRISE_POPUP, point=InteractionPoint.QUERY_EXECUTION)
    result = driver.run_query(KNOWN_REF)
    assert result.screen == "results"
    assert result.attempts == 2  # popup on the first try, success on the second


def test_extended_busy_produces_driver_timeout(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector)
    driver.open_query()
    injector.force(
        FaultKind.EXTENDED_BUSY,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=30.0,
    )
    with pytest.raises(DriverTimeoutError):
        driver.run_query(KNOWN_REF)


def test_slow_response_succeeds_within_deadline(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector)
    driver.open_query()
    injector.force(
        FaultKind.SLOW_RESPONSE,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=2.0,
    )
    result = driver.run_query(KNOWN_REF)
    assert result.screen == "results"


def test_scroll_results_exposes_stall_as_observation(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=4, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.PAGE_MOVEMENT)
    assert driver.scroll_results(4) == 0  # stall observable: position unchanged
    assert driver.scroll_results(4) == 4  # next attempt scrolls


def test_interact_waits_and_retries_after_busy_rejection(store: RecordStore) -> None:
    """A busy rejection is retryable: wait it out, then try again."""
    from las.simulator import SystemBusyError

    driver = make_driver(store)
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise SystemBusyError("the system is busy")
        return "done"

    value, attempts = driver._interact("flaky_op", flaky)
    assert value == "done"
    assert attempts == 2


def test_operation_exhausted_by_recurring_popups_raises_navigation_error(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector)
    driver.open_query()
    for _ in range(POLICY.max_attempts):
        injector.force(FaultKind.SURPRISE_POPUP, point=InteractionPoint.QUERY_EXECUTION)
    with pytest.raises(DriverNavigationError, match="did not reach 'results'") as excinfo:
        driver.run_query(KNOWN_REF)
    assert excinfo.value.attempts == POLICY.max_attempts


def test_driver_wait_wrappers_use_policy_defaults(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.open_query()
    assert driver.wait_for_screen(ScreenId.QUERY) == 0.0
    assert driver.wait_for_control("run_query") == 0.0
    driver.system.set_field("external_ref_input", "EXT-90000")
    driver.system.press("run_query")
    assert driver.wait_for_popup() == NO_MATCHES_MESSAGE


def test_open_query_is_a_noop_when_already_there(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.open_query()
    result = driver.open_query()
    assert result.attempts == 0
    assert result.screen == "query"


def test_run_query_recovers_from_deep_state(store: RecordStore) -> None:
    driver = make_driver(store)
    driver.run_query(KNOWN_REF)
    driver.open_result(0)  # now on detail
    result = driver.run_query(KNOWN_REF)  # must recover, reopen query, rerun
    assert result.screen == "results"


def test_visible_texts_while_busy_falls_back_to_labels(store: RecordStore) -> None:
    driver = make_driver(store, busy_delay_seconds=5.0)
    driver.open_query()
    driver.system.set_field("external_ref_input", KNOWN_REF)
    driver.system.press("run_query")  # busy now, values unreadable
    texts = driver.visible_texts()
    assert "Result Rows" in texts  # labels still scanned
    assert driver.system.is_busy  # scan did not wait or interact


# --- audit emission -----------------------------------------------------------


def test_audit_events_are_optional_and_safe(store: RecordStore) -> None:
    sink = RecordingSink()
    injector = FaultInjector("none", seed=0)
    system = LegacySystem(store, clock=SimulatedClock(), fault_injector=injector)
    driver = LegacyDriver(system, policy=POLICY, audit=sink)
    injector.force(FaultKind.SURPRISE_POPUP, point=InteractionPoint.QUERY_EXECUTION)
    driver.run_query(KNOWN_REF)

    events = [event for _, event, _ in sink.events]
    assert "transient_popup_dismissed" in events
    assert "operation_ok" in events
    for stage, _, payload in sink.events:
        assert stage == "driver"
        for value in payload.values():
            assert isinstance(value, str | int | float)

    # And entirely optional: no sink, no error.
    silent = make_driver(store)
    assert silent.run_query(KNOWN_REF).screen == "results"
