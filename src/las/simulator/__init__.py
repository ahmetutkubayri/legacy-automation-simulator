"""The simulated legacy system: store, screens, and state machine.

This package is fully self-contained: it must never import from the
driver, pipeline, or any other processing package. Those layers depend
on the simulator — never the reverse. An architecture test enforces
this boundary.
"""

from las.simulator.faults import (
    FAULTS_AT_POINT,
    PROFILES,
    FaultDecision,
    FaultInjector,
    FaultKind,
    FaultProfile,
    FaultRecord,
    InteractionPoint,
)
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
    NO_MATCHES_MESSAGE,
    SURPRISE_POPUP_MESSAGE,
    Clock,
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
    "FAULTS_AT_POINT",
    "NO_MATCHES_MESSAGE",
    "POPUP_SCREEN",
    "PROFILES",
    "SURPRISE_POPUP_MESSAGE",
    "Clock",
    "SCREENS",
    "STATUS_TRANSITIONS",
    "AccountRecord",
    "Control",
    "ControlNotFoundError",
    "ControlType",
    "FaultDecision",
    "FaultInjector",
    "FaultKind",
    "FaultProfile",
    "FaultRecord",
    "FixtureLoadError",
    "InteractionPoint",
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
