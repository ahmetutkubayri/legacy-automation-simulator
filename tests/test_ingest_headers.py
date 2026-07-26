"""T9 header normalization, schema definition, and alias resolution."""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ingest_helpers import TEST_ALIASES, TEST_FIELDS, schema
from las.config import CANONICAL_FIELDS, FeedConfig, load_config
from las.ingest import (
    ACCOUNT_LINK_SCHEMA,
    ACCOUNT_SCHEMA,
    ExtraColumnPolicy,
    FieldSpec,
    HeaderResolutionError,
    IngestSchema,
    SchemaError,
    feed_schema,
    normalize_header,
    resolve_headers,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"



def resolve(headers, **overrides):
    return resolve_headers(
        schema(**overrides), list(headers), operation="test", source="probe.csv"
    )


# --- normalization ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("record_id", "record id"),
        ("Record Id", "record id"),
        ("RECORD ID", "record id"),
        ("  record   ID  ", "record id"),
        ("record-id", "record id"),
        ("record.id", "record id"),
        ("record/id", "record id"),
        ("record\\id", "record id"),
        ("record\tid", "record id"),
        ("record id", "record id"),
        ("Ｒｅｃｏｒｄ Ｉｄ", "record id"),
        ("EXT_REF", "ext ref"),
        ("", ""),
        ("   ", ""),
        ("___", ""),
    ],
)
def test_normalization_algorithm(raw: str, expected: str) -> None:
    assert normalize_header(raw) == expected


def test_normalization_is_idempotent() -> None:
    for raw in ("Record Id", "REC_ID", "  Quantity  Unit "):
        once = normalize_header(raw)
        assert normalize_header(once) == once


# --- canonical and alias resolution -------------------------------------------


def test_canonical_header_resolves_to_itself() -> None:
    got = resolve(["record_id", "external_ref", "amount"])
    assert got.headers == ("record_id", "external_ref", "amount")
    assert got.unmapped == ()


@pytest.mark.parametrize(("alias", "canonical"), sorted(TEST_ALIASES.items()))
def test_each_explicit_alias_resolves(alias: str, canonical: str) -> None:
    required = ["record_id", "external_ref", "amount"]
    headers = [alias] + [name for name in required if name != canonical]
    got = resolve(headers)
    assert got.headers[0] == canonical


def test_every_committed_config_alias_resolves() -> None:
    """All ten aliases in the committed sample config map as declared."""
    config = load_config(SAMPLES_DIR / "config.yaml")
    total = 0
    for feed in config.feeds:
        built = feed_schema(feed)
        for raw_alias, canonical in feed.header_aliases.items():
            assert built.resolve(normalize_header(raw_alias)) == canonical
            total += 1
    assert total == 10  # five canonical fields across two committed feeds


@pytest.mark.parametrize(
    "variant", ["Record Id", "record id", "RECORD_ID", " record-ID ", "Record.Id"]
)
def test_case_whitespace_and_separator_variants_all_resolve(variant: str) -> None:
    got = resolve([variant, "external_ref", "amount"])
    assert got.headers[0] == "record_id"


def test_resolution_is_independent_of_alias_declaration_order() -> None:
    items = list(TEST_ALIASES.items())
    baseline = None
    shuffler = random.Random(7)  # test-local only; nothing under test is random
    for _ in range(12):
        shuffler.shuffle(items)
        built = IngestSchema(fields=TEST_FIELDS, aliases=dict(items))
        got = resolve_headers(
            built,
            ["Quantity", "Record Id", "External Reference", "Quantity Unit"],
            operation="test",
            source="probe.csv",
        )
        if baseline is None:
            baseline = got
        assert got == baseline
    assert baseline is not None
    assert baseline.headers == ("amount", "record_id", "external_ref", "unit")


def test_lookup_covers_canonical_names_and_aliases() -> None:
    built = schema()
    assert built.resolve("record id") == "record_id"
    assert built.resolve("quantity") == "amount"
    assert built.resolve("nothing like a field") is None


# --- rejected header rows -----------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "___", None])
def test_blank_header_is_rejected_with_its_column(blank: str | None) -> None:
    with pytest.raises(HeaderResolutionError, match="blank") as excinfo:
        resolve(["record_id", blank, "external_ref", "amount"])
    assert excinfo.value.column == 2


def test_empty_header_row_is_rejected() -> None:
    with pytest.raises(HeaderResolutionError, match="no columns"):
        resolve([])


def test_duplicate_raw_header_is_rejected() -> None:
    with pytest.raises(HeaderResolutionError, match="repeats") as excinfo:
        resolve(["record_id", "external_ref", "amount", "record_id"])
    assert excinfo.value.column == 4


def test_header_duplicated_only_after_normalization_is_rejected() -> None:
    with pytest.raises(HeaderResolutionError, match="repeats") as excinfo:
        resolve(["record_id", "external_ref", "amount", "  RECORD-ID "])
    assert excinfo.value.column == 4


def test_alias_sharing_a_canonical_name_is_caught_as_a_duplicate_header() -> None:
    """'Record Id' normalizes to the canonical name, so it repeats column 1."""
    with pytest.raises(HeaderResolutionError, match="repeats") as excinfo:
        resolve(["record_id", "Record Id ", "external_ref", "amount"])
    assert excinfo.value.column == 2


def test_alias_cannot_smuggle_in_a_second_column_for_one_field() -> None:
    """'Quantity' and 'amount' differ as text but mean the same field."""
    with pytest.raises(HeaderResolutionError, match="already claimed") as excinfo:
        resolve(["Quantity", "amount", "record_id", "external_ref"])
    assert excinfo.value.column == 2
    assert "amount" in str(excinfo.value)


def test_missing_required_field_is_rejected_naming_the_fields() -> None:
    with pytest.raises(HeaderResolutionError, match="absent") as excinfo:
        resolve(["record_id"])
    message = str(excinfo.value)
    assert "amount" in message
    assert "external_ref" in message
    assert "unit" not in message  # optional, so never demanded


def test_optional_field_may_be_absent() -> None:
    got = resolve(["record_id", "external_ref", "amount"])
    assert "unit" not in got.headers
    assert got.unmapped == ()


# --- extra-column policy ------------------------------------------------------


def test_retain_policy_reports_extra_columns() -> None:
    got = resolve(
        ["record_id", "external_ref", "amount", "Reported By"],
        extra_columns=ExtraColumnPolicy.RETAIN,
    )
    assert got.unmapped == ("reported by",)
    assert got.columns == ("record_id", "external_ref", "amount", None)


def test_ignore_policy_still_reports_extra_columns() -> None:
    got = resolve(
        ["record_id", "external_ref", "amount", "Reported By"],
        extra_columns=ExtraColumnPolicy.IGNORE,
    )
    assert got.unmapped == ("reported by",)  # reported, never silent


def test_reject_policy_refuses_extra_columns() -> None:
    with pytest.raises(HeaderResolutionError, match="rejects extra columns") as excinfo:
        resolve(
            ["record_id", "external_ref", "amount", "Reported By"],
            extra_columns=ExtraColumnPolicy.REJECT,
        )
    assert excinfo.value.column == 4


# --- schema definition errors -------------------------------------------------


def test_schema_needs_at_least_one_field() -> None:
    with pytest.raises(SchemaError, match="at least one"):
        IngestSchema(fields=())


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_canonical_field_name_is_rejected(bad: str) -> None:
    with pytest.raises(SchemaError, match="non-blank"):
        FieldSpec(bad)


def test_field_that_normalizes_to_nothing_is_rejected() -> None:
    with pytest.raises(SchemaError, match="normalizes to nothing"):
        IngestSchema(fields=(FieldSpec("_"),))


def test_duplicate_canonical_fields_are_rejected() -> None:
    with pytest.raises(SchemaError, match="duplicate canonical"):
        IngestSchema(fields=(FieldSpec("amount"), FieldSpec("amount")))


def test_fields_indistinguishable_after_normalization_are_rejected() -> None:
    with pytest.raises(SchemaError, match="indistinguishable"):
        IngestSchema(fields=(FieldSpec("record_id"), FieldSpec("record-id")))


def test_alias_targeting_an_undeclared_field_is_rejected() -> None:
    with pytest.raises(SchemaError, match="does not declare"):
        IngestSchema(fields=TEST_FIELDS, aliases={"Whatever": "warehouse_code"})


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_alias_key_is_rejected(bad: str) -> None:
    with pytest.raises(SchemaError, match="non-blank"):
        IngestSchema(fields=TEST_FIELDS, aliases={bad: "amount"})


def test_alias_normalizing_to_nothing_is_rejected() -> None:
    with pytest.raises(SchemaError, match="normalizes to nothing"):
        IngestSchema(fields=TEST_FIELDS, aliases={"-.-": "amount"})


def test_two_aliases_colliding_after_normalization_are_rejected() -> None:
    with pytest.raises(SchemaError, match="collides"):
        IngestSchema(
            fields=TEST_FIELDS,
            aliases={"Qty Total": "amount", "qty-total": "record_id"},
        )


def test_alias_that_would_shadow_a_canonical_name_is_rejected() -> None:
    """An alias must not redirect a canonical header to a different field."""
    with pytest.raises(SchemaError, match="collides"):
        IngestSchema(fields=TEST_FIELDS, aliases={"Record Id": "amount"})


def test_alias_restating_its_own_canonical_name_is_harmless() -> None:
    built = IngestSchema(fields=TEST_FIELDS, aliases={"Record-Id": "record_id"})
    assert built.resolve("record id") == "record_id"


# --- feed schemas from configuration ------------------------------------------


def test_feed_schema_uses_canonical_fields_and_config_aliases() -> None:
    feed = FeedConfig(
        name="feed_probe",
        path="data/samples/feed_probe.csv",
        header_aliases={"Record Id": "record_id"},
    )
    built = feed_schema(feed)
    assert set(built.field_names) == set(CANONICAL_FIELDS)
    assert built.field_names == tuple(sorted(CANONICAL_FIELDS))  # order is stable
    assert built.required_field_names == tuple(sorted(CANONICAL_FIELDS))
    assert built.resolve("record id") == "record_id"
    assert built.name == "feed_probe"


def test_feed_schema_can_relax_required_fields() -> None:
    feed = FeedConfig(name="feed_probe", path="data/samples/feed_probe.csv")
    built = feed_schema(feed, required_fields=["record_id"])
    assert built.required_field_names == ("record_id",)
    assert set(built.optional_field_names) == set(CANONICAL_FIELDS) - {"record_id"}


def test_feed_schema_rejects_non_canonical_required_fields() -> None:
    feed = FeedConfig(name="feed_probe", path="data/samples/feed_probe.csv")
    with pytest.raises(SchemaError, match="not canonical"):
        feed_schema(feed, required_fields=["warehouse_code"])


def test_master_data_schemas_match_the_committed_fixture_headers() -> None:
    assert ACCOUNT_SCHEMA.extra_columns is ExtraColumnPolicy.REJECT
    assert ACCOUNT_LINK_SCHEMA.extra_columns is ExtraColumnPolicy.REJECT
    assert ACCOUNT_LINK_SCHEMA.field_names == ("external_ref", "account_ref")
    assert "account_ref" in ACCOUNT_SCHEMA.field_names


# --- immutability -------------------------------------------------------------


def test_schema_and_field_objects_are_immutable() -> None:
    built = schema()
    with pytest.raises(FrozenInstanceError):
        built.extra_columns = ExtraColumnPolicy.REJECT  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        built.fields[0].name = "other"  # type: ignore[misc]


def test_schema_alias_and_lookup_mappings_cannot_be_mutated() -> None:
    built = schema()
    with pytest.raises(TypeError):
        built.aliases["New"] = "amount"  # type: ignore[index]
    with pytest.raises(TypeError):
        built.lookup["new"] = "amount"  # type: ignore[index]


def test_schema_ignores_later_mutation_of_the_alias_dict_passed_in() -> None:
    supplied = dict(TEST_ALIASES)
    built = IngestSchema(fields=TEST_FIELDS, aliases=supplied)
    supplied["Sneaky"] = "amount"
    assert built.resolve("sneaky") is None


def test_header_resolution_is_immutable() -> None:
    got = resolve(["record_id", "external_ref", "amount"])
    with pytest.raises(FrozenInstanceError):
        got.headers = ()  # type: ignore[misc]
    assert got.width == 3
