"""T13 input contract: a malformed reconciliation result never becomes a report."""

from __future__ import annotations

import contextlib
import dataclasses
from decimal import Decimal

import pytest

from las.reconcile import (
    BatchReconcileResult,
    ReconcileOutcome,
    Side,
    SideAggregate,
    summarize,
)
from las.report import (
    DuplicateReportRowError,
    OutputFormat,
    ReportContractError,
    UnsupportedFormatError,
    build_report,
    render,
    render_artifacts,
    verify_result,
)
from report_helpers import aggregate, golden_result, group, make_policy

POLICY = make_policy()
RESULT = golden_result()


def rebuilt(groups=None, rows=None, *, summary=None) -> BatchReconcileResult:
    """A result whose summary is recomputed unless one is supplied."""
    groups = RESULT.groups if groups is None else tuple(groups)
    rows = RESULT.unresolved if rows is None else tuple(rows)
    return BatchReconcileResult(
        groups=groups,
        unresolved=rows,
        summary=summary
        or summarize(
            groups,
            rows,
            feed_rows_in=sum(len(g.feed.contributions) for g in groups) + len(rows),
            system_rows_in=sum(len(g.system.contributions) for g in groups),
        ),
    )


# --- the valid case ------------------------------------------------------------


def test_a_valid_result_passes_verification() -> None:
    verify_result(RESULT, POLICY)
    assert build_report(RESULT, POLICY).reconciliation.row_count == len(RESULT.groups)


def test_an_empty_result_is_valid() -> None:
    verify_result(rebuilt(groups=(), rows=()), POLICY)


def test_verification_is_not_a_reconciliation() -> None:
    """A total is read, never recomputed: only presence and zero are checked."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "src/las/report/engine.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, ast.Sub | ast.Div | ast.FloorDiv | ast.Mult)


# --- summary versus detail -----------------------------------------------------


def test_a_wrong_group_count_is_refused() -> None:
    broken = dataclasses.replace(
        RESULT.summary, group_count=RESULT.summary.group_count + 1
    )
    with pytest.raises(ReportContractError, match="group count disagrees"):
        verify_result(rebuilt(summary=broken), POLICY)


def test_a_wrong_unresolved_count_is_refused() -> None:
    broken = dataclasses.replace(RESULT.summary, unresolved_by_reason={"invalid_unit": 99})
    with pytest.raises(ReportContractError, match="unresolved"):
        verify_result(rebuilt(summary=broken), POLICY)


def test_wrong_outcome_counts_are_refused() -> None:
    broken = dataclasses.replace(RESULT.summary, groups_by_outcome={"balanced": 99})
    with pytest.raises(ReportContractError, match="outcome counts disagree"):
        verify_result(rebuilt(summary=broken), POLICY)


def test_wrong_reason_counts_are_refused() -> None:
    broken = dataclasses.replace(RESULT.summary, groups_by_reason={"totals_equal": 99})
    with pytest.raises(ReportContractError, match="reason counts disagree"):
        verify_result(rebuilt(summary=broken), POLICY)


def test_wrong_contribution_counts_are_refused() -> None:
    broken = dataclasses.replace(
        RESULT.summary, contributions_by_side={"feed": 999, "system": 999}
    )
    with pytest.raises(ReportContractError, match="contribution count disagrees"):
        verify_result(rebuilt(summary=broken), POLICY)


def test_accounting_that_does_not_balance_is_refused() -> None:
    broken = dataclasses.replace(RESULT.summary, feed_rows_in=9_999)
    with pytest.raises(ReportContractError, match="accounting does not balance"):
        verify_result(rebuilt(summary=broken), POLICY)


# --- identity ------------------------------------------------------------------


def test_a_duplicate_group_key_is_refused() -> None:
    twice = (*RESULT.groups, RESULT.groups[0])
    with pytest.raises(DuplicateReportRowError, match="one business key"):
        verify_result(rebuilt(groups=twice), POLICY)


def test_a_duplicate_unresolved_identity_is_refused() -> None:
    twice = (*RESULT.unresolved, RESULT.unresolved[0])
    with pytest.raises(DuplicateReportRowError, match="one physical identity"):
        verify_result(rebuilt(rows=twice), POLICY)


# --- per-outcome invariants ----------------------------------------------------


def test_a_unit_mismatch_carrying_a_variance_is_refused() -> None:
    bad = dataclasses.replace(
        next(g for g in RESULT.groups if g.outcome is ReconcileOutcome.UNIT_MISMATCH),
        signed_difference=Decimal("1"),
    )
    others = [g for g in RESULT.groups if g.outcome is not ReconcileOutcome.UNIT_MISMATCH]
    with pytest.raises(ReportContractError, match="no variance at all"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


@pytest.mark.parametrize(
    "outcome", [ReconcileOutcome.MISSING_IN_FEED, ReconcileOutcome.MISSING_IN_SYSTEM]
)
def test_a_missing_side_carrying_a_variance_is_refused(outcome) -> None:
    original = next(g for g in RESULT.groups if g.outcome is outcome)
    bad = dataclasses.replace(original, variance_band=Decimal("5"))
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="no variance at all"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_a_balanced_group_with_a_nonzero_difference_is_refused() -> None:
    original = next(g for g in RESULT.groups if g.outcome is ReconcileOutcome.BALANCED)
    bad = dataclasses.replace(
        original, signed_difference=Decimal("2"), absolute_difference=Decimal("2")
    )
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="exactly zero"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_a_tolerated_group_with_a_zero_difference_is_refused() -> None:
    original = next(
        g for g in RESULT.groups if g.outcome is ReconcileOutcome.WITHIN_TOLERANCE
    )
    bad = dataclasses.replace(
        original, signed_difference=Decimal("0"), absolute_difference=Decimal("0")
    )
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="banded amount"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_a_mismatch_that_selected_a_band_is_refused() -> None:
    original = next(g for g in RESULT.groups if g.outcome is ReconcileOutcome.MISMATCH)
    bad = dataclasses.replace(original, variance_band=Decimal("5"))
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="select no band"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_a_compared_group_missing_a_difference_is_refused() -> None:
    original = next(g for g in RESULT.groups if g.outcome is ReconcileOutcome.BALANCED)
    bad = dataclasses.replace(original, absolute_difference=None)
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="both differences"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_a_difference_pair_that_disagrees_is_refused() -> None:
    original = next(g for g in RESULT.groups if g.outcome is ReconcileOutcome.MISMATCH)
    bad = dataclasses.replace(original, absolute_difference=Decimal("1"))
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="absolute difference does not match"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_a_compared_group_without_two_totals_is_refused() -> None:
    bad = group(
        "ACC-999999", "2026-01", ReconcileOutcome.BALANCED, "totals_equal",
        SideAggregate(side=Side.FEED),
        aggregate(Side.SYSTEM, ("1.00", "EA")),
        signed="0", absolute="0", band="0",
    )
    with pytest.raises(ReportContractError, match="one total on each side"):
        verify_result(rebuilt(groups=(bad,), rows=()), POLICY)


def test_a_compared_group_with_incompatible_units_is_refused() -> None:
    bad = group(
        "ACC-999999", "2026-01", ReconcileOutcome.BALANCED, "totals_equal",
        aggregate(Side.FEED, ("1.00", "EA")),
        aggregate(Side.SYSTEM, ("1.00", "KG")),
        signed="0", absolute="0", band="0",
    )
    with pytest.raises(ReportContractError, match="exactly one unit"):
        verify_result(rebuilt(groups=(bad,), rows=()), POLICY)


def test_a_group_without_a_reason_is_refused() -> None:
    original = RESULT.groups[0]
    bad = dataclasses.replace(original, reason="")
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="no outcome or reason"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_a_band_the_policy_does_not_declare_is_refused() -> None:
    original = next(g for g in RESULT.groups if g.outcome is ReconcileOutcome.BALANCED)
    bad = dataclasses.replace(original, variance_band=Decimal("99"))
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError, match="band the report policy"):
        verify_result(rebuilt(groups=(*others, bad)), POLICY)


def test_something_that_is_not_a_reconciliation_result_is_refused() -> None:
    with pytest.raises(ReportContractError, match="one reconciliation result"):
        verify_result(object(), POLICY)  # type: ignore[arg-type]


# --- failure produces nothing ---------------------------------------------------


def test_no_artifact_is_produced_from_a_malformed_result() -> None:
    broken = rebuilt(summary=dataclasses.replace(RESULT.summary, group_count=99))
    produced = None
    with contextlib.suppress(ReportContractError):
        produced = render_artifacts(broken, POLICY)
    assert produced is None


def test_a_contract_error_names_no_cell_value() -> None:
    original = next(g for g in RESULT.groups if g.outcome is ReconcileOutcome.MISMATCH)
    bad = dataclasses.replace(original, absolute_difference=Decimal("1"))
    others = [g for g in RESULT.groups if g is not original]
    with pytest.raises(ReportContractError) as excinfo:
        verify_result(rebuilt(groups=(*others, bad)), POLICY)
    message = str(excinfo.value)
    assert "89.25" not in message
    assert "0x" not in message
    # Safe categorical context only.
    assert excinfo.value.account_ref == "ACC-100001"
    assert excinfo.value.outcome == "mismatch"


def test_the_input_is_unchanged_after_a_failure() -> None:
    broken = rebuilt(summary=dataclasses.replace(RESULT.summary, group_count=99))
    before = (broken.groups, broken.unresolved, broken.summary.group_count)
    with pytest.raises(ReportContractError):
        build_report(broken, POLICY)
    assert (broken.groups, broken.unresolved, broken.summary.group_count) == before


def test_a_valid_result_is_never_mutated_by_reporting() -> None:
    before = (RESULT.groups, RESULT.unresolved, dict(RESULT.summary.groups_by_outcome))
    render_artifacts(RESULT, POLICY)
    assert (
        RESULT.groups,
        RESULT.unresolved,
        dict(RESULT.summary.groups_by_outcome),
    ) == before


# --- artifacts -----------------------------------------------------------------


def test_artifacts_arrive_in_the_configured_format_order() -> None:
    artifacts = render_artifacts(RESULT, POLICY)
    assert [a.output_format for a in artifacts] == list(POLICY.formats)
    assert [a.suffix for a in artifacts] == [".xlsx", ".json", ".html"]


def test_a_reordered_format_list_reorders_the_artifacts() -> None:
    policy = make_policy(formats=(OutputFormat.HTML, OutputFormat.JSON))
    artifacts = render_artifacts(RESULT, policy)
    assert [a.output_format for a in artifacts] == [OutputFormat.HTML, OutputFormat.JSON]


def test_every_artifact_carries_bytes_and_fixed_metadata() -> None:
    for item in render_artifacts(RESULT, POLICY):
        assert isinstance(item.content, bytes) and item.size > 0
        assert item.suffix.startswith(".")
        assert "/" in item.media_type


def test_rendering_an_unknown_format_is_refused() -> None:
    report = build_report(RESULT, POLICY)
    with pytest.raises(UnsupportedFormatError):
        render(report, "pdf", POLICY)  # type: ignore[arg-type]


def test_repeated_renders_of_the_documents_are_byte_identical() -> None:
    first = render_artifacts(RESULT, POLICY)
    second = render_artifacts(RESULT, POLICY)
    for left, right in zip(first, second, strict=True):
        if left.output_format is OutputFormat.XLSX:
            continue  # semantic equality only; see test_report_xlsx
        assert left.content == right.content


# --- the defensive failure paths ------------------------------------------------


def test_only_the_system_contribution_count_disagreeing_is_refused() -> None:
    """The feed check passes first, so the system branch needs its own case."""
    correct = RESULT.summary.contributions_by_side
    broken = dataclasses.replace(
        RESULT.summary,
        contributions_by_side={"feed": correct["feed"], "system": correct["system"] + 1},
    )
    with pytest.raises(ReportContractError, match="system contribution count"):
        verify_result(rebuilt(summary=broken), POLICY)


def test_a_renderer_failure_becomes_a_typed_error_without_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from las.report import RenderError, documents, xlsx

    report = build_report(RESULT, POLICY)

    def explode_json(summary):
        raise TypeError("a secret cell value that must not travel")

    monkeypatch.setattr(documents, "summary_payload", explode_json)
    with pytest.raises(RenderError, match="json rendering raised TypeError") as excinfo:
        documents.render_json(report, POLICY)
    assert "a secret cell value" not in str(excinfo.value)

    def explode_html(*args, **kwargs):
        raise ValueError("another secret")

    monkeypatch.setattr(documents, "_table", explode_html)
    with pytest.raises(RenderError, match="html rendering raised ValueError") as excinfo:
        documents.render_html(report, POLICY)
    assert "another secret" not in str(excinfo.value)

    def explode_workbook(*args, **kwargs):
        raise ValueError("a workbook secret")

    monkeypatch.setattr(xlsx, "Workbook", explode_workbook)
    with pytest.raises(RenderError, match="workbook rendering raised ValueError") as excinfo:
        xlsx.render_xlsx(report, POLICY)
    assert "a workbook secret" not in str(excinfo.value)


def test_an_unresolved_breakdown_that_totals_correctly_but_differs_is_refused() -> None:
    """Right total, wrong shape: six rows filed under one reason instead of six."""
    broken = dataclasses.replace(
        RESULT.summary, unresolved_by_reason={"invalid_unit": len(RESULT.unresolved)}
    )
    assert broken.unresolved_count == len(RESULT.unresolved)
    with pytest.raises(ReportContractError, match="unresolved counts disagree"):
        verify_result(rebuilt(summary=broken), POLICY)
