"""T10 result models: codes, severities, deep immutability, safe context."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from las.ingest import RowProvenance, SourceLabelError
from las.validate import (
    CODE_DUPLICATE_KEY,
    CODE_REQUIRED_VALUE_MISSING,
    CODE_UNIT_NOT_SUPPORTED,
    MESSAGE_BY_CODE,
    SEVERITY_BY_CODE,
    SEVERITY_RANK,
    BatchValidationResult,
    RowOutcome,
    Severity,
    ValidatedRow,
    ValidationError,
    ValidationIssue,
    ValidationPolicy,
    ValidationSummary,
    summarize,
)
from validate_helpers import make_policy, make_row

PROVENANCE = RowProvenance(source="probe.csv", source_row=7)


def issue(code: str = CODE_REQUIRED_VALUE_MISSING, **kwargs) -> ValidationIssue:
    return ValidationIssue.create(code, PROVENANCE, **kwargs)


# --- the code registry --------------------------------------------------------


def test_every_code_has_a_severity_and_a_fixed_message() -> None:
    assert set(SEVERITY_BY_CODE) == set(MESSAGE_BY_CODE)
    for code, severity in SEVERITY_BY_CODE.items():
        assert isinstance(severity, Severity)
        assert MESSAGE_BY_CODE[code]
        assert code == code.lower()
        assert " " not in code


def test_only_two_severities_exist_and_they_rank_deterministically() -> None:
    assert set(Severity) == {Severity.ERROR, Severity.WARN}
    assert SEVERITY_RANK[Severity.ERROR] < SEVERITY_RANK[Severity.WARN]


def test_unresolved_relationship_codes_are_warnings_not_errors() -> None:
    """Whether a reference resolves, and which duplicate wins, are later calls."""
    assert SEVERITY_BY_CODE["external_ref_unknown"] is Severity.WARN
    assert SEVERITY_BY_CODE[CODE_DUPLICATE_KEY] is Severity.WARN


def test_registry_mappings_are_read_only() -> None:
    for mapping in (SEVERITY_BY_CODE, MESSAGE_BY_CODE, SEVERITY_RANK):
        with pytest.raises(TypeError):
            mapping["injected"] = Severity.ERROR  # type: ignore[index]


# --- issues -------------------------------------------------------------------


def test_issue_carries_only_safe_provenance_context() -> None:
    got = issue(field_name="amount")
    assert got.code == CODE_REQUIRED_VALUE_MISSING
    assert got.severity is Severity.ERROR
    assert got.message == "required value is missing"
    assert got.source == "probe.csv"
    assert got.source_row == 7
    assert got.field == "amount"
    assert got.worksheet is None


def test_issue_preserves_worksheet_provenance() -> None:
    got = ValidationIssue.create(
        CODE_REQUIRED_VALUE_MISSING,
        RowProvenance(source="probe.xlsx", source_row=3, worksheet="records"),
    )
    assert got.worksheet == "records"


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown issue code"):
        ValidationIssue.create("no_such_code", PROVENANCE)


def test_issue_cannot_be_built_with_a_mismatched_severity_or_message() -> None:
    with pytest.raises(ValidationError, match="does not carry that severity"):
        ValidationIssue(
            code=CODE_REQUIRED_VALUE_MISSING,
            severity=Severity.WARN,
            message=MESSAGE_BY_CODE[CODE_REQUIRED_VALUE_MISSING],
            source="probe.csv",
            source_row=1,
        )
    with pytest.raises(ValidationError, match="fixed message template"):
        ValidationIssue(
            code=CODE_REQUIRED_VALUE_MISSING,
            severity=Severity.ERROR,
            message="something a developer typed by hand",
            source="probe.csv",
            source_row=1,
        )


def test_issue_rejects_a_non_one_based_row() -> None:
    with pytest.raises(ValidationError, match="one-based"):
        ValidationIssue(
            code=CODE_REQUIRED_VALUE_MISSING,
            severity=Severity.ERROR,
            message=MESSAGE_BY_CODE[CODE_REQUIRED_VALUE_MISSING],
            source="probe.csv",
            source_row=0,
        )


def test_issue_metadata_accepts_the_scalar_allowlist() -> None:
    ok = issue(
        CODE_UNIT_NOT_SUPPORTED,
        metadata={"allowed_units": "BOX, EA, KG", "count": 3, "flagged": True, "extra": None},
    )
    assert ok.metadata == {
        "allowed_units": "BOX, EA, KG",
        "count": 3,
        "extra": None,
        "flagged": True,
    }


def test_issue_metadata_keys_are_sorted_regardless_of_insertion_order() -> None:
    forward = issue(CODE_UNIT_NOT_SUPPORTED, metadata={"aaa": 1, "zzz": 2})
    reverse = issue(CODE_UNIT_NOT_SUPPORTED, metadata={"zzz": 2, "aaa": 1})
    assert list(forward.metadata) == list(reverse.metadata) == ["aaa", "zzz"]
    assert forward == reverse


def test_issue_metadata_is_copied_then_frozen() -> None:
    supplied = {"allowed_units": "EA"}
    got = issue(CODE_UNIT_NOT_SUPPORTED, metadata=supplied)
    supplied["allowed_units"] = "TAMPERED"
    supplied["injected"] = "x"
    assert got.metadata == {"allowed_units": "EA"}
    with pytest.raises(TypeError):
        got.metadata["injected"] = "x"  # type: ignore[index]


def test_issue_is_frozen_and_compares_by_value() -> None:
    with pytest.raises(FrozenInstanceError):
        issue().code = "other"  # type: ignore[misc]
    assert issue(field_name="amount") == issue(field_name="amount")


def test_issue_never_retains_a_path_object() -> None:
    got = issue(field_name="amount")
    for value in (getattr(got, name) for name in vars(got)):
        assert not isinstance(value, Path)


def test_issue_cannot_be_given_an_unsafe_source_label() -> None:
    """Provenance already refuses a path-shaped label, so issues cannot carry one."""
    with pytest.raises(SourceLabelError):
        RowProvenance(source="/" + "opt/secret/feed.csv", source_row=1)


# --- validated rows -----------------------------------------------------------


def test_row_with_no_issues_is_valid() -> None:
    row = ValidatedRow(row=make_row(), index=0)
    assert row.outcome is RowOutcome.VALID
    assert row.error_count == 0
    assert row.warning_count == 0
    assert row.codes == ()


def test_row_with_only_warnings_is_valid_with_warnings() -> None:
    row = ValidatedRow(row=make_row(), index=0, issues=(issue(CODE_DUPLICATE_KEY),))
    assert row.outcome is RowOutcome.VALID_WITH_WARNINGS
    assert row.error_count == 0
    assert row.warning_count == 1


def test_a_single_error_makes_a_row_invalid_regardless_of_warnings() -> None:
    row = ValidatedRow(
        row=make_row(),
        index=0,
        issues=(issue(CODE_DUPLICATE_KEY), issue(CODE_REQUIRED_VALUE_MISSING)),
    )
    assert row.outcome is RowOutcome.INVALID
    assert row.error_count == 1
    assert row.warning_count == 1


def test_validated_row_preserves_the_ingested_row_untouched() -> None:
    source = make_row(source_row=5, worksheet="records")
    row = ValidatedRow(row=source, index=0)
    assert row.row is source
    assert row.provenance.source_row == 5
    assert row.provenance.worksheet == "records"
    with pytest.raises(TypeError):
        row.row.values["amount"] = "changed"  # type: ignore[index]


def test_validated_row_issue_sequence_is_a_tuple_even_from_a_list() -> None:
    supplied = [issue()]
    row = ValidatedRow(row=make_row(), index=0, issues=supplied)
    assert isinstance(row.issues, tuple)
    supplied.append(issue(CODE_DUPLICATE_KEY))
    assert len(row.issues) == 1
    with pytest.raises(AttributeError):
        row.issues.append(issue())  # type: ignore[attr-defined]


def test_validated_row_rejects_a_negative_index() -> None:
    with pytest.raises(ValidationError, match="zero-based"):
        ValidatedRow(row=make_row(), index=-1)


# --- summaries ----------------------------------------------------------------


def test_summary_counts_and_orders_deterministically() -> None:
    rows = (
        ValidatedRow(row=make_row(2), index=0),
        ValidatedRow(row=make_row(3), index=1, issues=(issue(CODE_DUPLICATE_KEY),)),
        ValidatedRow(row=make_row(4), index=2, issues=(issue(),)),
    )
    got = summarize(rows, [i for row in rows for i in row.issues])

    assert got.total_rows == 3
    assert got.valid_rows == 1
    assert got.rows_with_warnings == 1
    assert got.invalid_rows == 1
    assert got.total_issues == 2
    # Severity keys follow the declared rank, and every severity appears.
    assert list(got.issues_by_severity) == ["error", "warn"]
    assert got.issues_by_severity == {"error": 1, "warn": 1}
    # Code keys are sorted, so the mapping never depends on insertion order.
    assert list(got.issues_by_code) == sorted(got.issues_by_code)
    assert got.completed is True


def test_summary_of_an_empty_batch_still_reports_every_severity() -> None:
    got = summarize((), ())
    assert got.total_rows == 0
    assert got.total_issues == 0
    assert got.issues_by_severity == {"error": 0, "warn": 0}
    assert got.issues_by_code == {}


def test_summary_mappings_cannot_be_mutated() -> None:
    got = summarize((), ())
    for mapping in (got.issues_by_severity, got.issues_by_code):
        with pytest.raises(TypeError):
            mapping["injected"] = 1  # type: ignore[index]
        with pytest.raises(AttributeError):
            mapping.clear()  # type: ignore[attr-defined]


def test_summary_copies_caller_mappings_before_freezing() -> None:
    severity = {"error": 1}
    code = {"required_value_missing": 1}
    got = ValidationSummary(
        total_rows=1,
        valid_rows=0,
        rows_with_warnings=0,
        invalid_rows=1,
        total_issues=1,
        issues_by_severity=severity,
        issues_by_code=code,
    )
    severity["error"] = 99
    code["injected"] = 1
    assert got.issues_by_severity == {"error": 1}
    assert got.issues_by_code == {"required_value_missing": 1}


# --- batch results ------------------------------------------------------------


def test_batch_result_coerces_caller_lists_to_tuples() -> None:
    rows = [ValidatedRow(row=make_row(), index=0)]
    issues: list[ValidationIssue] = []
    rule_ids = ["required_value"]
    got = BatchValidationResult(
        source="probe.csv",
        rows=rows,
        issues=issues,
        summary=summarize(rows, issues),
        rule_ids=rule_ids,
    )
    for value in (got.rows, got.issues, got.rule_ids):
        assert isinstance(value, tuple)

    rows.append(ValidatedRow(row=make_row(3), index=1))
    issues.append(issue())
    rule_ids.append("injected")
    assert got.row_count == 1
    assert got.issues == ()
    assert got.rule_ids == ("required_value",)

    for value in (got.rows, got.issues, got.rule_ids):
        with pytest.raises(AttributeError):
            value.append("x")  # type: ignore[attr-defined]


def test_batch_result_helpers() -> None:
    warned = ValidatedRow(row=make_row(3), index=1, issues=(issue(CODE_DUPLICATE_KEY),))
    rows = (ValidatedRow(row=make_row(2), index=0), warned)
    all_issues = tuple(i for row in rows for i in row.issues)
    got = BatchValidationResult(
        source="probe.csv", rows=rows, issues=all_issues, summary=summarize(rows, all_issues)
    )
    assert got.is_clean is False
    assert got.rows_with_outcome(RowOutcome.VALID_WITH_WARNINGS) == (warned,)
    assert got.rows_with_outcome(RowOutcome.INVALID) == ()
    assert got.issues_with_code(CODE_DUPLICATE_KEY) == all_issues
    assert got.issues_with_code("amount_not_decimal") == ()


def test_a_clean_batch_reports_itself_clean() -> None:
    rows = (ValidatedRow(row=make_row(), index=0),)
    got = BatchValidationResult(
        source="probe.csv", rows=rows, issues=(), summary=summarize(rows, ())
    )
    assert got.is_clean is True


# --- policy immutability ------------------------------------------------------


def test_policy_freezes_every_collection_it_is_given() -> None:
    required = ["record_id", "amount"]
    duplicate = ["record_id"]
    units = ["EA", "KG"]
    refs = ["EXT-10000"]
    policy = ValidationPolicy(
        required_fields=frozenset(required),
        record_id_pattern=r"OPR-[0-9]{8}",
        external_ref_pattern=r"EXT-[0-9]{5}",
        amount_min=Decimal("0"),
        amount_max=Decimal("10"),
        duplicate_key_fields=duplicate,
        allowed_units=units,
        known_external_refs=refs,
    )
    assert isinstance(policy.required_fields, frozenset)
    assert isinstance(policy.duplicate_key_fields, tuple)
    assert isinstance(policy.allowed_units, tuple)
    assert isinstance(policy.known_external_refs, frozenset)

    required.append("injected")
    duplicate.append("injected")
    units.append("PALLET")
    refs.append("EXT-99999")
    assert policy.required_fields == frozenset({"record_id", "amount"})
    assert policy.duplicate_key_fields == ("record_id",)
    assert policy.allowed_units == ("EA", "KG")
    assert policy.known_external_refs == frozenset({"EXT-10000"})


def test_policy_sorts_allowed_units_for_deterministic_reporting() -> None:
    policy = make_policy(allowed_units=("KG", "BOX", "EA", "EA"))
    assert policy.allowed_units == ("BOX", "EA", "KG")
    assert policy.allowed_units_text == "BOX, EA, KG"


def test_policy_is_frozen() -> None:
    policy = make_policy()
    with pytest.raises(FrozenInstanceError):
        policy.amount_min = Decimal("5")  # type: ignore[misc]


def test_policy_requires_reports_membership() -> None:
    policy = make_policy(required_fields=("record_id",))
    assert policy.requires("record_id") is True
    assert policy.requires("unit") is False


def test_direct_construction_with_an_unknown_code_is_rejected() -> None:
    """``create`` guards this too, but the dataclass must guard itself."""
    with pytest.raises(ValidationError, match="unknown issue code"):
        ValidationIssue(
            code="invented_out_of_thin_air",
            severity=Severity.ERROR,
            message="whatever",
            source="probe.csv",
            source_row=1,
        )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"required_fields": frozenset()}, "at least one required field"),
        ({"duplicate_key_fields": ()}, "at least one duplicate-key field"),
        ({"allowed_units": ()}, "at least one allowed unit"),
        (
            {"amount_min": Decimal("10"), "amount_max": Decimal("1")},
            "must not exceed",
        ),
        ({"record_id_pattern": "OPR-("}, "not a valid regular expression"),
        ({"period_pattern": "[0-9"}, "not a valid regular expression"),
    ],
)
def test_contradictory_policies_are_rejected_when_built(
    overrides: dict, expected: str
) -> None:
    from las.validate import PolicyError

    base = {
        "required_fields": frozenset({"record_id"}),
        "record_id_pattern": r"OPR-[0-9]{8}",
        "external_ref_pattern": r"EXT-[0-9]{5}",
        "amount_min": Decimal("0"),
        "amount_max": Decimal("10"),
        "duplicate_key_fields": ("record_id",),
    }
    with pytest.raises(PolicyError, match=expected):
        ValidationPolicy(**{**base, **overrides})


def test_policy_from_config_rejects_a_non_canonical_required_field() -> None:
    from las.config import ValidationConfig
    from las.validate import PolicyError, policy_from_config

    config = ValidationConfig()
    object.__setattr__(config, "required_fields", ["record_id", "warehouse_code"])
    with pytest.raises(PolicyError, match="not canonical"):
        policy_from_config(config)


def test_a_bound_that_is_not_convertible_is_rejected() -> None:
    """Configuration cannot produce this, so the guard is exercised directly."""
    from las.validate import PolicyError
    from las.validate.policy import _decimal_bound

    assert _decimal_bound(1.5, "amount_min") == Decimal("1.5")
    with pytest.raises(PolicyError, match="not a usable decimal bound"):
        _decimal_bound("not-a-bound", "amount_min")  # type: ignore[arg-type]


def test_policy_from_config_builds_from_the_committed_sample_configuration() -> None:
    from pathlib import Path as _Path

    from las.config import load_config
    from las.validate import policy_from_config

    root = _Path(__file__).resolve().parents[1]
    config = load_config(root / "data" / "samples" / "config.yaml")
    policy = policy_from_config(config.validation, known_external_refs=("EXT-10000",))
    assert policy.required_fields == frozenset(config.validation.required_fields)
    assert policy.amount_min == Decimal("0.0")
    assert policy.amount_max == Decimal("1000000.0")
    assert policy.duplicate_key_fields == ("record_id",)
    assert policy.known_external_refs == frozenset({"EXT-10000"})
