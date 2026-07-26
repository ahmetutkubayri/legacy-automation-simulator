"""Shared, non-test fixtures for the ingestion suites.

Deliberately not named ``test_*``: it is imported by several T9 test modules,
and importing shared data from another test module would couple those modules
together and make each one depend on the other being collectable.

Everything here mirrors the committed sample configuration's canonical fields
and aliases, so the suites exercise the same vocabulary the real feeds use.
"""

from __future__ import annotations

from las.ingest import FieldSpec, IngestSchema

#: Canonical fields for the shared probe schema. ``unit`` is optional so the
#: suites can exercise both required and optional resolution paths.
TEST_FIELDS = (
    FieldSpec("record_id"),
    FieldSpec("external_ref"),
    FieldSpec("amount"),
    FieldSpec("unit", required=False),
)

#: Invented aliases, matching those declared in ``data/samples/config.yaml``.
TEST_ALIASES = {
    "Record Id": "record_id",
    "External Reference": "external_ref",
    "Quantity": "amount",
    "Quantity Unit": "unit",
}


def schema(**overrides) -> IngestSchema:
    """Build the shared probe schema, with any field overridden per test."""
    options = {"fields": TEST_FIELDS, "aliases": dict(TEST_ALIASES)}
    options.update(overrides)
    return IngestSchema(**options)
