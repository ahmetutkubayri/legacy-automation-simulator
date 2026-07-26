"""Shared, non-test fixtures for the validation suites.

Deliberately not named ``test_*``: several T10 modules import it, and importing
shared data from another test module would couple those modules together.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from las.ingest import IngestedRow, RowProvenance
from las.validate import DEFAULT_ALLOWED_UNITS, ValidationPolicy

#: The canonical feed fields, in the order a feed schema declares them
#: (``sorted(CANONICAL_FIELDS)``), so test rows mirror real ingested rows.
FEED_FIELDS = ("amount", "external_ref", "period", "record_id", "unit")

VALID_VALUES = {
    "record_id": "OPR-00000001",
    "external_ref": "EXT-10000",
    "amount": "10.50",
    "unit": "EA",
    "period": "2026-01",
}


def make_row(
    source_row: int = 2,
    *,
    source: str = "probe.csv",
    worksheet: str | None = None,
    extras: dict[str, str | None] | None = None,
    **overrides: str | None,
) -> IngestedRow:
    """One ingested row with valid values, minus whatever a test overrides.

    Keys are inserted in :data:`FEED_FIELDS` order so the engine's canonical
    field ordering is exercised exactly as it would be on a real schema.
    """
    values: dict[str, str | None] = {}
    for name in FEED_FIELDS:
        values[name] = overrides[name] if name in overrides else VALID_VALUES[name]
    return IngestedRow(
        provenance=RowProvenance(
            source=source, source_row=source_row, worksheet=worksheet
        ),
        values=values,
        extras=extras or {},
    )


def make_policy(
    *,
    required_fields: Iterable[str] = ("record_id", "external_ref", "amount", "period"),
    record_id_pattern: str = r"^OPR-\d{8}$",
    external_ref_pattern: str = r"^EXT-\d{5}$",
    amount_min: str = "0.0",
    amount_max: str = "1000000.0",
    duplicate_key_fields: Iterable[str] = ("record_id",),
    allowed_units: Iterable[str] = DEFAULT_ALLOWED_UNITS,
    known_external_refs: Iterable[str] | None = None,
    **extra,
) -> ValidationPolicy:
    """A policy mirroring the committed sample configuration."""
    return ValidationPolicy(
        required_fields=frozenset(required_fields),
        record_id_pattern=record_id_pattern,
        external_ref_pattern=external_ref_pattern,
        amount_min=Decimal(amount_min),
        amount_max=Decimal(amount_max),
        duplicate_key_fields=tuple(duplicate_key_fields),
        allowed_units=tuple(allowed_units),
        known_external_refs=(
            None if known_external_refs is None else frozenset(known_external_refs)
        ),
        **extra,
    )


class Explosive:
    """A value whose serialization raises, proving nothing stringifies it."""

    def __repr__(self) -> str:  # pragma: no cover - must never run
        raise AssertionError("the value must never be serialized")

    def __str__(self) -> str:  # pragma: no cover - must never run
        raise AssertionError("the value must never be serialized")
