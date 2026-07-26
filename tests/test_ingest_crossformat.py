"""T9 cross-format equivalence: the same table, read from CSV and from XLSX.

Each test builds two temporary sources holding the same logical table and
proves the canonical results agree. Where the two formats genuinely cannot
represent a value identically, the divergence is asserted explicitly rather
than papered over.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from ingest_helpers import schema
from las.ingest import IngestResult, SourceFormat, read_source

HEADERS = ["record_id", "external_ref", "amount", "unit"]


def write_pair(
    tmp_path: Path,
    headers: list[str],
    rows: list[list],
    *,
    xlsx_rows: list[list] | None = None,
) -> tuple[Path, Path]:
    """Write one logical table as both a CSV and an XLSX file.

    ``xlsx_rows`` lets a test place native spreadsheet types where the CSV
    necessarily holds text.
    """
    csv_path = tmp_path / "probe.csv"
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join("" if cell is None else cell for cell in row))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(headers)
    for row in xlsx_rows if xlsx_rows is not None else rows:
        sheet.append(row)
    xlsx_path = tmp_path / "probe.xlsx"
    workbook.save(xlsx_path)
    workbook.close()
    return csv_path, xlsx_path


def canonical(result: IngestResult) -> list[dict[str, str | None]]:
    return [dict(row.values) for row in result.rows]


def read_both(csv_path: Path, xlsx_path: Path) -> tuple[IngestResult, IngestResult]:
    return read_source(csv_path, schema()), read_source(xlsx_path, schema())


# --- structural equivalence ---------------------------------------------------


def test_identical_headers_ordering_and_values(tmp_path: Path) -> None:
    rows = [
        ["OPR-00000001", "EXT-10000", "10.50", "EA"],
        ["OPR-00000002", "EXT-10001", "20.25", "KG"],
        ["OPR-00000003", "EXT-10002", "0.01", "BOX"],
    ]
    from_csv, from_xlsx = read_both(*write_pair(tmp_path, HEADERS, rows))

    assert from_csv.headers == from_xlsx.headers
    assert from_csv.row_count == from_xlsx.row_count == 3
    assert canonical(from_csv) == canonical(from_xlsx)
    # Source order is preserved identically, not merely as a set.
    assert from_csv.column("record_id") == from_xlsx.column("record_id")
    assert from_csv.column("record_id") == (
        "OPR-00000001",
        "OPR-00000002",
        "OPR-00000003",
    )


def test_alias_headers_resolve_the_same_way_in_both_formats(tmp_path: Path) -> None:
    headers = ["Record Id", "External Reference", "Quantity", "Quantity Unit"]
    rows = [["OPR-00000001", "EXT-10000", "1", "EA"]]
    from_csv, from_xlsx = read_both(*write_pair(tmp_path, headers, rows))
    assert from_csv.headers == from_xlsx.headers
    assert canonical(from_csv) == canonical(from_xlsx)


def test_missing_cells_behave_identically(tmp_path: Path) -> None:
    """An empty CSV field and an absent XLSX cell both mean 'missing'."""
    csv_rows = [["OPR-00000001", None, None, None]]
    xlsx_rows = [["OPR-00000001", None, None, None]]
    from_csv, from_xlsx = read_both(
        *write_pair(tmp_path, HEADERS, csv_rows, xlsx_rows=xlsx_rows)
    )

    assert canonical(from_csv) == canonical(from_xlsx)
    for result in (from_csv, from_xlsx):
        assert result.rows[0].get("external_ref") is None
        assert result.rows[0].get("amount") is None
        assert result.rows[0].get("unit") is None


def test_empty_xlsx_string_matches_an_empty_csv_field(tmp_path: Path) -> None:
    from_csv, from_xlsx = read_both(
        *write_pair(
            tmp_path,
            HEADERS,
            [["OPR-00000001", None, "1", "EA"]],
            xlsx_rows=[["OPR-00000001", "", "1", "EA"]],
        )
    )
    assert canonical(from_csv) == canonical(from_xlsx)


def test_blank_rows_are_skipped_and_counted_alike(tmp_path: Path) -> None:
    csv_path = tmp_path / "probe.csv"
    csv_path.write_text(
        "record_id,external_ref,amount,unit\n"
        "OPR-00000001,EXT-10000,1,EA\n"
        ",,,\n"
        "OPR-00000002,EXT-10001,2,KG\n",
        encoding="utf-8",
        newline="",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(HEADERS)
    sheet.append(["OPR-00000001", "EXT-10000", "1", "EA"])
    sheet.append([None, None, None, None])
    sheet.append(["OPR-00000002", "EXT-10001", "2", "KG"])
    xlsx_path = tmp_path / "probe.xlsx"
    workbook.save(xlsx_path)
    workbook.close()

    from_csv, from_xlsx = read_both(csv_path, xlsx_path)
    assert from_csv.skipped_blank_rows == from_xlsx.skipped_blank_rows == 1
    assert canonical(from_csv) == canonical(from_xlsx)
    # Physical row numbers agree because the blank row occupies one line each.
    assert [row.provenance.source_row for row in from_csv.rows] == [2, 4]
    assert [row.provenance.source_row for row in from_xlsx.rows] == [2, 4]


def test_header_only_sources_agree(tmp_path: Path) -> None:
    from_csv, from_xlsx = read_both(*write_pair(tmp_path, HEADERS, []))
    assert from_csv.outcome is from_xlsx.outcome
    assert from_csv.rows == from_xlsx.rows == ()
    assert from_csv.headers == from_xlsx.headers


def test_business_invalid_values_survive_identically(tmp_path: Path) -> None:
    rows = [
        ["OPR12345678", "EXT-90000", "not-a-number", "PALLET"],
        ["opr-00000001", None, "12.34.56", None],
    ]
    from_csv, from_xlsx = read_both(*write_pair(tmp_path, HEADERS, rows))
    assert canonical(from_csv) == canonical(from_xlsx)
    assert from_csv.column("amount") == ("not-a-number", "12.34.56")


# --- native spreadsheet types -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "native"),
    [
        ("52", 52),
        ("12.5", 12.5),
        ("-7", -7),
        ("0", 0),
        ("0.01", 0.01),
    ],
)
def test_unambiguous_numbers_agree_across_formats(
    tmp_path: Path, text: str, native: object
) -> None:
    """Where a number has one obvious text form, both formats produce it."""
    from_csv, from_xlsx = read_both(
        *write_pair(
            tmp_path,
            HEADERS,
            [["OPR-00000001", "EXT-10000", text, "EA"]],
            xlsx_rows=[["OPR-00000001", "EXT-10000", native, "EA"]],
        )
    )
    assert canonical(from_csv) == canonical(from_xlsx)
    assert from_xlsx.rows[0].get("amount") == text


def test_trailing_zero_is_the_one_documented_divergence(tmp_path: Path) -> None:
    """A trailing zero exists in text but not in the numeric domain.

    Ingestion neither invents nor removes one, so this pair legitimately
    differs. It is the boundary of the equivalence guarantee, which covers
    values the two formats represent unambiguously.
    """
    from_csv, from_xlsx = read_both(
        *write_pair(
            tmp_path,
            HEADERS,
            [["OPR-00000001", "EXT-10000", "12.50", "EA"]],
            xlsx_rows=[["OPR-00000001", "EXT-10000", 12.50, "EA"]],
        )
    )
    assert from_csv.rows[0].get("amount") == "12.50"
    assert from_xlsx.rows[0].get("amount") == "12.5"
    assert canonical(from_csv) != canonical(from_xlsx)


# --- metadata differs only where it must --------------------------------------


def test_only_format_specific_metadata_differs(tmp_path: Path) -> None:
    rows = [["OPR-00000001", "EXT-10000", "1", "EA"]]
    from_csv, from_xlsx = read_both(*write_pair(tmp_path, HEADERS, rows))

    assert from_csv.source_format is SourceFormat.CSV
    assert from_xlsx.source_format is SourceFormat.XLSX
    assert from_csv.worksheet is None
    assert from_xlsx.worksheet == "records"
    assert from_csv.rows[0].provenance.worksheet is None
    assert from_xlsx.rows[0].provenance.worksheet == "records"
    # Everything that is not about the container agrees.
    assert from_csv.headers == from_xlsx.headers
    assert from_csv.header_row == from_xlsx.header_row == 1
    assert from_csv.unmapped_headers == from_xlsx.unmapped_headers
    assert from_csv.outcome is from_xlsx.outcome
