"""Shared, non-test fixtures for the reconciliation suites.

Deliberately not named ``test_*``: several T12 modules import it, and importing
shared data from another test module would couple those modules together.

The system-side builders here use the public simulator API on purpose. Tests
are allowed to, because they must construct realistic ledger input; the
``las.reconcile`` package itself imports neither the simulator nor the driver,
which :mod:`tests.test_reconcile_boundary` asserts. Everything crosses into
reconciliation as the neutral :class:`~las.reconcile.SystemLedgerRow` DTO,
exactly as the T14 adapter will do it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path

from las.ingest import IngestedRow, RowProvenance
from las.match import (
    REASON_AMBIGUOUS_TIE,
    REASON_BELOW_REVIEW,
    REASON_EXACT_REFERENCE,
    REASON_FUZZY_ACCEPTED,
    REASON_NO_MATCHING_INPUT,
    BatchMatchResult,
    MasterIndex,
    MatchDecision,
    MatchMethod,
    MatchOutcome,
    Score,
)
from las.match import (
    summarize as summarize_matches,
)
from las.reconcile import FeedBatch, ReconcilePolicy, SystemLedgerRow
from las.validate import (
    BatchValidationResult,
    ValidatedRow,
    ValidationIssue,
)
from las.validate import (
    summarize as summarize_validation,
)

#: The committed sample ladder, as exact decimals.
SAMPLE_BANDS: tuple[Decimal, ...] = (Decimal(0), Decimal(1), Decimal(5))

ACCOUNT_A = "ACC-100000"
ACCOUNT_B = "ACC-100001"
PERIOD_A = "2026-01"
PERIOD_B = "2026-02"

#: A real T11 method and reason for each outcome, so helper-built decisions are
#: the same shape matching actually produces rather than a plausible fake.
MATCH_SHAPE: dict[MatchOutcome, tuple[MatchMethod, str]] = {
    MatchOutcome.EXACT: (MatchMethod.EXTERNAL_REFERENCE, REASON_EXACT_REFERENCE),
    MatchOutcome.FUZZY: (MatchMethod.FUZZY_NAME, REASON_FUZZY_ACCEPTED),
    MatchOutcome.NEEDS_REVIEW: (MatchMethod.FUZZY_NAME, REASON_AMBIGUOUS_TIE),
    MatchOutcome.UNMATCHED: (MatchMethod.FUZZY_NAME, REASON_BELOW_REVIEW),
    MatchOutcome.INELIGIBLE: (MatchMethod.NONE, REASON_NO_MATCHING_INPUT),
}


def make_policy(bands: Iterable[Decimal] = SAMPLE_BANDS) -> ReconcilePolicy:
    """A policy mirroring the committed sample configuration's bands."""
    return ReconcilePolicy(variance_bands=tuple(bands))


def ledger_row(
    account_ref: str = ACCOUNT_A,
    period: str = PERIOD_A,
    unit: str = "EA",
    amount_text: str = "10.50",
    position: int = 0,
    record_id: str | None = None,
    external_ref: str | None = "EXT-10000",
) -> SystemLedgerRow:
    """One neutral system-side row, with its account already resolved.

    ``record_id`` derives from the position by default, so two helper-built
    rows are two distinct ledger records rather than one record supplied twice
    — which reconciliation now refuses.
    """
    return SystemLedgerRow(
        account_ref=account_ref,
        period=period,
        unit=unit,
        amount_text=amount_text,
        position=position,
        record_id=(
            record_id
            if record_id is not None
            else f"OPR-{88000000 + position}"
            if isinstance(position, int) and not isinstance(position, bool)
            else "OPR-88000000"
        ),
        external_ref=external_ref,
    )


def feed_row(
    source_row: int = 2,
    *,
    amount: str | None = "10.50",
    unit: str | None = "EA",
    period: str | None = PERIOD_A,
    record_id: str | None = "OPR-00000001",
    external_ref: str | None = "EXT-10000",
    source: str = "probe.csv",
    worksheet: str | None = None,
) -> IngestedRow:
    return IngestedRow(
        provenance=RowProvenance(
            source=source, source_row=source_row, worksheet=worksheet
        ),
        values={
            "amount": amount,
            "external_ref": external_ref,
            "period": period,
            "record_id": record_id,
            "unit": unit,
        },
    )


def make_batch(
    rows: Sequence[IngestedRow],
    *,
    outcomes: Sequence[MatchOutcome] | None = None,
    accounts: Sequence[str | None] | None = None,
    issues_by_row: Sequence[tuple[ValidationIssue, ...]] | None = None,
    source: str | None = None,
    worksheet: str | None = None,
) -> FeedBatch:
    """One aligned feed batch, built without re-running T10 or T11.

    Outcomes and accounts default to a clean exact match, so a test only says
    what it is actually about.
    """
    label = source if source is not None else (rows[0].provenance.source if rows else "probe.csv")
    sheet = worksheet if worksheet is not None else (rows[0].provenance.worksheet if rows else None)
    outcomes = outcomes if outcomes is not None else [MatchOutcome.EXACT] * len(rows)
    accounts = accounts if accounts is not None else [ACCOUNT_A] * len(rows)
    issues_by_row = issues_by_row if issues_by_row is not None else [()] * len(rows)

    validated = tuple(
        ValidatedRow(row=row, index=position, issues=issues_by_row[position])
        for position, row in enumerate(rows)
    )
    decisions = tuple(
        MatchDecision(
            provenance=row.provenance,
            row_index=position,
            outcome=outcomes[position],
            method=MATCH_SHAPE[outcomes[position]][0],
            reason=MATCH_SHAPE[outcomes[position]][1],
            account_ref=accounts[position],
            score=Score(10000) if accounts[position] else None,
        )
        for position, row in enumerate(rows)
    )
    all_issues = tuple(issue for issues in issues_by_row for issue in issues)
    return FeedBatch(
        validation=BatchValidationResult(
            source=label,
            rows=validated,
            issues=all_issues,
            summary=summarize_validation(validated, all_issues),
            worksheet=sheet,
        ),
        matching=BatchMatchResult(
            source=label,
            decisions=decisions,
            summary=summarize_matches(decisions),
            worksheet=sheet,
        ),
    )


def ledger_rows_for_fixtures(
    fixtures_dir: Path, index: MasterIndex
) -> tuple[SystemLedgerRow, ...]:
    """The committed ledger, adapted into neutral rows exactly as T14 will.

    Two steps, and only two: read the public store snapshot, then resolve each
    record's external reference through the existing master index. The amount
    crosses over as text so that no binary float ever reaches reconciliation.
    """
    from las.simulator import RecordStore

    snapshot = RecordStore.from_fixtures(fixtures_dir).snapshot()
    rows: list[SystemLedgerRow] = []
    for position, record in enumerate(snapshot.records):
        account_ref = index.resolve_link(record.external_ref)
        if account_ref is None:  # pragma: no cover - every fixture link resolves
            continue
        rows.append(
            SystemLedgerRow(
                account_ref=account_ref,
                period=record.period,
                unit=record.unit,
                amount_text=f"{record.amount:.2f}",
                position=position,
                record_id=record.record_id,
                external_ref=record.external_ref,
            )
        )
    return tuple(rows)
