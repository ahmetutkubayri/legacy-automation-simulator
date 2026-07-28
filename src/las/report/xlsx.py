"""The workbook renderer: four sheets, fixed styles, and no formulas ever.

Text, deliberately
------------------
Every cell is written as text, including the numbers. Excel stores numerics as
binary doubles, which cannot hold the hundred-, hundred-and-fifty- or
three-hundred-digit totals reconciliation is capable of producing, and a report
that silently rounds its own evidence is worse than one that is awkward to sum
in a spreadsheet. The canonical decimal text is exact, and it is what the JSON
and HTML artifacts carry too, so all three agree to the last digit.

Forcing the type also closes the formula door. openpyxl classifies a string
beginning with ``=`` as a formula, so a source label like ``=cmd`` would become
executable on open. Every cell's type is set explicitly to text afterwards,
which leaves the original characters untouched — nothing is trimmed, prefixed
or rewritten — while guaranteeing the workbook contains no formula at all.

Two construction paths, one workbook
------------------------------------
Above the configured threshold the workbook is built write-only, which streams
rows out instead of holding them in memory. The two paths must be
indistinguishable after reload: same sheets, order, dimensions, values, types,
header styles and fills. Only the ZIP container may differ, which is why this
package promises semantic determinism and never byte determinism.
"""

from __future__ import annotations

import io
from datetime import datetime
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.writer.excel import ExcelWriter

from las.report.errors import RenderError
from las.report.models import ReportModel, SheetId, Table
from las.report.policy import (
    WORKBOOK_CREATOR,
    WORKBOOK_DESCRIPTION,
    WORKBOOK_EPOCH,
    WORKBOOK_SUBJECT,
    WORKBOOK_TITLE,
    ReportPolicy,
    check_sheet_names,
)

#: Text cell type, in openpyxl's vocabulary.
TEXT_TYPE = "s"


def _header_font(policy: ReportPolicy) -> Font:
    _, colour, bold = policy.header_style()
    return Font(bold=bold, color=colour)


def _header_fill(policy: ReportPolicy) -> PatternFill:
    fill, _, _ = policy.header_style()
    return PatternFill(fill_type="solid", fgColor=fill)


def _solid(argb: str) -> PatternFill:
    return PatternFill(fill_type="solid", fgColor=argb)


def _stamp(workbook: Workbook) -> None:
    """Fixed document properties: no clock, no machine, no user, no path."""
    epoch = datetime(*WORKBOOK_EPOCH)
    properties = workbook.properties
    properties.creator = WORKBOOK_CREATOR
    properties.lastModifiedBy = WORKBOOK_CREATOR
    properties.title = WORKBOOK_TITLE
    properties.subject = WORKBOOK_SUBJECT
    properties.description = WORKBOOK_DESCRIPTION
    properties.created = epoch
    properties.modified = epoch


def _fills_rows(table: Table) -> bool:
    """Only ``Reconciliation`` paints outcome fills.

    Every table row knows the colour its outcome earned, but the contract
    fills one sheet. Exceptions is already a list of things that went wrong,
    so colouring it says nothing a reader does not already know.
    """
    return table.sheet is SheetId.RECONCILIATION


def _write_standard(workbook: Workbook, table: Table, policy: ReportPolicy) -> None:
    sheet = workbook.create_sheet(table.sheet.value)
    font, fill = _header_font(policy), _header_fill(policy)
    paint = _fills_rows(table)

    for column, header in enumerate(table.headers, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.data_type = TEXT_TYPE
        cell.font = font
        cell.fill = fill

    for offset, row in enumerate(table.rows):
        row_fill = _solid(row.fill) if paint and row.fill else None
        for column, value in enumerate(row.texts, start=1):
            cell = sheet.cell(row=2 + offset, column=column)
            if value is not None:
                cell.value = value
                cell.data_type = TEXT_TYPE
            if row_fill is not None:
                cell.fill = row_fill


def _write_streaming(workbook: Workbook, table: Table, policy: ReportPolicy) -> None:
    sheet = workbook.create_sheet(table.sheet.value)
    font, fill = _header_font(policy), _header_fill(policy)
    paint = _fills_rows(table)

    header = []
    for name in table.headers:
        cell = WriteOnlyCell(sheet, value=name)
        cell.data_type = TEXT_TYPE
        cell.font = font
        cell.fill = fill
        header.append(cell)
    sheet.append(header)

    for row in table.rows:
        row_fill = _solid(row.fill) if paint and row.fill else None
        cells = []
        for value in row.texts:
            cell = WriteOnlyCell(sheet, value=value)
            if value is not None:
                cell.data_type = TEXT_TYPE
            if row_fill is not None:
                cell.fill = row_fill
            cells.append(cell)
        sheet.append(cells)


def uses_streaming(report: ReportModel, policy: ReportPolicy) -> bool:
    """Whether this report's detail sheets are large enough to stream.

    Summary is excluded on purpose: it is bounded by the vocabulary, not by
    the data, so it can never be the reason a workbook streams.
    """
    return any(
        policy.streams(report.table_for(sheet).row_count)
        for sheet in (
            report.exceptions.sheet,
            report.reconciliation.sheet,
            report.review_queue.sheet,
        )
    )


def render_xlsx(report: ReportModel, policy: ReportPolicy) -> bytes:
    """Render the workbook, choosing its construction path by size."""
    check_sheet_names(policy.sheet_order)
    streaming = uses_streaming(report, policy)

    try:
        workbook = Workbook(write_only=streaming)
        if not streaming:
            # A fresh workbook already holds one sheet; the four are created
            # explicitly, in contract order, so remove it first.
            workbook.remove(workbook.active)
        for sheet in policy.sheet_order:
            table = report.table_for(sheet)
            if streaming:
                _write_streaming(workbook, table, policy)
            else:
                _write_standard(workbook, table, policy)
        _stamp(workbook)

        # Written through ``ExcelWriter`` rather than ``Workbook.save``:
        # openpyxl's save path overwrites ``properties.modified`` with the
        # current UTC time just before writing, which would put a clock reading
        # into every workbook and make two runs differ. Driving the writer
        # directly keeps the fixed epoch this contract requires.
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED, allowZip64=True) as archive:
            ExcelWriter(workbook, archive).write_data()
        workbook.close()
        return buffer.getvalue()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # Only the exception's type name travels: its message could quote a
        # cell value, and a cell value belongs in the workbook, not an error.
        raise RenderError(
            f"workbook rendering raised {type(exc).__name__}",
            operation="render_xlsx",
            output_format="xlsx",
        ) from None
