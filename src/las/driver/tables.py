"""Bounded full-table extraction and dry-run-safe status write-back.

Everything here drives the simulated legacy system exclusively through the
public screen surface exposed by :class:`~las.driver.client.LegacyDriver`
(visible controls, the results window, the detail screen). The record
store is never touched, and the fault injector is never read, reset, or
bypassed — faults are simply survived.

Extraction algorithm
--------------------
The results widget shows a fixed-size window onto a longer logical table
and pads the tail with all-empty buffer rows, while reporting a row count
inflated by that padding. The extractor therefore reconstructs the
logical table from *observed content* rather than trusting the count:

1. Seek the first window (logical position 0) with bounded re-attempts.
   If position 0 is unreachable, the extraction can never be complete and
   says so — it does not silently start mid-table.
2. Read the visible window and absorb it at absolute positions
   ``scroll_position + offset``. Rows carrying no ``record_id`` are
   placeholder/buffer rows: counted, never returned as records.
3. Advance by the observed window length. Because the widget clamps
   scrolling near the end, the achieved position may be lower than the
   request, so windows overlap; overlap is resolved by absolute position,
   which keeps coverage contiguous and gap-free.
4. Stop on terminal evidence (below), on exhausting the range the widget
   admits to, or on a stall that outlives its re-attempts.
5. A scroll request that yields no forward progress is a stall: it is
   re-attempted a bounded number of times, and if progress still fails
   the result is returned as **incomplete** (or raised as
   :class:`DriverExtractionError` when ``strict`` is set).

The widget-reported count is used only as an upper bound on how many
positions can exist — never as the logical row count, which is always
``len(result.rows)`` derived from real observed content.

Completeness rule
-----------------
``complete=True`` requires **both** of:

* the scan began at logical position 0, and
* at least one piece of *terminal evidence* was observed.

Terminal evidence is exactly one of:

* ``placeholder_cutoff`` — at least ``empty_row_cutoff`` consecutive
  placeholder rows were read after the last real row (or from the very
  first position, for a table holding no records at all).
* ``short_final_window`` — the widget served fewer rows than the largest
  window it had already served, so it has run out of rows to give.
* ``end_of_data_signal`` — a caller-supplied predicate reported the end.
* ``zero_match_query_signal`` — the query itself matched nothing.

Exhausting the range implied by the reported row count is deliberately
**not** terminal evidence. A legacy widget may under-report its rows, and
a final accessible window still full of real records proves nothing about
what lies past it. That situation yields ``complete=False`` with the
``reported_range_exhausted_without_tail_evidence`` warning — or raises
under ``strict`` — and keeps whatever was observed as an accurate ordered
prefix rather than passing a partial scan off as total.

The practical consequence is that provable completeness needs the widget
to expose at least ``empty_row_cutoff`` trailing buffer rows (the sample
configuration exposes three against a cutoff of two) or to serve a short
final window. A table with no padding at all extracts accurately but is
reported as not provably complete, which is the honest answer.

Every loop is a bounded ``for``; every wait runs on the injected clock,
and the whole extraction is additionally capped by its own deadline.

Write-back
----------
:func:`update_status` defaults to ``dry_run=True``. A dry run walks the
real path — open the record, read its current state, check the requested
transition against the invented lifecycle — and then stops, mutating
nothing. A real write goes through the visible controls (set the status
field, press apply), waits for completion, and then **independently
re-reads the detail screen's record summary**, which the system derives
live from its own state, to confirm the requested status actually
persisted. A mismatch is a typed failure, not a shrug. Rejected
transitions are never retried; only transient faults are, and only
through the driver's existing bounded retry policy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from las.driver.waits import (
    DriverError,
    DriverNavigationError,
    DriverTimeoutError,
)
from las.simulator import STATUS_TRANSITIONS, RecordStatus, ScreenId, TableRow

if TYPE_CHECKING:
    from las.driver.client import LegacyDriver

#: Trailing all-empty rows needed before the tail is considered reached.
DEFAULT_EMPTY_ROW_CUTOFF = 2

#: Default cap on one whole extraction, measured on the injected clock.
DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 60.0

#: Safe, generic warning tokens. They name a condition and never carry
#: extracted data, identifiers, or object state.
WARN_NO_FORWARD_PROGRESS = "stalled_pagination_no_forward_progress"
WARN_FIRST_WINDOW_UNREACHABLE = "first_window_unreachable"
WARN_WINDOW_BUDGET_EXHAUSTED = "window_visit_budget_exhausted"
WARN_ROW_WITHOUT_RECORD_ID = "row_without_record_id_ignored"
WARN_REPEATED_RECORD_ID = "repeated_record_id_at_new_position_ignored"
WARN_POSITION_CONTENT_CHANGED = "position_content_changed_between_reads"
WARN_REPORTED_RANGE_EXHAUSTED = "reported_range_exhausted_without_tail_evidence"

#: The only accepted proofs that the end of the data was actually reached.
#: The widget's own row count is deliberately absent from this list.
EVIDENCE_PLACEHOLDER_CUTOFF = "placeholder_cutoff"
EVIDENCE_SHORT_FINAL_WINDOW = "short_final_window"
EVIDENCE_END_OF_DATA_SIGNAL = "end_of_data_signal"
EVIDENCE_ZERO_MATCH_QUERY = "zero_match_query_signal"


class DriverExtractionError(DriverError):
    """Full-table extraction could not be completed within its bounds."""


class DriverWriteBackError(DriverError):
    """A status write-back was rejected, unverifiable, or misdirected."""


@dataclass(frozen=True)
class ExtractedRow:
    """One immutable logical row, tagged with its absolute table position."""

    position: int
    record_id: str
    external_ref: str
    amount: str
    unit: str
    period: str
    status: str


@dataclass(frozen=True)
class ExtractionResult:
    """Immutable outcome of one extraction, plus safe scalar metadata.

    ``complete`` is the only claim of totality, and it is true only when
    the scan started at position 0 *and* observed terminal evidence.
    ``terminal_evidence`` names which proof was seen, or is ``None`` when
    none was — see the module's completeness rule.
    """

    rows: tuple[ExtractedRow, ...]
    complete: bool
    reported_row_count: int
    window_size: int
    windows_visited: int
    duplicate_rows_suppressed: int
    placeholder_rows_ignored: int
    stall_reattempts: int
    warnings: tuple[str, ...] = ()
    terminal_evidence: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(row.record_id for row in self.rows)


@dataclass(frozen=True)
class StatusUpdateResult:
    """Immutable outcome of one status write-back.

    ``applied_status`` is ``None`` whenever nothing was written, which is
    always the case for a dry run.
    """

    record_id: str
    requested_status: str
    previous_status: str
    dry_run: bool
    verified: bool
    attempts: int
    applied_status: str | None = None

    @property
    def changed(self) -> bool:
        return self.applied_status is not None


def _is_blank_row(row: TableRow) -> bool:
    """True when every visible cell of the row is empty."""
    return not any(
        value.strip()
        for value in (
            row.record_id,
            row.external_ref,
            row.amount,
            row.unit,
            row.period,
            row.status,
        )
    )


@dataclass
class _ScanState:
    """Mutable bookkeeping for one extraction; never returned to callers."""

    rows_by_position: dict[int, ExtractedRow] = field(default_factory=dict)
    position_by_record: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duplicates: int = 0
    placeholders: int = 0
    stall_reattempts: int = 0
    windows_visited: int = 0
    window_capacity: int = 0
    short_window_seen: bool = False
    last_real_position: int = -1
    highest_scanned_position: int = -1

    @property
    def trailing_empty_run(self) -> int:
        """Placeholder rows scanned after the last real row."""
        return self.highest_scanned_position - self.last_real_position

    def has_placeholder_tail(self, cutoff: int) -> bool:
        """Whether enough consecutive placeholder rows closed out the scan."""
        return self.highest_scanned_position >= 0 and self.trailing_empty_run >= cutoff

    def note(self, token: str) -> None:
        if token not in self.warnings:
            self.warnings.append(token)

    def absorb(self, position: int, window: tuple[TableRow, ...]) -> None:
        """Classify one visible window at absolute ``position``."""
        self.windows_visited += 1
        length = len(window)
        if length > self.window_capacity:
            self.window_capacity = length
        elif length < self.window_capacity:
            # The widget served fewer rows than it has already shown it can
            # serve, so it has genuinely run out — regardless of its count.
            self.short_window_seen = True

        for offset, row in enumerate(window):
            index = position + offset
            self.highest_scanned_position = max(self.highest_scanned_position, index)

            identity = row.record_id.strip()
            if not identity:
                # A row with no identity cannot be a record: buffer padding.
                self.placeholders += 1
                if not _is_blank_row(row):
                    self.note(WARN_ROW_WITHOUT_RECORD_ID)
                continue

            self.last_real_position = max(self.last_real_position, index)

            seen_here = self.rows_by_position.get(index)
            if seen_here is not None:
                # Overlapping or repeated window: keep the first reading.
                self.duplicates += 1
                if seen_here.record_id != identity:
                    self.note(WARN_POSITION_CONTENT_CHANGED)
                continue

            claimed_at = self.position_by_record.get(identity)
            if claimed_at is not None:
                # The same record surfacing at a second position would
                # duplicate data; refuse it and say so.
                self.duplicates += 1
                self.note(WARN_REPEATED_RECORD_ID)
                continue

            extracted = ExtractedRow(
                position=index,
                record_id=identity,
                external_ref=row.external_ref,
                amount=row.amount,
                unit=row.unit,
                period=row.period,
                status=row.status,
            )
            self.rows_by_position[index] = extracted
            self.position_by_record[identity] = index

    def ordered_rows(self) -> tuple[ExtractedRow, ...]:
        return tuple(self.rows_by_position[key] for key in sorted(self.rows_by_position))


def _terminal_evidence(
    state: _ScanState,
    cutoff: int,
    end_of_data: Callable[[], bool] | None,
) -> str | None:
    """Name the end-of-data proof observed so far, or ``None`` if there is none.

    The widget's reported row count is intentionally not consulted here:
    a count is a claim, not evidence.
    """
    if state.has_placeholder_tail(cutoff):
        return EVIDENCE_PLACEHOLDER_CUTOFF
    if state.short_window_seen:
        return EVIDENCE_SHORT_FINAL_WINDOW
    if end_of_data is not None and end_of_data():
        return EVIDENCE_END_OF_DATA_SIGNAL
    return None


def _clear_blocking_popup(driver: LegacyDriver, *, operation: str) -> None:
    """Apply the driver's popup policy before touching the table."""
    if driver.popup_text is not None:
        driver.handle_popup(operation=operation)


def _read_window(driver: LegacyDriver, *, operation: str) -> tuple[TableRow, ...]:
    _clear_blocking_popup(driver, operation=operation)
    return driver.read_table_rows()


def _scroll_to(driver: LegacyDriver, target: int, *, operation: str) -> int:
    _clear_blocking_popup(driver, operation=operation)
    return driver.scroll_results(target)


def _reported_count(driver: LegacyDriver, *, operation: str) -> int:
    _clear_blocking_popup(driver, operation=operation)
    return driver.table_row_count()


def _guard_deadline(
    driver: LegacyDriver,
    deadline: float,
    *,
    operation: str,
    timeout_seconds: float,
) -> None:
    if driver.system.clock.now() >= deadline:
        raise DriverTimeoutError(
            "the extraction deadline expired before the table was covered",
            operation=operation,
            screen=driver.screen_name,
            timeout_seconds=timeout_seconds,
        )


def _seek_first_window(
    driver: LegacyDriver,
    state: _ScanState,
    deadline: float,
    *,
    operation: str,
    timeout_seconds: float,
) -> tuple[int, bool]:
    """Bring the window to logical position 0; returns (position, at_top)."""
    policy = driver.policy
    clock = driver.system.clock
    position = 0
    for attempt in range(1, policy.max_attempts + 1):
        _guard_deadline(driver, deadline, operation=operation, timeout_seconds=timeout_seconds)
        position = _scroll_to(driver, 0, operation=operation)
        if position == 0:
            return 0, True
        state.stall_reattempts += 1
        clock.sleep(policy.delay_for_attempt(attempt))
    state.note(WARN_FIRST_WINDOW_UNREACHABLE)
    return position, False


def extract_all_rows(
    driver: LegacyDriver,
    *,
    empty_row_cutoff: int = DEFAULT_EMPTY_ROW_CUTOFF,
    timeout_seconds: float = DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
    max_window_visits: int | None = None,
    end_of_data: Callable[[], bool] | None = None,
    strict: bool = False,
    operation: str = "extract_all_rows",
) -> ExtractionResult:
    """Extract the whole logical results table through the visible window.

    Requires the results screen. Returns typed immutable rows in logical
    order, free of placeholder rows and of duplicates introduced by
    overlapping windows.

    ``complete=True`` is claimed only under the module's completeness rule:
    the scan started at position 0 and terminal evidence was observed.
    ``end_of_data`` optionally supplies that evidence for a widget with an
    explicit end signal. ``strict`` turns an incomplete scan into a
    :class:`DriverExtractionError` instead of a ``complete=False`` result;
    either way completeness is never overstated. Exceeding
    ``timeout_seconds`` on the injected clock raises
    :class:`DriverTimeoutError`.
    """
    if empty_row_cutoff < 1:
        raise ValueError("empty_row_cutoff must be at least 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_window_visits is not None and max_window_visits < 1:
        raise ValueError("max_window_visits must be at least 1")

    system = driver.system
    if system.current_screen is not ScreenId.RESULTS:
        raise DriverNavigationError(
            "full-table extraction requires the results screen",
            operation=operation,
            screen=driver.screen_name,
        )

    policy = driver.policy
    clock = system.clock
    deadline = clock.now() + timeout_seconds
    state = _ScanState()

    reported = _reported_count(driver, operation=operation)
    position, reached_top = _seek_first_window(
        driver, state, deadline, operation=operation, timeout_seconds=timeout_seconds
    )

    # One visit per position is the worst case (a clamped scroll can
    # advance by a single row), plus slack for the first and last windows.
    visit_budget = max(1, reported) + 2 if max_window_visits is None else max_window_visits

    evidence: str | None = None
    exhausted_budget = True
    for _ in range(visit_budget):
        _guard_deadline(driver, deadline, operation=operation, timeout_seconds=timeout_seconds)
        state.absorb(position, _read_window(driver, operation=operation))

        evidence = _terminal_evidence(state, empty_row_cutoff, end_of_data)
        if evidence is not None:
            exhausted_budget = False
            break

        if state.highest_scanned_position + 1 >= reported:
            # Every position the widget admits to has now been read, but the
            # widget's own count is not proof that the data ended there.
            state.note(WARN_REPORTED_RANGE_EXHAUSTED)
            exhausted_budget = False
            break

        target = position + max(1, state.window_capacity)
        moved = False
        for attempt in range(1, policy.max_attempts + 1):
            _guard_deadline(driver, deadline, operation=operation, timeout_seconds=timeout_seconds)
            achieved = _scroll_to(driver, target, operation=operation)
            if achieved > position:
                position = achieved
                moved = True
                break
            state.stall_reattempts += 1
            clock.sleep(policy.delay_for_attempt(attempt))

        if not moved:
            state.note(WARN_NO_FORWARD_PROGRESS)
            exhausted_budget = False
            break

    if exhausted_budget:
        state.note(WARN_WINDOW_BUDGET_EXHAUSTED)

    complete = evidence is not None and reached_top
    result = ExtractionResult(
        rows=state.ordered_rows(),
        complete=complete,
        reported_row_count=reported,
        window_size=state.window_capacity,
        windows_visited=state.windows_visited,
        duplicate_rows_suppressed=state.duplicates,
        placeholder_rows_ignored=state.placeholders,
        stall_reattempts=state.stall_reattempts,
        warnings=tuple(state.warnings),
        terminal_evidence=evidence,
    )

    if complete:
        driver._emit(
            "extraction_completed",
            operation=operation,
            rows=result.row_count,
            windows=result.windows_visited,
            duplicates_suppressed=result.duplicate_rows_suppressed,
            placeholders_ignored=result.placeholder_rows_ignored,
            evidence=evidence,
        )
        return result

    driver._emit(
        "extraction_incomplete",
        operation=operation,
        rows=result.row_count,
        windows=result.windows_visited,
        stall_reattempts=result.stall_reattempts,
    )
    if strict:
        raise DriverExtractionError(
            "full-table extraction did not cover the table within its bounds",
            operation=operation,
            attempts=policy.max_attempts,
            screen=driver.screen_name,
        )
    return result


def extract_query_results(
    driver: LegacyDriver,
    external_ref: str = "",
    period: str = "",
    *,
    empty_row_cutoff: int = DEFAULT_EMPTY_ROW_CUTOFF,
    timeout_seconds: float = DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
    max_window_visits: int | None = None,
    end_of_data: Callable[[], bool] | None = None,
    strict: bool = False,
    operation: str = "extract_query_results",
) -> ExtractionResult:
    """Run a query and extract everything it matched.

    A query matching nothing never reaches the results screen. That is an
    explicit end-of-data signal from the system, so it yields a complete,
    empty result rather than an error — zero rows is a legitimate answer.
    """
    outcome = driver.run_query(external_ref, period)
    if outcome.popup_text is not None or driver.system.current_screen is not ScreenId.RESULTS:
        driver._emit(
            "extraction_completed",
            operation=operation,
            rows=0,
            windows=0,
            evidence=EVIDENCE_ZERO_MATCH_QUERY,
        )
        return ExtractionResult(
            rows=(),
            complete=True,
            reported_row_count=0,
            window_size=0,
            windows_visited=0,
            duplicate_rows_suppressed=0,
            placeholder_rows_ignored=0,
            stall_reattempts=0,
            terminal_evidence=EVIDENCE_ZERO_MATCH_QUERY,
        )
    return extract_all_rows(
        driver,
        empty_row_cutoff=empty_row_cutoff,
        timeout_seconds=timeout_seconds,
        max_window_visits=max_window_visits,
        end_of_data=end_of_data,
        strict=strict,
        operation=operation,
    )


def _coerce_status(value: RecordStatus | str, *, operation: str) -> RecordStatus:
    """Map a supplied value onto the invented lifecycle, or fail typed."""
    if isinstance(value, RecordStatus):
        return value
    if not isinstance(value, str):
        raise DriverWriteBackError(
            "the status must be a lifecycle member or its name",
            operation=operation,
        )
    try:
        return RecordStatus(value.strip())
    except ValueError:
        allowed = ", ".join(status.value for status in RecordStatus)
        raise DriverWriteBackError(
            f"the status is not part of the record lifecycle (allowed: {allowed})",
            operation=operation,
        ) from None


def _read_detail_state(driver: LegacyDriver, *, operation: str) -> tuple[str, RecordStatus]:
    """Read (record_id, status) from the detail screen's own summary.

    The summary is derived live by the system from its own record state,
    which makes it an independent read rather than an echo of whatever the
    driver just typed into the status field.
    """
    summary = driver.get_field("record_summary", operation=operation)
    parts = [part.strip() for part in summary.split(" | ")]
    if len(parts) < 5 or not parts[0]:
        raise DriverWriteBackError(
            "the detail screen summary could not be read",
            operation=operation,
            screen=driver.screen_name,
        )
    return parts[0], _coerce_status(parts[-1], operation=operation)


def _ensure_results_screen(driver: LegacyDriver, *, operation: str) -> None:
    """Bring the driver back to the results screen, or fail typed."""
    _clear_blocking_popup(driver, operation=operation)
    if driver.system.current_screen is ScreenId.DETAIL:
        driver.back_to_results()
    if driver.system.current_screen is not ScreenId.RESULTS:
        raise DriverNavigationError(
            "the status write-back requires the results screen",
            operation=operation,
            screen=driver.screen_name,
        )


def update_status(
    driver: LegacyDriver,
    row: ExtractedRow,
    new_status: RecordStatus | str,
    *,
    dry_run: bool = True,
    operation: str = "update_status",
) -> StatusUpdateResult:
    """Change one record's status through the simulated UI.

    Defaults to a dry run: the record is opened and the transition is
    checked, but nothing is written and ``applied_status`` stays ``None``.

    A real write sets the status field, presses apply, waits for the
    system to settle, and then re-reads the detail summary to confirm the
    new status persisted. Anything else is a :class:`DriverWriteBackError`.
    An illegal transition is refused up front and never retried; transient
    faults are absorbed only by the driver's bounded retry policy.
    """
    target = _coerce_status(new_status, operation=operation)
    _ensure_results_screen(driver, operation=operation)

    # A rejected row selection surfaces as the driver's typed operation
    # error; positions are never guessed at or retried into existence.
    driver.open_result(row.position)

    opened_id, previous = _read_detail_state(driver, operation=operation)
    if opened_id != row.record_id:
        raise DriverWriteBackError(
            "the detail screen opened a different record than requested",
            operation=operation,
            screen=driver.screen_name,
        )

    if target not in STATUS_TRANSITIONS[previous]:
        driver._emit(
            "status_update_refused",
            operation=operation,
            requested=target.value,
            previous=previous.value,
        )
        raise DriverWriteBackError(
            "the record lifecycle does not allow the requested transition",
            operation=operation,
            screen=driver.screen_name,
        )

    if dry_run:
        driver._emit(
            "status_update_dry_run",
            operation=operation,
            requested=target.value,
            previous=previous.value,
        )
        result = StatusUpdateResult(
            record_id=row.record_id,
            requested_status=target.value,
            previous_status=previous.value,
            dry_run=True,
            verified=False,
            attempts=0,
        )
        _ensure_results_screen(driver, operation=operation)
        return result

    attempts_used = 0
    applied_cleanly = False
    for attempt in range(1, driver.policy.max_attempts + 1):
        attempts_used = attempt
        driver.set_field("status_input", target.value, operation=operation)
        driver.press(("apply_status",), operation=operation)
        driver.wait_until_idle()  # a swallowed action surfaces as a timeout
        if driver.popup_text is None:
            applied_cleanly = True
            break
        # A rejection popup raises here; a transient surprise popup is
        # dismissed and the bounded loop tries once more.
        driver.handle_popup(operation=operation)

    if not applied_cleanly:
        raise DriverWriteBackError(
            "the status change did not go through within the retry budget",
            operation=operation,
            attempts=attempts_used,
            screen=driver.screen_name,
        )

    verified_id, observed = _read_detail_state(driver, operation=operation)
    if verified_id != row.record_id or observed is not target:
        driver._emit(
            "status_update_unverified",
            operation=operation,
            requested=target.value,
            observed=observed.value,
        )
        raise DriverWriteBackError(
            "the status change could not be verified after applying it",
            operation=operation,
            attempts=attempts_used,
            screen=driver.screen_name,
        )

    driver._emit(
        "status_update_applied",
        operation=operation,
        requested=target.value,
        previous=previous.value,
        attempts=attempts_used,
    )
    result = StatusUpdateResult(
        record_id=row.record_id,
        requested_status=target.value,
        previous_status=previous.value,
        dry_run=False,
        verified=True,
        attempts=attempts_used,
        applied_status=observed.value,
    )
    _ensure_results_screen(driver, operation=operation)
    return result
