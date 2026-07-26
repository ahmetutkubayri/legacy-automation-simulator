"""T10 hardening: mapping-order independence, metadata safety, no cascades.

Three properties that the first T10 pass did not actually guarantee:

* field ranking was taken from the first row's value mapping, so two rows
  holding identical values under keys inserted in opposite orders produced
  differently ordered issues. Ranking is now owned by the policy.
* issue metadata was screened only as text, so a nested container or a
  path-shaped string could slip through. It now passes an explicit scalar
  allowlist.
* referential integrity and duplicate detection could pile a second, misleading
  issue on a defect an earlier rule had already reported.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from openpyxl import Workbook

from las.ingest import FieldSpec, IngestedRow, IngestSchema, RowProvenance, read_source
from las.validate import (
    BUILT_IN_BATCH_RULES,
    BUILT_IN_FIELD_RULES,
    CODE_DUPLICATE_KEY,
    CODE_EXTERNAL_REF_FORMAT_INVALID,
    CODE_EXTERNAL_REF_UNKNOWN,
    CODE_RECORD_ID_FORMAT_INVALID,
    CODE_REQUIRED_VALUE_MISSING,
    CODE_UNIT_NOT_SUPPORTED,
    PolicyError,
    RuleSet,
    ValidationError,
    ValidationIssue,
    ValidationPolicy,
    validate_rows,
)
from las.validate.policy import DEFAULT_FIELD_ORDER
from validate_helpers import Explosive, make_policy

POLICY = make_policy()
PROVENANCE = RowProvenance(source="probe.csv", source_row=7)

#: A row failing on four different fields at once, so ordering is observable.
BROKEN = {
    "record_id": "REC-1",
    "external_ref": "EXT-10000",
    "amount": "not-a-number",
    "unit": "PALLET",
    "period": "2026-13",
}
SORTED_KEYS = ("amount", "external_ref", "period", "record_id", "unit")
REVERSED_KEYS = tuple(reversed(SORTED_KEYS))


def row_with_key_order(order, source_row: int = 2, **overrides) -> IngestedRow:
    """One row whose value mapping is built in a chosen insertion order."""
    values = {**BROKEN, **overrides}
    return IngestedRow(
        provenance=RowProvenance(source="probe.csv", source_row=source_row),
        values={name: values[name] for name in order},
    )


# =============================================================================
# 1. mapping insertion order cannot affect anything
# =============================================================================


def test_opposite_mapping_orders_give_identical_results() -> None:
    """The reproduction case: same values, opposite key insertion order."""
    forward = validate_rows([row_with_key_order(SORTED_KEYS)], POLICY)
    reverse = validate_rows([row_with_key_order(REVERSED_KEYS)], POLICY)

    assert forward.issues == reverse.issues
    assert [i.field for i in forward.issues] == [i.field for i in reverse.issues]
    assert forward.rows[0].codes == reverse.rows[0].codes
    assert forward.rows[0].outcome is reverse.rows[0].outcome
    assert forward.summary == reverse.summary
    assert forward == reverse


def test_issue_order_follows_the_policy_field_order_not_the_row() -> None:
    reverse = validate_rows([row_with_key_order(REVERSED_KEYS)], POLICY)
    ranked = [i.field for i in reverse.issues]
    known = [name for name in POLICY.field_order if name in ranked]
    assert ranked == known


@pytest.mark.parametrize(
    "order",
    [
        SORTED_KEYS,
        REVERSED_KEYS,
        ("unit", "amount", "record_id", "period", "external_ref"),
        ("period", "record_id", "unit", "external_ref", "amount"),
    ],
)
def test_every_mapping_order_produces_the_same_batch(order: tuple[str, ...]) -> None:
    baseline = validate_rows([row_with_key_order(SORTED_KEYS)], POLICY)
    assert validate_rows([row_with_key_order(order)], POLICY) == baseline


def test_the_first_row_having_a_reversed_mapping_changes_nothing() -> None:
    """The old implementation let row zero decide for the whole batch."""
    mixed = [
        row_with_key_order(REVERSED_KEYS, 2),
        row_with_key_order(SORTED_KEYS, 3),
    ]
    uniform = [
        row_with_key_order(SORTED_KEYS, 2),
        row_with_key_order(SORTED_KEYS, 3),
    ]
    assert validate_rows(mixed, POLICY).issues == validate_rows(uniform, POLICY).issues


def test_rows_using_different_mapping_orders_within_one_batch_agree() -> None:
    mixed = [
        row_with_key_order(SORTED_KEYS, 2),
        row_with_key_order(REVERSED_KEYS, 3),
        row_with_key_order(("unit", "period", "amount", "external_ref", "record_id"), 4),
    ]
    result = validate_rows(mixed, POLICY)
    per_row = [
        [i.field for i in result.issues if i.source_row == n] for n in (2, 3, 4)
    ]
    assert per_row[0] == per_row[1] == per_row[2]


def test_mapping_order_independence_survives_shuffled_rule_declaration() -> None:
    shuffler = random.Random(17)
    baseline = validate_rows([row_with_key_order(SORTED_KEYS)], POLICY)
    field_rules = list(BUILT_IN_FIELD_RULES)
    for _ in range(8):
        shuffler.shuffle(field_rules)
        rule_set = RuleSet(
            field_rules=tuple(field_rules), batch_rules=BUILT_IN_BATCH_RULES
        )
        for order in (SORTED_KEYS, REVERSED_KEYS):
            got = validate_rows([row_with_key_order(order)], POLICY, rule_set=rule_set)
            assert got.issues == baseline.issues


def test_an_empty_batch_needs_no_row_to_establish_an_order() -> None:
    """Ranking comes from the policy, so nothing is inferred and nothing fails."""
    result = validate_rows([], POLICY)
    assert result.issues == ()
    assert result.rows == ()
    assert result.summary.total_rows == 0
    assert POLICY.field_rank("amount") == (0, POLICY.field_order.index("amount"), "")


# --- the field order itself ----------------------------------------------------


def test_the_default_field_order_is_explicit_and_sorted() -> None:
    assert DEFAULT_FIELD_ORDER == (
        "account_ref",
        "amount",
        "external_ref",
        "period",
        "record_id",
        "unit",
    )
    assert tuple(sorted(DEFAULT_FIELD_ORDER)) == DEFAULT_FIELD_ORDER


def test_field_order_is_defensively_copied_from_the_caller() -> None:
    supplied = ["record_id", "amount"]
    policy = make_policy(field_order=supplied)
    assert isinstance(policy.field_order, tuple)
    supplied.append("injected")
    assert policy.field_order == ("record_id", "amount")


def test_a_duplicated_field_in_the_order_is_rejected() -> None:
    with pytest.raises(PolicyError, match="repeats"):
        make_policy(field_order=("record_id", "amount", "record_id"))


@pytest.mark.parametrize("bad", [(), ("",), ("   ",), ("amount", " ")])
def test_an_unusable_field_order_is_rejected(bad: tuple[str, ...]) -> None:
    with pytest.raises(PolicyError):
        make_policy(field_order=bad)


def test_a_custom_field_order_changes_issue_ordering_deterministically() -> None:
    policy = make_policy(
        field_order=("unit", "record_id", "period", "external_ref", "amount")
    )
    result = validate_rows([row_with_key_order(SORTED_KEYS)], policy)
    assert [i.field for i in result.issues] == ["unit", "record_id", "period", "amount"]


def test_a_field_absent_from_the_order_ranks_after_every_named_field() -> None:
    """Unknown fields still sort deterministically, by their own name."""
    policy = make_policy(field_order=("amount",), required_fields=("amount", "zebra", "kiwi"))
    row = IngestedRow(
        provenance=PROVENANCE,
        values={"zebra": None, "amount": None, "kiwi": None},
    )
    result = validate_rows([row], policy)
    assert [i.field for i in result.issues] == ["amount", "kiwi", "zebra"]


def test_row_scope_issues_still_rank_after_unknown_fields() -> None:
    policy = make_policy(field_order=("amount",), required_fields=("zebra",))
    rows = [
        IngestedRow(
            provenance=RowProvenance(source="probe.csv", source_row=n),
            values={"zebra": None, "record_id": "OPR-00000001"},
        )
        for n in (2, 3)
    ]
    result = validate_rows(rows, policy)
    first = [i.field for i in result.issues if i.source_row == 2]
    assert first == ["zebra", None]  # unknown field, then the row-scope duplicate


def test_field_rank_is_a_total_order_over_every_case() -> None:
    policy = make_policy(field_order=("amount", "unit"))
    ranks = [
        policy.field_rank("amount"),
        policy.field_rank("unit"),
        policy.field_rank("aardvark"),
        policy.field_rank("zebra"),
        policy.field_rank(None),
    ]
    assert ranks == sorted(ranks)


# --- cross-format, mappings built differently ----------------------------------


def test_equivalent_csv_and_xlsx_rows_agree_despite_different_mappings(
    tmp_path: Path,
) -> None:
    """The two readers build value mappings independently; results must agree."""
    headers = ["unit", "period", "amount", "external_ref", "record_id"]
    values = ["PALLET", "2026-13", "not-a-number", "EXT-10000", "REC-1"]

    csv_path = tmp_path / "probe.csv"
    csv_path.write_text(
        ",".join(headers) + "\n" + ",".join(values) + "\n", encoding="utf-8", newline=""
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(list(reversed(headers)))
    sheet.append(list(reversed(values)))
    xlsx_path = tmp_path / "probe.xlsx"
    workbook.save(xlsx_path)
    workbook.close()

    schema = IngestSchema(
        fields=(
            FieldSpec("amount"),
            FieldSpec("external_ref"),
            FieldSpec("period"),
            FieldSpec("record_id"),
            FieldSpec("unit", required=False),
        )
    )
    from_csv = validate_rows(read_source(csv_path, schema).rows, POLICY)
    from_xlsx = validate_rows(read_source(xlsx_path, schema).rows, POLICY)

    shape = lambda r: [(i.source_row, i.field, i.code) for i in r.issues]  # noqa: E731
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.summary == from_xlsx.summary


# =============================================================================
# 2. metadata is a scalar allowlist, deeply
# =============================================================================


def issue(**kwargs) -> ValidationIssue:
    return ValidationIssue.create(CODE_UNIT_NOT_SUPPORTED, PROVENANCE, **kwargs)


@pytest.mark.parametrize(
    "value",
    [
        ["a", "b"],
        {"nested": "dict"},
        {"a", "b"},
        ("a", "b"),
        bytearray(b"x"),
        b"bytes",
        Path("probe.csv"),
        1.5,
        Explosive(),
    ],
)
def test_metadata_rejects_every_non_scalar_value(value: object) -> None:
    with pytest.raises(ValidationError, match="is not allowed"):
        issue(metadata={"probe": value})  # type: ignore[dict-item]


def test_metadata_rejection_names_the_type_and_never_the_value() -> None:
    """An object whose serialization raises must still be reported safely."""
    with pytest.raises(ValidationError, match="Explosive") as excinfo:
        issue(metadata={"probe": Explosive()})  # type: ignore[dict-item]
    assert "probe" in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    [
        "/" + "opt/secret/feed.csv",
        "D" + ":" + "\\records",
        "\\\\" + "fileserver\\share",
        "https" + "://" + "example.com",
        "mailto" + ":" + "ops-team",
        "value\nwith newline",
        "value\twith tab",
        "value\x00with nul",
        "bidi‮override",
        "zero​width",
        "x" * 200,
        "",
    ],
)
def test_metadata_rejects_unsafe_text(value: str) -> None:
    with pytest.raises(ValidationError, match="not a safe categorical token"):
        issue(metadata={"probe": value})


def test_metadata_rejection_does_not_echo_the_unsafe_text() -> None:
    secret = "/" + "opt/secret/feed.csv"
    with pytest.raises(ValidationError) as excinfo:
        issue(metadata={"probe": secret})
    message = str(excinfo.value)
    assert secret not in message
    assert "secret" not in message


@pytest.mark.parametrize("key", ["Upper", "with space", "1leading", "", "x" * 80, "a-b"])
def test_metadata_rejects_unsafe_keys(key: str) -> None:
    with pytest.raises(ValidationError, match="metadata keys"):
        issue(metadata={key: "EA"})


def test_metadata_copies_the_caller_mapping_and_freezes_it() -> None:
    supplied: dict[str, object] = {"allowed_units": "EA"}
    got = issue(metadata=supplied)  # type: ignore[arg-type]

    supplied["allowed_units"] = "TAMPERED"
    supplied["injected"] = "x"
    assert got.metadata == {"allowed_units": "EA"}

    with pytest.raises(TypeError):
        got.metadata["injected"] = "x"  # type: ignore[index]
    with pytest.raises(AttributeError):
        got.metadata.clear()  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        got.metadata.update({"a": "b"})  # type: ignore[attr-defined]


def test_no_nested_container_is_ever_shallowly_wrapped() -> None:
    """Rejection, not wrapping: a nested list cannot reach the result at all."""
    nested = ["mutable"]
    with pytest.raises(ValidationError):
        issue(metadata={"probe": nested})  # type: ignore[dict-item]
    nested.append("changed")  # the caller still owns it; nothing was retained


def test_every_engine_emitted_metadata_value_is_an_allowed_scalar() -> None:
    from validate_helpers import make_row

    result = validate_rows([make_row(unit="PALLET")], POLICY)
    assert result.issues
    for value in result.issues[0].metadata.values():
        assert value is None or isinstance(value, bool | int | str)


# =============================================================================
# 3. no redundant cascading issues
# =============================================================================


def make_row_for(**overrides) -> IngestedRow:
    from validate_helpers import make_row

    return make_row(**overrides)


def test_a_missing_reference_is_reported_missing_only() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    result = validate_rows([make_row_for(external_ref=None)], policy)
    assert result.rows[0].codes == (CODE_REQUIRED_VALUE_MISSING,)
    assert CODE_EXTERNAL_REF_UNKNOWN not in result.rows[0].codes
    assert CODE_EXTERNAL_REF_FORMAT_INVALID not in result.rows[0].codes


def test_a_blank_reference_is_reported_missing_only() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    result = validate_rows([make_row_for(external_ref="   ")], policy)
    assert result.rows[0].codes == (CODE_REQUIRED_VALUE_MISSING,)


def test_a_malformed_reference_is_reported_malformed_only() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    result = validate_rows([make_row_for(external_ref="EXT-1")], policy)
    assert result.rows[0].codes == (CODE_EXTERNAL_REF_FORMAT_INVALID,)
    assert CODE_EXTERNAL_REF_UNKNOWN not in result.rows[0].codes


def test_referential_integrity_runs_only_on_a_well_formed_present_reference() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    present_and_valid = validate_rows([make_row_for(external_ref="EXT-90000")], policy)
    assert present_and_valid.rows[0].codes == (CODE_EXTERNAL_REF_UNKNOWN,)
    known = validate_rows([make_row_for(external_ref="EXT-10000")], policy)
    assert known.is_clean


def test_duplicate_detection_skips_rows_whose_key_is_missing() -> None:
    rows = [make_row_for(record_id=None), make_row_for(record_id=None)]
    result = validate_rows(rows, POLICY)
    assert CODE_DUPLICATE_KEY not in [i.code for i in result.issues]
    assert all(row.codes == (CODE_REQUIRED_VALUE_MISSING,) for row in result.rows)


def test_duplicate_detection_skips_rows_whose_key_is_malformed() -> None:
    """Two rows sharing an invalid identifier are not "the same record"."""
    from validate_helpers import make_row

    rows = [make_row(2, record_id="REC-1"), make_row(3, record_id="REC-1")]
    result = validate_rows(rows, POLICY)
    assert CODE_DUPLICATE_KEY not in [i.code for i in result.issues]
    for row in result.rows:
        assert row.codes == (CODE_RECORD_ID_FORMAT_INVALID,)


def test_duplicate_detection_still_fires_on_well_formed_repeated_keys() -> None:
    from validate_helpers import make_row

    rows = [make_row(2), make_row(3)]
    result = validate_rows(rows, POLICY)
    assert [row.codes for row in result.rows] == [
        (CODE_DUPLICATE_KEY,),
        (CODE_DUPLICATE_KEY,),
    ]


def test_a_malformed_key_part_in_a_composite_key_suppresses_duplication() -> None:
    from validate_helpers import make_row

    policy = make_policy(duplicate_key_fields=("record_id", "period"))
    rows = [make_row(2, period="2026-13"), make_row(3, period="2026-13")]
    result = validate_rows(rows, policy)
    assert CODE_DUPLICATE_KEY not in [i.code for i in result.issues]


def test_a_key_field_whose_own_check_passes_still_supports_duplication() -> None:
    """``unit`` is checked by membership rather than a pattern, and passes."""
    from validate_helpers import make_row

    policy = make_policy(duplicate_key_fields=("unit",))
    rows = [make_row(2), make_row(3)]
    result = validate_rows(rows, policy)
    assert [i.code for i in result.issues] == [CODE_DUPLICATE_KEY, CODE_DUPLICATE_KEY]


def test_one_defect_never_emits_the_same_code_twice_for_one_field() -> None:
    from validate_helpers import make_row

    rows = [make_row(2, record_id="REC-1", amount="oops", unit="PALLET")]
    result = validate_rows(rows, POLICY)
    markers = [(i.source_row, i.field, i.code) for i in result.issues]
    assert len(markers) == len(set(markers))


def test_the_full_cascade_on_a_maximally_broken_row_is_minimal() -> None:
    """Every defect reported once, and nothing reported twice."""
    policy = make_policy(known_external_refs=("EXT-10000",))
    row = IngestedRow(
        provenance=PROVENANCE,
        values={
            "amount": None,
            "external_ref": None,
            "period": None,
            "record_id": None,
            "unit": "PALLET",
        },
    )
    result = validate_rows([row], policy)
    assert result.rows[0].codes == (
        CODE_REQUIRED_VALUE_MISSING,  # amount
        CODE_REQUIRED_VALUE_MISSING,  # external_ref
        CODE_REQUIRED_VALUE_MISSING,  # period
        CODE_REQUIRED_VALUE_MISSING,  # record_id
        CODE_UNIT_NOT_SUPPORTED,
    )
    assert result.rows[0].error_count == 5
    assert result.rows[0].warning_count == 0


def test_an_unsupported_unit_key_suppresses_duplication() -> None:
    """The key check reuses each field's own rule, not a looser copy."""
    from validate_helpers import make_row

    policy = make_policy(duplicate_key_fields=("unit",))
    rows = [make_row(2, unit="PALLET"), make_row(3, unit="PALLET")]
    result = validate_rows(rows, policy)
    assert CODE_DUPLICATE_KEY not in [i.code for i in result.issues]
    assert all(CODE_UNIT_NOT_SUPPORTED in row.codes for row in result.rows)


def test_an_out_of_range_amount_key_suppresses_duplication() -> None:
    from validate_helpers import make_row

    policy = make_policy(duplicate_key_fields=("amount",))
    rows = [make_row(2, amount="-1"), make_row(3, amount="-1")]
    result = validate_rows(rows, policy)
    assert CODE_DUPLICATE_KEY not in [i.code for i in result.issues]


def test_a_policy_is_still_frozen_after_the_new_fields() -> None:
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        POLICY.field_order = ()  # type: ignore[misc]
    assert isinstance(POLICY, ValidationPolicy)
