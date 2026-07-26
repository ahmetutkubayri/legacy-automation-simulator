"""The validation pass: pure, deterministic, and total-ordered.

The engine reads rows, asks each applicable rule about each applicable field,
and assembles immutable results. It performs no I/O, consults no clock, uses
no randomness, and never writes to a row, a value mapping, an extras mapping,
a provenance record, a schema, or a policy.

Issue ordering
--------------
One total order, applied to every issue in a batch:

1. **input row index** — source order, exactly as ingested;
2. **field rank from the policy** — ``ValidationPolicy.field_order``, which is
   schema-owned and never inferred from a row. A field the order does not name
   ranks after every named field, by its own name; a row-scope issue (no field
   at all) ranks after every field-scope issue;
3. **rule priority** — the integer each rule declares;
4. **stable issue code** — the final tiebreak.

Every component is an integer or a fixed string, so the order cannot depend on
dictionary order, set order, rule-registration order, hash randomization, or
the operating system. In particular it cannot depend on the insertion order of
a row's value mapping: two rows carrying identical values under keys inserted
in opposite orders validate identically. Validating the same immutable input
twice returns equal results with identical ordering.

A row appears exactly once in ``rows`` however many issues it collected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from las.ingest import IngestedRow, IngestResult
from las.validate.checks import is_blank
from las.validate.errors import RuleExecutionError
from las.validate.models import (
    BatchValidationResult,
    ValidatedRow,
    ValidationIssue,
    summarize,
)
from las.validate.policy import ValidationPolicy
from las.validate.rules import BatchRule, FieldRule, RuleSet, default_rule_set


def _run_field_rule(
    rule: FieldRule,
    field_name: str,
    value: str | None,
    policy: ValidationPolicy,
    row: IngestedRow,
) -> str | None:
    try:
        return rule.check(value, policy)
    except Exception as exc:
        # Only the exception's type name travels: its message could quote the
        # very source value this package refuses to echo.
        raise RuleExecutionError(
            f"the rule raised {type(exc).__name__} while checking a value",
            rule_id=rule.rule_id,
            source=row.provenance.source,
            row=row.provenance.source_row,
            field=field_name,
        ) from None


def _run_batch_rule(
    rule: BatchRule, rows: Sequence[IngestedRow], policy: ValidationPolicy
) -> Mapping[int, str]:
    try:
        return rule.check(rows, policy)
    except Exception as exc:
        raise RuleExecutionError(
            f"the rule raised {type(exc).__name__} while checking the batch",
            rule_id=rule.rule_id,
        ) from None


def validate_rows(
    rows: Sequence[IngestedRow],
    policy: ValidationPolicy,
    *,
    rule_set: RuleSet | None = None,
    source: str | None = None,
    worksheet: str | None = None,
) -> BatchValidationResult:
    """Validate a sequence of ingested rows without touching any of them."""
    active = rule_set if rule_set is not None else default_rule_set()
    ordered_rows = tuple(rows)

    # (row index, field rank, rule priority, code) -> the total order. The
    # field rank comes from the policy, so an empty batch needs no special
    # case and no row can influence the ordering of any other.
    collected: list[
        tuple[tuple[int, tuple[int, int, str], int, str], ValidationIssue]
    ] = []
    seen: set[tuple[int, str | None, str]] = set()

    def remember(index: int, field_name: str | None, code: str, priority: int) -> None:
        marker = (index, field_name, code)
        if marker in seen:
            return  # never emit the same code twice for one field of one row
        seen.add(marker)
        rank = policy.field_rank(field_name)
        issue = ValidationIssue.create(
            code,
            ordered_rows[index].provenance,
            field_name=field_name,
            metadata=_metadata_for(code, policy),
        )
        collected.append(((index, rank, priority, code), issue))

    for rule in active.ordered_field_rules:
        applicable = rule.applicable_fields(policy)
        for index, row in enumerate(ordered_rows):
            for field_name in applicable:
                if field_name not in row.values:
                    continue  # the schema does not carry this field
                value = row.values[field_name]
                blank = is_blank(value)
                if blank != rule.applies_to_blank:
                    # Required rules speak only about absent values; domain
                    # rules only about present ones. That keeps a single
                    # defect from producing two overlapping issues.
                    continue
                code = _run_field_rule(rule, field_name, value, policy, row)
                if code is not None:
                    remember(index, field_name, code, rule.priority)

    for batch_rule in active.ordered_batch_rules:
        for index, code in sorted(_run_batch_rule(batch_rule, ordered_rows, policy).items()):
            remember(index, batch_rule.field, code, batch_rule.priority)

    collected.sort(key=lambda entry: entry[0])
    issues = tuple(issue for _, issue in collected)

    by_row: dict[int, list[ValidationIssue]] = {}
    for key, issue in collected:
        by_row.setdefault(key[0], []).append(issue)

    validated = tuple(
        ValidatedRow(row=row, index=index, issues=tuple(by_row.get(index, ())))
        for index, row in enumerate(ordered_rows)
    )

    return BatchValidationResult(
        source=source if source is not None else _batch_source(ordered_rows),
        rows=validated,
        issues=issues,
        summary=summarize(validated, issues),
        rule_ids=active.rule_ids,
        worksheet=worksheet if worksheet is not None else _batch_worksheet(ordered_rows),
    )


def _metadata_for(code: str, policy: ValidationPolicy) -> Mapping[str, str]:
    """Safe categorical context only: invented tokens, never source values."""
    from las.validate.models import CODE_UNIT_NOT_SUPPORTED

    if code == CODE_UNIT_NOT_SUPPORTED:
        return {"allowed_units": policy.allowed_units_text}
    return {}


def _batch_source(rows: Sequence[IngestedRow]) -> str:
    """The batch's source label, carried over from T9 provenance."""
    return rows[0].provenance.source if rows else "unknown"


def _batch_worksheet(rows: Sequence[IngestedRow]) -> str | None:
    return rows[0].provenance.worksheet if rows else None


def validate_ingest_result(
    result: IngestResult,
    policy: ValidationPolicy,
    *,
    rule_set: RuleSet | None = None,
) -> BatchValidationResult:
    """Validate a T9 ingestion result, leaving it exactly as it was.

    No row is filtered, reordered, corrected, or de-duplicated: the batch that
    comes out has one validated row per ingested row, in the same order.
    """
    return validate_rows(
        result.rows,
        policy,
        rule_set=rule_set,
        source=result.source,
        worksheet=result.worksheet,
    )
