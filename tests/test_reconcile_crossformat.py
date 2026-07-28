"""T12 cross-format consistency: one logical feed, reconciled from CSV and XLSX.

Each test writes the same records twice, carries both through T9, T10 and T11,
reconciles each against the same system side, and proves the verdicts agree.
Only genuinely format-specific provenance — the source label and the worksheet
name — is allowed to differ.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from las.ingest import FieldSpec, IngestSchema, read_source
from las.match import build_master_index, match_validated
from las.reconcile import (
    BatchReconcileResult,
    FeedBatch,
    ReconcileOutcome,
    SystemLedgerRow,
    reconcile,
)
from las.validate import ValidationPolicy, validate_ingest_result
from match_helpers import account_row, link_row
from match_helpers import make_policy as match_policy
from reconcile_helpers import ACCOUNT_A, PERIOD_A, PERIOD_B, make_policy

HEADERS = ["record_id", "external_ref", "amount", "unit", "period"]

SCHEMA = IngestSchema(
    fields=(
        FieldSpec("amount"),
        FieldSpec("external_ref", required=False),
        FieldSpec("period"),
        FieldSpec("record_id"),
        FieldSpec("unit", required=False),
    )
)
VALIDATION_POLICY = ValidationPolicy(
    required_fields=frozenset({"record_id", "amount", "period"}),
    record_id_pattern=r"^OPR-\d{8}$",
    external_ref_pattern=r"^EXT-\d{5}$",
    amount_min=Decimal("0.0"),
    amount_max=Decimal("1000000.0"),
    duplicate_key_fields=("record_id",),
)
MATCH_POLICY = match_policy()
INDEX = build_master_index(
    [account_row(ACCOUNT_A, "Demo Account 000000", 2)],
    [link_row("EXT-10000", ACCOUNT_A, 2)],
    max_name_length=MATCH_POLICY.max_name_length,
)
RECONCILE_POLICY = make_policy()


def write_pair(tmp_path: Path, rows: list[list], *, xlsx_rows: list[list] | None = None):
    csv_path = tmp_path / "probe.csv"
    lines = [",".join(HEADERS)]
    for row in rows:
        lines.append(",".join("" if cell is None else cell for cell in row))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(HEADERS)
    for row in xlsx_rows if xlsx_rows is not None else rows:
        sheet.append(row)
    xlsx_path = tmp_path / "probe.xlsx"
    workbook.save(xlsx_path)
    workbook.close()
    return csv_path, xlsx_path


def batch_for(path: Path) -> FeedBatch:
    validated = validate_ingest_result(read_source(path, SCHEMA), VALIDATION_POLICY)
    return FeedBatch(
        validation=validated,
        matching=match_validated(validated, INDEX, MATCH_POLICY),
    )


def system_side(*specs: tuple[str, str, str]) -> tuple[SystemLedgerRow, ...]:
    return tuple(
        SystemLedgerRow(
            account_ref=ACCOUNT_A,
            period=period,
            unit=unit,
            amount_text=amount,
            position=position,
        )
        for position, (period, unit, amount) in enumerate(specs)
    )


def reconcile_both(
    tmp_path: Path, rows: list[list], system: tuple[SystemLedgerRow, ...],
    *, xlsx_rows: list[list] | None = None,
) -> tuple[BatchReconcileResult, BatchReconcileResult]:
    csv_path, xlsx_path = write_pair(tmp_path, rows, xlsx_rows=xlsx_rows)
    return (
        reconcile([batch_for(csv_path)], system, RECONCILE_POLICY),
        reconcile([batch_for(xlsx_path)], system, RECONCILE_POLICY),
    )


def shape(result: BatchReconcileResult) -> list[tuple]:
    """Everything about a result except its format-specific provenance."""
    return [
        (
            group.key.sort_key,
            group.outcome.value,
            group.reason,
            group.signed_difference,
            group.absolute_difference,
            group.variance_band,
            tuple(group.feed.totals_by_unit.items()),
            tuple(group.system.totals_by_unit.items()),
            tuple((c.source_row, c.row_index, c.unit, c.amount) for c in group.feed.contributions),
        )
        for group in result.groups
    ]


VALID = ["OPR-00000001", "EXT-10000", "10.50", "EA", PERIOD_A]


# --- identical outcomes -------------------------------------------------------


def test_a_balanced_table_reconciles_alike_in_both_formats(tmp_path: Path) -> None:
    from_csv, from_xlsx = reconcile_both(
        tmp_path, [VALID], system_side((PERIOD_A, "EA", "10.50"))
    )
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.groups[0].outcome is ReconcileOutcome.BALANCED
    assert from_xlsx.groups[0].outcome is ReconcileOutcome.BALANCED


def test_identical_group_keys_in_both_formats(tmp_path: Path) -> None:
    rows = [
        VALID,
        ["OPR-00000002", "EXT-10000", "20.25", "EA", PERIOD_B],
    ]
    from_csv, from_xlsx = reconcile_both(
        tmp_path, rows, system_side((PERIOD_A, "EA", "10.50"), (PERIOD_B, "EA", "20.25"))
    )
    assert [g.key for g in from_csv.groups] == [g.key for g in from_xlsx.groups]
    assert shape(from_csv) == shape(from_xlsx)


def test_identical_decimal_totals_in_both_formats(tmp_path: Path) -> None:
    rows = [VALID, ["OPR-00000002", "EXT-10000", "0.1", "EA", PERIOD_A]]
    from_csv, from_xlsx = reconcile_both(
        tmp_path, rows, system_side((PERIOD_A, "EA", "10.60"))
    )
    assert from_csv.groups[0].feed.total == from_xlsx.groups[0].feed.total == Decimal("10.60")
    assert from_csv.groups[0].outcome is ReconcileOutcome.BALANCED
    assert shape(from_csv) == shape(from_xlsx)


def test_native_numeric_cells_reconcile_the_same_as_their_text_form(
    tmp_path: Path,
) -> None:
    """A spreadsheet number and its text twin must total identically."""
    from_csv, from_xlsx = reconcile_both(
        tmp_path,
        [["OPR-00000001", "EXT-10000", "52", "EA", PERIOD_A]],
        system_side((PERIOD_A, "EA", "52")),
        xlsx_rows=[["OPR-00000001", "EXT-10000", 52, "EA", PERIOD_A]],
    )
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.groups[0].feed.total == Decimal(52)


def test_identical_unit_mismatch_behaviour(tmp_path: Path) -> None:
    rows = [VALID, ["OPR-00000002", "EXT-10000", "5.00", "KG", PERIOD_A]]
    from_csv, from_xlsx = reconcile_both(
        tmp_path, rows, system_side((PERIOD_A, "EA", "10.50"))
    )
    assert shape(from_csv) == shape(from_xlsx)
    for result in (from_csv, from_xlsx):
        group = result.groups[0]
        assert group.outcome is ReconcileOutcome.UNIT_MISMATCH
        assert group.signed_difference is None
        assert dict(group.feed.totals_by_unit) == {"EA": Decimal("10.50"), "KG": Decimal("5.00")}


def test_identical_variance_banding(tmp_path: Path) -> None:
    from_csv, from_xlsx = reconcile_both(
        tmp_path,
        [["OPR-00000001", "EXT-10000", "11.50", "EA", PERIOD_A]],
        system_side((PERIOD_A, "EA", "10.50")),
    )
    assert shape(from_csv) == shape(from_xlsx)
    for result in (from_csv, from_xlsx):
        assert result.groups[0].outcome is ReconcileOutcome.WITHIN_TOLERANCE
        assert result.groups[0].variance_band == Decimal(1)


def test_identical_summary_counts(tmp_path: Path) -> None:
    rows = [VALID, ["OPR-00000002", "EXT-10000", "99.00", "EA", PERIOD_B]]
    from_csv, from_xlsx = reconcile_both(
        tmp_path, rows, system_side((PERIOD_A, "EA", "10.50"), (PERIOD_B, "EA", "1.00"))
    )
    assert from_csv.summary.groups_by_outcome == from_xlsx.summary.groups_by_outcome
    assert from_csv.summary.groups_by_reason == from_xlsx.summary.groups_by_reason
    assert from_csv.summary.contributions_by_side == from_xlsx.summary.contributions_by_side
    assert from_csv.summary.feed_rows_in == from_xlsx.summary.feed_rows_in


def test_identical_unresolved_handling(tmp_path: Path) -> None:
    """An empty CSV field and an absent spreadsheet cell agree, as in T9."""
    rows = [["OPR-00000001", "EXT-10000", None, "EA", PERIOD_A]]
    from_csv, from_xlsx = reconcile_both(
        tmp_path, rows, system_side((PERIOD_A, "EA", "10.50"))
    )
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.summary.unresolved_by_reason == from_xlsx.summary.unresolved_by_reason
    assert len(from_csv.unresolved) == len(from_xlsx.unresolved) == 1


def test_identical_contribution_ordering(tmp_path: Path) -> None:
    rows = [
        VALID,
        ["OPR-00000002", "EXT-10000", "1.00", "EA", PERIOD_A],
        ["OPR-00000003", "EXT-10000", "2.00", "EA", PERIOD_A],
    ]
    from_csv, from_xlsx = reconcile_both(
        tmp_path, rows, system_side((PERIOD_A, "EA", "13.50"))
    )
    csv_rows = [(c.source_row, c.amount) for c in from_csv.groups[0].feed.contributions]
    xlsx_rows = [(c.source_row, c.amount) for c in from_xlsx.groups[0].feed.contributions]
    assert csv_rows == xlsx_rows


def test_duplicates_contribute_twice_in_both_formats(tmp_path: Path) -> None:
    from_csv, from_xlsx = reconcile_both(
        tmp_path, [VALID, list(VALID)], system_side((PERIOD_A, "EA", "21.00"))
    )
    assert shape(from_csv) == shape(from_xlsx)
    for result in (from_csv, from_xlsx):
        assert len(result.groups[0].feed.contributions) == 2
        assert result.groups[0].feed.total == Decimal("21.00")
        assert result.groups[0].outcome is ReconcileOutcome.BALANCED


# --- the permitted divergence -------------------------------------------------


def test_only_the_source_label_and_worksheet_differ(tmp_path: Path) -> None:
    from_csv, from_xlsx = reconcile_both(
        tmp_path, [VALID], system_side((PERIOD_A, "EA", "10.50"))
    )
    csv_contribution = from_csv.groups[0].feed.contributions[0]
    xlsx_contribution = from_xlsx.groups[0].feed.contributions[0]

    assert csv_contribution.source == "probe.csv"
    assert xlsx_contribution.source == "probe.xlsx"
    assert csv_contribution.worksheet is None
    assert xlsx_contribution.worksheet == "records"
    # Everything that is not provenance is identical.
    assert csv_contribution.amount == xlsx_contribution.amount
    assert csv_contribution.unit == xlsx_contribution.unit
    assert csv_contribution.source_row == xlsx_contribution.source_row


def test_a_source_label_never_decides_a_side_or_an_outcome(tmp_path: Path) -> None:
    """Provenance is evidence; it takes no part in the verdict."""
    from_csv, from_xlsx = reconcile_both(
        tmp_path, [VALID], system_side((PERIOD_A, "EA", "99.00"))
    )
    assert from_csv.groups[0].outcome is from_xlsx.groups[0].outcome
    assert from_csv.groups[0].reason == from_xlsx.groups[0].reason
    assert [c.side for c in from_csv.groups[0].feed.contributions] == [
        c.side for c in from_xlsx.groups[0].feed.contributions
    ]


@pytest.mark.parametrize("period", [PERIOD_A, PERIOD_B])
def test_the_group_key_is_format_independent(tmp_path: Path, period: str) -> None:
    rows = [["OPR-00000001", "EXT-10000", "10.50", "EA", period]]
    from_csv, from_xlsx = reconcile_both(tmp_path, rows, system_side((period, "EA", "10.50")))
    assert from_csv.groups[0].key == from_xlsx.groups[0].key
    assert from_csv.groups[0].key.period == period
