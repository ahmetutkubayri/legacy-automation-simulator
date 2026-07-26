"""T10 cross-format consistency: one logical table, ingested and validated twice.

Each test writes the same records as a CSV and as an XLSX, ingests both through
T9, validates both, and proves the verdicts agree. Only genuinely
format-specific metadata — the worksheet name — is allowed to differ.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from las.ingest import FieldSpec, IngestSchema, read_source
from las.validate import (
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_DUPLICATE_KEY,
    CODE_EXTERNAL_REF_UNKNOWN,
    CODE_RECORD_ID_FORMAT_INVALID,
    CODE_REQUIRED_VALUE_MISSING,
    BatchValidationResult,
    validate_ingest_result,
)
from validate_helpers import make_policy

HEADERS = ["record_id", "external_ref", "amount", "unit", "period"]

SCHEMA = IngestSchema(
    fields=(
        FieldSpec("amount"),
        FieldSpec("external_ref"),
        FieldSpec("period"),
        FieldSpec("record_id"),
        FieldSpec("unit", required=False),
    )
)
POLICY = make_policy(known_external_refs=("EXT-10000", "EXT-10001"))


def write_pair(
    tmp_path: Path, rows: list[list], *, xlsx_rows: list[list] | None = None
) -> tuple[Path, Path]:
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


def validate_both(
    csv_path: Path, xlsx_path: Path
) -> tuple[BatchValidationResult, BatchValidationResult]:
    return (
        validate_ingest_result(read_source(csv_path, SCHEMA), POLICY),
        validate_ingest_result(read_source(xlsx_path, SCHEMA), POLICY),
    )


def shape(result: BatchValidationResult) -> list[tuple]:
    """Everything about a result except its format-specific metadata."""
    return [
        (issue.source_row, issue.field, issue.code, issue.severity.value)
        for issue in result.issues
    ]


VALID = ["OPR-00000001", "EXT-10000", "10.50", "EA", "2026-01"]


# --- identical outcomes -------------------------------------------------------


def test_a_clean_table_validates_clean_in_both_formats(tmp_path: Path) -> None:
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, [VALID]))
    assert from_csv.is_clean
    assert from_xlsx.is_clean
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.summary == from_xlsx.summary


def test_identical_issue_codes_and_ordering(tmp_path: Path) -> None:
    rows = [
        ["REC-1", "EXT-10000", "not-a-number", "PALLET", "2026-13"],
        ["OPR-00000002", "EXT-90000", "10.50", "EA", "2026-02"],
    ]
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, rows))
    assert shape(from_csv) == shape(from_xlsx)
    assert [row.codes for row in from_csv.rows] == [row.codes for row in from_xlsx.rows]
    assert [row.outcome for row in from_csv.rows] == [
        row.outcome for row in from_xlsx.rows
    ]


def test_equivalent_summaries(tmp_path: Path) -> None:
    rows = [
        ["REC-1", "EXT-10000", "10.50", "EA", "2026-01"],
        ["OPR-00000002", "EXT-90000", "oops", "EA", "2026-02"],
        VALID,
    ]
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, rows))
    assert from_csv.summary == from_xlsx.summary
    assert from_csv.summary.issues_by_code == from_xlsx.summary.issues_by_code
    assert from_csv.summary.issues_by_severity == from_xlsx.summary.issues_by_severity


def test_equivalent_missing_value_behaviour(tmp_path: Path) -> None:
    """An empty CSV field and an absent spreadsheet cell agree, as in T9."""
    csv_rows = [["OPR-00000001", None, None, None, None]]
    xlsx_rows = [["OPR-00000001", None, None, None, None]]
    from_csv, from_xlsx = validate_both(
        *write_pair(tmp_path, csv_rows, xlsx_rows=xlsx_rows)
    )
    assert shape(from_csv) == shape(from_xlsx)
    for result in (from_csv, from_xlsx):
        codes = result.rows[0].codes
        assert codes.count(CODE_REQUIRED_VALUE_MISSING) == 3  # ref, amount, period


def test_an_empty_xlsx_string_matches_an_empty_csv_field(tmp_path: Path) -> None:
    from_csv, from_xlsx = validate_both(
        *write_pair(
            tmp_path,
            [["OPR-00000001", None, "10.50", "EA", "2026-01"]],
            xlsx_rows=[["OPR-00000001", "", "10.50", "EA", "2026-01"]],
        )
    )
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.rows[0].codes == (CODE_REQUIRED_VALUE_MISSING,)


def test_duplicates_are_detected_alike_in_both_formats(tmp_path: Path) -> None:
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, [VALID, list(VALID)]))
    assert shape(from_csv) == shape(from_xlsx)
    for result in (from_csv, from_xlsx):
        assert result.summary.issues_by_code == {CODE_DUPLICATE_KEY: 2}


def test_referential_integrity_agrees_across_formats(tmp_path: Path) -> None:
    rows = [["OPR-00000001", "EXT-90000", "10.50", "EA", "2026-01"]]
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, rows))
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.rows[0].codes == (CODE_EXTERNAL_REF_UNKNOWN,)


# --- native spreadsheet types -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "native"), [("52", 52), ("10.5", 10.5), ("0", 0), ("1000", 1000)]
)
def test_numeric_cells_validate_the_same_as_their_text_form(
    tmp_path: Path, text: str, native: object
) -> None:
    from_csv, from_xlsx = validate_both(
        *write_pair(
            tmp_path,
            [["OPR-00000001", "EXT-10000", text, "EA", "2026-01"]],
            xlsx_rows=[["OPR-00000001", "EXT-10000", native, "EA", "2026-01"]],
        )
    )
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.is_clean
    assert from_xlsx.is_clean


def test_a_malformed_amount_fails_identically_however_it_was_stored(
    tmp_path: Path,
) -> None:
    rows = [["OPR-00000001", "EXT-10000", "12.34.56", "EA", "2026-01"]]
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, rows))
    assert shape(from_csv) == shape(from_xlsx)
    assert from_csv.rows[0].codes == (CODE_AMOUNT_NOT_DECIMAL,)


# --- provenance ---------------------------------------------------------------


def test_provenance_agrees_except_for_the_worksheet(tmp_path: Path) -> None:
    rows = [["REC-1", "EXT-10000", "10.50", "EA", "2026-01"]]
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, rows))

    csv_issue, xlsx_issue = from_csv.issues[0], from_xlsx.issues[0]
    assert csv_issue.source_row == xlsx_issue.source_row == 2
    assert csv_issue.code == xlsx_issue.code == CODE_RECORD_ID_FORMAT_INVALID
    assert csv_issue.field == xlsx_issue.field == "record_id"

    # The only permitted divergence.
    assert csv_issue.source == "probe.csv"
    assert xlsx_issue.source == "probe.xlsx"
    assert csv_issue.worksheet is None
    assert xlsx_issue.worksheet == "records"
    assert from_csv.worksheet is None
    assert from_xlsx.worksheet == "records"


def test_row_numbers_track_physical_rows_in_both_formats(tmp_path: Path) -> None:
    rows = [VALID, ["REC-1", "EXT-10000", "10.50", "EA", "2026-01"], VALID]
    from_csv, from_xlsx = validate_both(*write_pair(tmp_path, rows))
    assert [row.provenance.source_row for row in from_csv.rows] == [2, 3, 4]
    assert [row.provenance.source_row for row in from_xlsx.rows] == [2, 3, 4]
    assert shape(from_csv) == shape(from_xlsx)
