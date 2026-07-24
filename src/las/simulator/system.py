"""The stateful simulated legacy system.

:class:`LegacySystem` behaves like a deliberately awkward terminal
application driven through screens (see :mod:`las.simulator.screens`):

* interaction happens only through visible controls on the current screen;
* selected actions (running a query, opening a record, applying a status,
  scrolling the table) leave the system *busy* for a configurable delay,
  during which every interaction raises :class:`SystemBusyError`;
* modal popups block all controls except their own until dismissed;
* the results table reports an inflated row count (real rows plus trailing
  buffer rows) and serves empty placeholder rows past the end of the real
  data, exactly the kind of lie legacy table widgets tell;
* faults are injected only at defined interaction points and only through
  the centralized :class:`~las.simulator.faults.FaultInjector` — the
  default ``none`` profile leaves behavior exactly as documented here;
* ``reset()`` is the one escape hatch that always works, returning the
  system to a pristine main menu (``navigate()`` and ``reset()`` are
  deliberately fault-free paths).

Time is injected: :class:`SimulatedClock` lets tests advance time
instantly and deterministically, while :class:`SystemClock` provides real
delays for demo runs. Nothing here reads wall-clock state on its own.

Navigation map
--------------
::

    MAIN_MENU --open_query-----> QUERY
    QUERY     --run_query------> RESULTS   (only with at least one match;
                                            zero matches -> popup, stay)
    QUERY     --back_to_menu---> MAIN_MENU
    RESULTS   --open_detail----> DETAIL    (valid row selection required)
    RESULTS   --back_to_query--> QUERY
    DETAIL    --back_to_results> RESULTS
    any state --reset()--------> MAIN_MENU
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from las.simulator.faults import FaultInjector, FaultKind, InteractionPoint
from las.simulator.screens import POPUP_SCREEN, SCREENS, Control, ControlType, ScreenId
from las.simulator.store import OperationalRecord, RecordStore, SimulatorError

#: Message shown by an injected surprise popup (invented, generic).
SURPRISE_POPUP_MESSAGE = "Unexpected system notice. Dismiss to continue."

#: Informational popup shown when a query matches nothing.
NO_MATCHES_MESSAGE = "No matching records found."

#: Buttons whose press is a distinct interaction point for fault decisions.
_BUTTON_POINTS: dict[str, InteractionPoint] = {
    "run_query": InteractionPoint.QUERY_EXECUTION,
    "open_detail": InteractionPoint.RECORD_OPENING,
    "apply_status": InteractionPoint.STATUS_APPLICATION,
}


class NavigationError(SimulatorError):
    """A screen transition that the navigation map does not allow."""


class ControlNotFoundError(SimulatorError):
    """The referenced control is not visible in the current state."""


class SystemBusyError(SimulatorError):
    """An interaction was attempted while the system is busy."""


class PopupActiveError(SimulatorError):
    """An interaction was attempted while a modal popup is open."""


class Clock(Protocol):
    """Injected time source; see SimulatedClock and SystemClock."""

    def now(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SimulatedClock:
    """Deterministic clock for tests: time moves only when told to."""

    def __init__(self) -> None:
        self._now = 0.0

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += max(0.0, seconds)

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)


class SystemClock:
    """Real monotonic clock for demo runs."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


@dataclass(frozen=True)
class TableRow:
    """One visible results-table row; placeholder rows are all-empty."""

    record_id: str
    external_ref: str
    amount: str
    unit: str
    period: str
    status: str


_EMPTY_ROW = TableRow("", "", "", "", "", "")

#: Structural navigations allowed via navigate(); data-dependent entries
#: (QUERY->RESULTS, RESULTS->DETAIL) happen only through their buttons.
_NAVIGATION_MAP: dict[ScreenId, frozenset[ScreenId]] = {
    ScreenId.MAIN_MENU: frozenset({ScreenId.QUERY}),
    ScreenId.QUERY: frozenset({ScreenId.MAIN_MENU}),
    ScreenId.RESULTS: frozenset({ScreenId.QUERY}),
    ScreenId.DETAIL: frozenset({ScreenId.RESULTS}),
}

_POPUP_CONTROL_IDS = frozenset(POPUP_SCREEN.control_ids())


class LegacySystem:
    """Deterministic, stateful facade over the record store."""

    def __init__(
        self,
        store: RecordStore,
        *,
        visible_rows: int = 12,
        buffer_rows: int = 3,
        busy_delay_seconds: float = 0.0,
        clock: Clock | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        if visible_rows < 1:
            raise ValueError("visible_rows must be >= 1")
        if buffer_rows < 0:
            raise ValueError("buffer_rows must be >= 0")
        if busy_delay_seconds < 0:
            raise ValueError("busy_delay_seconds must be >= 0")
        self._store = store
        self._visible_rows = visible_rows
        self._buffer_rows = buffer_rows
        self._busy_delay = busy_delay_seconds
        self._clock: Clock = clock if clock is not None else SimulatedClock()
        self._faults = fault_injector if fault_injector is not None else FaultInjector()
        self._screen = ScreenId.MAIN_MENU
        self._fields: dict[str, str] = {}
        self._status_texts: dict[str, str] = {}
        self._popup: str | None = None
        self._busy_until = 0.0
        self._result_ids: list[str] = []
        self._scroll = 0
        self._detail_record_id: str | None = None

    # -- observable state (always readable) --------------------------------

    @property
    def current_screen(self) -> ScreenId:
        return self._screen

    @property
    def is_busy(self) -> bool:
        return self._clock.now() < self._busy_until

    @property
    def popup_message(self) -> str | None:
        return self._popup

    @property
    def fault_injector(self) -> FaultInjector:
        return self._faults

    @property
    def clock(self) -> Clock:
        """The injected time source; callers share it to wait on this system."""
        return self._clock

    def visible_controls(self) -> tuple[Control, ...]:
        if self._popup is not None:
            return POPUP_SCREEN.controls
        return SCREENS[self._screen].controls

    # -- interaction gating -------------------------------------------------

    def _require_interactive(self, control_id: str | None = None) -> None:
        if self.is_busy:
            raise SystemBusyError("the system is busy; wait before interacting")
        if self._popup is not None and control_id not in _POPUP_CONTROL_IDS:
            raise PopupActiveError(
                f"a popup is open ('{self._popup}'); dismiss it before interacting"
            )

    def _visible_control(self, control_id: str, *expected: ControlType) -> Control:
        active = POPUP_SCREEN if self._popup is not None else SCREENS[self._screen]
        control = active.find(control_id)
        if control is None or control.control_type not in expected:
            wanted = "/".join(kind.value for kind in expected)
            available = ", ".join(
                c.control_id for c in active.controls if c.control_type in expected
            )
            raise ControlNotFoundError(
                f"no {wanted} '{control_id}' on '{active.title}' "
                f"(available: {available or 'none'})"
            )
        decision = self._faults.decide(InteractionPoint.CONTROL_LOOKUP)
        if decision.fault is FaultKind.TRANSIENT_CONTROL_MISSING:
            raise ControlNotFoundError(f"control '{control_id}' is temporarily unavailable")
        return control

    def _begin_busy(self) -> None:
        self._busy_until = self._clock.now() + self._busy_delay

    def _open_popup(self, message: str) -> None:
        self._popup = message

    # -- fields -------------------------------------------------------------

    def set_field(self, control_id: str, value: str) -> None:
        self._require_interactive(control_id)
        self._visible_control(control_id, ControlType.TEXT_FIELD)
        self._fields[control_id] = str(value)

    def get_field(self, control_id: str) -> str:
        self._require_interactive(control_id)
        if self._popup is not None and control_id == "popup_message":
            return self._popup
        control = self._visible_control(
            control_id, ControlType.TEXT_FIELD, ControlType.STATUS_TEXT
        )
        if control.control_type == ControlType.STATUS_TEXT:
            if control_id == "record_summary":
                return self._record_summary()
            return self._status_texts.get(control_id, "")
        return self._fields.get(control_id, "")

    def _record_summary(self) -> str:
        assert self._detail_record_id is not None  # summary exists only on DETAIL
        record = self._store.get_record(self._detail_record_id)
        return (
            f"{record.record_id} | {record.external_ref} | {record.amount:.2f} "
            f"{record.unit} | {record.period} | {record.status.value}"
        )

    # -- buttons ------------------------------------------------------------

    def press(self, control_id: str) -> None:
        self._require_interactive(control_id)
        self._visible_control(control_id, ControlType.BUTTON)

        if self._popup is not None:
            # Gating above guarantees only popup controls reach this point,
            # and 'dismiss' is the popup's only button.
            self._popup = None
            return

        decision = self._faults.decide(
            _BUTTON_POINTS.get(control_id, InteractionPoint.BUTTON_PRESS)
        )
        if decision.fault is FaultKind.SURPRISE_POPUP:
            self._open_popup(SURPRISE_POPUP_MESSAGE)
            return
        if decision.fault is FaultKind.EXTENDED_BUSY:
            # The action is swallowed: long busy, nothing else happens.
            self._busy_until = self._clock.now() + self._busy_delay + decision.delay_seconds
            return

        handlers = {
            (ScreenId.MAIN_MENU, "open_query"): self._handle_open_query,
            (ScreenId.QUERY, "run_query"): self._handle_run_query,
            (ScreenId.QUERY, "back_to_menu"): self._handle_back_to_menu,
            (ScreenId.RESULTS, "open_detail"): self._handle_open_detail,
            (ScreenId.RESULTS, "back_to_query"): self._handle_back_to_query,
            (ScreenId.DETAIL, "apply_status"): self._handle_apply_status,
            (ScreenId.DETAIL, "back_to_results"): self._handle_back_to_results,
        }
        handlers[(self._screen, control_id)]()
        if decision.fault is FaultKind.SLOW_RESPONSE:
            self._busy_until = max(
                self._busy_until, self._clock.now() + decision.delay_seconds
            )

    def _handle_open_query(self) -> None:
        self._screen = ScreenId.QUERY

    def _handle_back_to_menu(self) -> None:
        self._screen = ScreenId.MAIN_MENU

    def _handle_back_to_query(self) -> None:
        self._screen = ScreenId.QUERY

    def _handle_back_to_results(self) -> None:
        self._screen = ScreenId.RESULTS

    def _handle_run_query(self) -> None:
        self._begin_busy()
        external_ref = self._fields.get("external_ref_input", "").strip() or None
        period = self._fields.get("period_input", "").strip() or None
        matches = self._store.find_records(external_ref=external_ref, period=period)
        if not matches:
            self._status_texts["query_status"] = "0 records found"
            self._open_popup(NO_MATCHES_MESSAGE)
            return
        self._result_ids = [record.record_id for record in matches]
        self._scroll = 0
        self._status_texts["query_status"] = f"{len(matches)} records found"
        self._status_texts["results_status"] = f"Showing {len(matches)} records"
        self._screen = ScreenId.RESULTS

    def _handle_open_detail(self) -> None:
        self._begin_busy()
        raw_selector = self._fields.get("row_selector", "").strip()
        try:
            logical_row = int(raw_selector)
        except ValueError:
            self._open_popup(f"Invalid row selection: '{raw_selector}'")
            return
        if not 0 <= logical_row < len(self._result_ids):
            self._open_popup(f"No record at row {logical_row}.")
            return
        self._detail_record_id = self._result_ids[logical_row]
        record = self._store.get_record(self._detail_record_id)
        self._fields["status_input"] = record.status.value
        self._status_texts["detail_status"] = ""
        self._screen = ScreenId.DETAIL

    def _handle_apply_status(self) -> None:
        self._begin_busy()
        assert self._detail_record_id is not None  # only reachable from RESULTS
        requested = self._fields.get("status_input", "")
        try:
            updated = self._store.update_status(self._detail_record_id, requested)
        except SimulatorError as exc:
            self._open_popup(f"Status change rejected: {exc}")
            return
        self._status_texts["detail_status"] = f"Status updated to {updated.status.value}"

    # -- navigation ---------------------------------------------------------

    def navigate(self, target: ScreenId) -> None:
        """Structural navigation; data-dependent screens need their buttons."""
        self._require_interactive()
        allowed = _NAVIGATION_MAP[self._screen]
        if target not in allowed:
            names = ", ".join(screen.value for screen in sorted(allowed)) or "none"
            raise NavigationError(
                f"cannot navigate from '{self._screen.value}' to '{target.value}' "
                f"(allowed: {names})"
            )
        self._screen = target

    def reset(self) -> None:
        """Return to a pristine main menu; always works (the escape hatch)."""
        self._screen = ScreenId.MAIN_MENU
        self._fields.clear()
        self._status_texts.clear()
        self._popup = None
        self._busy_until = 0.0
        self._result_ids = []
        self._scroll = 0
        self._detail_record_id = None

    # -- results table ------------------------------------------------------

    def table_row_count(self) -> int:
        """The count the widget *reports*: real rows plus trailing buffer."""
        self._require_interactive("results_table")
        self._visible_control("results_table", ControlType.TABLE)
        return len(self._result_ids) + self._buffer_rows

    def scroll_table(self, position: int) -> int:
        """Scroll to a logical position (clamped); returns the actual position."""
        self._require_interactive("results_table")
        self._visible_control("results_table", ControlType.TABLE)
        decision = self._faults.decide(InteractionPoint.PAGE_MOVEMENT)
        self._begin_busy()
        if decision.fault is FaultKind.STALLED_PAGINATION:
            # The scroll request is ignored; the position stays put.
            return self._scroll
        reported = len(self._result_ids) + self._buffer_rows
        max_scroll = max(0, reported - self._visible_rows)
        self._scroll = min(max(0, position), max_scroll)
        if decision.fault is FaultKind.SLOW_RESPONSE:
            self._busy_until = max(
                self._busy_until, self._clock.now() + decision.delay_seconds
            )
        return self._scroll

    def read_table_rows(self) -> tuple[TableRow, ...]:
        """The visible window, including empty placeholder rows past the end."""
        self._require_interactive("results_table")
        self._visible_control("results_table", ControlType.TABLE)
        reported = len(self._result_ids) + self._buffer_rows
        window_end = min(self._scroll + self._visible_rows, reported)
        rows: list[TableRow] = []
        for position in range(self._scroll, window_end):
            if position < len(self._result_ids):
                record = self._store.get_record(self._result_ids[position])
                rows.append(_row_from_record(record))
            else:
                rows.append(_EMPTY_ROW)
        return tuple(rows)


def _row_from_record(record: OperationalRecord) -> TableRow:
    return TableRow(
        record_id=record.record_id,
        external_ref=record.external_ref,
        amount=f"{record.amount:.2f}",
        unit=record.unit,
        period=record.period,
        status=record.status.value,
    )
