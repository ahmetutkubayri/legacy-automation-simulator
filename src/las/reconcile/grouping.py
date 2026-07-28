"""Turning aligned batches into contributions, and contributions into groups.

Input alignment is checked before anything is read. The two sequences a feed
batch carries — validated rows and matching decisions — are never zipped: a
shorter one would silently truncate the longer and rows would vanish with
nobody told. Counts, logical indices and provenance must agree exactly, and a
disagreement is a typed contract error.

Eligibility, in one fixed order
-------------------------------
A feed row contributes only when matching accepted it *and* the three fields
reconciliation actually uses are trustworthy:

1. the matching outcome — ``EXACT`` and ``FUZZY`` may contribute, and each must
   name an account; ``NEEDS_REVIEW``, ``UNMATCHED`` and ``INELIGIBLE`` never
   do, and are restated, never revisited;
2. the amount, then the unit, then the period.

The order is fixed so that a row failing two checks always reports the same
one. Validation defects *outside* those three fields are deliberately ignored:
a malformed record identifier says nothing about whether an amount can be
added up, so such a row still contributes.

Business duplicates versus replay
---------------------------------
Nothing here deduplicates. Two identical business rows arriving with different
provenance are two rows, and both contribute; the duplicate warning T10 raised
stays visible on its own row and never changes the arithmetic.

Replay is the opposite problem and is refused. One physical feed row — the same
source label, worksheet and physical row number — handed to reconciliation
twice would double a total while looking exactly like ordinary duplicate data,
and so would the same batch or the same ledger record. Those identities are
checked before any aggregation begins. They are computed from declared values
only, never from object identity, memory address or arrival order, and none of
them is part of the business group key.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from las.match import MatchDecision, MatchOutcome
from las.reconcile.errors import (
    DuplicateFeedBatchError,
    DuplicateInputIdentityError,
    MatchDecisionContractError,
    ReconcileExecutionError,
    RowAlignmentError,
)
from las.reconcile.models import (
    REASON_INVALID_AMOUNT,
    REASON_INVALID_PERIOD,
    REASON_INVALID_UNIT,
    REASON_MATCH_INELIGIBLE,
    REASON_MATCH_NEEDS_REVIEW,
    REASON_MATCH_UNMATCHED,
    Contribution,
    FeedBatch,
    GroupKey,
    Side,
    SystemLedgerRow,
    UnresolvedRow,
)
from las.validate import Severity, ValidatedRow, parse_amount

FIELD_AMOUNT = "amount"
FIELD_UNIT = "unit"
FIELD_PERIOD = "period"

#: Matching outcomes allowed into aggregation, and the reason each refused
#: outcome is recorded under. Nothing here promotes or downgrades a verdict.
ACCEPTED_OUTCOMES: tuple[MatchOutcome, ...] = (MatchOutcome.EXACT, MatchOutcome.FUZZY)
REASON_BY_REFUSED_OUTCOME = {
    MatchOutcome.NEEDS_REVIEW: REASON_MATCH_NEEDS_REVIEW,
    MatchOutcome.UNMATCHED: REASON_MATCH_UNMATCHED,
    MatchOutcome.INELIGIBLE: REASON_MATCH_INELIGIBLE,
}


def check_batches(feeds: Sequence[FeedBatch]) -> None:
    """Refuse feed input that cannot be read row for row, or arrives twice.

    Two identities are enforced, and neither is the business key:

    * the **batch**, by its ``batch_id``, so an empty or wholly excluded batch
      cannot be replayed unnoticed;
    * the **physical row**, by its provenance — source label, worksheet and
      physical row number — which is what "the same row of the same file"
      means. Two business-identical rows sitting at different physical rows
      have different identities and both contribute, exactly as intended.

    Both are computed from declared values, never from object identity, memory
    address or arrival order, so the same inputs are accepted or refused
    identically however they are shuffled.
    """
    seen_batches: set[str] = set()
    seen_rows: set[tuple[str, str | None, int]] = set()

    for batch in feeds:
        if batch.batch_id in seen_batches:
            raise DuplicateFeedBatchError(
                "the same feed batch was supplied more than once",
                operation="check_batches",
                side=Side.FEED.value,
                source=batch.batch_id,
            )
        seen_batches.add(batch.batch_id)
        check_alignment(batch)

    for batch in feeds:
        for validated in batch.validation.rows:
            provenance = validated.row.provenance
            identity = (provenance.source, provenance.worksheet, provenance.source_row)
            if identity in seen_rows:
                raise DuplicateInputIdentityError(
                    "the same physical feed row was supplied more than once",
                    operation="check_batches",
                    side=Side.FEED.value,
                    source=provenance.source,
                    row=provenance.source_row,
                )
            seen_rows.add(identity)


def check_system_rows(rows: Sequence[SystemLedgerRow]) -> None:
    """Refuse a system side that carries the same ledger record twice.

    Identity is the invented ``record_id`` where a row declares one, and the
    stable ``position`` always — position is part of the public DTO contract,
    so two rows claiming one position describe an inconsistent extraction
    rather than two records. Business-identical rows with genuinely distinct
    identities are two records and both contribute.
    """
    seen_records: set[str] = set()
    seen_positions: set[int] = set()

    for row in rows:
        if row.record_id is not None:
            if row.record_id in seen_records:
                raise DuplicateInputIdentityError(
                    "the same system ledger record was supplied more than once",
                    operation="check_system_rows",
                    side=Side.SYSTEM.value,
                    record_id=row.record_id,
                )
            seen_records.add(row.record_id)
        if row.position in seen_positions:
            raise DuplicateInputIdentityError(
                "two system ledger rows claim the same position",
                operation="check_system_rows",
                side=Side.SYSTEM.value,
                row=row.position,
            )
        seen_positions.add(row.position)


def check_alignment(batch: FeedBatch) -> None:
    """Validated rows and matching decisions must describe the same batch."""
    rows = batch.validation.rows
    decisions = batch.matching.decisions
    if len(rows) != len(decisions):
        raise RowAlignmentError(
            "the batch carries a different number of rows and decisions",
            operation="check_alignment",
            side=Side.FEED.value,
            source=batch.source,
        )
    if batch.validation.source != batch.matching.source:
        raise RowAlignmentError(
            "the validation and matching results carry different source labels",
            operation="check_alignment",
            side=Side.FEED.value,
            source=batch.source,
        )
    for position, (validated, decision) in enumerate(zip(rows, decisions, strict=True)):
        if decision.row_index != position or validated.index != position:
            raise RowAlignmentError(
                "a decision does not sit at the row index it claims",
                operation="check_alignment",
                side=Side.FEED.value,
                source=batch.source,
                row_index=position,
            )
        provenance = validated.row.provenance
        if (
            decision.provenance.source != provenance.source
            or decision.provenance.source_row != provenance.source_row
            or decision.provenance.worksheet != provenance.worksheet
        ):
            raise RowAlignmentError(
                "a decision belongs to a different row than the one beside it",
                operation="check_alignment",
                side=Side.FEED.value,
                source=batch.source,
                row=provenance.source_row,
                row_index=position,
            )


def _error_fields(validated: ValidatedRow) -> frozenset[str]:
    """Fields this row carries an ``ERROR`` on. Warnings never exclude a row."""
    return frozenset(
        issue.field
        for issue in validated.issues
        if issue.severity is Severity.ERROR and issue.field is not None
    )


def _field_fault(validated: ValidatedRow) -> str | None:
    """The first reconciliation field this row cannot be trusted on."""
    failed = _error_fields(validated)
    values = validated.row.values

    if FIELD_AMOUNT in failed or parse_amount(values.get(FIELD_AMOUNT)) is None:
        return REASON_INVALID_AMOUNT
    unit = values.get(FIELD_UNIT)
    if FIELD_UNIT in failed or not unit or not unit.strip():
        return REASON_INVALID_UNIT
    period = values.get(FIELD_PERIOD)
    if FIELD_PERIOD in failed or not period or not period.strip():
        return REASON_INVALID_PERIOD
    return None


def _unresolved(
    validated: ValidatedRow,
    decision: MatchDecision,
    reason: str,
    *,
    account: str | None = None,
) -> UnresolvedRow:
    """One row recorded as taking no part, with its matching verdict restated."""
    provenance = validated.row.provenance
    return UnresolvedRow(
        side=Side.FEED,
        reason=reason,
        source=provenance.source,
        source_row=provenance.source_row,
        row_index=decision.row_index,
        worksheet=provenance.worksheet,
        match_outcome=decision.outcome.value,
        account_ref=account,
    )


def feed_contributions(
    feeds: Sequence[FeedBatch],
) -> tuple[tuple[tuple[GroupKey, Contribution], ...], tuple[UnresolvedRow, ...]]:
    """Split every feed row into a contribution or an unresolved record.

    Exactly one of the two, for every row, always — which is what makes the
    accounting invariant hold.
    """
    contributions: list[tuple[GroupKey, Contribution]] = []
    unresolved: list[UnresolvedRow] = []

    for batch in feeds:
        for validated, decision in zip(
            batch.validation.rows, batch.matching.decisions, strict=True
        ):
            provenance = validated.row.provenance

            if decision.outcome not in ACCEPTED_OUTCOMES:
                unresolved.append(
                    _unresolved(
                        validated, decision, REASON_BY_REFUSED_OUTCOME[decision.outcome]
                    )
                )
                continue
            if not decision.account_ref:
                raise MatchDecisionContractError(
                    "a matched decision names no account to reconcile against",
                    operation="feed_contributions",
                    side=Side.FEED.value,
                    source=provenance.source,
                    row=provenance.source_row,
                    row_index=decision.row_index,
                )

            fault = _field_fault(validated)
            if fault is not None:
                unresolved.append(
                    _unresolved(validated, decision, fault, account=decision.account_ref)
                )
                continue

            values = validated.row.values
            amount = parse_amount(values.get(FIELD_AMOUNT))
            key = GroupKey(
                account_ref=decision.account_ref, period=values[FIELD_PERIOD]
            )
            contributions.append(
                (
                    key,
                    Contribution(
                        side=Side.FEED,
                        amount=amount,
                        unit=values[FIELD_UNIT],
                        source=provenance.source,
                        source_row=provenance.source_row,
                        row_index=decision.row_index,
                        worksheet=provenance.worksheet,
                        record_id=values.get("record_id"),
                    ),
                )
            )

    return tuple(contributions), tuple(unresolved)


def system_contributions(
    rows: Sequence[SystemLedgerRow],
) -> tuple[tuple[GroupKey, Contribution], ...]:
    """Every supplied ledger row becomes exactly one contribution.

    The system side has no eligibility rules: a row that could not be resolved
    to an account never became a :class:`SystemLedgerRow` in the first place,
    and one that is malformed was refused when it was constructed.
    """
    return tuple(
        (
            GroupKey(account_ref=row.account_ref, period=row.period),
            Contribution(
                side=Side.SYSTEM,
                amount=row.amount,
                unit=row.unit,
                source=Side.SYSTEM.value,
                source_row=row.position,
                row_index=row.position,
                record_id=row.record_id,
            ),
        )
        for row in rows
    )


def exact_sum(values: Sequence[Decimal]) -> Decimal:
    """Add decimals exactly, with no precision limit of any kind.

    Every operand is rescaled to one shared exponent — the smallest any of them
    uses — and the resulting integer coefficients are added as Python integers,
    which are unbounded. The total is then rebuilt straight from its digits.

    No arithmetic here consults a :class:`~decimal.Context`, neither the
    process-wide one nor a private one, so the result cannot change because
    something elsewhere altered the global precision or rounding mode, and no
    hidden ceiling can reject an amount the T10 parser accepted. A value with
    three hundred significant digits adds up as exactly as ``10.50`` does.
    """
    if not values:
        return Decimal(0)
    parts = []
    for value in values:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ReconcileExecutionError(
                "a non-finite value cannot take part in an exact total",
                operation="exact_sum",
            )
        parts.append(value.as_tuple())

    exponent = min(part.exponent for part in parts)
    total = 0
    for part in parts:
        coefficient = 0
        for digit in part.digits:
            coefficient = coefficient * 10 + digit
        # Every shift is non-negative: the shared exponent is the smallest.
        scaled = coefficient * 10 ** (part.exponent - exponent)
        total += -scaled if part.sign else scaled

    # ``Decimal(int)`` is exact and consults no context, so this recovers the
    # total's digits without ever formatting it as text.
    whole = Decimal(total).as_tuple()
    return Decimal((whole.sign, whole.digits, exponent))


def exact_difference(left: Decimal, right: Decimal) -> Decimal:
    """``left - right``, exactly.

    ``copy_negate`` rather than unary minus: the operators consult the active
    decimal context and would round a long value, whereas the ``copy_``
    family is defined to leave the operand untouched.
    """
    return exact_sum((left, right.copy_negate()))


def totals_by_unit(contributions: Sequence[Contribution]) -> dict[str, Decimal]:
    """Exact per-unit subtotals. Nothing is summed across units."""
    by_unit: dict[str, list[Decimal]] = {}
    for contribution in contributions:
        by_unit.setdefault(contribution.unit, []).append(contribution.amount)
    return {unit: exact_sum(amounts) for unit, amounts in by_unit.items()}


def group_keys(
    *pairs: Sequence[tuple[GroupKey, Contribution]],
) -> tuple[GroupKey, ...]:
    """Every distinct key, in the declared total order.

    Sorted rather than first-seen, so the result cannot depend on which batch
    was supplied first or on any mapping's iteration order.
    """
    keys = {key for group in pairs for key, _ in group}
    return tuple(sorted(keys, key=lambda key: key.sort_key))
