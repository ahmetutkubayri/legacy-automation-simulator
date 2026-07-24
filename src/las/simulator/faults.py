"""Deterministic fault injection for the simulated legacy system.

The simulator only *produces* faults. Detecting them, waiting them out,
or working around them is entirely the caller's concern (the driver
layer, built later). Nothing in this package reacts to a fault it
raised.

Fault kinds (semantically distinct)
-----------------------------------
* ``slow_response`` — the action completes normally but the system stays
  busy for an extra short delay, within normal operating bounds.
* ``extended_busy`` — the action is swallowed: the system goes busy far
  beyond a caller's reasonable timeout and the intended effect never
  happens.
* ``transient_control_missing`` — an otherwise valid, visible control is
  temporarily unavailable for exactly one interaction.
* ``surprise_popup`` — a modal message appears instead of the intended
  action and blocks everything until dismissed.
* ``stalled_pagination`` — a scroll request is ignored; the visible
  table position stays where it was.

Profiles (all values invented and generic)
------------------------------------------
* ``none``    — probability 0.0; completely fault-free.
* ``mild``    — probability 0.08; slow 0.2–0.8 s; extended 5–10 s.
* ``hostile`` — probability 0.35; slow 0.5–2.0 s; extended 8–20 s.

Determinism
-----------
Each :class:`FaultInjector` owns a dedicated ``random.Random(seed)``;
no module-level randomness exists. The same profile, seed, and sequence
of :meth:`FaultInjector.decide` calls always yields the same decisions,
and separate injector instances never share state. Delays are pure
numbers consumed by the system's injected clock — no wall time here.

For focused tests, :meth:`FaultInjector.force` queues an exact fault for
the next matching interaction point, bypassing probability entirely.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum


class FaultKind(StrEnum):
    EXTENDED_BUSY = "extended_busy"
    TRANSIENT_CONTROL_MISSING = "transient_control_missing"
    SURPRISE_POPUP = "surprise_popup"
    STALLED_PAGINATION = "stalled_pagination"
    SLOW_RESPONSE = "slow_response"


class InteractionPoint(StrEnum):
    CONTROL_LOOKUP = "control_lookup"
    BUTTON_PRESS = "button_press"
    QUERY_EXECUTION = "query_execution"
    PAGE_MOVEMENT = "page_movement"
    RECORD_OPENING = "record_opening"
    STATUS_APPLICATION = "status_application"


#: Which fault kinds are meaningful at each interaction point.
FAULTS_AT_POINT: dict[InteractionPoint, tuple[FaultKind, ...]] = {
    InteractionPoint.CONTROL_LOOKUP: (FaultKind.TRANSIENT_CONTROL_MISSING,),
    InteractionPoint.BUTTON_PRESS: (FaultKind.SURPRISE_POPUP, FaultKind.SLOW_RESPONSE),
    InteractionPoint.QUERY_EXECUTION: (
        FaultKind.SLOW_RESPONSE,
        FaultKind.EXTENDED_BUSY,
        FaultKind.SURPRISE_POPUP,
    ),
    InteractionPoint.PAGE_MOVEMENT: (FaultKind.SLOW_RESPONSE, FaultKind.STALLED_PAGINATION),
    InteractionPoint.RECORD_OPENING: (FaultKind.SLOW_RESPONSE, FaultKind.EXTENDED_BUSY),
    InteractionPoint.STATUS_APPLICATION: (
        FaultKind.SLOW_RESPONSE,
        FaultKind.EXTENDED_BUSY,
        FaultKind.SURPRISE_POPUP,
    ),
}

_DELAYED_KINDS = frozenset({FaultKind.SLOW_RESPONSE, FaultKind.EXTENDED_BUSY})


def _validate_range(name: str, bounds: tuple[float, float]) -> None:
    low, high = bounds
    if low < 0 or high < 0 or low > high:
        raise ValueError(f"{name} must be a (low, high) pair with 0 <= low <= high")


@dataclass(frozen=True)
class FaultProfile:
    """Immutable set of fault parameters (all values invented)."""

    name: str
    fault_probability: float
    slow_response_seconds: tuple[float, float]
    extended_busy_seconds: tuple[float, float]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("profile name must be non-empty")
        if not 0.0 <= self.fault_probability <= 1.0:
            raise ValueError("fault_probability must be between 0.0 and 1.0")
        _validate_range("slow_response_seconds", self.slow_response_seconds)
        _validate_range("extended_busy_seconds", self.extended_busy_seconds)


PROFILES: dict[str, FaultProfile] = {
    "none": FaultProfile("none", 0.0, (0.0, 0.0), (0.0, 0.0)),
    "mild": FaultProfile("mild", 0.08, (0.2, 0.8), (5.0, 10.0)),
    "hostile": FaultProfile("hostile", 0.35, (0.5, 2.0), (8.0, 20.0)),
}


@dataclass(frozen=True)
class FaultDecision:
    """The outcome of one interaction-point evaluation."""

    fault: FaultKind | None = None
    delay_seconds: float = 0.0

    @property
    def triggered(self) -> bool:
        return self.fault is not None


NO_FAULT = FaultDecision()


@dataclass(frozen=True)
class FaultRecord:
    """Audit-safe record of one triggered fault; nothing else is stored."""

    sequence: int
    fault: FaultKind
    point: InteractionPoint
    delay_seconds: float


@dataclass
class _ForcedFault:
    point: InteractionPoint | None
    fault: FaultKind
    delay_seconds: float = field(default=0.0)


class FaultInjector:
    """Centralized fault decisions for one simulated system instance.

    ``decide(point)`` is the single entry point the system calls at each
    defined interaction point; all randomness lives here. ``force()``
    queues an exact fault for deterministic tests. Triggered faults are
    counted and recorded with safe generic fields only.
    """

    def __init__(self, profile: FaultProfile | str = "none", seed: int = 0) -> None:
        if isinstance(profile, str):
            try:
                profile = PROFILES[profile]
            except KeyError:
                names = ", ".join(sorted(PROFILES))
                raise ValueError(f"unknown fault profile '{profile}' (known: {names})") from None
        self._profile = profile
        self._rng = random.Random(seed)
        self._sequence = 0
        self._records: list[FaultRecord] = []
        self._forced: list[_ForcedFault] = []

    @property
    def profile(self) -> FaultProfile:
        return self._profile

    @property
    def records(self) -> tuple[FaultRecord, ...]:
        return tuple(self._records)

    def counts(self) -> dict[FaultKind, int]:
        totals = dict.fromkeys(FaultKind, 0)
        for record in self._records:
            totals[record.fault] += 1
        return totals

    def force(
        self,
        fault: FaultKind,
        *,
        point: InteractionPoint | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        """Queue an exact fault for the next matching ``decide`` call.

        For delayed kinds with no explicit delay, the midpoint of the
        profile's configured range is used, keeping forced runs fully
        deterministic.
        """
        if point is not None and fault not in FAULTS_AT_POINT[point]:
            eligible = ", ".join(k.value for k in FAULTS_AT_POINT[point])
            raise ValueError(
                f"fault '{fault.value}' cannot occur at '{point.value}' (eligible: {eligible})"
            )
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be >= 0")
        if delay_seconds == 0.0 and fault in _DELAYED_KINDS:
            bounds = (
                self._profile.slow_response_seconds
                if fault is FaultKind.SLOW_RESPONSE
                else self._profile.extended_busy_seconds
            )
            delay_seconds = (bounds[0] + bounds[1]) / 2.0
        self._forced.append(_ForcedFault(point=point, fault=fault, delay_seconds=delay_seconds))

    def decide(self, point: InteractionPoint) -> FaultDecision:
        """Evaluate one interaction point; returns a typed decision."""
        self._sequence += 1

        forced = self._take_forced(point)
        if forced is not None:
            return self._trigger(forced.fault, point, forced.delay_seconds)

        probability = self._profile.fault_probability
        if probability <= 0.0:
            return NO_FAULT
        if self._rng.random() >= probability:
            return NO_FAULT

        kind = self._rng.choice(FAULTS_AT_POINT[point])
        delay = 0.0
        if kind is FaultKind.SLOW_RESPONSE:
            delay = self._rng.uniform(*self._profile.slow_response_seconds)
        elif kind is FaultKind.EXTENDED_BUSY:
            delay = self._rng.uniform(*self._profile.extended_busy_seconds)
        return self._trigger(kind, point, delay)

    def _take_forced(self, point: InteractionPoint) -> _ForcedFault | None:
        for index, entry in enumerate(self._forced):
            matches_point = entry.point == point
            matches_wildcard = entry.point is None and entry.fault in FAULTS_AT_POINT[point]
            if matches_point or matches_wildcard:
                del self._forced[index]
                return entry
        return None

    def _trigger(
        self, kind: FaultKind, point: InteractionPoint, delay: float
    ) -> FaultDecision:
        self._records.append(
            FaultRecord(sequence=self._sequence, fault=kind, point=point, delay_seconds=delay)
        )
        return FaultDecision(fault=kind, delay_seconds=delay)
