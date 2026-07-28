"""The reconciliation pass: pure, deterministic, and total-ordered.

Reads two sides and a policy, produces one result per account-and-period group
plus one record for every feed row that took no part, and writes to nothing. No
I/O, no clock, no randomness, no environment lookups, and no mutation of a row,
a validation issue, a matching decision, a ledger row, or the policy.

The two sides
-------------
``FEED`` is both feeds together — alpha and beta are two sources contributing
to one side, never opponents. ``SYSTEM`` is the ledger, supplied as neutral
:class:`~las.reconcile.models.SystemLedgerRow` values whose account references
the caller has already resolved through the existing exact master-link
relationship. This package imports neither the driver nor the simulator, and
performs no matching of its own.

Outcome precedence
------------------
Strictly in this order, and the first one that applies wins:

1. **presence** — a side with no contributions gives ``MISSING_IN_FEED`` or
   ``MISSING_IN_SYSTEM``. Presence is contribution count, never total value, so
   a side that reported rows summing to zero is present and says zero;
2. **units** — more than one distinct unit on either side, or two sides
   reporting different units, gives ``UNIT_MISMATCH``;
3. **arithmetic** — only now, with both sides present and one shared unit, are
   the totals compared.

The order is the point. A variance computed across ``EA`` and ``KG`` would be a
number with no meaning, so for a missing side or a unit conflict this package
publishes no signed difference, no absolute difference and no band at all — the
per-unit subtotals stand alone as the evidence.

Comparison
----------
``signed_difference = feed_total - system_total`` and
``absolute_difference = abs(signed_difference)``, both exact ``Decimal``. The
absolute difference selects the narrowest configured band whose inclusive upper
edge contains it: zero is ``BALANCED``, any other band is ``WITHIN_TOLERANCE``,
and a difference past the largest bound is ``MISMATCH``. No division is
performed anywhere, so a zero total needs no special case.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from las.reconcile.grouping import (
    check_batches,
    check_system_rows,
    exact_difference,
    feed_contributions,
    group_keys,
    system_contributions,
    totals_by_unit,
)
from las.reconcile.models import (
    REASON_MISSING_IN_FEED,
    REASON_MISSING_IN_SYSTEM,
    REASON_MULTIPLE_UNITS_ON_ONE_SIDE,
    REASON_OUTSIDE_VARIANCE_BANDS,
    REASON_TOTALS_EQUAL,
    REASON_UNIT_DIFFERS_ACROSS_SIDES,
    REASON_WITHIN_VARIANCE_BAND,
    BatchReconcileResult,
    Contribution,
    FeedBatch,
    GroupKey,
    GroupResult,
    ReconcileOutcome,
    Side,
    SideAggregate,
    SystemLedgerRow,
    summarize,
)
from las.reconcile.policy import ReconcilePolicy


def _aggregate(side: Side, contributions: Sequence[Contribution]) -> SideAggregate:
    return SideAggregate(
        side=side,
        contributions=tuple(contributions),
        totals_by_unit=totals_by_unit(contributions),
    )


def _decide(
    key: GroupKey, feed: SideAggregate, system: SideAggregate, policy: ReconcilePolicy
) -> GroupResult:
    """One group, one outcome, one reason — in the documented precedence."""

    def result(
        outcome: ReconcileOutcome,
        reason: str,
        *,
        signed: Decimal | None = None,
        absolute: Decimal | None = None,
        band: Decimal | None = None,
    ) -> GroupResult:
        return GroupResult(
            key=key,
            feed=feed,
            system=system,
            outcome=outcome,
            reason=reason,
            signed_difference=signed,
            absolute_difference=absolute,
            variance_band=band,
        )

    # 1. presence, decided by contribution count and never by total value.
    if not feed.is_present:
        return result(ReconcileOutcome.MISSING_IN_FEED, REASON_MISSING_IN_FEED)
    if not system.is_present:
        return result(ReconcileOutcome.MISSING_IN_SYSTEM, REASON_MISSING_IN_SYSTEM)

    # 2. units, before any arithmetic touches the two totals.
    if len(feed.units) > 1 or len(system.units) > 1:
        return result(ReconcileOutcome.UNIT_MISMATCH, REASON_MULTIPLE_UNITS_ON_ONE_SIDE)
    if feed.units != system.units:
        return result(ReconcileOutcome.UNIT_MISMATCH, REASON_UNIT_DIFFERS_ACROSS_SIDES)

    # 3. arithmetic, now that both sides are present and share one unit.
    # Exact, and context-free: ``copy_abs`` leaves its operand alone where
    # ``abs()`` would round through whatever context happens to be active.
    signed = exact_difference(feed.total, system.total)
    absolute = signed.copy_abs()
    band = policy.band_for(absolute)
    if band is None:
        return result(
            ReconcileOutcome.MISMATCH,
            REASON_OUTSIDE_VARIANCE_BANDS,
            signed=signed,
            absolute=absolute,
        )
    if absolute == 0:
        return result(
            ReconcileOutcome.BALANCED,
            REASON_TOTALS_EQUAL,
            signed=signed,
            absolute=absolute,
            band=band,
        )
    return result(
        ReconcileOutcome.WITHIN_TOLERANCE,
        REASON_WITHIN_VARIANCE_BAND,
        signed=signed,
        absolute=absolute,
        band=band,
    )


def reconcile(
    feeds: Sequence[FeedBatch],
    system_rows: Sequence[SystemLedgerRow],
    policy: ReconcilePolicy,
) -> BatchReconcileResult:
    """Reconcile both feeds, as one side, against the system ledger.

    Every feed row leaves as exactly one of a contribution or an unresolved
    record, and every supplied ledger row leaves as exactly one contribution,
    so the counts in the summary always add back up to what came in.
    """
    check_batches(feeds)
    check_system_rows(system_rows)

    feed_pairs, unresolved = feed_contributions(feeds)
    system_pairs = system_contributions(system_rows)

    feed_by_key: dict[GroupKey, list[Contribution]] = {}
    for key, contribution in feed_pairs:
        feed_by_key.setdefault(key, []).append(contribution)
    system_by_key: dict[GroupKey, list[Contribution]] = {}
    for key, contribution in system_pairs:
        system_by_key.setdefault(key, []).append(contribution)

    groups = tuple(
        _decide(
            key,
            _aggregate(Side.FEED, feed_by_key.get(key, ())),
            _aggregate(Side.SYSTEM, system_by_key.get(key, ())),
            policy,
        )
        for key in group_keys(feed_pairs, system_pairs)
    )
    ordered_unresolved = tuple(sorted(unresolved, key=lambda row: row.sort_key))

    return BatchReconcileResult(
        groups=groups,
        unresolved=ordered_unresolved,
        summary=summarize(
            groups,
            ordered_unresolved,
            feed_rows_in=sum(batch.row_count for batch in feeds),
            system_rows_in=len(system_rows),
        ),
    )
