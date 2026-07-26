"""T10 engine: row and batch validation, total ordering, purity, determinism."""

from __future__ import annotations

import random

import pytest

from las.ingest import IngestedRow, RowProvenance
from las.validate import (
    BUILT_IN_BATCH_RULES,
    BUILT_IN_FIELD_RULES,
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_AMOUNT_OUT_OF_RANGE,
    CODE_DUPLICATE_KEY,
    CODE_EXTERNAL_REF_FORMAT_INVALID,
    CODE_EXTERNAL_REF_UNKNOWN,
    CODE_PERIOD_FORMAT_INVALID,
    CODE_RECORD_ID_FORMAT_INVALID,
    CODE_REQUIRED_VALUE_MISSING,
    CODE_UNIT_NOT_SUPPORTED,
    RowOutcome,
    RuleSet,
    Severity,
    validate_rows,
)
from validate_helpers import make_policy, make_row

POLICY = make_policy()


def codes_of(result, index: int = 0) -> tuple[str, ...]:
    return result.rows[index].codes


# --- a fully valid row --------------------------------------------------------


def test_a_valid_row_produces_nothing() -> None:
    result = validate_rows([make_row()], POLICY)
    assert result.is_clean
    assert result.issues == ()
    assert result.rows[0].outcome is RowOutcome.VALID
    assert result.summary.total_rows == 1
    assert result.summary.valid_rows == 1


def test_the_rules_that_ran_are_reported_in_execution_order() -> None:
    result = validate_rows([make_row()], POLICY)
    assert result.rule_ids[0] == "required_value"
    assert result.rule_ids[-1] == "duplicate_key"


# --- one issue at a time ------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"record_id": None}, CODE_REQUIRED_VALUE_MISSING),
        ({"record_id": "REC-1"}, CODE_RECORD_ID_FORMAT_INVALID),
        ({"external_ref": None}, CODE_REQUIRED_VALUE_MISSING),
        ({"external_ref": "EXT-1"}, CODE_EXTERNAL_REF_FORMAT_INVALID),
        ({"amount": None}, CODE_REQUIRED_VALUE_MISSING),
        ({"amount": "not-a-number"}, CODE_AMOUNT_NOT_DECIMAL),
        ({"amount": "-42.50"}, CODE_AMOUNT_OUT_OF_RANGE),
        ({"unit": "PALLET"}, CODE_UNIT_NOT_SUPPORTED),
        ({"period": "2026-1"}, CODE_PERIOD_FORMAT_INVALID),
        ({"period": None}, CODE_REQUIRED_VALUE_MISSING),
    ],
)
def test_each_defect_yields_exactly_its_own_code(overrides: dict, expected: str) -> None:
    result = validate_rows([make_row(**overrides)], POLICY)
    assert codes_of(result) == (expected,)


def test_a_missing_amount_is_reported_missing_and_not_also_malformed() -> None:
    """One defect, one issue: domain rules skip absent values."""
    result = validate_rows([make_row(amount=None)], POLICY)
    assert codes_of(result) == (CODE_REQUIRED_VALUE_MISSING,)
    assert CODE_AMOUNT_NOT_DECIMAL not in codes_of(result)


def test_whitespace_only_counts_as_missing_rather_than_malformed() -> None:
    result = validate_rows([make_row(amount="   ", unit="  ")], POLICY)
    assert codes_of(result) == (CODE_REQUIRED_VALUE_MISSING,)
    # ``unit`` is not required and is blank, so no unit issue is raised either.
    assert CODE_UNIT_NOT_SUPPORTED not in codes_of(result)


def test_a_blank_optional_field_produces_nothing_at_all() -> None:
    policy = make_policy(required_fields=("record_id",))
    result = validate_rows([make_row(unit=None, amount=None, period=None)], policy)
    assert result.is_clean


def test_a_whitespace_padded_unit_is_unsupported_rather_than_missing() -> None:
    """Padding is content, so it reaches the domain rule and fails there."""
    result = validate_rows([make_row(unit=" EA ")], POLICY)
    assert codes_of(result) == (CODE_UNIT_NOT_SUPPORTED,)


def test_unsupported_unit_carries_the_allowed_set_as_safe_metadata() -> None:
    result = validate_rows([make_row(unit="PALLET")], POLICY)
    issue = result.issues[0]
    assert issue.metadata == {"allowed_units": "BOX, EA, KG"}
    assert "PALLET" not in issue.message
    assert "PALLET" not in str(issue.metadata)


# --- multiple independent issues ----------------------------------------------


def test_a_row_with_several_defects_collects_every_one_but_appears_once() -> None:
    result = validate_rows(
        [make_row(record_id="REC-1", amount="12.34.56", unit="PALLET", period="2026-13")],
        POLICY,
    )
    assert result.summary.total_rows == 1
    assert len(result.rows) == 1
    # Canonical field order for a feed schema is sorted(CANONICAL_FIELDS), so
    # the issues arrive amount, period, record_id, unit.
    assert codes_of(result) == (
        CODE_AMOUNT_NOT_DECIMAL,
        "period_month_invalid",
        CODE_RECORD_ID_FORMAT_INVALID,
        CODE_UNIT_NOT_SUPPORTED,
    )
    assert result.rows[0].outcome is RowOutcome.INVALID
    assert result.rows[0].error_count == 4


def test_errors_and_warnings_are_counted_separately_on_one_row() -> None:
    policy = make_policy(known_external_refs=("EXT-99999",))
    rows = [make_row(2, amount="not-a-number"), make_row(3, amount="not-a-number")]
    result = validate_rows(rows, policy)
    # Both rows share record_id, so both are duplicates, and both references
    # are outside the reference set.
    for row in result.rows:
        assert row.error_count == 1
        assert row.warning_count == 2
        assert row.outcome is RowOutcome.INVALID
    assert result.summary.issues_by_severity == {"error": 2, "warn": 4}


# --- issue ordering -----------------------------------------------------------


def test_issues_sort_by_row_then_canonical_field_then_priority_then_code() -> None:
    rows = [
        make_row(2, record_id="REC-1", amount="oops"),
        make_row(3, unit="PALLET", period="2026-13"),
    ]
    result = validate_rows(rows, POLICY)
    ordered = [(i.source_row, i.field, i.code) for i in result.issues]
    assert ordered == [
        # row 2: amount precedes record_id in the schema's field order
        (2, "amount", CODE_AMOUNT_NOT_DECIMAL),
        (2, "record_id", CODE_RECORD_ID_FORMAT_INVALID),
        # row 3: period precedes unit
        (3, "period", "period_month_invalid"),
        (3, "unit", CODE_UNIT_NOT_SUPPORTED),
    ]


def test_row_scope_issues_sort_after_every_field_scope_issue_for_that_row() -> None:
    rows = [make_row(2, amount="oops"), make_row(3, amount="oops")]
    result = validate_rows(rows, POLICY)
    first_row = [i for i in result.issues if i.source_row == 2]
    assert [i.field for i in first_row] == ["amount", None]
    assert first_row[-1].code == CODE_DUPLICATE_KEY


def test_ordering_survives_shuffled_rule_declaration() -> None:
    shuffler = random.Random(5)
    rows = [make_row(2, record_id="REC-1", amount="oops", unit="PALLET")]
    baseline = validate_rows(rows, POLICY)
    field_rules = list(BUILT_IN_FIELD_RULES)
    for _ in range(8):
        shuffler.shuffle(field_rules)
        shuffled = RuleSet(
            field_rules=tuple(field_rules), batch_rules=BUILT_IN_BATCH_RULES
        )
        got = validate_rows(rows, POLICY, rule_set=shuffled)
        assert got.issues == baseline.issues
        assert got.rows == baseline.rows
        assert got.summary == baseline.summary


def test_repeated_validation_of_the_same_input_is_equal() -> None:
    rows = [make_row(2, amount="oops"), make_row(3, unit="PALLET")]
    first = validate_rows(rows, POLICY)
    second = validate_rows(rows, POLICY)
    assert first == second
    assert first.issues == second.issues
    assert first.summary == second.summary


def test_no_duplicate_issue_is_emitted_for_one_field_of_one_row() -> None:
    rows = [make_row(2, record_id="REC-1")]
    result = validate_rows(rows, POLICY)
    markers = [(i.source_row, i.field, i.code) for i in result.issues]
    assert len(markers) == len(set(markers))


# --- duplicates ---------------------------------------------------------------


def test_duplicate_keys_flag_every_copy_and_remove_none() -> None:
    rows = [make_row(2), make_row(3), make_row(4, record_id="OPR-00000002")]
    result = validate_rows(rows, POLICY)
    assert result.summary.total_rows == 3  # nothing filtered
    assert [row.codes for row in result.rows] == [
        (CODE_DUPLICATE_KEY,),
        (CODE_DUPLICATE_KEY,),
        (),
    ]
    for row in result.rows[:2]:
        assert row.outcome is RowOutcome.VALID_WITH_WARNINGS


def test_duplicate_rows_stay_distinct_by_provenance() -> None:
    rows = [make_row(2), make_row(3)]
    result = validate_rows(rows, POLICY)
    assert [row.provenance.source_row for row in result.rows] == [2, 3]
    assert result.issues[0].source_row == 2
    assert result.issues[1].source_row == 3


def test_a_blank_key_component_cannot_establish_duplication() -> None:
    rows = [make_row(2, record_id=None), make_row(3, record_id=None)]
    result = validate_rows(rows, POLICY)
    assert CODE_DUPLICATE_KEY not in [i.code for i in result.issues]


def test_duplicate_detection_honours_a_multi_field_key() -> None:
    policy = make_policy(duplicate_key_fields=("record_id", "period"))
    rows = [make_row(2), make_row(3, period="2026-02"), make_row(4)]
    result = validate_rows(rows, policy)
    assert [bool(row.codes) for row in result.rows] == [True, False, True]


# --- referential integrity ----------------------------------------------------


def test_reference_set_membership_is_checked_when_supplied() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    ok = validate_rows([make_row(external_ref="EXT-10000")], policy)
    assert ok.is_clean
    orphan = validate_rows([make_row(external_ref="EXT-90000")], policy)
    assert orphan.rows[0].codes == (CODE_EXTERNAL_REF_UNKNOWN,)
    assert orphan.rows[0].outcome is RowOutcome.VALID_WITH_WARNINGS


def test_without_a_reference_set_the_rule_stays_silent() -> None:
    """No set means the question is unanswerable, so nothing is claimed."""
    result = validate_rows([make_row(external_ref="EXT-90000")], make_policy())
    assert result.is_clean


def test_a_malformed_reference_is_not_also_reported_as_unknown() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    result = validate_rows([make_row(external_ref="EXT-1")], policy)
    assert result.rows[0].codes == (CODE_EXTERNAL_REF_FORMAT_INVALID,)


def test_an_orphan_reference_never_makes_a_row_invalid() -> None:
    """Resolution belongs to later stages, so this is a warning by design."""
    policy = make_policy(known_external_refs=("EXT-10000",))
    result = validate_rows([make_row(external_ref="EXT-90000")], policy)
    assert result.rows[0].outcome is not RowOutcome.INVALID
    assert result.issues[0].severity is Severity.WARN


# --- batches ------------------------------------------------------------------


def test_an_empty_batch_is_handled() -> None:
    result = validate_rows([], POLICY)
    assert result.rows == ()
    assert result.issues == ()
    assert result.summary.total_rows == 0
    assert result.summary.issues_by_severity == {"error": 0, "warn": 0}
    assert result.source == "unknown"


def test_a_batch_where_every_row_is_valid() -> None:
    rows = [make_row(2), make_row(3, record_id="OPR-00000002")]
    result = validate_rows(rows, POLICY)
    assert result.summary.valid_rows == 2
    assert result.summary.invalid_rows == 0
    assert result.is_clean


def test_a_batch_where_every_row_is_invalid() -> None:
    rows = [
        make_row(2, record_id="REC-1"),
        make_row(3, record_id="REC-2", amount="oops"),
    ]
    result = validate_rows(rows, POLICY)
    assert result.summary.invalid_rows == 2
    assert result.summary.valid_rows == 0


def test_a_mixed_batch_reports_consistent_counts() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    rows = [
        make_row(2, record_id="OPR-00000001"),
        make_row(3, record_id="OPR-00000002", amount="oops"),
        make_row(4, record_id="OPR-00000003", external_ref="EXT-90000"),
    ]
    result = validate_rows(rows, policy)
    summary = result.summary
    assert summary.total_rows == 3
    assert summary.valid_rows == 1
    assert summary.rows_with_warnings == 1
    assert summary.invalid_rows == 1
    assert summary.valid_rows + summary.rows_with_warnings + summary.invalid_rows == 3
    assert summary.total_issues == len(result.issues)
    assert sum(summary.issues_by_code.values()) == summary.total_issues
    assert sum(summary.issues_by_severity.values()) == summary.total_issues


def test_batch_ordering_follows_input_order_not_severity() -> None:
    rows = [make_row(2, record_id="OPR-00000001"), make_row(3, record_id="REC-1")]
    result = validate_rows(rows, POLICY)
    assert [row.index for row in result.rows] == [0, 1]
    assert [row.provenance.source_row for row in result.rows] == [2, 3]


def test_source_and_worksheet_are_taken_from_provenance_when_not_supplied() -> None:
    rows = [make_row(2, source="feed_probe.xlsx", worksheet="records")]
    result = validate_rows(rows, POLICY)
    assert result.source == "feed_probe.xlsx"
    assert result.worksheet == "records"


def test_explicit_source_and_worksheet_override_provenance() -> None:
    rows = [make_row(2, source="feed_probe.csv")]
    result = validate_rows(rows, POLICY, source="explicit", worksheet="sheet")
    assert result.source == "explicit"
    assert result.worksheet == "sheet"


# --- purity -------------------------------------------------------------------


def test_validation_never_mutates_the_rows_it_is_given() -> None:
    rows = [make_row(2, amount="oops", extras={"reported by": "Demo Account 000001"})]
    before_values = dict(rows[0].values)
    before_extras = dict(rows[0].extras)
    before_provenance = rows[0].provenance

    validate_rows(rows, POLICY)

    assert dict(rows[0].values) == before_values
    assert dict(rows[0].extras) == before_extras
    assert rows[0].provenance == before_provenance
    with pytest.raises(TypeError):
        rows[0].values["amount"] = "changed"  # type: ignore[index]


def test_the_validated_row_holds_the_very_same_ingested_row() -> None:
    row = make_row()
    result = validate_rows([row], POLICY)
    assert result.rows[0].row is row


def test_validation_does_not_mutate_the_policy_or_rule_set() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    rule_set = RuleSet(field_rules=BUILT_IN_FIELD_RULES, batch_rules=BUILT_IN_BATCH_RULES)
    before = (policy.required_fields, policy.allowed_units, rule_set.rule_ids)
    validate_rows([make_row(amount="oops")], policy, rule_set=rule_set)
    assert (policy.required_fields, policy.allowed_units, rule_set.rule_ids) == before


def test_a_schema_without_a_field_simply_skips_its_rules() -> None:
    """Master-data rows carry no amount, so amount rules never apply."""
    row = IngestedRow(
        provenance=RowProvenance(source="account_links.csv", source_row=2),
        values={"external_ref": "EXT-10000", "account_ref": "ACC-100000"},
    )
    policy = make_policy(
        required_fields=("external_ref",), duplicate_key_fields=("external_ref",)
    )
    result = validate_rows([row], policy)
    assert result.is_clean


def test_an_account_reference_is_checked_when_the_schema_carries_one() -> None:
    row = IngestedRow(
        provenance=RowProvenance(source="account_links.csv", source_row=2),
        values={"external_ref": "EXT-10000", "account_ref": "ACC-1"},
    )
    policy = make_policy(
        required_fields=("external_ref",), duplicate_key_fields=("external_ref",)
    )
    result = validate_rows([row], policy)
    assert result.rows[0].codes == ("account_ref_format_invalid",)


def test_unit_and_period_rules_can_be_switched_off_by_policy() -> None:
    """Master-data policies disable them; a feed row proves they go quiet."""
    policy = make_policy(check_units=False, check_period=False)
    result = validate_rows([make_row(unit="PALLET", period="2026-13")], policy)
    assert result.is_clean


def test_unit_and_period_rules_are_on_by_default() -> None:
    result = validate_rows([make_row(unit="PALLET", period="2026-13")], POLICY)
    assert set(result.rows[0].codes) == {CODE_UNIT_NOT_SUPPORTED, "period_month_invalid"}


def test_a_rule_listing_one_field_twice_still_emits_one_issue() -> None:
    """The engine's duplicate guard, exercised through a deliberate rule defect."""
    from las.validate import FieldRule

    def always_fails(value, policy):
        return CODE_AMOUNT_NOT_DECIMAL

    rule_set = RuleSet(
        field_rules=(
            FieldRule(
                rule_id="doubled",
                priority=1,
                codes=(CODE_AMOUNT_NOT_DECIMAL,),
                check=always_fails,
                fields=("amount", "amount"),
            ),
        )
    )
    result = validate_rows([make_row()], POLICY, rule_set=rule_set)
    assert result.rows[0].codes == (CODE_AMOUNT_NOT_DECIMAL,)
    assert len(result.issues) == 1
