"""Verify one reconciliation result, build one report model, render artifacts.

Three steps, in one direction
-----------------------------
1. **verify** — the whole T12 contract is checked before a single table exists.
2. **build** — one format-independent model is assembled from the result.
3. **render** — each configured format reads that model and nothing else.

Why verification comes first
----------------------------
A report is a statement about what reconciliation found. If a summary count
disagrees with the rows it summarizes, there is no honest way to publish
either: printing the summary hides rows, printing the detail contradicts the
summary, and adjusting one to match the other invents a reconciliation that
never happened. So a malformed result raises before any artifact exists, and
nothing is repaired.

The checks re-read T12's invariants; they never recompute its arithmetic. A
difference is compared against zero, or checked for presence or absence — it is
never recalculated from the totals, because a second implementation of the same
sum would eventually disagree with the first and there would be no way to know
which was right.
"""

from __future__ import annotations

from decimal import Decimal

from las.reconcile import BatchReconcileResult, ReconcileOutcome, Side
from las.report.documents import render_html, render_json
from las.report.errors import (
    DuplicateReportRowError,
    ReportContractError,
    UnsupportedFormatError,
)
from las.report.models import OutputFormat, RenderedArtifact, ReportModel, artifact
from las.report.policy import ReportPolicy
from las.report.tabular import (
    build_summary,
    exceptions_table,
    reconciliation_table,
    review_queue_table,
    summary_table,
)
from las.report.xlsx import render_xlsx

#: Outcomes for which T12 publishes no variance at all.
_NO_VARIANCE = (
    ReconcileOutcome.MISSING_IN_FEED,
    ReconcileOutcome.MISSING_IN_SYSTEM,
    ReconcileOutcome.UNIT_MISMATCH,
)


def _fail(message: str, **context) -> None:
    raise ReportContractError(message, operation="verify_result", **context)


def _check_identities(result: BatchReconcileResult) -> None:
    seen_keys: set[tuple[str, str]] = set()
    for group in result.groups:
        identity = group.key.sort_key
        if identity in seen_keys:
            raise DuplicateReportRowError(
                "two reconciliation groups share one business key",
                operation="verify_result",
                account_ref=group.key.account_ref,
                period=group.key.period,
            )
        seen_keys.add(identity)

    seen_rows: set[tuple[str, str | None, int]] = set()
    for row in result.unresolved:
        identity = (row.source, row.worksheet, row.source_row)
        if identity in seen_rows:
            raise DuplicateReportRowError(
                "two unresolved rows share one physical identity",
                operation="verify_result",
                row=row.source_row,
            )
        seen_rows.add(identity)


def _check_group_invariants(result: BatchReconcileResult) -> None:
    for group in result.groups:
        outcome, where = group.outcome, {
            "account_ref": group.key.account_ref,
            "period": group.key.period,
            "outcome": group.outcome.value,
        }
        if not isinstance(outcome, ReconcileOutcome) or not group.reason:
            _fail("a reconciliation group carries no outcome or reason", **where)

        published = (
            group.signed_difference,
            group.absolute_difference,
            group.variance_band,
        )
        if outcome in _NO_VARIANCE:
            if any(value is not None for value in published):
                _fail("this outcome must publish no variance at all", **where)
            continue

        if group.signed_difference is None or group.absolute_difference is None:
            _fail("a compared group must publish both differences", **where)
        if group.feed.total is None or group.system.total is None:
            _fail("a compared group must have one total on each side", **where)
        if group.feed.units != group.system.units or len(group.feed.units) != 1:
            _fail("a compared group must share exactly one unit", **where)

        absolute = group.absolute_difference
        if absolute != group.signed_difference.copy_abs():
            _fail("the absolute difference does not match the signed one", **where)

        if outcome is ReconcileOutcome.BALANCED:
            if absolute != Decimal(0) or group.variance_band is None:
                _fail("a balanced group must differ by exactly zero", **where)
        elif outcome is ReconcileOutcome.WITHIN_TOLERANCE:
            if absolute == Decimal(0) or group.variance_band is None:
                _fail("a tolerated group must differ by a banded amount", **where)
        elif group.variance_band is not None:
            _fail("a mismatched group must select no band", **where)


def _check_bands(result: BatchReconcileResult, policy: ReportPolicy) -> None:
    allowed = set(policy.variance_bands)
    for group in result.groups:
        if group.variance_band is not None and group.variance_band not in allowed:
            _fail(
                "a group selected a band the report policy does not declare",
                account_ref=group.key.account_ref,
                period=group.key.period,
                outcome=group.outcome.value,
            )


def _check_summary(result: BatchReconcileResult) -> None:
    summary = result.summary
    if summary.group_count != len(result.groups):
        _fail("the summary group count disagrees with the groups")
    if summary.unresolved_count != len(result.unresolved):
        _fail("the summary unresolved count disagrees with the unresolved rows")

    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    feed_contributions = system_contributions = 0
    for group in result.groups:
        outcomes[group.outcome.value] = outcomes.get(group.outcome.value, 0) + 1
        reasons[group.reason] = reasons.get(group.reason, 0) + 1
        feed_contributions += len(group.feed.contributions)
        system_contributions += len(group.system.contributions)
    if dict(summary.groups_by_outcome) != outcomes:
        _fail("the summary outcome counts disagree with the groups")
    if dict(summary.groups_by_reason) != reasons:
        _fail("the summary reason counts disagree with the groups")

    unresolved: dict[str, int] = {}
    for row in result.unresolved:
        unresolved[row.reason] = unresolved.get(row.reason, 0) + 1
    if dict(summary.unresolved_by_reason) != unresolved:
        _fail("the summary unresolved counts disagree with the unresolved rows")

    if summary.contributions_by_side.get(Side.FEED.value, 0) != feed_contributions:
        _fail("the summary feed contribution count disagrees with the groups")
    if summary.contributions_by_side.get(Side.SYSTEM.value, 0) != system_contributions:
        _fail("the summary system contribution count disagrees with the groups")
    if not summary.accounting_holds:
        _fail("the reconciliation accounting does not balance")


def verify_result(result: BatchReconcileResult, policy: ReportPolicy) -> None:
    """Refuse a reconciliation result the report cannot state faithfully."""
    if not isinstance(result, BatchReconcileResult):
        _fail("a report is built from one reconciliation result")
    _check_identities(result)
    _check_group_invariants(result)
    _check_bands(result, policy)
    _check_summary(result)


def build_report(result: BatchReconcileResult, policy: ReportPolicy) -> ReportModel:
    """Verify, then assemble the one model every renderer reads."""
    verify_result(result, policy)
    summary = build_summary(result, policy)
    return ReportModel(
        summary=summary,
        summary_table=summary_table(summary, policy),
        exceptions=exceptions_table(result, policy),
        reconciliation=reconciliation_table(result, policy),
        review_queue=review_queue_table(result, policy),
    )


#: One renderer per format, so adding a format is one entry and one function.
RENDERERS = {
    OutputFormat.XLSX: render_xlsx,
    OutputFormat.JSON: render_json,
    OutputFormat.HTML: render_html,
}


def render(report: ReportModel, output_format: OutputFormat, policy: ReportPolicy) -> bytes:
    """Render one format from an already-built model."""
    renderer = RENDERERS.get(output_format)
    if renderer is None:
        raise UnsupportedFormatError(
            "an output format outside the authorized set was requested",
            operation="render",
        )
    return renderer(report, policy)


def render_artifacts(
    result: BatchReconcileResult, policy: ReportPolicy
) -> tuple[RenderedArtifact, ...]:
    """Every configured format, in configured order, entirely in memory.

    No path, no filename, no write. The caller receives bytes and the fixed
    metadata describing them; where they belong is T14's decision.
    """
    report = build_report(result, policy)
    return tuple(
        artifact(output_format, render(report, output_format, policy))
        for output_format in policy.formats
    )
