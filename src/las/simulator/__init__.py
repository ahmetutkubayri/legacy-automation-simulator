"""The simulated legacy system: store, screens, and state machine.

This package is fully self-contained: it must never import from the
driver, pipeline, or any other processing package. Those layers depend
on the simulator — never the reverse. An architecture test enforces
this boundary.
"""

from las.simulator.screens import (
    POPUP_SCREEN,
    SCREENS,
    Control,
    ControlType,
    ScreenDefinition,
    ScreenId,
)
from las.simulator.store import (
    STATUS_TRANSITIONS,
    AccountRecord,
    FixtureLoadError,
    InvalidStatusError,
    InvalidStatusTransitionError,
    OperationalRecord,
    RecordStatus,
    RecordStore,
    SimulatorError,
    StoreSnapshot,
    UnknownRecordError,
)
from las.simulator.system import (
    ControlNotFoundError,
    LegacySystem,
    NavigationError,
    PopupActiveError,
    SimulatedClock,
    SystemBusyError,
    SystemClock,
    TableRow,
)

__all__ = [
    "POPUP_SCREEN",
    "SCREENS",
    "STATUS_TRANSITIONS",
    "AccountRecord",
    "Control",
    "ControlNotFoundError",
    "ControlType",
    "FixtureLoadError",
    "InvalidStatusError",
    "InvalidStatusTransitionError",
    "LegacySystem",
    "NavigationError",
    "OperationalRecord",
    "PopupActiveError",
    "RecordStatus",
    "RecordStore",
    "ScreenDefinition",
    "ScreenId",
    "SimulatedClock",
    "SimulatorError",
    "StoreSnapshot",
    "SystemBusyError",
    "SystemClock",
    "TableRow",
    "UnknownRecordError",
]
