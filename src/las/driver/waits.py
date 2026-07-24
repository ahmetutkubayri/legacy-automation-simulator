"""Driver foundations: error hierarchy, retry policy, deadline-based waits.

Everything here runs on the *simulator's injected clock* — the driver
never touches wall-clock time directly, so tests advance time instantly
and deterministically.

Retryability classification (used across the driver package)
------------------------------------------------------------
RETRYABLE — transient conditions where a bounded further attempt can
legitimately succeed:

* the system reporting busy (``SystemBusyError``) — cleared by waiting;
* a control that *is visible* on the current screen but fails on
  interaction (transient unavailability). The driver confirms visibility
  first, so a genuinely absent control is never spun on.

NON-RETRYABLE — deterministic outcomes where another attempt cannot
change anything:

* invalid user input (bad row selections, malformed values);
* invalid status transitions and unknown records;
* navigation the screen model does not allow;
* programming errors (``ValueError`` and friends).

Every retry loop in this package is a bounded ``for`` over
``RetryPolicy.max_attempts``; every wait is bounded by a deadline.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, fields

from las.simulator import LegacySystem, ScreenId


class DriverError(Exception):
    """Base driver error carrying only safe, generic context fields."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        attempts: int | None = None,
        screen: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        context = []
        if operation is not None:
            context.append(f"operation={operation}")
        if attempts is not None:
            context.append(f"attempts={attempts}")
        if screen is not None:
            context.append(f"screen={screen}")
        if timeout_seconds is not None:
            context.append(f"timeout={timeout_seconds:g}s")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)
        self.operation = operation
        self.attempts = attempts
        self.screen = screen
        self.timeout_seconds = timeout_seconds


class DriverTimeoutError(DriverError):
    """A deadline expired while waiting on the system."""


class DriverControlError(DriverError):
    """A control could not be found or kept failing on interaction."""


class DriverNavigationError(DriverError):
    """An operation did not reach its expected screen."""


class DriverOperationError(DriverError):
    """An operation failed for a non-retryable reason (e.g. rejected input)."""


class DriverRecoveryError(DriverError):
    """Recovery to the main menu could not be completed."""


@dataclass(frozen=True)
class RetryPolicy:
    """Immutable bounded-retry parameters (all values invented defaults)."""

    max_attempts: int = 3
    initial_delay_seconds: float = 0.1
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 2.0
    operation_timeout_seconds: float = 10.0
    poll_interval_seconds: float = 0.05

    def __post_init__(self) -> None:
        for spec in fields(self):
            value = getattr(self, spec.name)
            if not math.isfinite(value):
                raise ValueError(f"{spec.name} must be finite")
            if value < 0:
                raise ValueError(f"{spec.name} must be non-negative")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be at least 1")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")

    def delay_for_attempt(self, attempt: int) -> float:
        """Backoff delay after the given 1-based attempt, capped at the max."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        raw = self.initial_delay_seconds * self.backoff_multiplier ** (attempt - 1)
        return min(raw, self.max_delay_seconds)


DEFAULT_POLICY = RetryPolicy()


def _await_condition(
    system: LegacySystem,
    predicate: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> float:
    """Poll ``predicate`` until true or the deadline passes; returns elapsed.

    The final sleep is truncated to the remaining time so the deadline is
    never overshot by more than one poll evaluation.
    """
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    clock = system.clock
    start = clock.now()
    deadline = start + timeout_seconds
    if predicate():
        return 0.0
    while clock.now() < deadline:
        clock.sleep(min(poll_interval_seconds, deadline - clock.now()))
        if predicate():
            return clock.now() - start
    raise DriverTimeoutError(
        f"timed out waiting for {description}",
        screen=system.current_screen.value,
        timeout_seconds=timeout_seconds,
    )


def wait_until_idle(
    system: LegacySystem,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
) -> float:
    """Wait for the busy state to clear; returns elapsed simulated seconds."""
    return _await_condition(
        system,
        lambda: not system.is_busy,
        description="the system to become idle",
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def wait_for_popup(
    system: LegacySystem,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
) -> str:
    """Wait for an expected popup to appear; returns its message text."""
    _await_condition(
        system,
        lambda: system.popup_message is not None,
        description="a popup to appear",
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    message = system.popup_message
    return message if message is not None else ""


def wait_for_screen(
    system: LegacySystem,
    target: ScreenId,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
) -> float:
    """Wait until ``target`` is the active screen and the system is idle."""
    return _await_condition(
        system,
        lambda: system.current_screen == target and not system.is_busy,
        description=f"screen '{target.value}' to become active",
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def wait_for_control(
    system: LegacySystem,
    control_id: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
) -> float:
    """Wait until a control is visible in the current state and system idle."""
    return _await_condition(
        system,
        lambda: not system.is_busy
        and any(control.control_id == control_id for control in system.visible_controls()),
        description=f"control '{control_id}' to become visible",
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
