"""Resilient driver for the simulated legacy system.

Depends only on the standard library and the public ``las.simulator``
API. Higher pipeline layers depend on this package — never the reverse.
"""

from las.driver.client import (
    AuditSink,
    ControlNode,
    LegacyDriver,
    OperationResult,
    collect_text,
)
from las.driver.recovery import RecoveryResult, recover_to_main_menu
from las.driver.waits import (
    DEFAULT_POLICY,
    DriverControlError,
    DriverError,
    DriverNavigationError,
    DriverOperationError,
    DriverRecoveryError,
    DriverTimeoutError,
    RetryPolicy,
    wait_for_control,
    wait_for_popup,
    wait_for_screen,
    wait_until_idle,
)

__all__ = [
    "DEFAULT_POLICY",
    "AuditSink",
    "ControlNode",
    "DriverControlError",
    "DriverError",
    "DriverNavigationError",
    "DriverOperationError",
    "DriverRecoveryError",
    "DriverTimeoutError",
    "LegacyDriver",
    "OperationResult",
    "RecoveryResult",
    "RetryPolicy",
    "collect_text",
    "recover_to_main_menu",
    "wait_for_control",
    "wait_for_popup",
    "wait_for_screen",
    "wait_until_idle",
]
