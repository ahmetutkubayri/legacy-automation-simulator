"""T9 CSV reader: encodings, delimiters, shapes, provenance, read-only purity."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ingest_helpers import TEST_ALIASES, TEST_FIELDS, schema
from las.ingest import (
    DEFAULT_ENCODINGS,
    ExtraColumnPolicy,
    HeaderResolutionError,
    IngestOutcome,
    MalformedRowError,
    SourceFormat,
    SourceReadError,
    UnsupportedFormatError,
    infer_delimiter,
    read_csv,
    read_source,
)

HEADER = "record_id,external_ref,amount,unit"
BODY = "OPR-00000001,EXT-10000,10.50,EA\nOPR-00000002,EXT-10001,20.25,KG\n"


def write(tmp_path: Path, text: str, *, name: str = "probe.csv", encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding=encoding, newline="")
    return path


def write_bytes(tmp_path: Path, raw: bytes, *, name: str = "probe.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(raw)
    return path


# --- ordinary reads -----------------------------------------------------------


def test_ordinary_valid_file(tmp_path: Path) -> None:
    result = read_csv(write(tmp_path, f"{HEADER}\n{BODY}"), schema())

    assert result.source == "probe.csv"
    assert result.source_format is SourceFormat.CSV
    assert result.worksheet is None
    assert result.headers == ("record_id", "external_ref", "amount", "unit")
    assert result.header_row == 1
    assert result.row_count == 2
    assert result.skipped_blank_rows == 0
    assert result.unmapped_headers == ()
    assert result.outcome is IngestOutcome.ROWS
    assert result.rows[0].values["record_id"] == "OPR-00000001"
    assert result.column("amount") == ("10.50", "20.25")


def test_alias_headers_resolve(tmp_path: Path) -> None:
    text = "Record Id,External Reference,Quantity,Quantity Unit\nOPR-00000001,EXT-10000,1,EA\n"
    result = read_csv(write(tmp_path, text), schema())
    assert result.headers == ("record_id", "external_ref", "amount", "unit")
    assert result.rows[0].get("amount") == "1"


def test_source_label_overrides_the_basename(tmp_path: Path) -> None:
    result = read_csv(write(tmp_path, f"{HEADER}\n{BODY}"), schema(), source_label="feed_probe")
    assert result.source == "feed_probe"
    assert result.rows[0].provenance.source == "feed_probe"


def test_read_source_dispatches_on_extension(tmp_path: Path) -> None:
    result = read_source(write(tmp_path, f"{HEADER}\n{BODY}"), schema())
    assert result.source_format is SourceFormat.CSV


@pytest.mark.parametrize("name", ["probe.parquet", "probe.txt", "probe"])
def test_unsupported_extension_is_rejected(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, f"{HEADER}\n{BODY}", name=name)
    with pytest.raises(UnsupportedFormatError, match="not a supported source format"):
        read_source(path, schema())


# --- encodings ----------------------------------------------------------------


def test_utf8_without_bom(tmp_path: Path) -> None:
    result = read_csv(write(tmp_path, f"{HEADER}\n{BODY}", encoding="utf-8"), schema())
    assert result.row_count == 2


def test_utf8_with_bom_does_not_corrupt_the_first_header(tmp_path: Path) -> None:
    raw = ("﻿" + HEADER + "\n" + BODY).encode("utf-8")
    result = read_csv(write_bytes(tmp_path, raw), schema())
    assert result.headers[0] == "record_id"  # BOM stripped, not folded into the name
    assert result.row_count == 2


def test_encoding_fallback_reaches_the_second_codec(tmp_path: Path) -> None:
    """A cp1252-only byte decodes through the chain's last entry."""
    raw = f"{HEADER}\nOPR-00000001,EXT-10000,10.50,caf\xe9\n".encode("cp1252")
    result = read_csv(write_bytes(tmp_path, raw), schema())
    assert DEFAULT_ENCODINGS[-1] == "cp1252"
    assert result.rows[0].get("unit") == "café"


def test_explicit_encoding_is_honoured(tmp_path: Path) -> None:
    raw = ("﻿" + HEADER + "\n" + BODY).encode("utf-8")
    result = read_csv(write_bytes(tmp_path, raw), schema(), encoding="utf-8-sig")
    assert result.headers[0] == "record_id"


def test_undecodable_source_raises_a_typed_error(tmp_path: Path) -> None:
    raw = b"\xff\xfe\x00\x01record_id\n"
    path = write_bytes(tmp_path, raw)
    with pytest.raises(SourceReadError, match="did not decode"):
        read_csv(path, schema(), encodings=("utf-8",))


def test_empty_encoding_chain_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SourceReadError, match="no candidate encodings"):
        read_csv(write(tmp_path, f"{HEADER}\n{BODY}"), schema(), encodings=())


def test_missing_file_raises_a_typed_error(tmp_path: Path) -> None:
    with pytest.raises(SourceReadError, match="could not be read") as excinfo:
        read_csv(tmp_path / "absent.csv", schema())
    assert excinfo.value.source == "absent.csv"


# --- delimiters ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("a,b,c", ","),
        ("a;b;c", ";"),
        ("a\tb\tc", "\t"),
        ("a|b|c", "|"),
        ("single", ","),  # nothing splits it: earliest candidate wins
        ("a,b;c", ","),  # comma yields 2 fields, semicolon 2 -> tie to earliest
    ],
)
def test_delimiter_inference_is_deterministic(line: str, expected: str) -> None:
    assert infer_delimiter(line) == expected
    assert infer_delimiter(line) == infer_delimiter(line)


def test_semicolon_delimited_file_is_inferred(tmp_path: Path) -> None:
    text = "record_id;external_ref;amount;unit\nOPR-00000001;EXT-10000;10.50;EA\n"
    result = read_csv(write(tmp_path, text), schema())
    assert result.headers == ("record_id", "external_ref", "amount", "unit")
    assert result.rows[0].get("amount") == "10.50"


def test_explicit_delimiter_overrides_inference(tmp_path: Path) -> None:
    text = "record_id|external_ref|amount|unit\nOPR-00000001|EXT-10000|10.50|EA\n"
    result = read_csv(write(tmp_path, text), schema(), delimiter="|")
    assert result.row_count == 1


# --- quoting and line endings -------------------------------------------------


def test_quoted_delimiter_inside_a_field(tmp_path: Path) -> None:
    text = f'{HEADER}\nOPR-00000001,EXT-10000,"1,234.50",EA\n'
    result = read_csv(write(tmp_path, text), schema())
    assert result.rows[0].get("amount") == "1,234.50"


def test_quoted_embedded_newline_keeps_physical_row_numbers_honest(
    tmp_path: Path,
) -> None:
    text = f'{HEADER}\n"OPR-1\ncontinued",EXT-10000,1,EA\nOPR-00000002,EXT-10001,2,KG\n'
    result = read_csv(write(tmp_path, text), schema())

    assert result.row_count == 2
    assert result.rows[0].get("record_id") == "OPR-1\ncontinued"
    # The first record starts on physical line 2 and spans line 3, so the
    # second record starts on line 4 -- not line 3.
    assert result.rows[0].provenance.source_row == 2
    assert result.rows[1].provenance.source_row == 4


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_lf_and_crlf_both_read_identically(tmp_path: Path, newline: str) -> None:
    lines = [HEADER, "OPR-00000001,EXT-10000,10.50,EA", "OPR-00000002,EXT-10001,20.25,KG", ""]
    raw = newline.join(lines).encode("utf-8")
    result = read_csv(write_bytes(tmp_path, raw), schema())
    assert result.row_count == 2
    assert result.column("record_id") == ("OPR-00000001", "OPR-00000002")
    assert [row.provenance.source_row for row in result.rows] == [2, 3]


# --- blank rows, empty cells, header-only ------------------------------------


def test_empty_cells_become_missing_values(tmp_path: Path) -> None:
    text = f"{HEADER}\nOPR-00000001,,,\n"
    result = read_csv(write(tmp_path, text), schema())
    row = result.rows[0]
    assert row.get("record_id") == "OPR-00000001"
    assert row.get("external_ref") is None
    assert row.get("amount") is None
    assert row.get("unit") is None


def test_whitespace_only_cell_is_content_not_missing(tmp_path: Path) -> None:
    text = f'{HEADER}\nOPR-00000001,EXT-10000,"   ",EA\n'
    result = read_csv(write(tmp_path, text), schema())
    assert result.rows[0].get("amount") == "   "


def test_blank_rows_are_skipped_and_counted(tmp_path: Path) -> None:
    text = f"{HEADER}\nOPR-00000001,EXT-10000,1,EA\n\n\nOPR-00000002,EXT-10001,2,KG\n\n"
    result = read_csv(write(tmp_path, text), schema())
    assert result.row_count == 2
    assert result.skipped_blank_rows == 3
    assert [row.provenance.source_row for row in result.rows] == [2, 5]


def test_row_of_only_delimiters_counts_as_blank(tmp_path: Path) -> None:
    text = f"{HEADER}\n,,,\nOPR-00000001,EXT-10000,1,EA\n"
    result = read_csv(write(tmp_path, text), schema())
    assert result.row_count == 1
    assert result.skipped_blank_rows == 1


def test_blank_lines_before_the_header_are_not_counted_as_data(tmp_path: Path) -> None:
    text = f"\n\n{HEADER}\nOPR-00000001,EXT-10000,1,EA\n"
    result = read_csv(write(tmp_path, text), schema())
    assert result.header_row == 3
    assert result.skipped_blank_rows == 0
    assert result.rows[0].provenance.source_row == 4


def test_header_only_file_is_a_deterministic_outcome(tmp_path: Path) -> None:
    result = read_csv(write(tmp_path, f"{HEADER}\n"), schema())
    assert result.rows == ()
    assert result.row_count == 0
    assert result.outcome is IngestOutcome.HEADER_ONLY
    assert result.headers == ("record_id", "external_ref", "amount", "unit")


def test_header_only_file_without_trailing_newline(tmp_path: Path) -> None:
    result = read_csv(write(tmp_path, HEADER), schema())
    assert result.outcome is IngestOutcome.HEADER_ONLY


@pytest.mark.parametrize("text", ["", "\n\n\n", "   \n"])
def test_file_with_no_header_row_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(SourceReadError, match="no header row"):
        read_csv(write(tmp_path, text), schema())


# --- structural row problems --------------------------------------------------


def test_short_row_is_a_typed_structural_error(tmp_path: Path) -> None:
    text = f"{HEADER}\nOPR-00000001,EXT-10000\n"
    with pytest.raises(MalformedRowError, match="declares 4") as excinfo:
        read_csv(write(tmp_path, text), schema())
    assert excinfo.value.row == 2
    assert "2 field" in str(excinfo.value)


def test_long_row_is_a_typed_structural_error(tmp_path: Path) -> None:
    text = f"{HEADER}\nOPR-00000001,EXT-10000,1,EA,surplus\n"
    with pytest.raises(MalformedRowError, match="5 field") as excinfo:
        read_csv(write(tmp_path, text), schema())
    assert excinfo.value.row == 2


def test_malformed_row_error_carries_no_row_contents(tmp_path: Path) -> None:
    text = f"{HEADER}\nOPR-SECRET-VALUE,EXT-10000\n"
    with pytest.raises(MalformedRowError) as excinfo:
        read_csv(write(tmp_path, text), schema())
    assert "OPR-SECRET-VALUE" not in str(excinfo.value)


def test_duplicate_header_is_rejected(tmp_path: Path) -> None:
    text = "record_id,external_ref,amount,record_id\nOPR-00000001,EXT-10000,1,x\n"
    with pytest.raises(HeaderResolutionError, match="repeats"):
        read_csv(write(tmp_path, text), schema())


def test_missing_required_header_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HeaderResolutionError, match="absent"):
        read_csv(write(tmp_path, "record_id\nOPR-00000001\n"), schema())


# --- extra columns ------------------------------------------------------------


def test_extra_column_is_retained_and_reported(tmp_path: Path) -> None:
    text = f"{HEADER},Reported By\nOPR-00000001,EXT-10000,1,EA,Demo Account 000001\n"
    result = read_csv(write(tmp_path, text), schema())
    assert result.unmapped_headers == ("reported by",)
    assert result.rows[0].extras == {"reported by": "Demo Account 000001"}


def test_extra_column_can_be_reported_but_dropped(tmp_path: Path) -> None:
    text = f"{HEADER},Reported By\nOPR-00000001,EXT-10000,1,EA,Demo Account 000001\n"
    result = read_csv(
        write(tmp_path, text), schema(extra_columns=ExtraColumnPolicy.IGNORE)
    )
    assert result.unmapped_headers == ("reported by",)
    assert result.rows[0].extras == {}


def test_extra_column_can_be_refused(tmp_path: Path) -> None:
    text = f"{HEADER},Reported By\nOPR-00000001,EXT-10000,1,EA,x\n"
    with pytest.raises(HeaderResolutionError, match="rejects extra columns"):
        read_csv(write(tmp_path, text), schema(extra_columns=ExtraColumnPolicy.REJECT))


# --- business defects survive -------------------------------------------------


def test_business_invalid_values_are_preserved_for_validation(tmp_path: Path) -> None:
    """Ingestion must not filter, fix, or coerce bad business content."""
    text = (
        f"{HEADER}\n"
        "OPR12345678,EXT-10000,not-a-number,EA\n"  # malformed id, unparseable amount
        "opr-00000001,EXT-90000,-42.50,PALLET\n"  # bad case, orphan ref, negative
        "OPR-00000003,,12.34.56,\n"  # missing ref, double-dotted amount, no unit
    )
    result = read_csv(write(tmp_path, text), schema())

    assert result.row_count == 3
    assert result.column("record_id") == ("OPR12345678", "opr-00000001", "OPR-00000003")
    assert result.column("amount") == ("not-a-number", "-42.50", "12.34.56")
    assert result.column("unit") == ("EA", "PALLET", None)
    assert result.column("external_ref") == ("EXT-10000", "EXT-90000", None)


def test_duplicate_business_rows_are_both_kept(tmp_path: Path) -> None:
    duplicate = "OPR-00000001,EXT-10000,1,EA\n"
    result = read_csv(write(tmp_path, f"{HEADER}\n{duplicate}{duplicate}"), schema())
    assert result.row_count == 2
    assert result.column("record_id") == ("OPR-00000001", "OPR-00000001")
    assert [row.provenance.source_row for row in result.rows] == [2, 3]


# --- purity, determinism, immutability ----------------------------------------


def test_repeated_reads_are_identical(tmp_path: Path) -> None:
    path = write(tmp_path, f"{HEADER}\n{BODY}")
    first, second = read_csv(path, schema()), read_csv(path, schema())
    assert first == second


def test_reading_leaves_the_source_byte_identical(tmp_path: Path) -> None:
    path = write(tmp_path, f"{HEADER}\n{BODY}")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    read_csv(path, schema())
    read_csv(path, schema())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_result_and_row_objects_are_immutable(tmp_path: Path) -> None:
    result = read_csv(write(tmp_path, f"{HEADER}\n{BODY}"), schema())
    with pytest.raises(FrozenInstanceError):
        result.rows = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.rows[0].provenance.source_row = 99  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.rows[0].values["record_id"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.rows[0].extras["x"] = "y"  # type: ignore[index]


def test_provenance_rejects_unsafe_construction() -> None:
    from las.ingest import RowProvenance, SourceLabelError

    with pytest.raises(SourceLabelError, match="must not be blank"):
        RowProvenance(source="", source_row=1)
    with pytest.raises(ValueError, match="one-based"):
        RowProvenance(source="probe.csv", source_row=0)


def test_errors_and_results_never_expose_a_filesystem_path(tmp_path: Path) -> None:
    """Only the basename travels, never the directory it lived in."""
    path = write(tmp_path, f"{HEADER}\nOPR-00000001,EXT-10000\n")
    result_path = write(tmp_path, f"{HEADER}\n{BODY}", name="fine.csv")

    with pytest.raises(MalformedRowError) as excinfo:
        read_csv(path, schema())
    message = str(excinfo.value)
    assert str(tmp_path) not in message
    assert "probe.csv" in message

    result = read_csv(result_path, schema())
    assert result.source == "fine.csv"
    assert str(tmp_path) not in result.source


def test_field_and_alias_constants_are_shared_with_the_header_suite() -> None:
    assert len(TEST_FIELDS) == 4
    assert set(TEST_ALIASES.values()) == {"record_id", "external_ref", "amount", "unit"}


def test_line_of_bare_delimiters_is_not_a_header_row(tmp_path: Path) -> None:
    """The line is not blank as text, but carries no header names at all."""
    with pytest.raises(SourceReadError, match="no header row"):
        read_csv(write(tmp_path, ",,,\n"), schema())
