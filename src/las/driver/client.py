"""Resilient driver client for the simulated legacy system.

:class:`LegacyDriver` wraps a :class:`~las.simulator.LegacySystem` with
the discipline a real legacy UI demands: confirm state before acting,
find controls through ordered fallback candidates, wait on deadlines
instead of hoping, retry only transient conditions, and confirm the
expected end state after every operation.

Popup policy
------------
* **Expected informational popups** (passed per operation, e.g. the
  no-matches message after a query) are dismissed and returned as data.
* **Transient surprise popups** (the injected fault message) are
  dismissed and the operation retries, bounded by the retry policy.
* **Any other popup is treated as a permanent failure**: it is dismissed
  once to restore interactivity, and a :class:`DriverOperationError`
  carrying the generic popup text is raised — never swallowed.

Text scanning
-------------
:func:`collect_text` walks a generic nested control representation
iteratively (deterministic pre-order), with cycle protection and hard
depth/node limits. It reads only ``text`` string attributes — arbitrary
objects are never serialized.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from las.driver.waits import (
    DEFAULT_POLICY,
    DriverControlError,
    DriverNavigationError,
    DriverOperationError,
    DriverTimeoutError,
    RetryPolicy,
)
from las.driver.waits import (
    wait_for_control as _wait_for_control,
)
from las.driver.waits import (
    wait_for_popup as _wait_for_popup,
)
from las.driver.waits import (
    wait_for_screen as _wait_for_screen,
)
from las.driver.waits import (
    wait_until_idle as _wait_until_idle,
)
from las.simulator import (
    NO_MATCHES_MESSAGE,
    SURPRISE_POPUP_MESSAGE,
    ControlNotFoundError,
    ControlType,
    LegacySystem,
    ScreenId,
    SimulatorError,
    SystemBusyError,
)

T = TypeVar("T")

#: Hard safety limits for control-tree scanning.
MAX_SCAN_DEPTH = 8
MAX_SCAN_NODES = 200


class AuditSink(Protocol):
    """Anything accepting (stage, event, payload) — e.g. las.audit.AuditTrail."""

    def record(
        self, stage: str, event: str, payload: Mapping[str, Any] | None = None
    ) -> None: ...


@dataclass(frozen=True)
class ControlNode:
    """Generic nested control representation for text scanning."""

    text: str
    children: Sequence[ControlNode] = ()


@dataclass(frozen=True)
class OperationResult:
    """Immutable outcome of one driver operation."""

    operation: str
    screen: str
    attempts: int
    popup_text: str | None = None


def collect_text(
    nodes: Sequence[ControlNode],
    *,
    max_depth: int = MAX_SCAN_DEPTH,
    max_nodes: int = MAX_SCAN_NODES,
) -> tuple[str, ...]:
    """Deterministic pre-order text collection with cycle/size protection."""
    results: list[str] = []
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(node, 0) for node in reversed(list(nodes))]
    while stack:
        node, depth = stack.pop()
        if id(node) in seen:
            continue  # cycle: already visited, skip
        seen.add(id(node))
        if depth > max_depth:
            raise DriverOperationError(
                f"control tree exceeds the maximum depth of {max_depth}",
                operation="collect_text",
            )
        if len(seen) > max_nodes:
            raise DriverOperationError(
                f"control tree exceeds the maximum of {max_nodes} nodes",
                operation="collect_text",
            )
        text = node.text
        if isinstance(text, str) and text.strip():
            results.append(text.strip())
        for child in reversed(list(node.children)):
            stack.append((child, depth + 1))
    return tuple(results)


class LegacyDriver:
    """Bounded, state-confirming operations against one legacy system."""

    #: Ordered fallback candidates per action; the first entries are
    #: plausible alternates so the fallback path is exercised for real.
    OPEN_QUERY_CANDIDATES = ("open_records_query", "open_query")
    RUN_QUERY_CANDIDATES = ("execute_query", "run_query")

    def __init__(
        self,
        system: LegacySystem,
        policy: RetryPolicy = DEFAULT_POLICY,
        audit: AuditSink | None = None,
    ) -> None:
        self.system = system
        self.policy = policy
        self._audit = audit

    # -- observability -----------------------------------------------------

    @property
    def screen_name(self) -> str:
        return self.system.current_screen.value

    @property
    def popup_text(self) -> str | None:
        return self.system.popup_message

    def _emit(self, event: str, **payload: str | int | float | None) -> None:
        if self._audit is not None:
            safe = {key: value for key, value in payload.items() if value is not None}
            self._audit.record("driver", event, safe)

    # -- waits (policy-scoped convenience) ---------------------------------

    def wait_until_idle(self, timeout_seconds: float | None = None) -> float:
        return _wait_until_idle(
            self.system,
            timeout_seconds=(
                self.policy.operation_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            poll_interval_seconds=self.policy.poll_interval_seconds,
        )

    def wait_for_screen(self, target: ScreenId, timeout_seconds: float | None = None) -> float:
        return _wait_for_screen(
            self.system,
            target,
            timeout_seconds=(
                self.policy.operation_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            poll_interval_seconds=self.policy.poll_interval_seconds,
        )

    def wait_for_popup(self, timeout_seconds: float | None = None) -> str:
        return _wait_for_popup(
            self.system,
            timeout_seconds=(
                self.policy.operation_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            poll_interval_seconds=self.policy.poll_interval_seconds,
        )

    def wait_for_control(self, control_id: str, timeout_seconds: float | None = None) -> float:
        return _wait_for_control(
            self.system,
            control_id,
            timeout_seconds=(
                self.policy.operation_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            poll_interval_seconds=self.policy.poll_interval_seconds,
        )

    # -- control lookup ----------------------------------------------------

    def find_control(self, candidate_ids: Sequence[str]) -> str:
        """Return the first candidate visible in the current state.

        Candidates are tried in the supplied order; duplicates are
        normalized deterministically (first occurrence wins). Visibility
        is re-checked over bounded attempts to ride out screen settling,
        but a control absent from the screen model is never spun on.
        """
        candidates: list[str] = []
        for candidate in candidate_ids:
            if candidate not in candidates:
                candidates.append(candidate)
        if not candidates:
            raise DriverControlError(
                "no candidate control ids supplied", operation="find_control"
            )

        for attempt in range(1, self.policy.max_attempts + 1):
            visible = {control.control_id for control in self.system.visible_controls()}
            for candidate in candidates:
                if candidate in visible:
                    return candidate
            if attempt < self.policy.max_attempts:
                self.system.clock.sleep(self.policy.delay_for_attempt(attempt))
        raise DriverControlError(
            f"none of the candidate controls are visible: {', '.join(candidates)}",
            operation="find_control",
            attempts=self.policy.max_attempts,
            screen=self.screen_name,
        )

    def _interact(self, operation: str, action: Callable[[], T]) -> tuple[T, int]:
        """Run ``action`` with bounded retries of retryable conditions only."""
        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return action(), attempt
            except SystemBusyError as busy:
                last_error = busy
                self._emit("busy_wait", operation=operation, attempt=attempt)
                self.wait_until_idle()  # DriverTimeoutError propagates
            except ControlNotFoundError as missing:
                # The caller verified visibility, so this is transient.
                last_error = missing
                self._emit("transient_control", operation=operation, attempt=attempt)
                if attempt < self.policy.max_attempts:
                    self.system.clock.sleep(self.policy.delay_for_attempt(attempt))
        raise DriverControlError(
            f"interaction kept failing after {self.policy.max_attempts} attempts",
            operation=operation,
            attempts=self.policy.max_attempts,
            screen=self.screen_name,
        ) from last_error

    def set_field(self, control_id: str, value: str, *, operation: str = "set_field") -> None:
        self.wait_until_idle()
        self._interact(operation, lambda: self.system.set_field(control_id, value))

    def get_field(self, control_id: str, *, operation: str = "get_field") -> str:
        self.wait_until_idle()
        value, _ = self._interact(operation, lambda: self.system.get_field(control_id))
        return value

    def press(self, candidate_ids: Sequence[str], *, operation: str = "press") -> int:
        """Press the first available candidate; returns attempts used."""
        control_id = self.find_control(candidate_ids)
        self.wait_until_idle()
        _, attempts = self._interact(operation, lambda: self.system.press(control_id))
        return attempts

    # -- popup handling ----------------------------------------------------

    def handle_popup(self, *, operation: str, expected: tuple[str, ...] = ()) -> str | None:
        """Apply the documented popup policy to the currently open popup.

        Returns the popup text when it was an *expected* informational
        message, ``None`` when there was no popup or a transient surprise
        popup was dismissed, and raises :class:`DriverOperationError` for
        anything else (permanent failure — dismissed once, never ignored).
        """
        message = self.system.popup_message
        if message is None:
            return None
        self._interact(operation, lambda: self.system.press("dismiss"))
        if message in expected:
            self._emit("expected_popup", operation=operation, text=message)
            return message
        if message == SURPRISE_POPUP_MESSAGE:
            self._emit("transient_popup_dismissed", operation=operation)
            return None
        self._emit("permanent_popup", operation=operation, text=message)
        raise DriverOperationError(
            f"the system rejected the operation: {message}",
            operation=operation,
            screen=self.screen_name,
        )

    # -- text scanning -----------------------------------------------------

    def visible_texts(self) -> tuple[str, ...]:
        """Normalized visible text of the current state, for status checks.

        Covers only the active screen (or popup) — never controls from
        other screens. Values are read through the simulator API; controls
        whose value cannot be read right now contribute their label only.
        """
        nodes: list[ControlNode] = []
        if self.system.popup_message is not None:
            nodes.append(
                ControlNode("Message", (ControlNode(self.system.popup_message),))
            )
            return collect_text(nodes)
        for control in self.system.visible_controls():
            children: tuple[ControlNode, ...] = ()
            if control.control_type in (ControlType.TEXT_FIELD, ControlType.STATUS_TEXT):
                try:
                    value = self.system.get_field(control.control_id)
                except SimulatorError:
                    value = ""
                if value:
                    children = (ControlNode(value),)
            nodes.append(ControlNode(control.label, children))
        return collect_text(nodes)

    # -- resilient operations ----------------------------------------------

    def _act_and_confirm(
        self,
        operation: str,
        candidates: Sequence[str],
        *,
        expect: ScreenId,
        expected_popups: tuple[str, ...] = (),
    ) -> OperationResult:
        """Press, wait, apply popup policy, confirm the end state; bounded."""
        for attempt in range(1, self.policy.max_attempts + 1):
            self.press(candidates, operation=operation)
            self.wait_until_idle()  # extended busy -> DriverTimeoutError
            popup = self.handle_popup(operation=operation, expected=expected_popups)
            if popup is not None:
                return OperationResult(operation, self.screen_name, attempt, popup_text=popup)
            if self.system.current_screen == expect:
                self._emit("operation_ok", operation=operation, attempts=attempt)
                return OperationResult(operation, self.screen_name, attempt)
            # A transient popup was consumed or the action was swallowed:
            # the bounded loop simply tries again.
        raise DriverNavigationError(
            f"'{operation}' did not reach '{expect.value}'",
            operation=operation,
            attempts=self.policy.max_attempts,
            screen=self.screen_name,
        )

    def open_query(self) -> OperationResult:
        """From anywhere, end up on the query screen."""
        if self.system.current_screen == ScreenId.QUERY and self.system.popup_message is None:
            return OperationResult("open_query", self.screen_name, 0)
        if self.system.current_screen != ScreenId.MAIN_MENU or self.system.popup_message:
            self.recover()
        return self._act_and_confirm(
            "open_query", self.OPEN_QUERY_CANDIDATES, expect=ScreenId.QUERY
        )

    def run_query(self, external_ref: str = "", period: str = "") -> OperationResult:
        """Execute a query; a no-matches outcome is data, not an error."""
        if self.system.current_screen != ScreenId.QUERY:
            self.open_query()
        self.set_field("external_ref_input", external_ref, operation="run_query")
        self.set_field("period_input", period, operation="run_query")
        return self._act_and_confirm(
            "run_query",
            self.RUN_QUERY_CANDIDATES,
            expect=ScreenId.RESULTS,
            expected_popups=(NO_MATCHES_MESSAGE,),
        )

    def open_result(self, row: int) -> OperationResult:
        """Open the detail screen for a logical result row."""
        if self.system.current_screen != ScreenId.RESULTS:
            raise DriverNavigationError(
                "open_result requires the results screen",
                operation="open_result",
                screen=self.screen_name,
            )
        self.set_field("row_selector", str(row), operation="open_result")
        return self._act_and_confirm("open_result", ("open_detail",), expect=ScreenId.DETAIL)

    def back_to_results(self) -> OperationResult:
        return self._act_and_confirm(
            "back_to_results", ("back_to_results",), expect=ScreenId.RESULTS
        )

    def back_to_query(self) -> OperationResult:
        return self._act_and_confirm("back_to_query", ("back_to_query",), expect=ScreenId.QUERY)

    def scroll_results(self, position: int) -> int:
        """Scroll the results table; returns the *actual* position.

        A stalled-pagination fault shows up as an unchanged return value —
        detecting and working around it is the extraction algorithm's job
        (next stage); here it is simply observable.
        """
        self.wait_until_idle()
        actual, _ = self._interact("scroll_results", lambda: self.system.scroll_table(position))
        self.wait_until_idle()
        return actual

    def recover(self):
        """Recover to the main menu; see las.driver.recovery."""
        from las.driver.recovery import recover_to_main_menu

        return recover_to_main_menu(self)


# Re-exported alias so callers can catch the timeout type from one place.
__all__ = [
    "AuditSink",
    "ControlNode",
    "DriverTimeoutError",
    "LegacyDriver",
    "MAX_SCAN_DEPTH",
    "MAX_SCAN_NODES",
    "OperationResult",
    "collect_text",
]
