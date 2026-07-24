"""Bounded recovery to the main menu.

Algorithm (every step bounded, business data never touched):

1. If busy, wait out a *normal* busy state; a busy state outlasting the
   policy deadline (an extended-busy condition) fails with
   :class:`DriverRecoveryError` — that situation needs a human or a
   longer policy, not silent resets.
2. Dismiss any blocking popup, a bounded number of times.
3. Walk back through the visible navigation buttons
   (detail → results → query → main menu), dismissing surprise popups
   along the way, all bounded.
4. Only if the normal path fails, use the simulator's explicit
   ``reset()`` escape hatch — the documented final fallback.
5. Verify the final state is the main menu.

The result is an immutable record of what happened: starting screen,
final screen, the ordered steps attempted, and whether the reset
fallback was required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from las.driver.waits import DriverError, DriverRecoveryError, DriverTimeoutError
from las.simulator import ScreenId

if TYPE_CHECKING:
    from las.driver.client import LegacyDriver

#: The visible navigation control leading one step back from each screen.
_BACK_BUTTONS: dict[ScreenId, str] = {
    ScreenId.DETAIL: "back_to_results",
    ScreenId.RESULTS: "back_to_query",
    ScreenId.QUERY: "back_to_menu",
}

#: Maximum walk-back hops (detail -> results -> query -> menu, plus slack).
_MAX_HOPS = 4


@dataclass(frozen=True)
class RecoveryResult:
    """Immutable, safe summary of one recovery run."""

    start_screen: str
    final_screen: str
    steps: tuple[str, ...]
    used_reset: bool


def _dismiss_popups(driver: LegacyDriver, steps: list[str]) -> None:
    """Dismiss whatever popup is open, a bounded number of times."""
    for _ in range(driver.policy.max_attempts):
        if driver.system.popup_message is None:
            return
        steps.append("dismiss_popup")
        driver._interact("recover", lambda: driver.system.press("dismiss"))
    if driver.system.popup_message is not None:
        raise DriverRecoveryError(
            "a popup kept reappearing during recovery",
            operation="recover",
            attempts=driver.policy.max_attempts,
            screen=driver.screen_name,
        )


def recover_to_main_menu(driver: LegacyDriver) -> RecoveryResult:
    """Return the system to a verified main menu; see module docstring."""
    system = driver.system
    steps: list[str] = []
    start_screen = system.current_screen.value

    # 1. A normal busy state is waited out; extended busy is a hard failure.
    if system.is_busy:
        steps.append("wait_idle")
        try:
            driver.wait_until_idle()
        except DriverTimeoutError as exc:
            raise DriverRecoveryError(
                "system stayed busy beyond the recovery deadline",
                operation="recover",
                screen=system.current_screen.value,
                timeout_seconds=driver.policy.operation_timeout_seconds,
            ) from exc

    used_reset = False
    try:
        # 2. Clear any blocking popup.
        _dismiss_popups(driver, steps)

        # 3. Walk back via normal visible navigation controls.
        for _ in range(_MAX_HOPS):
            if system.current_screen == ScreenId.MAIN_MENU:
                break
            button = _BACK_BUTTONS[system.current_screen]
            steps.append(f"press_{button}")
            driver.press((button,), operation="recover")
            driver.wait_until_idle()
            _dismiss_popups(driver, steps)
    except DriverError:
        # 4. Normal path failed: the documented final fallback.
        steps.append("reset")
        system.reset()
        used_reset = True

    # 5. Verify.
    if system.current_screen != ScreenId.MAIN_MENU or system.popup_message is not None:
        steps.append("reset")
        system.reset()
        used_reset = True
    if system.current_screen != ScreenId.MAIN_MENU:
        raise DriverRecoveryError(
            "recovery could not reach the main menu",
            operation="recover",
            screen=system.current_screen.value,
        )

    return RecoveryResult(
        start_screen=start_screen,
        final_screen=system.current_screen.value,
        steps=tuple(steps),
        used_reset=used_reset,
    )
