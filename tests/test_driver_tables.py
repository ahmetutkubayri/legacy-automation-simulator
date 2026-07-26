"""T8: paginated extraction and dry-run-safe status write-back.

Extraction correctness is asserted against the store's own contents as
ground truth. The extractor itself never reads the store — these tests do,
purely to have an independent reference, and a static check in this file
proves the module under test has no such access.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from las.driver import (
    DriverControlError,
    DriverExtractionError,
    DriverNavigationError,
    DriverOperationError,
    DriverTimeoutError,
    DriverWriteBackError,
    ExtractedRow,
    ExtractionResult,
    LegacyDriver,
    RetryPolicy,
    StatusUpdateResult,
    extract_all_rows,
    extract_query_results,
    update_status,
)
from las.driver.tables import (
    WARN_FIRST_WINDOW_UNREACHABLE,
    WARN_NO_FORWARD_PROGRESS,
    WARN_POSITION_CONTENT_CHANGED,
    WARN_REPEATED_RECORD_ID,
    WARN_REPORTED_RANGE_EXHAUSTED,
    WARN_ROW_WITHOUT_RECORD_ID,
    WARN_WINDOW_BUDGET_EXHAUSTED,
)
from las.simulator import (
    FaultInjector,
    FaultKind,
    InteractionPoint,
    LegacySystem,
    RecordStatus,
    RecordStore,
    SimulatedClock,
    TableRow,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
TABLES_PATH = REPO_ROOT / "src" / "las" / "driver" / "tables.py"

KNOWN_REF = "EXT-10000"  # first mapping row: exactly six ledger records
KNOWN_PERIOD = "2026-01"  # with KNOWN_REF, exactly one record
ORPHAN_REF = "EXT-90000"  # present in no mapping row: zero matches

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

    def names(self) -> list[str]:
        return [event for _, event, _ in self.events]


@pytest.fixture()
def store() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


def make_driver(
    store: RecordStore,
    injector: FaultInjector | None = None,
    *,
    audit=None,
    **system_kwargs,
) -> LegacyDriver:
    system_kwargs.setdefault("clock", SimulatedClock())
    if injector is not None:
        system_kwargs["fault_injector"] = injector
    return LegacyDriver(LegacySystem(store, **system_kwargs), policy=POLICY, audit=audit)


def ground_truth(
    store: RecordStore, external_ref: str | None = None, period: str | None = None
) -> tuple[tuple[str, ...], ...]:
    """The store's own view of a query, shaped like extracted rows."""
    return tuple(
        (
            record.record_id,
            record.external_ref,
            f"{record.amount:.2f}",
            record.unit,
            record.period,
            record.status.value,
        )
        for record in store.find_records(external_ref=external_ref, period=period)
    )


def extracted(result: ExtractionResult) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (row.record_id, row.external_ref, row.amount, row.unit, row.period, row.status)
        for row in result.rows
    )


def blank_row() -> TableRow:
    return TableRow("", "", "", "", "", "")


def fake_row(record_id: str) -> TableRow:
    return TableRow(record_id, "EXT-10000", "12.50", "EA", KNOWN_PERIOD, "PENDING")


def swallow_apply(driver: LegacyDriver) -> None:
    """Make only the apply button do nothing, leaving navigation intact.

    This mimics the nastiest realistic failure: the system accepts the
    press, raises no popup, and quietly does not perform the write.
    """
    real_press = driver.system.press

    def press(control_id: str) -> None:
        if control_id == "apply_status":
            return
        real_press(control_id)

    driver.system.press = press


class ScriptedTable:
    """Replaces the driver's table surface with a fixed position script.

    Used only to reach defensive branches the well-behaved simulator
    cannot produce (identity-less rows, a position whose content changes
    between reads, a record surfacing twice).
    """

    def __init__(
        self,
        windows: dict[int, tuple[TableRow, ...]],
        reported: int,
        max_scroll: int,
        scroll_plan: list[int] | None = None,
    ) -> None:
        self.windows = windows
        self.reported = reported
        self.max_scroll = max_scroll
        self.scroll_plan = list(scroll_plan) if scroll_plan is not None else None
        self.position = 0

    def install(self, driver: LegacyDriver) -> None:
        driver.table_row_count = lambda: self.reported
        driver.read_table_rows = lambda: self.windows[self.position]

        def scroll(target: int) -> int:
            if target == 0:
                self.position = 0
            elif self.scroll_plan:
                # An explicit walk, for scripting partial (overlapping) moves.
                self.position = self.scroll_plan.pop(0)
            else:
                self.position = min(max(0, target), self.max_scroll)
            return self.position

        driver.scroll_results = scroll


# =============================================================================
# Extraction: boundary sizes against store ground truth
# =============================================================================


@pytest.mark.parametrize("profile", ["none", "mild"])
@pytest.mark.parametrize(
    ("visible_rows", "buffer_rows", "label"),
    [
        (7, 2, "fewer rows than the window"),
        (6, 2, "exactly one full window"),
        (5, 2, "one more row than the window"),
        (2, 2, "several windows"),
        (1, 2, "single-row window"),
        (12, 3, "one window wider than the whole table"),
        (3, 4, "several windows, generous padding"),
    ],
)
def test_six_row_table_extracts_exactly_the_store_contents(
    store: RecordStore, profile: str, visible_rows: int, buffer_rows: int, label: str
) -> None:
    """Every case here exposes at least the cutoff in trailing buffer rows,
    so the placeholder sentinel supplies genuine terminal evidence."""
    driver = make_driver(
        store,
        FaultInjector(profile, seed=11),
        visible_rows=visible_rows,
        buffer_rows=buffer_rows,
    )
    result = extract_query_results(driver, KNOWN_REF)

    assert result.complete, f"{label}: extraction reported incomplete"
    assert result.terminal_evidence == "placeholder_cutoff", label
    assert extracted(result) == ground_truth(store, KNOWN_REF), label
    assert result.row_count == 6


def test_single_row_table(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=12, buffer_rows=3)
    result = extract_query_results(driver, KNOWN_REF, KNOWN_PERIOD)

    assert result.complete
    assert result.row_count == 1
    assert extracted(result) == ground_truth(store, KNOWN_REF, KNOWN_PERIOD)


def test_zero_rows_via_the_zero_match_query_path(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=4, buffer_rows=2)
    result = extract_query_results(driver, ORPHAN_REF)

    assert result.complete  # zero rows is an answer, not a failure
    assert result.rows == ()
    assert result.row_count == 0
    assert result.warnings == ()
    assert ground_truth(store, ORPHAN_REF) == ()


def test_many_pages_cover_the_whole_ledger(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=12, buffer_rows=3)
    result = extract_query_results(driver)  # no filters: the entire ledger

    assert result.complete
    assert result.row_count == store.record_count == 270
    assert extracted(result) == ground_truth(store)
    assert result.windows_visited > 1


@pytest.mark.parametrize("visible_rows", [1, 2, 3, 4, 5, 6, 7, 11, 13])
@pytest.mark.parametrize("buffer_rows", [2, 4, 7])
def test_extraction_equals_ground_truth_across_window_and_buffer_sizes(
    store: RecordStore, visible_rows: int, buffer_rows: int
) -> None:
    """Property-style sweep: window size never matters when padding proves
    the tail, no matter how the windows happen to align."""
    driver = make_driver(
        store,
        FaultInjector("mild", seed=visible_rows * 10 + buffer_rows),
        visible_rows=visible_rows,
        buffer_rows=buffer_rows,
    )
    result = extract_query_results(driver, KNOWN_REF)

    assert result.complete
    assert extracted(result) == ground_truth(store, KNOWN_REF)


@pytest.mark.parametrize("visible_rows", [1, 2, 3, 5, 6, 12])
@pytest.mark.parametrize("buffer_rows", [0, 1])
def test_thin_padding_extracts_accurately_but_is_not_provably_complete(
    store: RecordStore, visible_rows: int, buffer_rows: int
) -> None:
    """Too little padding to satisfy the cutoff: the rows are all there and
    correct, but nothing observed proves the table ended, so completeness
    is not claimed. Accurate-but-incomplete beats a false claim."""
    driver = make_driver(store, visible_rows=visible_rows, buffer_rows=buffer_rows)
    result = extract_query_results(driver, KNOWN_REF)

    assert result.complete is False
    assert result.terminal_evidence is None
    assert WARN_REPORTED_RANGE_EXHAUSTED in result.warnings
    # Accurate: correctly ordered, duplicate-free, and in fact everything.
    assert extracted(result) == ground_truth(store, KNOWN_REF)
    assert len(set(result.record_ids)) == result.row_count


def test_zero_buffer_table_is_honestly_reported_as_unproven(
    store: RecordStore,
) -> None:
    """A widget with no padding gives no end-of-data evidence at all."""
    driver = make_driver(store, visible_rows=12, buffer_rows=0)
    result = extract_query_results(driver, KNOWN_REF)

    assert result.reported_row_count == 6  # exactly the real rows, no padding
    assert result.row_count == 6
    assert extracted(result) == ground_truth(store, KNOWN_REF)
    assert result.complete is False
    assert result.terminal_evidence is None
    assert WARN_REPORTED_RANGE_EXHAUSTED in result.warnings


def test_zero_buffer_table_raises_in_strict_mode(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=12, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    with pytest.raises(DriverExtractionError):
        extract_all_rows(driver, strict=True)


def test_thin_padding_completes_when_the_cutoff_matches_it(
    store: RecordStore,
) -> None:
    """One buffer row is proof enough when the caller says so."""
    driver = make_driver(store, visible_rows=2, buffer_rows=1)
    result = extract_query_results(driver, KNOWN_REF, empty_row_cutoff=1)

    assert result.complete
    assert result.terminal_evidence == "placeholder_cutoff"
    assert extracted(result) == ground_truth(store, KNOWN_REF)


def test_over_reported_count_with_padding_remains_complete(
    store: RecordStore,
) -> None:
    """The ordinary, supported shape: the widget over-reports and pads."""
    driver = make_driver(store, visible_rows=4, buffer_rows=3)
    result = extract_query_results(driver, KNOWN_REF)

    assert result.reported_row_count == 9 > result.row_count == 6
    assert result.complete
    assert result.terminal_evidence == "placeholder_cutoff"
    assert result.placeholder_rows_ignored >= 2
    assert extracted(result) == ground_truth(store, KNOWN_REF)


# =============================================================================
# Extraction: ordering, placeholders, duplicates, widget count
# =============================================================================


def test_logical_ordering_is_preserved_and_positions_ascend(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=2)
    result = extract_query_results(driver, KNOWN_REF)

    positions = [row.position for row in result.rows]
    assert positions == sorted(positions) == list(range(6))
    assert [row.period for row in result.rows] == [f"2026-0{n}" for n in range(1, 7)]


def test_trailing_placeholder_rows_are_counted_but_never_returned(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=12, buffer_rows=4)
    result = extract_query_results(driver, KNOWN_REF)

    assert result.row_count == 6
    assert result.placeholder_rows_ignored == 4
    assert all(row.record_id for row in result.rows)


def test_widget_row_count_exceeds_the_logical_row_count(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=12, buffer_rows=5)
    result = extract_query_results(driver, KNOWN_REF)

    assert result.reported_row_count == 11  # six real rows plus five buffer
    assert result.row_count == 6
    assert result.reported_row_count > result.row_count


def test_overlapping_windows_are_deduplicated(store: RecordStore) -> None:
    """A clamped scroll re-serves rows; they must not be extracted twice."""
    driver = make_driver(store, visible_rows=5, buffer_rows=2)
    result = extract_query_results(driver, KNOWN_REF)

    assert result.complete
    assert result.duplicate_rows_suppressed > 0
    assert result.row_count == 6
    assert len(set(result.record_ids)) == 6


def test_repeated_identical_window_does_not_duplicate_rows(store: RecordStore) -> None:
    """Re-reading the same window is idempotent, not additive."""
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    first = extract_all_rows(driver)

    # Re-extract from scratch over the same table.
    second = extract_all_rows(driver)
    assert extracted(first) == extracted(second) == ground_truth(store, KNOWN_REF)
    assert second.row_count == 6


# =============================================================================
# Extraction: faults
# =============================================================================


def test_transient_control_failures_are_ridden_out(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=2, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    # Two consecutive failures sit inside the three-attempt budget.
    for _ in range(POLICY.max_attempts - 1):
        injector.force(
            FaultKind.TRANSIENT_CONTROL_MISSING, point=InteractionPoint.CONTROL_LOOKUP
        )

    result = extract_all_rows(driver)
    assert result.complete
    assert extracted(result) == ground_truth(store, KNOWN_REF)


def test_control_failures_beyond_the_budget_surface_as_a_typed_error(
    store: RecordStore,
) -> None:
    """The driver never spins forever on a control that keeps failing."""
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=2, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    for _ in range(POLICY.max_attempts):
        injector.force(
            FaultKind.TRANSIENT_CONTROL_MISSING, point=InteractionPoint.CONTROL_LOOKUP
        )

    with pytest.raises(DriverControlError) as excinfo:
        extract_all_rows(driver)
    assert excinfo.value.attempts == POLICY.max_attempts


def test_surprise_popup_during_the_query_is_absorbed(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=3, buffer_rows=2)
    injector.force(FaultKind.SURPRISE_POPUP, point=InteractionPoint.QUERY_EXECUTION)

    result = extract_query_results(driver, KNOWN_REF)
    assert result.complete
    assert extracted(result) == ground_truth(store, KNOWN_REF)


def test_slow_response_during_paging_stays_inside_the_deadline(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=2, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    injector.force(
        FaultKind.SLOW_RESPONSE, point=InteractionPoint.PAGE_MOVEMENT, delay_seconds=2.0
    )

    result = extract_all_rows(driver, timeout_seconds=30.0)
    assert result.complete
    assert extracted(result) == ground_truth(store, KNOWN_REF)


def test_extended_busy_on_the_query_times_out(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=3, buffer_rows=2)
    injector.force(
        FaultKind.EXTENDED_BUSY,
        point=InteractionPoint.QUERY_EXECUTION,
        delay_seconds=30.0,
    )
    with pytest.raises(DriverTimeoutError):
        extract_query_results(driver, KNOWN_REF)


def test_single_stalled_scroll_is_reattempted_and_absorbed(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=2, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    # The first stall lands on the seek-to-top, which is already satisfied
    # and therefore costs nothing; the second stalls a real advance.
    for _ in range(2):
        injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.PAGE_MOVEMENT)

    result = extract_all_rows(driver)
    assert result.complete
    assert result.stall_reattempts == 1
    assert extracted(result) == ground_truth(store, KNOWN_REF)


def test_stall_storm_reports_incomplete_and_never_claims_completeness(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=2, buffer_rows=1)
    driver.run_query(KNOWN_REF)
    # One stall is consumed seeking the first window (harmlessly, since the
    # table already sits there); the rest starve the advance loop.
    for _ in range(POLICY.max_attempts + 1):
        injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.PAGE_MOVEMENT)

    result = extract_all_rows(driver)
    assert result.complete is False
    assert WARN_NO_FORWARD_PROGRESS in result.warnings
    assert result.stall_reattempts >= POLICY.max_attempts
    # What it did extract is still correct, ordered, and duplicate-free.
    assert result.row_count < 6
    assert extracted(result) == ground_truth(store, KNOWN_REF)[: result.row_count]
    assert len(set(result.record_ids)) == result.row_count


def test_stall_storm_raises_a_typed_error_in_strict_mode(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=2, buffer_rows=1)
    driver.run_query(KNOWN_REF)
    for _ in range(POLICY.max_attempts + 1):
        injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.PAGE_MOVEMENT)

    with pytest.raises(DriverExtractionError) as excinfo:
        extract_all_rows(driver, strict=True)
    assert excinfo.value.attempts == POLICY.max_attempts
    assert excinfo.value.screen == "results"


def test_unreachable_first_window_is_never_reported_complete(
    store: RecordStore,
) -> None:
    """Starting mid-table must not be dressed up as a full extraction."""
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=2, buffer_rows=1)
    driver.run_query(KNOWN_REF)
    assert driver.scroll_results(4) == 4  # park the window away from the top
    for _ in range(POLICY.max_attempts):
        injector.force(FaultKind.STALLED_PAGINATION, point=InteractionPoint.PAGE_MOVEMENT)

    result = extract_all_rows(driver)
    assert result.complete is False
    assert WARN_FIRST_WINDOW_UNREACHABLE in result.warnings
    assert result.row_count < 6  # the head of the table was never seen


def test_window_visit_budget_exhaustion_is_reported_incomplete(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=2)
    driver.run_query(KNOWN_REF)

    result = extract_all_rows(driver, max_window_visits=1)
    assert result.complete is False
    assert WARN_WINDOW_BUDGET_EXHAUSTED in result.warnings
    assert result.windows_visited == 1


def test_extraction_deadline_expires_with_a_typed_timeout(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=2, busy_delay_seconds=3.0)
    driver.run_query(KNOWN_REF)
    driver.wait_until_idle()

    with pytest.raises(DriverTimeoutError) as excinfo:
        extract_all_rows(driver, timeout_seconds=5.0)
    assert excinfo.value.timeout_seconds == 5.0


# =============================================================================
# Extraction: defensive branches via a scripted table surface
# =============================================================================


def under_reporting_table() -> ScriptedTable:
    """A widget claiming four positions while eight logical rows exist.

    The last reachable window is still full of real records, there is no
    placeholder tail, and no window is short — so nothing observed proves
    the data ended at position 3.
    """
    return ScriptedTable(
        windows={
            0: (fake_row("OPR-88000000"), fake_row("OPR-88000001")),
            2: (fake_row("OPR-88000002"), fake_row("OPR-88000003")),
        },
        reported=4,  # under-reported: rows 4..7 exist but are never offered
        max_scroll=2,
    )


def test_under_reported_row_count_cannot_produce_false_completeness(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    under_reporting_table().install(driver)

    result = extract_all_rows(driver)

    assert result.complete is False
    assert result.terminal_evidence is None
    assert WARN_REPORTED_RANGE_EXHAUSTED in result.warnings
    # It visited every position the widget admitted to, and stopped there.
    assert result.reported_row_count == 4
    assert result.placeholder_rows_ignored == 0


def test_under_reported_scan_keeps_an_ordered_duplicate_free_prefix(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    under_reporting_table().install(driver)

    result = extract_all_rows(driver)

    assert result.record_ids == (
        "OPR-88000000",
        "OPR-88000001",
        "OPR-88000002",
        "OPR-88000003",
    )
    assert [row.position for row in result.rows] == [0, 1, 2, 3]
    assert len(set(result.record_ids)) == result.row_count == 4
    # Nothing beyond what was actually observed is invented.
    assert all(row.record_id for row in result.rows)


def test_under_reported_row_count_raises_in_strict_mode(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    under_reporting_table().install(driver)

    with pytest.raises(DriverExtractionError) as excinfo:
        extract_all_rows(driver, strict=True)
    assert excinfo.value.screen == "results"


def test_under_reported_scan_leaves_the_store_untouched(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    under_reporting_table().install(driver)
    before = store.snapshot()

    result = extract_all_rows(driver)
    assert result.complete is False
    assert store.snapshot() == before


def test_short_final_window_is_terminal_evidence(store: RecordStore) -> None:
    """A widget serving a stub last page has genuinely run out of rows,
    even though its reported count claims more positions remain."""
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    ScriptedTable(
        windows={
            0: (fake_row("OPR-88000000"), fake_row("OPR-88000001")),
            2: (fake_row("OPR-88000002"),),  # one row where two would fit
        },
        reported=9,  # still claims more, but the widget stopped filling
        max_scroll=2,
    ).install(driver)

    result = extract_all_rows(driver)

    assert result.complete
    assert result.terminal_evidence == "short_final_window"
    assert result.record_ids == ("OPR-88000000", "OPR-88000001", "OPR-88000002")
    assert WARN_REPORTED_RANGE_EXHAUSTED not in result.warnings


def test_explicit_end_of_data_signal_is_terminal_evidence(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    ScriptedTable(
        windows={0: (fake_row("OPR-88000000"), fake_row("OPR-88000001"))},
        reported=9,
        max_scroll=0,
    ).install(driver)

    result = extract_all_rows(driver, end_of_data=lambda: True)

    assert result.complete
    assert result.terminal_evidence == "end_of_data_signal"
    assert result.record_ids == ("OPR-88000000", "OPR-88000001")


def test_end_of_data_signal_that_stays_false_does_not_grant_completeness(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    under_reporting_table().install(driver)

    result = extract_all_rows(driver, end_of_data=lambda: False)
    assert result.complete is False
    assert result.terminal_evidence is None


def test_zero_match_query_records_its_own_terminal_evidence(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=4, buffer_rows=2)
    result = extract_query_results(driver, ORPHAN_REF)
    assert result.complete
    assert result.terminal_evidence == "zero_match_query_signal"


def test_row_without_a_record_id_is_treated_as_padding(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=4, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    partial = TableRow("", "EXT-10000", "12.50", "EA", KNOWN_PERIOD, "PENDING")
    ScriptedTable(
        windows={0: (partial, blank_row(), blank_row(), blank_row())},
        reported=4,
        max_scroll=0,
    ).install(driver)

    result = extract_all_rows(driver)
    assert result.rows == ()  # a row with no identity is never a record
    assert result.placeholder_rows_ignored == 4
    assert WARN_ROW_WITHOUT_RECORD_ID in result.warnings
    # Four consecutive placeholders from the very first position are
    # evidence of a table that holds no records at all.
    assert result.complete
    assert result.terminal_evidence == "placeholder_cutoff"


def test_same_record_at_a_second_position_is_refused(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    row_a, row_b, row_c = fake_row("OPR-88000000"), fake_row("OPR-88000001"), fake_row(
        "OPR-88000002"
    )
    ScriptedTable(
        windows={
            0: (row_a, row_b),
            2: (row_a, row_c),  # row_a reappears at position 2
            4: (blank_row(), blank_row()),
        },
        reported=6,
        max_scroll=4,
    ).install(driver)

    result = extract_all_rows(driver)
    assert result.complete
    assert result.record_ids == ("OPR-88000000", "OPR-88000001", "OPR-88000002")
    assert result.duplicate_rows_suppressed == 1
    assert WARN_REPEATED_RECORD_ID in result.warnings


def test_position_whose_content_changes_between_reads_is_flagged(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=2, buffer_rows=0)
    driver.run_query(KNOWN_REF)
    ScriptedTable(
        windows={
            0: (fake_row("OPR-88000000"), fake_row("OPR-88000001")),
            # Position 1 now claims a different record than the first read.
            1: (fake_row("OPR-88000009"), fake_row("OPR-88000002")),
            3: (blank_row(), blank_row()),
        },
        reported=6,
        max_scroll=3,
        scroll_plan=[1, 3],  # a partial move, then on to the padded tail
    ).install(driver)

    result = extract_all_rows(driver)
    assert result.complete
    assert result.record_ids == ("OPR-88000000", "OPR-88000001", "OPR-88000002")
    assert WARN_POSITION_CONTENT_CHANGED in result.warnings
    assert result.duplicate_rows_suppressed == 1


# =============================================================================
# Extraction: purity, determinism, argument validation
# =============================================================================


def test_extraction_mutates_no_records(store: RecordStore) -> None:
    driver = make_driver(store, FaultInjector("mild", seed=3), visible_rows=2, buffer_rows=2)
    before = store.snapshot()

    result = extract_query_results(driver)
    assert result.complete
    assert store.snapshot() == before


def test_repeated_runs_are_deterministic() -> None:
    def run() -> tuple:
        store = RecordStore.from_fixtures(SAMPLES_DIR)
        driver = make_driver(
            store, FaultInjector("mild", seed=5), visible_rows=3, buffer_rows=2
        )
        result = extract_query_results(driver, KNOWN_REF)
        return (
            extracted(result),
            result.complete,
            result.windows_visited,
            result.duplicate_rows_suppressed,
            result.placeholder_rows_ignored,
            result.stall_reattempts,
            result.warnings,
        )

    assert run() == run()


def test_extraction_off_the_results_screen_is_a_navigation_error(
    store: RecordStore,
) -> None:
    driver = make_driver(store)
    with pytest.raises(DriverNavigationError, match="requires the results screen"):
        extract_all_rows(driver)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"empty_row_cutoff": 0},
        {"timeout_seconds": 0.0},
        {"timeout_seconds": -1.0},
        {"max_window_visits": 0},
    ],
)
def test_invalid_extraction_arguments_are_rejected(
    store: RecordStore, kwargs: dict
) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    with pytest.raises(ValueError):
        extract_all_rows(driver, **kwargs)


def test_extraction_result_is_immutable(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    result = extract_query_results(driver, KNOWN_REF)
    with pytest.raises(FrozenInstanceError):
        result.complete = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.rows[0].record_id = "OPR-00000000"  # type: ignore[misc]


def test_extraction_audit_events_carry_only_safe_scalars(store: RecordStore) -> None:
    sink = RecordingSink()
    driver = make_driver(store, audit=sink, visible_rows=2, buffer_rows=2)
    extract_query_results(driver, KNOWN_REF)

    assert "extraction_completed" in sink.names()
    for stage, _, payload in sink.events:
        assert stage == "driver"
        for value in payload.values():
            assert isinstance(value, str | int | float)


def test_incomplete_extraction_emits_its_own_audit_event(store: RecordStore) -> None:
    sink = RecordingSink()
    driver = make_driver(store, audit=sink, visible_rows=2, buffer_rows=2)
    driver.run_query(KNOWN_REF)
    extract_all_rows(driver, max_window_visits=1)

    assert "extraction_incomplete" in sink.names()
    assert "extraction_completed" not in sink.names()


# =============================================================================
# Write-back: dry run
# =============================================================================


def first_row(driver: LegacyDriver) -> ExtractedRow:
    return extract_query_results(driver, KNOWN_REF).rows[0]


def test_dry_run_is_the_default_and_writes_nothing(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    before = store.snapshot()

    result = update_status(driver, row, RecordStatus.READY)

    assert result.dry_run is True
    assert result.applied_status is None
    assert result.changed is False
    assert result.verified is False
    assert result.attempts == 0
    assert store.snapshot() == before  # snapshot-identical: nothing moved


def test_dry_run_reports_the_intended_transition(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)

    result = update_status(driver, row, "READY")
    assert result.record_id == row.record_id
    assert result.previous_status == "PENDING"
    assert result.requested_status == "READY"
    assert store.get_record(row.record_id).status is RecordStatus.PENDING


def test_dry_run_leaves_the_driver_on_the_results_screen(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    update_status(driver, row, "READY")
    assert driver.screen_name == "results"


def test_dry_run_still_refuses_an_illegal_transition(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)  # PENDING
    with pytest.raises(DriverWriteBackError, match="does not allow"):
        update_status(driver, row, RecordStatus.COMPLETE)


# =============================================================================
# Write-back: real writes and independent verification
# =============================================================================


def test_real_update_persists_and_is_independently_verified(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)

    result = update_status(driver, row, RecordStatus.READY, dry_run=False)

    assert result.dry_run is False
    assert result.verified is True
    assert result.applied_status == "READY"
    assert result.changed is True
    assert result.attempts == 1
    assert store.get_record(row.record_id).status is RecordStatus.READY


def test_verification_reads_the_record_summary_not_the_confirmation_text(
    store: RecordStore,
) -> None:
    """Blanking the summary must break verification, proving it is the source."""
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    real_get_field = driver.system.get_field

    def stale_summary(control_id: str) -> str:
        if control_id == "record_summary":
            return f"{row.record_id} | {row.external_ref} | 12.50 EA | {row.period} | PENDING"
        return real_get_field(control_id)

    driver.system.get_field = stale_summary
    with pytest.raises(DriverWriteBackError, match="could not be verified"):
        update_status(driver, row, RecordStatus.READY, dry_run=False)


def test_verification_mismatch_produces_a_typed_error(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    swallow_apply(driver)

    with pytest.raises(DriverWriteBackError, match="could not be verified") as excinfo:
        update_status(driver, row, RecordStatus.READY, dry_run=False)
    assert excinfo.value.attempts == 1
    assert store.get_record(row.record_id).status is RecordStatus.PENDING


def test_every_documented_transition_can_be_applied_and_verified(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=12, buffer_rows=2)
    result = extract_query_results(driver, KNOWN_REF)

    pending = next(row for row in result.rows if row.status == "PENDING")
    applied = update_status(driver, pending, RecordStatus.READY, dry_run=False)
    assert applied.applied_status == "READY"

    # READY -> COMPLETE, read back through a fresh extraction.
    refreshed = extract_query_results(driver, KNOWN_REF)
    ready = next(row for row in refreshed.rows if row.record_id == pending.record_id)
    assert ready.status == "READY"
    finished = update_status(driver, ready, RecordStatus.COMPLETE, dry_run=False)
    assert finished.applied_status == "COMPLETE"
    assert store.get_record(pending.record_id).status is RecordStatus.COMPLETE


# =============================================================================
# Write-back: rejections, faults, and typed errors
# =============================================================================


def test_illegal_transition_is_refused_without_pressing_apply(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)  # PENDING; COMPLETE is not reachable from it
    pressed: list[str] = []
    real_press = driver.system.press

    def counting_press(control_id: str) -> None:
        pressed.append(control_id)
        real_press(control_id)

    driver.system.press = counting_press
    with pytest.raises(DriverWriteBackError, match="does not allow"):
        update_status(driver, row, RecordStatus.COMPLETE, dry_run=False)

    assert "apply_status" not in pressed  # refused up front, never retried
    assert store.get_record(row.record_id).status is RecordStatus.PENDING


def test_terminal_record_cannot_be_moved(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=12, buffer_rows=2)
    result = extract_query_results(driver, KNOWN_REF)
    complete = next(row for row in result.rows if row.status == "COMPLETE")

    for target in RecordStatus:
        with pytest.raises(DriverWriteBackError):
            update_status(driver, complete, target, dry_run=False)


@pytest.mark.parametrize("bad_status", ["ARCHIVED", "", "  ", "ready-ish", 7, None])
def test_statuses_outside_the_lifecycle_are_rejected(
    store: RecordStore, bad_status: object
) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    with pytest.raises(DriverWriteBackError, match="lifecycle"):
        update_status(driver, row, bad_status, dry_run=False)  # type: ignore[arg-type]


def test_row_pointing_at_a_different_record_is_a_typed_error(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    impostor = ExtractedRow(
        position=row.position,
        record_id="OPR-00000000",  # no such record at this position
        external_ref=row.external_ref,
        amount=row.amount,
        unit=row.unit,
        period=row.period,
        status=row.status,
    )
    with pytest.raises(DriverWriteBackError, match="different record"):
        update_status(driver, impostor, RecordStatus.READY, dry_run=False)


def test_row_position_beyond_the_table_is_a_typed_error(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    off_table = ExtractedRow(
        position=99,
        record_id=row.record_id,
        external_ref=row.external_ref,
        amount=row.amount,
        unit=row.unit,
        period=row.period,
        status=row.status,
    )
    with pytest.raises(DriverOperationError, match="No record at row 99"):
        update_status(driver, off_table, RecordStatus.READY, dry_run=False)


def test_unreadable_detail_summary_is_a_typed_error(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    driver.system.get_field = lambda control_id: "unparseable"

    with pytest.raises(DriverWriteBackError, match="could not be read"):
        update_status(driver, row, RecordStatus.READY)


def test_transient_control_fault_retries_then_the_write_succeeds(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    injector.force(
        FaultKind.TRANSIENT_CONTROL_MISSING, point=InteractionPoint.CONTROL_LOOKUP
    )

    result = update_status(driver, row, RecordStatus.READY, dry_run=False)
    assert result.verified is True
    assert store.get_record(row.record_id).status is RecordStatus.READY


def test_surprise_popup_on_apply_is_dismissed_and_the_write_retried(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    injector.force(
        FaultKind.SURPRISE_POPUP, point=InteractionPoint.STATUS_APPLICATION
    )

    result = update_status(driver, row, RecordStatus.READY, dry_run=False)
    assert result.attempts == 2  # popup on the first try, applied on the second
    assert result.verified is True


def test_recurring_surprise_popups_exhaust_the_budget_and_fail_typed(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    for _ in range(POLICY.max_attempts):
        injector.force(
            FaultKind.SURPRISE_POPUP, point=InteractionPoint.STATUS_APPLICATION
        )

    with pytest.raises(DriverWriteBackError, match="retry budget") as excinfo:
        update_status(driver, row, RecordStatus.READY, dry_run=False)
    assert excinfo.value.attempts == POLICY.max_attempts
    assert store.get_record(row.record_id).status is RecordStatus.PENDING


def test_extended_busy_on_apply_times_out(store: RecordStore) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    injector.force(
        FaultKind.EXTENDED_BUSY,
        point=InteractionPoint.STATUS_APPLICATION,
        delay_seconds=30.0,
    )
    with pytest.raises(DriverTimeoutError):
        update_status(driver, row, RecordStatus.READY, dry_run=False)


def test_write_back_off_the_results_screen_is_a_navigation_error(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    driver.recover()  # back to the main menu
    with pytest.raises(DriverNavigationError, match="requires the results screen"):
        update_status(driver, row, RecordStatus.READY)


def test_write_back_recovers_from_a_left_over_detail_screen(
    store: RecordStore,
) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    driver.open_result(row.position)
    assert driver.screen_name == "detail"

    result = update_status(driver, row, RecordStatus.READY, dry_run=False)
    assert result.verified is True


def test_write_back_clears_a_blocking_popup_before_starting(
    store: RecordStore,
) -> None:
    injector = FaultInjector("none", seed=0)
    driver = make_driver(store, injector, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    injector.force(FaultKind.SURPRISE_POPUP, point=InteractionPoint.BUTTON_PRESS)
    driver.system.press("back_to_query")  # raw press: leaves a popup open
    assert driver.popup_text is not None

    result = update_status(driver, row, RecordStatus.READY)
    assert result.dry_run is True
    assert driver.popup_text is None


# =============================================================================
# Write-back: audit and immutability
# =============================================================================


def test_write_back_audit_events_carry_only_safe_scalars(store: RecordStore) -> None:
    sink = RecordingSink()
    driver = make_driver(store, audit=sink, visible_rows=3, buffer_rows=2)
    row = first_row(driver)

    update_status(driver, row, RecordStatus.READY)  # dry run
    update_status(driver, row, RecordStatus.READY, dry_run=False)

    names = sink.names()
    assert "status_update_dry_run" in names
    assert "status_update_applied" in names
    for stage, _, payload in sink.events:
        assert stage == "driver"
        for value in payload.values():
            assert isinstance(value, str | int | float)
            if isinstance(value, str):
                assert "OPR-" not in value  # no record data in audit payloads


def test_refused_transition_emits_a_safe_audit_event(store: RecordStore) -> None:
    sink = RecordingSink()
    driver = make_driver(store, audit=sink, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    with pytest.raises(DriverWriteBackError):
        update_status(driver, row, RecordStatus.COMPLETE)
    assert "status_update_refused" in sink.names()


def test_unverified_write_emits_a_safe_audit_event(store: RecordStore) -> None:
    sink = RecordingSink()
    driver = make_driver(store, audit=sink, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    swallow_apply(driver)

    with pytest.raises(DriverWriteBackError):
        update_status(driver, row, RecordStatus.READY, dry_run=False)
    assert "status_update_unverified" in sink.names()


def test_status_update_result_is_immutable(store: RecordStore) -> None:
    driver = make_driver(store, visible_rows=3, buffer_rows=2)
    row = first_row(driver)
    result = update_status(driver, row, RecordStatus.READY)
    with pytest.raises(FrozenInstanceError):
        result.dry_run = False  # type: ignore[misc]


def test_result_types_are_exported_from_the_driver_package() -> None:
    import las.driver as driver_package

    for name in (
        "ExtractedRow",
        "ExtractionResult",
        "StatusUpdateResult",
        "DriverExtractionError",
        "DriverWriteBackError",
        "extract_all_rows",
        "extract_query_results",
        "update_status",
    ):
        assert name in driver_package.__all__
        assert hasattr(driver_package, name)
    assert issubclass(DriverExtractionError, Exception)
    assert issubclass(StatusUpdateResult, object)
    assert ExtractionResult((), True, 0, 0, 0, 0, 0, 0).row_count == 0


# =============================================================================
# Static and architecture checks for the new module
# =============================================================================


def tables_source() -> str:
    return TABLES_PATH.read_text(encoding="utf-8")


def test_every_loop_in_tables_is_bounded() -> None:
    """No while loops at all: each retry is a bounded for over a budget."""
    tree = ast.parse(tables_source())
    whiles = [node for node in ast.walk(tree) if isinstance(node, ast.While)]
    assert whiles == [], "tables.py must use bounded for-loops only"

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        assert isinstance(node.iter, ast.Call | ast.Name | ast.Attribute), (
            "every for-loop must iterate a finite, named collection or range"
        )


def test_tables_never_serializes_arbitrary_objects() -> None:
    text = tables_source()
    assert ("repr" + "(") not in text
    assert "!r" not in text
    assert ("str" + "(value)") not in text


def test_tables_uses_no_real_time_and_no_new_randomness() -> None:
    text = tables_source()
    assert ("import " + "time") not in text
    assert ("time" + ".sleep") not in text
    assert "random" not in text


def test_tables_never_reaches_into_the_store_or_the_fault_injector() -> None:
    """Attribute-level proof: no store, no private simulator state, no faults.

    Tokens are dotted so they match real attribute access rather than this
    module's own helper names.
    """
    text = tables_source()
    for forbidden in (
        ".store",
        ".snapshot(",
        ".update_status(",
        ".get_record(",
        ".find_records(",
        ".list_records(",
        "RecordStore",
        "fault_injector",
        ".force(",
        "._faults",
        "._records",
        "._result_ids",
        "._scroll",
        "._store",
    ):
        assert forbidden not in text, f"tables.py must not reference {forbidden}"


def test_tables_imports_only_driver_and_simulator() -> None:
    tree = ast.parse(tables_source())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)

    forbidden = (
        "las.pipeline",
        "las.ingest",
        "las.validate",
        "las.match",
        "las.reconcile",
        "las.report",
        "las.cli",
    )
    for module in modules:
        assert not module.startswith(forbidden), f"tables.py imports {module}"
        if module == "las" or module.startswith("las."):
            assert module.startswith(("las.driver", "las.simulator")), module
