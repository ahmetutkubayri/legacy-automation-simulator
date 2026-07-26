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
from las.driver.tables import (
    DEFAULT_EMPTY_ROW_CUTOFF,
    DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
    DriverExtractionError,
    DriverWriteBackError,
    ExtractedRow,
    ExtractionResult,
    StatusUpdateResult,
    extract_all_rows,
    extract_query_results,
    update_status,
)
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
    "DEFAULT_EMPTY_ROW_CUTOFF",
    "DEFAULT_EXTRACTION_TIMEOUT_SECONDS",
    "DEFAULT_POLICY",
    "AuditSink",
    "ControlNode",
    "DriverControlError",
    "DriverError",
    "DriverExtractionError",
    "DriverNavigationError",
    "DriverOperationError",
    "DriverRecoveryError",
    "DriverTimeoutError",
    "DriverWriteBackError",
    "ExtractedRow",
    "ExtractionResult",
    "LegacyDriver",
    "OperationResult",
    "RecoveryResult",
    "RetryPolicy",
    "StatusUpdateResult",
    "collect_text",
    "extract_all_rows",
    "extract_query_results",
    "recover_to_main_menu",
    "update_status",
    "wait_for_control",
    "wait_for_popup",
    "wait_for_screen",
    "wait_until_idle",
]
