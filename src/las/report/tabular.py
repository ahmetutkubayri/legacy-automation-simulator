"""Turning one reconciliation result into the four format-independent tables.

Nothing here decides anything. Every outcome, reason, total and difference is
read from T12 exactly as reconciliation left it; the only arithmetic is
counting, and the only judgement is which of the four sheets a record belongs
on. Where T12 published no value the cell is blank — never a zero, never a
recomputed total.

Row order is T12's own: groups by account reference then period, unresolved
rows in the order reconciliation established. The Exceptions sheet is a filter
over the reconciliation rows, not a re-sort, so a reader can follow one account
down either sheet and find it in the same place.
"""

from __future__ import annotations

from las.reconcile import (
    BatchReconcileResult,
    GroupResult,
    Side,
    UnresolvedRow,
)
from las.report.models import (
    ACCOUNTING_METRICS,
    BLANK,
    Cell,
    ReportSummary,
    Row,
    SheetId,
    Table,
    render_count,
    render_decimal,
)
from las.report.policy import (
    GROUP_REASON_ORDER,
    OUTCOME_DISPLAY_ORDER,
    UNRESOLVED_REASON_ORDER,
    ReportPolicy,
)

#: Units are joined with a pipe so a multi-unit side reads as one token that
#: no spreadsheet will mistake for a number or a list separator.
UNIT_SEPARATOR = "|"


def _text(value: str | None) -> Cell:
    """A cell from an optional token: absent and blank are the same thing."""
    return BLANK if value is None or value == "" else Cell(value)


def _decimal(value) -> Cell:
    """A cell from an optional exact decimal."""
    return BLANK if value is None else Cell(render_decimal(value))


def _units(group: GroupResult, side: Side) -> Cell:
    """The side's distinct units, sorted, joined — blank when it is absent."""
    aggregate = group.feed if side is Side.FEED else group.system
    if not aggregate.units:
        return BLANK
    return Cell(UNIT_SEPARATOR.join(sorted(aggregate.units)))


def group_row(group: GroupResult, policy: ReportPolicy) -> Row:
    """One reconciliation group as a row, with the fill its outcome earned."""
    return Row(
        cells=(
            Cell(group.key.account_ref),
            Cell(group.key.period),
            Cell(group.outcome.value),
            Cell(group.reason),
            _units(group, Side.FEED),
            _units(group, Side.SYSTEM),
            _decimal(group.feed.total),
            _decimal(group.system.total),
            _decimal(group.signed_difference),
            _decimal(group.absolute_difference),
            _decimal(group.variance_band),
            Cell(render_count(len(group.feed.contributions))),
            Cell(render_count(len(group.system.contributions))),
        ),
        fill=policy.fill_for(group.outcome),
    )


def unresolved_row(row: UnresolvedRow) -> Row:
    """One unresolved feed row.

    ``match_method``, ``external_ref``, ``period`` and ``unit`` are blank: the
    reconciliation record does not carry them, and a report may not go looking
    for values its input never published.
    """
    return Row(
        cells=(
            Cell(row.source),
            _text(row.worksheet),
            Cell(render_count(row.source_row)),
            Cell(render_count(row.row_index)),
            Cell(row.reason),
            _text(row.match_outcome),
            BLANK,  # match_method
            _text(row.account_ref),
            BLANK,  # external_ref
            BLANK,  # period
            BLANK,  # unit
        )
    )


def reconciliation_table(
    result: BatchReconcileResult, policy: ReportPolicy
) -> Table:
    """Every group, exactly once, in T12's order."""
    return Table(
        sheet=SheetId.RECONCILIATION,
        columns=policy.columns_for(SheetId.RECONCILIATION),
        rows=tuple(group_row(group, policy) for group in result.groups),
    )


def exceptions_table(result: BatchReconcileResult, policy: ReportPolicy) -> Table:
    """The unsettled groups only, filtered without re-sorting.

    Balanced and within-tolerance groups are absent by design: this sheet is
    what somebody has to act on, and a settled account is not that.
    """
    return Table(
        sheet=SheetId.EXCEPTIONS,
        columns=policy.columns_for(SheetId.EXCEPTIONS),
        rows=tuple(
            group_row(group, policy)
            for group in result.groups
            if policy.is_exception(group.outcome)
        ),
    )


def review_queue_table(result: BatchReconcileResult, policy: ReportPolicy) -> Table:
    """Every unresolved feed row, whatever stopped it.

    The sheet's name is not a filter. A row excluded for an unusable amount
    needs a person just as much as one matching flagged for review, and
    leaving either out would break the accounting the rest of the report
    depends on.
    """
    return Table(
        sheet=SheetId.REVIEW_QUEUE,
        columns=policy.columns_for(SheetId.REVIEW_QUEUE),
        rows=tuple(unresolved_row(row) for row in result.unresolved),
    )


def band_counts(
    result: BatchReconcileResult, policy: ReportPolicy
) -> tuple[tuple[str, int], ...]:
    """How many groups landed in each configured band, ascending."""
    counted = {band: 0 for band in policy.variance_bands}
    for group in result.groups:
        if group.variance_band is not None:
            counted[group.variance_band] += 1
    return tuple(
        (render_decimal(band), counted[band]) for band in policy.variance_bands
    )


def build_summary(
    result: BatchReconcileResult, policy: ReportPolicy
) -> ReportSummary:
    """The counts, every one of them stated whether or not it is zero.

    A zero row is information: it says the report looked and found none, which
    is a different statement from a row that was quietly left out.
    """
    summary = result.summary
    exception_count = sum(
        1 for group in result.groups if policy.is_exception(group.outcome)
    )
    accounting = {
        "feed_rows_input": summary.feed_rows_in,
        "system_rows_input": summary.system_rows_in,
        "feed_contributions": summary.feed_contributions,
        "system_contributions": summary.system_contributions,
        "unresolved_rows": summary.unresolved_count,
        "group_count": summary.group_count,
        "exception_group_count": exception_count,
        "review_queue_count": len(result.unresolved),
    }
    return ReportSummary(
        recipients=policy.recipients,
        accounting=accounting,
        group_outcomes=tuple(
            (outcome.value, summary.groups_by_outcome.get(outcome.value, 0))
            for outcome in OUTCOME_DISPLAY_ORDER
        ),
        group_reasons=tuple(
            (reason, summary.groups_by_reason.get(reason, 0))
            for reason in GROUP_REASON_ORDER
        ),
        unresolved_reasons=tuple(
            (reason, summary.unresolved_by_reason.get(reason, 0))
            for reason in UNRESOLVED_REASON_ORDER
        ),
        variance_bands=band_counts(result, policy),
    )


def summary_table(summary: ReportSummary, policy: ReportPolicy) -> Table:
    """The summary as one long, section-ordered table."""
    rows: list[Row] = [
        _summary_row("metadata", "schema_version", summary.schema_version),
        _summary_row("metadata", "recipients", summary.recipients_text),
    ]
    rows.extend(
        _summary_row("accounting", metric, render_count(summary.accounting[metric]))
        for metric in ACCOUNTING_METRICS
    )
    for section, entries in (
        ("group_outcome", summary.group_outcomes),
        ("group_reason", summary.group_reasons),
        ("unresolved_reason", summary.unresolved_reasons),
    ):
        rows.extend(
            _summary_row(section, key, render_count(count)) for key, count in entries
        )
    rows.extend(
        _summary_row("variance_band", f"upper_bound:{bound}", render_count(count))
        for bound, count in summary.variance_bands
    )
    return Table(
        sheet=SheetId.SUMMARY,
        columns=policy.columns_for(SheetId.SUMMARY),
        rows=tuple(rows),
    )


def _summary_row(section: str, metric: str, value: str) -> Row:
    return Row(cells=(Cell(section), Cell(metric), Cell(value)))
