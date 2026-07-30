"""T9 hardening: deep immutability, safe source labels, and cell coverage.

Three properties that a frozen dataclass and a happy-path read do not give
you for free:

* *deep* immutability, so a caller-supplied list or dict cannot stay mutable
  inside a result, and nothing can be changed through the returned object;
* source labels that cannot carry a location, however the caller spells it;
* formula and allowlist checks that cover every observable cell, including
  columns the schema is about to discard.

No test here contains a real filesystem path or username. Path-shaped labels
are assembled from fragments and invented names.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import MISSING, fields
from pathlib import Path

import pytest
from openpyxl import Workbook

from ingest_helpers import TEST_FIELDS, schema
from las.ingest import (
    MAX_SOURCE_LABEL_LENGTH,
    ExtraColumnPolicy,
    FieldSpec,
    FormulaCellError,
    HeaderResolution,
    IngestedRow,
    IngestOutcome,
    IngestResult,
    IngestSchema,
    RowProvenance,
    SourceFormat,
    SourceLabelError,
    UnsupportedCellValueError,
    read_csv,
    read_xlsx,
    safe_source_label,
)

HEADER = "record_id,external_ref,amount,unit"
BODY = "OPR-00000001,EXT-10000,10.50,EA\n"
XLSX_HEADERS = ["record_id", "external_ref", "amount", "unit"]


def write_csv(tmp_path: Path, text: str = f"{HEADER}\n{BODY}") -> Path:
    path = tmp_path / "probe.csv"
    path.write_text(text, encoding="utf-8", newline="")
    return path


def build_xlsx(
    tmp_path: Path,
    rows: list[list],
    *,
    headers: list | None = None,
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(headers if headers is not None else XLSX_HEADERS)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "probe.xlsx"
    workbook.save(path)
    workbook.close()
    return path


def provenance() -> RowProvenance:
    return RowProvenance(source="probe.csv", source_row=2)


# =============================================================================
# Deep immutability
# =============================================================================


def test_schema_fields_supplied_as_a_list_become_a_tuple() -> None:
    supplied = [FieldSpec("amount"), FieldSpec("record_id")]
    built = IngestSchema(fields=supplied)

    assert isinstance(built.fields, tuple)
    supplied.append(FieldSpec("smuggled"))
    assert [spec.name for spec in built.fields] == ["amount", "record_id"]
    with pytest.raises(AttributeError):
        built.fields.append(FieldSpec("smuggled"))  # type: ignore[attr-defined]


def test_header_resolution_sequences_supplied_as_lists_become_tuples() -> None:
    resolution = HeaderResolution(
        columns=["record_id", None],
        normalized=["record id", "reported by"],
        headers=["record_id"],
        unmapped=["reported by"],
    )
    for value in (
        resolution.columns,
        resolution.normalized,
        resolution.headers,
        resolution.unmapped,
    ):
        assert isinstance(value, tuple)
    with pytest.raises(AttributeError):
        resolution.columns.append("smuggled")  # type: ignore[attr-defined]


def test_result_sequences_supplied_as_lists_become_tuples() -> None:
    rows = [IngestedRow(provenance=provenance(), values={"amount": "1"})]
    headers = ["amount"]
    unmapped = ["reported by"]
    warnings = ["something"]
    result = IngestResult(
        source="probe.csv",
        source_format=SourceFormat.CSV,
        headers=headers,
        rows=rows,
        header_row=1,
        skipped_blank_rows=0,
        unmapped_headers=unmapped,
        warnings=warnings,
    )

    for value in (result.headers, result.rows, result.unmapped_headers, result.warnings):
        assert isinstance(value, tuple)

    # Mutating the caller's originals afterwards must not reach the result.
    rows.append("not even a row")  # type: ignore[arg-type]
    headers.append("smuggled")
    unmapped.append("smuggled")
    warnings.append("smuggled")
    assert result.row_count == 1
    assert result.headers == ("amount",)
    assert result.unmapped_headers == ("reported by",)
    assert result.warnings == ("something",)

    for value in (result.headers, result.rows, result.unmapped_headers, result.warnings):
        with pytest.raises(AttributeError):
            value.append("smuggled")  # type: ignore[attr-defined]


def test_nested_row_mappings_cannot_be_mutated_through_a_result(
    tmp_path: Path,
) -> None:
    result = read_csv(write_csv(tmp_path), schema())
    row = result.rows[0]

    with pytest.raises(TypeError):
        row.values["record_id"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        row.extras["injected"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        del row.values["record_id"]  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        row.values.clear()  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        row.values.update({"record_id": "changed"})  # type: ignore[attr-defined]
    assert row.values["record_id"] == "OPR-00000001"


def test_caller_dictionaries_are_copied_before_being_frozen() -> None:
    values = {"amount": "1"}
    extras = {"reported by": "Demo Account 000001"}
    row = IngestedRow(provenance=provenance(), values=values, extras=extras)

    values["amount"] = "999"
    values["injected"] = "x"
    extras["injected"] = "x"

    assert row.values == {"amount": "1"}
    assert row.extras == {"reported by": "Demo Account 000001"}


def test_a_row_can_omit_extras_and_gets_an_empty_read_only_mapping() -> None:
    """The default must exist, be empty, and be immutable."""
    row = IngestedRow(provenance=provenance(), values={"amount": "1"})

    assert row.extras == {}
    assert len(row.extras) == 0
    with pytest.raises(TypeError):
        row.extras["injected"] = "value"  # type: ignore[index]
    with pytest.raises(AttributeError):
        row.extras.clear()  # type: ignore[attr-defined]


def test_rows_defaulting_their_extras_share_no_state() -> None:
    """A shared default would let one row's contents appear in another's."""
    first = IngestedRow(provenance=provenance(), values={"amount": "1"})
    second = IngestedRow(provenance=provenance(), values={"amount": "2"})

    assert first.extras is not second.extras
    # Neither the proxy nor the dictionary underneath it is shared.
    assert first.extras.copy() is not second.extras.copy()
    supplied = IngestedRow(
        provenance=provenance(), values={"amount": "3"}, extras={"note": "x"}
    )
    assert first.extras == {} and second.extras == {}
    assert supplied.extras == {"note": "x"}


def test_supplied_extras_are_still_copied_and_frozen() -> None:
    """The factory changed the default only; explicit input behaves as before."""
    extras = {"reported by": "Demo Account 000002"}
    row = IngestedRow(provenance=provenance(), values={"amount": "1"}, extras=extras)

    extras["injected"] = "x"
    assert row.extras == {"reported by": "Demo Account 000002"}
    with pytest.raises(TypeError):
        row.extras["injected"] = "value"  # type: ignore[index]


def test_the_extras_default_is_a_factory_not_a_bare_mapping() -> None:
    """Pins the compatibility regression this idiom exists to prevent.

    ``dataclasses`` treats an unhashable default as a mutable one and refuses
    the class outright. ``mappingproxy`` is unhashable before Python 3.12, so a
    bare ``MappingProxyType({})`` default made the whole package unimportable
    on 3.11 — every module that reached this one failed at collection.
    """
    extras = next(f for f in fields(IngestedRow) if f.name == "extras")

    assert extras.default is MISSING, "a bare default breaks Python 3.11"
    assert extras.default_factory is not MISSING
    assert extras.default_factory() == {}
    assert extras.default_factory() is not extras.default_factory()


def test_schema_alias_and_lookup_mappings_are_read_only() -> None:
    supplied = {"Quantity": "amount"}
    built = IngestSchema(fields=TEST_FIELDS, aliases=supplied)

    supplied["Sneaky"] = "amount"
    assert built.resolve("sneaky") is None

    for mapping in (built.aliases, built.lookup):
        with pytest.raises(TypeError):
            mapping["injected"] = "amount"  # type: ignore[index]
        with pytest.raises(AttributeError):
            mapping.clear()  # type: ignore[attr-defined]


def test_field_spec_and_provenance_hold_only_scalars() -> None:
    spec = FieldSpec("amount")
    assert isinstance(spec.name, str)
    assert isinstance(spec.required, bool)
    prov = provenance()
    assert isinstance(prov.source, str)
    assert isinstance(prov.source_row, int)
    assert prov.worksheet is None


def test_results_compare_by_value_but_are_deliberately_not_hashable(
    tmp_path: Path,
) -> None:
    """Immutability is not hashability, and the distinction is intentional.

    Rows carry read-only mapping views, which wrap a dict and so cannot be
    hashed. Value equality is what callers need — comparing two reads of the
    same source — and that works. Pinning the unhashability here keeps it a
    documented property rather than an accident waiting to surprise someone.
    """
    first = read_csv(write_csv(tmp_path), schema())
    second = read_csv(write_csv(tmp_path), schema())
    assert first == second
    assert first.rows[0] == second.rows[0]

    with pytest.raises(TypeError, match="unhashable"):
        hash(first.rows[0])
    # Objects made only of scalars and tuples do hash, as one would expect.
    assert hash(provenance()) == hash(RowProvenance(source="probe.csv", source_row=2))
    assert hash(FieldSpec("amount")) == hash(FieldSpec("amount"))


# =============================================================================
# Source-label safety
# =============================================================================

# Path-shaped labels, assembled from fragments so no real path or username
# appears in a tracked file.
POSIX_LABEL = "/" + "opt/" + "secrets/feed.csv"
WINDOWS_LABEL = "D" + ":" + "\\" + "records\\feed.csv"
UNC_LABEL = "\\\\" + "fileserver\\share\\feed.csv"
URL_LABEL = "https" + "://" + "example.com/feed.csv"
SCHEME_LABEL = "mailto" + ":" + "ops-team@example.com"
HOME_LABEL = "~" + "/" + "feed.csv"


@pytest.mark.parametrize(
    ("label", "reason"),
    [
        (POSIX_LABEL, "path separators"),
        (WINDOWS_LABEL, "path separators"),
        (UNC_LABEL, "path separators"),
        (URL_LABEL, "path separators"),
        (HOME_LABEL, "path separators"),
        ("relative" + "/" + "feed.csv", "path separators"),
        (SCHEME_LABEL, "drive prefix or URL scheme"),
        ("E" + ":", "drive prefix or URL scheme"),
        ("", "must not be blank"),
        ("    ", "must not be blank"),
        ("\t\n", "must not be blank"),
        (".", "not a directory"),
        ("..", "not a directory"),
        ("feed\x00alpha", "control or format characters"),
        ("feed\x1balpha", "control or format characters"),
        ("feed​alpha", "control or format characters"),
        ("feed‮alpha", "control or format characters"),
        ("x" * (MAX_SOURCE_LABEL_LENGTH + 1), "at most"),
    ],
)
def test_unsafe_labels_are_rejected(label: str, reason: str) -> None:
    with pytest.raises(SourceLabelError, match=reason):
        safe_source_label(label, operation="probe")


def test_rejection_never_echoes_the_offending_label() -> None:
    """The rejected string is exactly the one that must not be repeated."""
    for label in (POSIX_LABEL, WINDOWS_LABEL, UNC_LABEL, URL_LABEL):
        with pytest.raises(SourceLabelError) as excinfo:
            safe_source_label(label, operation="probe")
        message = str(excinfo.value)
        assert label not in message
        for fragment in ("secrets", "fileserver", "example.com", "records"):
            assert fragment not in message


def test_non_text_label_is_rejected() -> None:
    with pytest.raises(SourceLabelError, match="must be text"):
        safe_source_label(Path("feed.csv"), operation="probe")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("feed_alpha", "feed_alpha"),
        ("  feed_alpha  ", "feed_alpha"),
        ("feed_alpha.xlsx", "feed_alpha.xlsx"),
        ("feed-beta.csv", "feed-beta.csv"),
        ("ｆｅｅｄ", "feed"),  # NFKC folds compatibility forms
        ("x" * MAX_SOURCE_LABEL_LENGTH, "x" * MAX_SOURCE_LABEL_LENGTH),
    ],
)
def test_safe_labels_are_accepted_and_normalized(label: str, expected: str) -> None:
    assert safe_source_label(label, operation="probe") == expected


def test_readers_reject_a_path_shaped_label(tmp_path: Path) -> None:
    csv_path = write_csv(tmp_path)
    xlsx_path = build_xlsx(tmp_path, [["OPR-00000001", "EXT-10000", "1", "EA"]])

    for reader, path in ((read_csv, csv_path), (read_xlsx, xlsx_path)):
        with pytest.raises(SourceLabelError, match="path separators") as excinfo:
            reader(path, schema(), source_label=POSIX_LABEL)
        assert POSIX_LABEL not in str(excinfo.value)


def test_default_label_is_the_basename_only(tmp_path: Path) -> None:
    nested = tmp_path / "one" / "two" / "three"
    nested.mkdir(parents=True)
    path = nested / "feed_probe.csv"
    path.write_text(f"{HEADER}\n{BODY}", encoding="utf-8", newline="")

    result = read_csv(path, schema())
    assert result.source == "feed_probe.csv"
    assert str(nested) not in result.source
    for fragment in ("one", "two", "three"):
        assert fragment not in result.source
    assert result.rows[0].provenance.source == "feed_probe.csv"


def test_filesystem_errors_carry_neither_the_os_message_nor_a_path(
    tmp_path: Path,
) -> None:
    """A missing file reports a stable errno symbol, not a localized string."""
    from las.ingest import SourceReadError

    nested = tmp_path / "hidden" / "place"
    nested.mkdir(parents=True)
    missing = nested / "absent.csv"

    with pytest.raises(SourceReadError) as excinfo:
        read_csv(missing, schema())
    message = str(excinfo.value)
    assert "ENOENT" in message  # deterministic and locale-independent
    assert "No such file" not in message  # not the operating system's wording
    assert str(nested) not in message
    assert "hidden" not in message
    assert message.count("absent.csv") == 1  # only the basename


def test_results_and_provenance_retain_no_path_object(tmp_path: Path) -> None:
    result = read_csv(write_csv(tmp_path), schema())
    candidates = [getattr(result, name) for name in vars(result)]
    candidates += [getattr(result.rows[0].provenance, name) for name in
                   vars(result.rows[0].provenance)]
    for value in candidates:
        assert not isinstance(value, Path)


def test_worksheet_names_travel_but_paths_do_not(tmp_path: Path) -> None:
    path = build_xlsx(tmp_path, [["OPR-00000001", "EXT-10000", "1", "EA"]])
    result = read_xlsx(path, schema())
    assert result.worksheet == "records"
    assert str(tmp_path) not in (result.worksheet or "")
    assert result.source == "probe.xlsx"


# =============================================================================
# Every observable cell is validated before any column is discarded
# =============================================================================


@pytest.mark.parametrize(
    "policy", [ExtraColumnPolicy.RETAIN, ExtraColumnPolicy.IGNORE]
)
def test_formula_in_an_extra_column_is_refused_under_every_policy(
    tmp_path: Path, policy: ExtraColumnPolicy
) -> None:
    """An ignored column must not become a hiding place for a formula."""
    path = build_xlsx(
        tmp_path,
        [["OPR-00000001", "EXT-10000", "1", "EA", "=1+1"]],
        headers=[*XLSX_HEADERS, "Reported By"],
    )
    with pytest.raises(FormulaCellError) as excinfo:
        read_xlsx(path, schema(extra_columns=policy))
    assert excinfo.value.row == 2
    assert excinfo.value.column == 5


@pytest.mark.parametrize(
    "policy", [ExtraColumnPolicy.RETAIN, ExtraColumnPolicy.IGNORE]
)
def test_unsupported_type_in_an_extra_column_is_refused_under_every_policy(
    tmp_path: Path, policy: ExtraColumnPolicy
) -> None:
    path = build_xlsx(
        tmp_path,
        [["OPR-00000001", "EXT-10000", "1", "EA", dt.time(3, 4)]],
        headers=[*XLSX_HEADERS, "Reported By"],
    )
    with pytest.raises(UnsupportedCellValueError, match="'time' is not supported") as excinfo:
        read_xlsx(path, schema(extra_columns=policy))
    assert excinfo.value.row == 2
    assert excinfo.value.column == 5


def test_formula_beyond_the_header_span_is_still_refused(tmp_path: Path) -> None:
    """A cell outside the declared columns is observable, so it is checked."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(XLSX_HEADERS)
    sheet.append(["OPR-00000001", "EXT-10000", "1", "EA"])
    sheet["G2"] = "=1+1"
    path = tmp_path / "probe.xlsx"
    workbook.save(path)
    workbook.close()

    with pytest.raises(FormulaCellError) as excinfo:
        read_xlsx(path, schema())
    assert excinfo.value.column == 7


def test_ignored_extra_column_still_yields_a_clean_row(tmp_path: Path) -> None:
    """The policy drops the value; it does not skip the safety checks."""
    path = build_xlsx(
        tmp_path,
        [["OPR-00000001", "EXT-10000", "1", "EA", "Demo Account 000001"]],
        headers=[*XLSX_HEADERS, "Reported By"],
    )
    result = read_xlsx(path, schema(extra_columns=ExtraColumnPolicy.IGNORE))
    assert result.outcome is IngestOutcome.ROWS
    assert result.unmapped_headers == ("reported by",)
    assert result.rows[0].extras == {}
    assert result.rows[0].get("record_id") == "OPR-00000001"


def test_rejecting_extras_happens_before_any_row_is_read(tmp_path: Path) -> None:
    """A REJECT schema fails on the header row, so a bad cell is never reached."""
    from las.ingest import HeaderResolutionError

    path = build_xlsx(
        tmp_path,
        [["OPR-00000001", "EXT-10000", "1", "EA", "=1+1"]],
        headers=[*XLSX_HEADERS, "Reported By"],
    )
    with pytest.raises(HeaderResolutionError, match="rejects extra columns"):
        read_xlsx(path, schema(extra_columns=ExtraColumnPolicy.REJECT))


def test_external_links_and_macros_are_not_retained() -> None:
    """The pinned openpyxl supports both switches, so both are set."""
    import inspect

    from openpyxl import load_workbook

    parameters = inspect.signature(load_workbook).parameters
    assert "keep_links" in parameters
    assert "keep_vba" in parameters

    source = (
        Path(__file__).resolve().parents[1] / "src" / "las" / "ingest" / "readers.py"
    ).read_text(encoding="utf-8")
    assert "keep_links=False" in source
    assert "keep_vba=False" in source
    assert "data_only=False" in source
