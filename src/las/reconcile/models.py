"""Immutable result types for reconciliation, and the stable vocabulary.

Every outcome and reason is a code from the tables below. Codes are part of
the public contract — reporting keys off them — so they are invented once,
spelled in ``lower_snake_case``, and never reworded. Human-readable text lives
in :data:`MESSAGE_BY_REASON` as a *fixed template*: a message names a
situation without ever quoting the data that produced it.

Deep immutability
-----------------
A frozen dataclass is not enough on its own, because a frozen field can still
hold a caller's live list. Every sequence field is coerced to a tuple and
every mapping field is copied and then wrapped read-only, so neither the
caller's original container nor the returned object can change afterwards.

What a result is allowed to remember
------------------------------------
Invented account references, validated period and unit tokens, exact
``Decimal`` totals, safe provenance (a source label, a physical row number, a
logical row index), the side a contribution came from, and stable codes. Never
a whole source row, a source or account name, an address, arbitrary extras, a
path, an exception, or a timestamp.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from las.match import BatchMatchResult
from las.reconcile.errors import InputContractError, LedgerRowError
from las.validate import BatchValidationResult, parse_amount


class Side(StrEnum):
    """Which side of the reconciliation a contribution belongs to.

    Deliberately neutral and deliberately only two. The two feeds are *not*
    opposing sides — they are two sources contributing to one ``FEED`` side,
    which is what the development plan means by reconciling both feeds against
    the ledger.
    """

    FEED = "feed"
    SYSTEM = "system"


#: Rank used wherever sides must be ordered, so ``FEED`` always precedes
#: ``SYSTEM`` regardless of how the enum happens to be iterated.
SIDE_RANK: Mapping[Side, int] = MappingProxyType({Side.FEED: 0, Side.SYSTEM: 1})


class ReconcileOutcome(StrEnum):
    """The single verdict a reconciliation group carries."""

    MISSING_IN_FEED = "missing_in_feed"
    MISSING_IN_SYSTEM = "missing_in_system"
    UNIT_MISMATCH = "unit_mismatch"
    BALANCED = "balanced"
    WITHIN_TOLERANCE = "within_tolerance"
    MISMATCH = "mismatch"


#: The order verdicts are decided in, and the order they are reported in.
#: Presence is settled before units, and units before any arithmetic, so a
#: variance is never computed for quantities that cannot be compared.
OUTCOME_RANK: Mapping[ReconcileOutcome, int] = MappingProxyType(
    {
        ReconcileOutcome.MISSING_IN_FEED: 0,
        ReconcileOutcome.MISSING_IN_SYSTEM: 1,
        ReconcileOutcome.UNIT_MISMATCH: 2,
        ReconcileOutcome.BALANCED: 3,
        ReconcileOutcome.WITHIN_TOLERANCE: 4,
        ReconcileOutcome.MISMATCH: 5,
    }
)

#: Group reason codes, one per outcome plus the two shapes a unit conflict
#: takes, which are worth telling apart when someone has to fix the data.
REASON_MISSING_IN_FEED = "no_feed_contribution"
REASON_MISSING_IN_SYSTEM = "no_system_contribution"
REASON_UNIT_DIFFERS_ACROSS_SIDES = "unit_differs_across_sides"
REASON_MULTIPLE_UNITS_ON_ONE_SIDE = "multiple_units_on_one_side"
REASON_TOTALS_EQUAL = "totals_equal"
REASON_WITHIN_VARIANCE_BAND = "within_variance_band"
REASON_OUTSIDE_VARIANCE_BANDS = "outside_variance_bands"

#: Unresolved-row reason codes. The first three restate a matching verdict
#: without altering it; the last three record that a field reconciliation
#: itself depends on could not be trusted.
REASON_MATCH_NEEDS_REVIEW = "match_needs_review"
REASON_MATCH_UNMATCHED = "match_unmatched"
REASON_MATCH_INELIGIBLE = "match_ineligible"
REASON_INVALID_AMOUNT = "invalid_amount"
REASON_INVALID_UNIT = "invalid_unit"
REASON_INVALID_PERIOD = "invalid_period"

#: Fixed templates. No message is ever assembled from a source value.
MESSAGE_BY_REASON: Mapping[str, str] = MappingProxyType(
    {
        REASON_MISSING_IN_FEED: "no feed row contributed to this account and period",
        REASON_MISSING_IN_SYSTEM: "no system row contributed to this account and period",
        REASON_UNIT_DIFFERS_ACROSS_SIDES: "the two sides report different units",
        REASON_MULTIPLE_UNITS_ON_ONE_SIDE: "one side reports more than one unit",
        REASON_TOTALS_EQUAL: "the two side totals are exactly equal",
        REASON_WITHIN_VARIANCE_BAND: "the difference falls inside a configured variance band",
        REASON_OUTSIDE_VARIANCE_BANDS: "the difference exceeds every configured variance band",
        REASON_MATCH_NEEDS_REVIEW: "matching routed this row to review",
        REASON_MATCH_UNMATCHED: "matching found no account for this row",
        REASON_MATCH_INELIGIBLE: "matching had nothing usable to match this row on",
        REASON_INVALID_AMOUNT: "the amount cannot be used for aggregation",
        REASON_INVALID_UNIT: "the unit cannot be used for comparison",
        REASON_INVALID_PERIOD: "the period cannot be used for grouping",
    }
)

#: Reasons that mean "matching said so", kept apart from reasons that mean
#: "a reconciliation field did not validate". Conflating them would hide which
#: stage a row actually stalled at.
MATCH_REASONS: tuple[str, ...] = (
    REASON_MATCH_NEEDS_REVIEW,
    REASON_MATCH_UNMATCHED,
    REASON_MATCH_INELIGIBLE,
)
VALIDATION_REASONS: tuple[str, ...] = (
    REASON_INVALID_AMOUNT,
    REASON_INVALID_UNIT,
    REASON_INVALID_PERIOD,
)


@dataclass(frozen=True)
class GroupKey:
    """The business identity of one reconciliation group.

    Exactly the account and the period, as the approved contract states.
    ``unit`` is deliberately absent: putting it in the key would file a feed
    row reporting ``EA`` and a system row reporting ``KG`` into two separate
    groups, and the conflict the reconciliation exists to surface would
    disappear into a pair of tidy half-empty results.

    Values are the validated originals. Nothing here trims, case-corrects or
    normalizes them — that would make two different keys look like one.
    """

    account_ref: str
    period: str

    def __post_init__(self) -> None:
        if not self.account_ref:
            raise LedgerRowError(
                "a reconciliation group key needs an account reference",
                operation="group_key",
            )
        if not self.period:
            raise LedgerRowError(
                "a reconciliation group key needs a period",
                operation="group_key",
                account_ref=self.account_ref,
            )

    @property
    def sort_key(self) -> tuple[str, str]:
        """Total order: account reference, then period."""
        return (self.account_ref, self.period)


@dataclass(frozen=True)
class SystemLedgerRow:
    """One ledger row from the simulated system, as reconciliation sees it.

    A neutral data-transfer object and nothing else. It holds no driver or
    simulator object, so :mod:`las.reconcile` never depends on either layer.

    ``account_ref`` arrives already resolved: the caller looks the row's
    external reference up in the existing exact master-link relationship and
    supplies the answer. Reconciliation performs no matching of its own, so a
    row that cannot be resolved simply never becomes a ``SystemLedgerRow``.

    ``amount_text`` is text, not a float, so the exact characters the system
    reported survive into the ``Decimal`` and nothing is rounded on the way in.
    """

    account_ref: str
    period: str
    unit: str
    amount_text: str
    position: int
    record_id: str | None = None
    external_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("account_ref", "period", "unit", "amount_text"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise LedgerRowError(
                    "a system ledger row is missing a required value",
                    operation="system_ledger_row",
                    side=Side.SYSTEM.value,
                    field=name,
                )
        if isinstance(self.position, bool) or not isinstance(self.position, int):
            raise LedgerRowError(
                "a system ledger row position must be an integer",
                operation="system_ledger_row",
                side=Side.SYSTEM.value,
                field="position",
            )
        if self.position < 0:
            raise LedgerRowError(
                "a system ledger row position must not be negative",
                operation="system_ledger_row",
                side=Side.SYSTEM.value,
                field="position",
            )
        parsed = parse_amount(self.amount_text)
        if parsed is None:
            # Refused here rather than at aggregation time, so an unusable
            # system side is impossible to assemble in the first place.
            raise LedgerRowError(
                "a system ledger row amount is not plain decimal text",
                operation="system_ledger_row",
                side=Side.SYSTEM.value,
                field="amount_text",
            )
        object.__setattr__(self, "_amount", parsed)

    @property
    def amount(self) -> Decimal:
        """The exact decimal this row reports, parsed once by the shared grammar."""
        return self._amount  # type: ignore[attr-defined]


@dataclass(frozen=True)
class FeedBatch:
    """One feed's aligned validation and matching results.

    Both feeds are supplied as separate batches and both land on the ``FEED``
    side; the batch exists to keep each feed's rows beside their own verdicts,
    not to give a feed a side of its own.

    ``batch_id`` names this batch so that supplying it twice is caught rather
    than silently doubling every total. It defaults to the validation source
    label, and it is an *input* identity only: it never enters the
    reconciliation group key and never influences an outcome.
    """

    validation: BatchValidationResult
    matching: BatchMatchResult
    batch_id: str | None = None

    def __post_init__(self) -> None:
        if self.batch_id is None:
            object.__setattr__(self, "batch_id", self.validation.source)
        if not isinstance(self.batch_id, str) or not self.batch_id.strip():
            raise InputContractError(
                "a feed batch identity must be a non-blank label",
                operation="feed_batch",
                side=Side.FEED.value,
                field="batch_id",
            )

    @property
    def source(self) -> str:
        """The batch's provenance label. Evidence only — it decides nothing."""
        return self.validation.source

    @property
    def row_count(self) -> int:
        return len(self.validation.rows)


@dataclass(frozen=True)
class Contribution:
    """One row's participation in one group's side total."""

    side: Side
    amount: Decimal
    unit: str
    source: str
    source_row: int
    row_index: int
    worksheet: str | None = None
    record_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise InputContractError(
                "a contribution amount must be a finite decimal",
                operation="contribution",
                side=self.side.value,
                source=self.source,
                row=self.source_row,
                field="amount",
            )

    @property
    def sort_key(self) -> tuple[int, str, int, int, str]:
        """Total order inside a side, from safe provenance only.

        Physical row before logical index, so two contributions from different
        sources stay stably ordered however the batches were supplied. Input
        sequence position is never used as an implicit tie-break.
        """
        return (
            SIDE_RANK[self.side],
            self.source,
            self.source_row,
            self.row_index,
            self.record_id or "",
        )


@dataclass(frozen=True)
class SideAggregate:
    """What one side of one group adds up to.

    ``total`` is present only when the side reports exactly one unit. Summing
    across units would produce a number with no meaning, so when several units
    appear the per-unit subtotals are all that is offered.
    """

    side: Side
    contributions: tuple[Contribution, ...] = ()
    totals_by_unit: Mapping[str, Decimal] = dc_field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "contributions", tuple(sorted(self.contributions, key=lambda c: c.sort_key))
        )
        object.__setattr__(
            self,
            "totals_by_unit",
            MappingProxyType(dict(sorted(self.totals_by_unit.items()))),
        )

    @property
    def is_present(self) -> bool:
        """Whether the side contributed at all.

        Decided by contribution count, never by total value: a side whose rows
        happen to sum to zero is present and reported zero, which is a wholly
        different statement from a side that reported nothing.
        """
        return bool(self.contributions)

    @property
    def units(self) -> tuple[str, ...]:
        """The distinct units this side reported, in one deterministic order."""
        return tuple(self.totals_by_unit)

    @property
    def total(self) -> Decimal | None:
        """The side total, or ``None`` when the units are not comparable."""
        if len(self.totals_by_unit) != 1:
            return None
        return next(iter(self.totals_by_unit.values()))


@dataclass(frozen=True)
class GroupResult:
    """One account-and-period group, with exactly one outcome and one reason."""

    key: GroupKey
    feed: SideAggregate
    system: SideAggregate
    outcome: ReconcileOutcome
    reason: str
    signed_difference: Decimal | None = None
    absolute_difference: Decimal | None = None
    variance_band: Decimal | None = None

    @property
    def message(self) -> str:
        """The fixed template for this group's reason."""
        return MESSAGE_BY_REASON[self.reason]

    @property
    def contribution_count(self) -> int:
        return len(self.feed.contributions) + len(self.system.contributions)

    @property
    def sort_key(self) -> tuple[str, str]:
        return self.key.sort_key


@dataclass(frozen=True)
class UnresolvedRow:
    """One feed row that took no part in aggregation, and why.

    Retained rather than dropped: reconciliation reports what it could not do
    as carefully as what it could, so no row ever simply disappears.
    """

    side: Side
    reason: str
    source: str
    source_row: int
    row_index: int
    worksheet: str | None = None
    match_outcome: str | None = None
    account_ref: str | None = None

    @property
    def message(self) -> str:
        return MESSAGE_BY_REASON[self.reason]

    @property
    def sort_key(self) -> tuple[int, str, int, int]:
        return (SIDE_RANK[self.side], self.source, self.source_row, self.row_index)


@dataclass(frozen=True)
class ReconcileSummary:
    """Deterministic counts over one reconciliation pass."""

    group_count: int
    groups_by_outcome: Mapping[str, int]
    groups_by_reason: Mapping[str, int]
    contributions_by_side: Mapping[str, int]
    unresolved_by_reason: Mapping[str, int]
    feed_rows_in: int
    system_rows_in: int

    def __post_init__(self) -> None:
        for name in (
            "groups_by_outcome",
            "groups_by_reason",
            "contributions_by_side",
            "unresolved_by_reason",
        ):
            value = dict(getattr(self, name))
            object.__setattr__(self, name, MappingProxyType(dict(sorted(value.items()))))

    @property
    def feed_contributions(self) -> int:
        return self.contributions_by_side.get(Side.FEED.value, 0)

    @property
    def system_contributions(self) -> int:
        return self.contributions_by_side.get(Side.SYSTEM.value, 0)

    @property
    def unresolved_count(self) -> int:
        return sum(self.unresolved_by_reason.values())

    @property
    def feed_rows_accounted(self) -> int:
        """Feed rows that ended up somewhere: contributing, or unresolved."""
        return self.feed_contributions + self.unresolved_count

    @property
    def accounting_holds(self) -> bool:
        """Whether every supplied row on both sides was accounted for once."""
        return (
            self.feed_rows_accounted == self.feed_rows_in
            and self.system_contributions == self.system_rows_in
        )


@dataclass(frozen=True)
class BatchReconcileResult:
    """The whole reconciliation: groups, what was left out, and the counts."""

    groups: tuple[GroupResult, ...]
    unresolved: tuple[UnresolvedRow, ...]
    summary: ReconcileSummary

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", tuple(self.groups))
        object.__setattr__(self, "unresolved", tuple(self.unresolved))

    @property
    def group_count(self) -> int:
        return len(self.groups)

    def with_outcome(self, outcome: ReconcileOutcome) -> tuple[GroupResult, ...]:
        return tuple(group for group in self.groups if group.outcome is outcome)


def summarize(
    groups: tuple[GroupResult, ...],
    unresolved: tuple[UnresolvedRow, ...],
    *,
    feed_rows_in: int,
    system_rows_in: int,
) -> ReconcileSummary:
    """Count one reconciliation pass, deterministically."""
    by_outcome: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_side: dict[str, int] = {Side.FEED.value: 0, Side.SYSTEM.value: 0}
    for group in groups:
        by_outcome[group.outcome.value] = by_outcome.get(group.outcome.value, 0) + 1
        by_reason[group.reason] = by_reason.get(group.reason, 0) + 1
        by_side[Side.FEED.value] += len(group.feed.contributions)
        by_side[Side.SYSTEM.value] += len(group.system.contributions)

    unresolved_by_reason: dict[str, int] = {}
    for row in unresolved:
        unresolved_by_reason[row.reason] = unresolved_by_reason.get(row.reason, 0) + 1

    return ReconcileSummary(
        group_count=len(groups),
        groups_by_outcome=by_outcome,
        groups_by_reason=by_reason,
        contributions_by_side=by_side,
        unresolved_by_reason=unresolved_by_reason,
        feed_rows_in=feed_rows_in,
        system_rows_in=system_rows_in,
    )
