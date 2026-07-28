"""Immutable, format-independent report structures.

One model, three renderers
--------------------------
The XLSX, JSON and HTML artifacts all read the same :class:`ReportModel`. No
renderer re-reads a reconciliation result or re-decides an outcome, so the
three cannot drift apart: if a count is wrong it is wrong identically
everywhere, and a test of one is very nearly a test of all three.

Deep immutability
-----------------
A frozen dataclass is not enough on its own, because a frozen field can still
hold a caller's live list. Every sequence field is coerced to a tuple and every
mapping field is copied and then wrapped read-only, so neither the caller's
original container nor the returned object can change afterwards.

What a report is allowed to remember
------------------------------------
Invented account references, validated period and unit tokens, canonical
decimal *text*, safe provenance (a source label, a worksheet name, a physical
row number, a logical row index), stable outcome and reason codes, and counts.
Never a source or account name, an address, arbitrary extras, a path, an
exception, an object representation, or a timestamp.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from las.report.errors import ColumnDefinitionError, UnsafeCellValueError

#: The contract version this report renders. Bumped only when the documented
#: schema in ``docs/REPORTING_CONTRACT.md`` changes shape.
SCHEMA_VERSION = "1.0"


class OutputFormat(StrEnum):
    """The three authorized artifact formats."""

    XLSX = "xlsx"
    JSON = "json"
    HTML = "html"


#: Fixed media type and filename suffix for each format. T13 never chooses a
#: filename; it states what the bytes are so that T14 can name them.
MEDIA_TYPE_BY_FORMAT: Mapping[OutputFormat, str] = MappingProxyType(
    {
        OutputFormat.XLSX: (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        OutputFormat.JSON: "application/json",
        OutputFormat.HTML: "text/html",
    }
)
SUFFIX_BY_FORMAT: Mapping[OutputFormat, str] = MappingProxyType(
    {
        OutputFormat.XLSX: ".xlsx",
        OutputFormat.JSON: ".json",
        OutputFormat.HTML: ".html",
    }
)


class SheetId(StrEnum):
    """The four workbook sheets, whose values are their exact sheet names."""

    SUMMARY = "Summary"
    EXCEPTIONS = "Exceptions"
    RECONCILIATION = "Reconciliation"
    REVIEW_QUEUE = "Review Queue"


#: Workbook sheet order. Fixed by the contract, not configurable.
SHEET_ORDER: tuple[SheetId, ...] = (
    SheetId.SUMMARY,
    SheetId.EXCEPTIONS,
    SheetId.RECONCILIATION,
    SheetId.REVIEW_QUEUE,
)

#: Summary section names, in the order they appear on the sheet.
SUMMARY_SECTIONS: tuple[str, ...] = (
    "metadata",
    "accounting",
    "group_outcome",
    "group_reason",
    "unresolved_reason",
    "variance_band",
)

#: The eight accounting metrics, in their fixed reporting order.
ACCOUNTING_METRICS: tuple[str, ...] = (
    "feed_rows_input",
    "system_rows_input",
    "feed_contributions",
    "system_contributions",
    "unresolved_rows",
    "group_count",
    "exception_group_count",
    "review_queue_count",
)


def render_decimal(value: Decimal) -> str:
    """The one canonical text form of a decimal, everywhere in the report.

    Plain base-10, fixed point, never exponent notation, never a thousands
    separator, never a locale, never a float, and never rounded or quantized.
    Trailing fractional zeros the value actually carries are kept, because
    ``12.3400`` and ``12.34`` are different statements about precision.

    Any zero — ``0``, ``-0``, ``0E-100`` — renders as exactly ``0``. A signed
    or exponent-bearing zero is still nothing, and printing ``-0`` in a report
    reads as a real negative quantity to anyone scanning a column.

    ``format(value, "f")`` is used rather than ``str``: it is the documented
    fixed-point conversion and it does not consult the global decimal context,
    so a three-hundred-digit total survives whatever precision the surrounding
    process happens to be running.
    """
    if not isinstance(value, Decimal):
        raise UnsafeCellValueError(
            "only an exact decimal can be rendered as a report number",
            operation="render_decimal",
        )
    if not value.is_finite():
        raise UnsafeCellValueError(
            "a non-finite value has no canonical report representation",
            operation="render_decimal",
        )
    if value.is_zero():
        return "0"
    return format(value, "f")


def strip_trailing_zeros(value: Decimal) -> Decimal:
    """Drop fractional trailing zeros a value does not actually assert.

    Used for **configured bounds only**, never for amounts. A band written as
    ``1.0`` in YAML arrives as the float ``1.0`` and becomes ``Decimal("1.0")``
    purely because that is how a float prints — the author was not claiming one
    decimal place of precision, and the contract's ladder reads
    ``upper_bound:1``. An *amount* is different: ``12.3400`` came from source
    text that really did carry those zeros, so :func:`render_decimal` keeps
    them.

    Exact and context-free: the digits are trimmed and the exponent raised by
    hand, so nothing is rounded and no global precision is consulted.
    """
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int) or exponent >= 0:
        return value
    trailing = 0
    for digit in reversed(digits):
        if digit != 0 or exponent + trailing >= 0:
            break
        trailing += 1
    if trailing == 0:
        return value
    kept = digits[: len(digits) - trailing] or (0,)
    return Decimal((sign, kept, exponent + trailing))


def render_count(value: int) -> str:
    """A count as text. Integers only, so nothing arbitrary is stringified."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnsafeCellValueError(
            "only an integer can be rendered as a report count",
            operation="render_count",
        )
    return f"{value:d}"


@dataclass(frozen=True)
class Column:
    """One named report column. The name is the literal header text."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ColumnDefinitionError(
                "a report column needs a non-blank name", operation="column"
            )


def columns_from(names: Sequence[str], *, sheet: str) -> tuple[Column, ...]:
    """Build a column tuple, refusing an empty or repeating definition."""
    if not names:
        raise ColumnDefinitionError(
            "a report sheet needs at least one column",
            operation="columns_from",
            sheet=sheet,
        )
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ColumnDefinitionError(
                "a report sheet declares one column twice",
                operation="columns_from",
                sheet=sheet,
                column=name,
            )
        seen.add(name)
    return tuple(Column(name) for name in names)


@dataclass(frozen=True)
class Cell:
    """One cell: canonical text, or blank.

    Blank is ``None`` and stays blank in every format. Where reconciliation
    deliberately published no value — a variance for a unit conflict, a total
    for an absent side — the report shows nothing rather than a zero, because
    a zero there would be a claim the reconciliation never made.
    """

    text: str | None = None

    def __post_init__(self) -> None:
        if self.text is not None and not isinstance(self.text, str):
            raise UnsafeCellValueError(
                "a report cell holds canonical text or nothing at all",
                operation="cell",
            )

    @property
    def is_blank(self) -> bool:
        return self.text is None


#: The single blank cell instance, so blankness is one value everywhere.
BLANK = Cell()


@dataclass(frozen=True)
class Row:
    """One data row, with the fill its outcome earned, if any."""

    cells: tuple[Cell, ...]
    fill: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cells", tuple(self.cells))

    @property
    def texts(self) -> tuple[str | None, ...]:
        return tuple(cell.text for cell in self.cells)


@dataclass(frozen=True)
class Table:
    """One sheet's worth of rows under one fixed column order."""

    sheet: SheetId
    columns: tuple[Column, ...]
    rows: tuple[Row, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(self.rows))
        width = len(self.columns)
        for position, row in enumerate(self.rows):
            if len(row.cells) != width:
                raise ColumnDefinitionError(
                    "a report row does not match its sheet's column count",
                    operation="table",
                    sheet=self.sheet.value,
                    row=position,
                )

    @property
    def headers(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def row_count(self) -> int:
        """Data rows only; the header is not data."""
        return len(self.rows)


@dataclass(frozen=True)
class ReportSummary:
    """The counts every format states, in one deterministic order."""

    recipients: tuple[str, ...]
    accounting: Mapping[str, int]
    group_outcomes: tuple[tuple[str, int], ...]
    group_reasons: tuple[tuple[str, int], ...]
    unresolved_reasons: tuple[tuple[str, int], ...]
    variance_bands: tuple[tuple[str, int], ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipients", tuple(self.recipients))
        for name in (
            "group_outcomes",
            "group_reasons",
            "unresolved_reasons",
            "variance_bands",
        ):
            object.__setattr__(
                self, name, tuple((key, count) for key, count in getattr(self, name))
            )
        # Copied then frozen, and left in its declared order rather than
        # sorted: the accounting metrics have a documented reading order.
        object.__setattr__(self, "accounting", MappingProxyType(dict(self.accounting)))

    @property
    def recipients_text(self) -> str:
        """Recipients as one comma-joined string, in configured order."""
        return ",".join(self.recipients)


@dataclass(frozen=True)
class ReportModel:
    """Everything the three renderers need, and nothing else."""

    summary: ReportSummary
    summary_table: Table
    exceptions: Table
    reconciliation: Table
    review_queue: Table

    @property
    def tables(self) -> tuple[Table, ...]:
        """The four tables in workbook sheet order."""
        return (
            self.summary_table,
            self.exceptions,
            self.reconciliation,
            self.review_queue,
        )

    def table_for(self, sheet: SheetId) -> Table:
        for table in self.tables:
            if table.sheet is sheet:
                return table
        raise ColumnDefinitionError(  # pragma: no cover - all four always exist
            "the report has no such sheet", operation="table_for", sheet=sheet.value
        )


@dataclass(frozen=True)
class RenderedArtifact:
    """One rendered artifact, held entirely in memory.

    Carries no path and no filename. T13 states what the bytes are; choosing
    where they go is T14's business, and a report that named its own file
    would have to know about a working directory it must never look at.
    """

    output_format: OutputFormat
    media_type: str
    suffix: str
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes):
            raise UnsafeCellValueError(
                "a rendered artifact holds immutable bytes",
                operation="artifact",
                output_format=self.output_format.value,
            )

    @property
    def size(self) -> int:
        return len(self.content)


def artifact(output_format: OutputFormat, content: bytes) -> RenderedArtifact:
    """Wrap rendered bytes with the fixed metadata for their format."""
    return RenderedArtifact(
        output_format=output_format,
        media_type=MEDIA_TYPE_BY_FORMAT[output_format],
        suffix=SUFFIX_BY_FORMAT[output_format],
        content=content,
    )
