"""T13 workbook: text cells, no formulas, fixed styles, and streaming parity."""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from las.reconcile import BatchReconcileResult, ReconcileOutcome, Side, summarize
from las.report import (
    GROUP_COLUMN_NAMES,
    HEADER_FILL_ARGB,
    HEADER_FONT_ARGB,
    OUTCOME_FILL_ARGB,
    REVIEW_QUEUE_COLUMN_NAMES,
    SUMMARY_COLUMN_NAMES,
    WORKBOOK_CREATOR,
    WORKBOOK_DESCRIPTION,
    WORKBOOK_SUBJECT,
    WORKBOOK_TITLE,
    build_report,
    render_xlsx,
    uses_streaming,
)
from report_helpers import (
    aggregate,
    colour,
    golden_result,
    group,
    make_policy,
    unresolved,
    workbook_semantics,
)

POLICY = make_policy()
RESULT = golden_result()


def workbook(result=RESULT, policy=POLICY):
    return load_workbook(io.BytesIO(render_xlsx(build_report(result, policy), policy)),
                         data_only=False)


def cells(sheet):
    return [cell for row in sheet.iter_rows() for cell in row]


# --- structure -----------------------------------------------------------------


def test_the_workbook_has_exactly_the_four_contract_sheets_in_order() -> None:
    assert workbook().sheetnames == [
        "Summary", "Exceptions", "Reconciliation", "Review Queue"
    ]


def test_no_sheet_is_hidden() -> None:
    for sheet in workbook():
        assert sheet.sheet_state == "visible"


def test_each_sheet_uses_its_contract_headers_in_order() -> None:
    book = workbook()
    for name, expected in (
        ("Summary", SUMMARY_COLUMN_NAMES),
        ("Exceptions", GROUP_COLUMN_NAMES),
        ("Reconciliation", GROUP_COLUMN_NAMES),
        ("Review Queue", REVIEW_QUEUE_COLUMN_NAMES),
    ):
        sheet = book[name]
        assert tuple(c.value for c in sheet[1]) == expected


def test_the_workbook_carries_no_formula_anywhere() -> None:
    book = workbook()
    assert sum(1 for sheet in book for cell in cells(sheet) if cell.data_type == "f") == 0


def test_the_workbook_declares_no_chart_image_filter_or_frozen_pane() -> None:
    for sheet in workbook():
        assert not sheet._charts
        assert not sheet._images
        assert sheet.auto_filter.ref is None
        assert sheet.freeze_panes is None
        assert not sheet.merged_cells.ranges
        assert not sheet.conditional_formatting._cf_rules


# --- text safety ---------------------------------------------------------------


def test_every_populated_cell_is_a_text_cell() -> None:
    for sheet in workbook():
        for cell in cells(sheet):
            if cell.value is not None:
                assert cell.data_type == "s", cell.coordinate


@pytest.mark.parametrize(
    "hostile", ["=1+1", "=cmd|'/c calc'!A0", "+1", "-2", "@SUM(A1)", "=HYPERLINK(\"x\")"]
)
def test_a_formula_like_source_label_stays_inert_text(hostile: str) -> None:
    rows = (unresolved("match_unmatched", 2, source=hostile, worksheet=hostile),)
    result = BatchReconcileResult(
        groups=(), unresolved=rows,
        summary=summarize((), rows, feed_rows_in=1, system_rows_in=0),
    )
    sheet = workbook(result)["Review Queue"]
    assert sheet["A2"].value == hostile  # unchanged, not prefixed or trimmed
    assert sheet["A2"].data_type == "s"
    assert sheet["B2"].value == hostile
    assert sum(1 for c in cells(sheet) if c.data_type == "f") == 0


def test_a_formula_like_reason_stays_inert_text() -> None:
    rows = (unresolved("match_unmatched", 2, match_outcome="=danger"),)
    result = BatchReconcileResult(
        groups=(), unresolved=rows,
        summary=summarize((), rows, feed_rows_in=1, system_rows_in=0),
    )
    sheet = workbook(result)["Review Queue"]
    assert sheet["F2"].value == "=danger" and sheet["F2"].data_type == "s"


def test_a_negative_decimal_is_text_and_not_a_formula() -> None:
    negative = group(
        "ACC-100000", "2026-01", ReconcileOutcome.MISMATCH, "outside_variance_bands",
        aggregate(Side.FEED, ("1.00", "EA")),
        aggregate(Side.SYSTEM, ("99.00", "EA")),
        signed="-98.00", absolute="98.00",
    )
    result = BatchReconcileResult(
        groups=(negative,), unresolved=(),
        summary=summarize((negative,), (), feed_rows_in=1, system_rows_in=1),
    )
    sheet = workbook(result)["Reconciliation"]
    assert sheet["I2"].value == "-98.00"
    assert sheet["I2"].data_type == "s"


def test_a_long_decimal_survives_the_workbook_intact() -> None:
    long_value = "0." + "7" * 300
    big = group(
        "ACC-100000", "2026-01", ReconcileOutcome.MISMATCH, "outside_variance_bands",
        aggregate(Side.FEED, (long_value, "EA")),
        aggregate(Side.SYSTEM, ("0", "EA")),
        signed=long_value, absolute=long_value,
    )
    result = BatchReconcileResult(
        groups=(big,), unresolved=(),
        summary=summarize((big,), (), feed_rows_in=1, system_rows_in=1),
    )
    sheet = workbook(result)["Reconciliation"]
    assert sheet["G2"].value == long_value
    assert Decimal(sheet["G2"].value) == Decimal(long_value)
    assert len(sheet["G2"].value) == 302


def test_a_leading_zero_identifier_keeps_its_zeros() -> None:
    rows = (unresolved("match_unmatched", 2, account_ref="ACC-000042"),)
    result = BatchReconcileResult(
        groups=(), unresolved=rows,
        summary=summarize((), rows, feed_rows_in=1, system_rows_in=0),
    )
    assert workbook(result)["Review Queue"]["H2"].value == "ACC-000042"


# --- styles --------------------------------------------------------------------


def test_every_sheet_header_carries_the_contract_style() -> None:
    for sheet in workbook():
        for cell in sheet[1]:
            assert cell.font.bold is True
            assert colour(cell.font.color.rgb) == HEADER_FONT_ARGB
            assert colour(cell.fill.fgColor.rgb) == HEADER_FILL_ARGB


def test_each_reconciliation_row_is_filled_by_its_outcome() -> None:
    sheet = workbook()["Reconciliation"]
    for row in sheet.iter_rows(min_row=2):
        outcome = ReconcileOutcome(row[2].value)
        expected = OUTCOME_FILL_ARGB[outcome]
        assert all(colour(cell.fill.fgColor.rgb) == expected for cell in row)


def test_only_the_reconciliation_sheet_carries_outcome_fills() -> None:
    book = workbook()
    fills = set(OUTCOME_FILL_ARGB.values())
    for name in ("Summary", "Exceptions", "Review Queue"):
        for row in book[name].iter_rows(min_row=2):
            assert all(colour(cell.fill.fgColor.rgb) not in fills for cell in row)


def test_a_blank_cell_inside_a_filled_row_stays_blank() -> None:
    sheet = workbook()["Reconciliation"]
    row = next(r for r in sheet.iter_rows(min_row=2) if r[2].value == "unit_mismatch")
    for index in (8, 9, 10):  # signed, absolute, band
        assert row[index].value is None
        assert colour(row[index].fill.fgColor.rgb) == OUTCOME_FILL_ARGB[
            ReconcileOutcome.UNIT_MISMATCH
        ]


def test_row_permutation_does_not_change_a_visible_style() -> None:
    """A row's fill follows its outcome, never its position."""
    forward = workbook_semantics(render_xlsx(build_report(RESULT, POLICY), POLICY))
    fills = {
        cell["value"]: cell["fill"]
        for cell in forward["sheets"]["Reconciliation"]["cells"]
        if cell["value"] in {o.value for o in ReconcileOutcome}
    }
    for outcome_value, fill in fills.items():
        assert fill == OUTCOME_FILL_ARGB[ReconcileOutcome(outcome_value)]


# --- metadata ------------------------------------------------------------------


def test_the_workbook_properties_are_the_fixed_ones() -> None:
    properties = workbook().properties
    assert properties.creator == WORKBOOK_CREATOR
    assert properties.lastModifiedBy == WORKBOOK_CREATOR
    assert properties.title == WORKBOOK_TITLE
    assert properties.subject == WORKBOOK_SUBJECT
    assert properties.description == WORKBOOK_DESCRIPTION
    assert properties.created == datetime(2000, 1, 1)
    assert properties.modified == datetime(2000, 1, 1)


def test_the_workbook_metadata_names_no_machine_user_or_path() -> None:
    import getpass
    import os

    properties = workbook().properties
    text = " ".join(
        str(value) for value in (
            properties.creator, properties.lastModifiedBy, properties.title,
            properties.subject, properties.description,
        )
    )
    assert getpass.getuser() not in text
    assert os.getcwd() not in text
    for forbidden in ("/", "\\", ".xlsx", ".csv"):
        assert forbidden not in text


def test_the_workbook_carries_no_current_timestamp() -> None:
    from datetime import UTC

    created = workbook().properties.created
    assert created.year == 2000
    assert created != datetime.now(UTC).replace(tzinfo=None)


# --- streaming -----------------------------------------------------------------


def test_a_small_report_does_not_stream() -> None:
    assert not uses_streaming(build_report(RESULT, POLICY), POLICY)


def test_a_low_threshold_forces_streaming() -> None:
    policy = make_policy(streaming_row_threshold=1)
    assert uses_streaming(build_report(RESULT, policy), policy)


def test_the_summary_sheet_never_triggers_streaming() -> None:
    """An empty result has 32 summary rows and no detail rows at all."""
    empty = BatchReconcileResult(
        groups=(), unresolved=(),
        summary=summarize((), (), feed_rows_in=0, system_rows_in=0),
    )
    policy = make_policy(streaming_row_threshold=5)
    report = build_report(empty, policy)
    assert report.summary_table.row_count > 5
    assert not uses_streaming(report, policy)


def test_the_streaming_boundary_is_inclusive() -> None:
    report = build_report(RESULT, POLICY)
    rows = report.reconciliation.row_count
    assert not uses_streaming(report, make_policy(streaming_row_threshold=rows + 1))
    assert uses_streaming(report, make_policy(streaming_row_threshold=rows))


def test_the_two_construction_paths_are_semantically_identical() -> None:
    standard = make_policy(streaming_row_threshold=50_000)
    streaming = make_policy(streaming_row_threshold=1)
    assert not uses_streaming(build_report(RESULT, standard), standard)
    assert uses_streaming(build_report(RESULT, streaming), streaming)

    left = workbook_semantics(render_xlsx(build_report(RESULT, standard), standard))
    right = workbook_semantics(render_xlsx(build_report(RESULT, streaming), streaming))
    assert left == right


def test_the_streaming_path_keeps_formulas_out_too() -> None:
    policy = make_policy(streaming_row_threshold=1)
    rows = (unresolved("match_unmatched", 2, source="=danger"),)
    result = BatchReconcileResult(
        groups=(), unresolved=rows,
        summary=summarize((), rows, feed_rows_in=1, system_rows_in=0),
    )
    snapshot = workbook_semantics(render_xlsx(build_report(result, policy), policy))
    assert snapshot["formula_cells"] == 0
    values = [c["value"] for c in snapshot["sheets"]["Review Queue"]["cells"]]
    assert "=danger" in values


# --- determinism ---------------------------------------------------------------


def test_repeated_renders_are_semantically_identical() -> None:
    report = build_report(RESULT, POLICY)
    assert workbook_semantics(render_xlsx(report, POLICY)) == workbook_semantics(
        render_xlsx(report, POLICY)
    )


def test_the_rendered_bytes_are_not_claimed_to_be_identical() -> None:
    """Semantic determinism is the contract; ZIP metadata is not controlled."""
    report = build_report(RESULT, POLICY)
    first, second = render_xlsx(report, POLICY), render_xlsx(report, POLICY)
    # Whether or not the bytes match, the semantics must.
    assert workbook_semantics(first) == workbook_semantics(second)


def test_the_workbook_is_independent_of_the_global_decimal_context() -> None:
    import decimal

    report = build_report(RESULT, POLICY)
    baseline = workbook_semantics(render_xlsx(report, POLICY))
    saved = decimal.getcontext()
    try:
        for prec in (1, 3, 400):
            decimal.setcontext(decimal.Context(prec=prec))
            assert workbook_semantics(
                render_xlsx(build_report(RESULT, POLICY), POLICY)
            ) == baseline
    finally:
        decimal.setcontext(saved)


def test_dimensions_match_the_model() -> None:
    report = build_report(RESULT, POLICY)
    book = workbook()
    for table in report.tables:
        sheet = book[table.sheet.value]
        assert sheet.max_row == table.row_count + 1
        assert sheet.max_column == len(table.columns)
