"""T13 models: canonical decimal text, cells, tables, and deep immutability."""

from __future__ import annotations

import dataclasses
import decimal
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

from las.report import (
    ACCOUNTING_METRICS,
    BLANK,
    MEDIA_TYPE_BY_FORMAT,
    SCHEMA_VERSION,
    SHEET_ORDER,
    SUFFIX_BY_FORMAT,
    SUMMARY_SECTIONS,
    Cell,
    Column,
    ColumnDefinitionError,
    OutputFormat,
    RenderedArtifact,
    ReportSummary,
    Row,
    SheetId,
    Table,
    UnsafeCellValueError,
    artifact,
    columns_from,
    render_count,
    render_decimal,
)


@contextmanager
def global_context(prec: int, rounding: str = decimal.ROUND_HALF_EVEN):
    saved = decimal.getcontext()
    decimal.setcontext(decimal.Context(prec=prec, rounding=rounding))
    try:
        yield
    finally:
        decimal.setcontext(saved)


# --- canonical decimal rendering ----------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12", "12"),
        ("12.3400", "12.3400"),
        ("-2.5", "-2.5"),
        ("0", "0"),
        ("-0", "0"),
        ("0E-100", "0"),
        ("0.00", "0"),
        ("-0.000", "0"),
        ("1000000", "1000000"),
        ("0.1", "0.1"),
        ("-0.0000000001", "-0.0000000001"),
    ],
)
def test_the_documented_decimal_examples(value: str, expected: str) -> None:
    assert render_decimal(Decimal(value)) == expected


@pytest.mark.parametrize("digits", [101, 150, 300])
def test_long_decimals_render_every_digit(digits: int) -> None:
    value = Decimal("0." + "1" * digits)
    rendered = render_decimal(value)
    assert rendered == "0." + "1" * digits
    assert Decimal(rendered) == value


def test_a_long_integral_decimal_keeps_its_digits() -> None:
    value = Decimal("9" * 200)
    assert render_decimal(value) == "9" * 200


def test_rendering_never_uses_exponent_notation() -> None:
    for value in ("1E+30", "1E-30", "1.5E+10"):
        assert "E" not in render_decimal(Decimal(value))
        assert "e" not in render_decimal(Decimal(value))


def test_rendering_uses_no_thousands_separator() -> None:
    assert render_decimal(Decimal("1234567.89")) == "1234567.89"
    assert "," not in render_decimal(Decimal("1234567.89"))


@pytest.mark.parametrize("prec", [1, 3, 28, 400])
def test_rendering_ignores_the_global_decimal_precision(prec: int) -> None:
    value = Decimal("0." + "7" * 150)
    baseline = render_decimal(value)
    with global_context(prec):
        assert render_decimal(value) == baseline


@pytest.mark.parametrize(
    "rounding",
    [decimal.ROUND_UP, decimal.ROUND_DOWN, decimal.ROUND_CEILING,
     decimal.ROUND_FLOOR, decimal.ROUND_05UP, decimal.ROUND_HALF_UP],
)
def test_rendering_ignores_the_global_rounding_mode(rounding: str) -> None:
    value = Decimal("10.005")
    with global_context(3, rounding):
        assert render_decimal(value) == "10.005"


def test_rendering_is_repeatable() -> None:
    value = Decimal("-0." + "3" * 120)
    assert render_decimal(value) == render_decimal(value)


@pytest.mark.parametrize("value", [1.5, "10", None, 10])
def test_only_a_decimal_can_be_rendered_as_a_number(value: object) -> None:
    with pytest.raises(UnsafeCellValueError, match="exact decimal"):
        render_decimal(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_decimal_has_no_representation(value: str) -> None:
    with pytest.raises(UnsafeCellValueError, match="non-finite"):
        render_decimal(Decimal(value))


def test_counts_render_as_plain_integers() -> None:
    assert render_count(0) == "0"
    assert render_count(240) == "240"


@pytest.mark.parametrize("value", [1.5, "3", None, True])
def test_only_an_integer_renders_as_a_count(value: object) -> None:
    with pytest.raises(UnsafeCellValueError, match="integer"):
        render_count(value)  # type: ignore[arg-type]


# --- format metadata ----------------------------------------------------------


def test_the_three_authorized_formats_and_their_fixed_metadata() -> None:
    assert [f.value for f in OutputFormat] == ["xlsx", "json", "html"]
    assert MEDIA_TYPE_BY_FORMAT[OutputFormat.XLSX] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert MEDIA_TYPE_BY_FORMAT[OutputFormat.JSON] == "application/json"
    assert MEDIA_TYPE_BY_FORMAT[OutputFormat.HTML] == "text/html"
    assert SUFFIX_BY_FORMAT[OutputFormat.XLSX] == ".xlsx"
    assert SUFFIX_BY_FORMAT[OutputFormat.JSON] == ".json"
    assert SUFFIX_BY_FORMAT[OutputFormat.HTML] == ".html"


def test_the_metadata_mappings_are_read_only() -> None:
    for mapping in (MEDIA_TYPE_BY_FORMAT, SUFFIX_BY_FORMAT):
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping[OutputFormat.JSON] = "x"  # type: ignore[index]


def test_an_artifact_carries_no_path_or_filename() -> None:
    item = artifact(OutputFormat.JSON, b"{}")
    fields = {f.name for f in dataclasses.fields(RenderedArtifact)}
    assert fields == {"output_format", "media_type", "suffix", "content"}
    assert not any(word in fields for word in ("path", "filename", "directory", "name"))
    assert item.size == 2
    assert not isinstance(item.suffix, Path)


def test_an_artifact_is_frozen_and_holds_bytes() -> None:
    item = artifact(OutputFormat.HTML, b"<html>")
    with pytest.raises(FrozenInstanceError):
        item.content = b"x"  # type: ignore[misc]
    with pytest.raises(UnsafeCellValueError, match="immutable bytes"):
        RenderedArtifact(
            output_format=OutputFormat.HTML, media_type="text/html",
            suffix=".html", content="not bytes",  # type: ignore[arg-type]
        )


# --- sheets, columns, cells, rows, tables -------------------------------------


def test_the_four_sheets_in_contract_order() -> None:
    assert [s.value for s in SHEET_ORDER] == [
        "Summary", "Exceptions", "Reconciliation", "Review Queue"
    ]
    assert [s.value for s in SheetId] == [
        "Summary", "Exceptions", "Reconciliation", "Review Queue"
    ]


def test_the_summary_sections_and_accounting_metrics_are_fixed() -> None:
    assert SUMMARY_SECTIONS == (
        "metadata", "accounting", "group_outcome",
        "group_reason", "unresolved_reason", "variance_band",
    )
    assert ACCOUNTING_METRICS == (
        "feed_rows_input", "system_rows_input", "feed_contributions",
        "system_contributions", "unresolved_rows", "group_count",
        "exception_group_count", "review_queue_count",
    )


@pytest.mark.parametrize("name", ["", "   ", None, 5])
def test_a_column_needs_a_non_blank_name(name: object) -> None:
    with pytest.raises(ColumnDefinitionError, match="non-blank name"):
        Column(name)  # type: ignore[arg-type]


def test_columns_reject_an_empty_or_repeating_definition() -> None:
    with pytest.raises(ColumnDefinitionError, match="at least one column"):
        columns_from((), sheet="Summary")
    with pytest.raises(ColumnDefinitionError, match="one column twice"):
        columns_from(("a", "b", "a"), sheet="Summary")


def test_a_blank_cell_is_blank_everywhere() -> None:
    assert BLANK.is_blank and BLANK.text is None
    assert Cell("x").is_blank is False
    assert Cell() == BLANK


def test_a_cell_holds_text_or_nothing() -> None:
    with pytest.raises(UnsafeCellValueError, match="canonical text"):
        Cell(Decimal("1"))  # type: ignore[arg-type]


def test_a_row_freezes_its_cells() -> None:
    cells = [Cell("a"), BLANK]
    row = Row(cells=cells)
    cells.append(Cell("c"))
    assert row.texts == ("a", None)
    assert isinstance(row.cells, tuple)
    with pytest.raises(AttributeError):
        row.cells.append(Cell("c"))  # type: ignore[attr-defined]


def test_a_table_freezes_its_columns_and_rows() -> None:
    columns = list(columns_from(("a", "b"), sheet="Summary"))
    rows = [Row(cells=(Cell("1"), BLANK))]
    table = Table(sheet=SheetId.SUMMARY, columns=columns, rows=rows)
    columns.append(Column("c"))
    rows.append(Row(cells=(Cell("2"), BLANK)))
    assert table.headers == ("a", "b")
    assert table.row_count == 1
    assert isinstance(table.columns, tuple) and isinstance(table.rows, tuple)


def test_a_row_that_does_not_fit_its_columns_is_refused() -> None:
    with pytest.raises(ColumnDefinitionError, match="column count"):
        Table(
            sheet=SheetId.SUMMARY,
            columns=columns_from(("a", "b"), sheet="Summary"),
            rows=(Row(cells=(Cell("only-one"),)),),
        )


def test_table_row_count_excludes_the_header() -> None:
    table = Table(
        sheet=SheetId.SUMMARY,
        columns=columns_from(("a",), sheet="Summary"),
        rows=tuple(Row(cells=(Cell("x"),)) for _ in range(3)),
    )
    assert table.row_count == 3


# --- the summary model ---------------------------------------------------------


def make_summary(**overrides) -> ReportSummary:
    base = {
        "recipients": ("ops-team@example.com",),
        "accounting": {metric: 0 for metric in ACCOUNTING_METRICS},
        "group_outcomes": (("balanced", 1),),
        "group_reasons": (("totals_equal", 1),),
        "unresolved_reasons": (("match_unmatched", 0),),
        "variance_bands": (("0", 1),),
    }
    return ReportSummary(**{**base, **overrides})


def test_the_summary_copies_and_freezes_everything() -> None:
    recipients = ["a@example.com", "b@example.com"]
    accounting = {metric: 1 for metric in ACCOUNTING_METRICS}
    summary = make_summary(recipients=recipients, accounting=accounting)
    recipients.append("c@example.com")
    accounting["injected"] = 9

    assert summary.recipients == ("a@example.com", "b@example.com")
    assert "injected" not in summary.accounting
    assert isinstance(summary.accounting, MappingProxyType)
    with pytest.raises(TypeError):
        summary.accounting["x"] = 1  # type: ignore[index]


def test_the_summary_keeps_the_accounting_reading_order() -> None:
    summary = make_summary(
        accounting={metric: 0 for metric in reversed(ACCOUNTING_METRICS)}
    )
    # Copied, not sorted: the metrics have a documented order that the
    # caller supplies, and re-sorting would scramble it.
    assert list(summary.accounting) == list(reversed(ACCOUNTING_METRICS))


def test_recipients_render_comma_joined_in_configured_order() -> None:
    summary = make_summary(recipients=("z@example.com", "a@example.com"))
    assert summary.recipients_text == "z@example.com,a@example.com"


def test_the_schema_version_is_the_documented_one() -> None:
    assert SCHEMA_VERSION == "1.0"
    assert make_summary().schema_version == "1.0"


def test_summary_pair_sequences_become_tuples() -> None:
    summary = make_summary(group_outcomes=[["balanced", 1], ["mismatch", 2]])
    assert summary.group_outcomes == (("balanced", 1), ("mismatch", 2))
    assert all(isinstance(pair, tuple) for pair in summary.group_outcomes)


def test_equal_summaries_compare_equal() -> None:
    assert make_summary() == make_summary()
    assert make_summary() != make_summary(group_outcomes=(("balanced", 2),))


# --- configured-bound normalisation ---------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.0", "1"), ("5.0", "5"), ("0.0", "0"), ("0.100", "0.1"),
     ("2.50", "2.5"), ("1", "1"), ("100", "100"), ("-1.0", "-1"),
     ("0.000", "0"), ("1.2345", "1.2345")],
)
def test_a_configured_bound_drops_its_float_artifact(value: str, expected: str) -> None:
    from las.report import strip_trailing_zeros

    assert render_decimal(strip_trailing_zeros(Decimal(value))) == expected


def test_stripping_is_exact_and_never_changes_the_value() -> None:
    from las.report import strip_trailing_zeros

    for value in ("1.0", "0.100", "5.000", "0." + "1" * 200 + "0"):
        assert strip_trailing_zeros(Decimal(value)) == Decimal(value)


def test_stripping_ignores_the_global_decimal_context() -> None:
    from las.report import strip_trailing_zeros

    value = Decimal("0." + "3" * 150 + "0")
    baseline = strip_trailing_zeros(value)
    for prec in (1, 3, 400):
        with global_context(prec):
            assert strip_trailing_zeros(value) == baseline
            assert render_decimal(strip_trailing_zeros(value)) == render_decimal(baseline)


def test_an_amount_is_never_stripped_of_meaningful_zeros() -> None:
    """Only configured bounds are normalised; source amounts keep their zeros."""
    assert render_decimal(Decimal("12.3400")) == "12.3400"
