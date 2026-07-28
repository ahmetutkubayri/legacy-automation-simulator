"""Deterministic reporting over reconciliation results.

Depends only on the standard library, ``las.reconcile``, the project's
configuration models, and ``openpyxl`` for the workbook. It knows nothing of
the simulator, the driver, ingestion, validation, matching, the pipeline or the
CLI, and it never reads a file, consults a clock, or uses randomness.

What this layer does
--------------------
States what reconciliation found, in three formats built from one model: a
four-sheet workbook (``Summary``, ``Exceptions``, ``Reconciliation``,
``Review Queue``), a JSON summary, and a self-contained HTML summary. The full
contract — sheet membership, column order, colours, schema and decimal
rendering — is written down in ``docs/REPORTING_CONTRACT.md``.

What it deliberately leaves alone
---------------------------------
Every artifact is returned in memory. Nothing here chooses a directory, derives
a filename, writes a file, looks at the working directory, sends mail or opens
a socket; ``recipients`` is metadata that appears in the output and nothing
more. Nothing re-decides an outcome, recomputes a total, resolves an unresolved
row, or converts a unit: a report that disagreed with its own reconciliation
would be worse than no report at all.
"""

from las.report.documents import (
    HTML_SECTIONS,
    NEWLINE,
    render_html,
    render_json,
    summary_payload,
)
from las.report.engine import (
    RENDERERS,
    build_report,
    render,
    render_artifacts,
    verify_result,
)
from las.report.errors import (
    ColumnDefinitionError,
    DuplicateReportRowError,
    RenderError,
    ReportContractError,
    ReportError,
    ReportPolicyError,
    SheetDefinitionError,
    StyleDefinitionError,
    UnsafeCellValueError,
    UnsupportedFormatError,
)
from las.report.models import (
    ACCOUNTING_METRICS,
    BLANK,
    MEDIA_TYPE_BY_FORMAT,
    SCHEMA_VERSION,
    SHEET_ORDER,
    SUFFIX_BY_FORMAT,
    SUMMARY_SECTIONS,
    Cell,
    Column,
    OutputFormat,
    RenderedArtifact,
    ReportModel,
    ReportSummary,
    Row,
    SheetId,
    Table,
    artifact,
    columns_from,
    render_count,
    render_decimal,
    strip_trailing_zeros,
)
from las.report.policy import (
    COLUMN_NAMES_BY_SHEET,
    DOCUMENT_TITLE,
    EXCEPTION_OUTCOMES,
    GROUP_COLUMN_NAMES,
    GROUP_REASON_ORDER,
    HEADER_FILL_ARGB,
    HEADER_FONT_ARGB,
    HTML_BORDER,
    HTML_FONT_FAMILY,
    HTML_HEADER_BACKGROUND,
    HTML_HEADER_TEXT,
    HTML_OUTCOME_BACKGROUND,
    HTML_PAGE_BACKGROUND,
    HTML_TEXT,
    OUTCOME_DISPLAY_ORDER,
    OUTCOME_FILL_ARGB,
    REVIEW_QUEUE_COLUMN_NAMES,
    SUMMARY_COLUMN_NAMES,
    UNRESOLVED_REASON_ORDER,
    WORKBOOK_CREATOR,
    WORKBOOK_DESCRIPTION,
    WORKBOOK_EPOCH,
    WORKBOOK_SUBJECT,
    WORKBOOK_TITLE,
    ReportPolicy,
    check_sheet_names,
    policy_from_config,
)
from las.report.tabular import (
    UNIT_SEPARATOR,
    band_counts,
    build_summary,
    exceptions_table,
    reconciliation_table,
    review_queue_table,
    summary_table,
)
from las.report.xlsx import TEXT_TYPE, render_xlsx, uses_streaming

__all__ = [
    "ACCOUNTING_METRICS",
    "BLANK",
    "COLUMN_NAMES_BY_SHEET",
    "DOCUMENT_TITLE",
    "EXCEPTION_OUTCOMES",
    "GROUP_COLUMN_NAMES",
    "GROUP_REASON_ORDER",
    "HEADER_FILL_ARGB",
    "HEADER_FONT_ARGB",
    "HTML_BORDER",
    "HTML_FONT_FAMILY",
    "HTML_HEADER_BACKGROUND",
    "HTML_HEADER_TEXT",
    "HTML_OUTCOME_BACKGROUND",
    "HTML_PAGE_BACKGROUND",
    "HTML_SECTIONS",
    "HTML_TEXT",
    "MEDIA_TYPE_BY_FORMAT",
    "NEWLINE",
    "OUTCOME_DISPLAY_ORDER",
    "OUTCOME_FILL_ARGB",
    "RENDERERS",
    "REVIEW_QUEUE_COLUMN_NAMES",
    "SCHEMA_VERSION",
    "SHEET_ORDER",
    "SUFFIX_BY_FORMAT",
    "SUMMARY_COLUMN_NAMES",
    "SUMMARY_SECTIONS",
    "TEXT_TYPE",
    "UNIT_SEPARATOR",
    "UNRESOLVED_REASON_ORDER",
    "WORKBOOK_CREATOR",
    "WORKBOOK_DESCRIPTION",
    "WORKBOOK_EPOCH",
    "WORKBOOK_SUBJECT",
    "WORKBOOK_TITLE",
    "Cell",
    "Column",
    "ColumnDefinitionError",
    "DuplicateReportRowError",
    "OutputFormat",
    "RenderError",
    "RenderedArtifact",
    "ReportContractError",
    "ReportError",
    "ReportModel",
    "ReportPolicy",
    "ReportPolicyError",
    "ReportSummary",
    "Row",
    "SheetDefinitionError",
    "SheetId",
    "StyleDefinitionError",
    "Table",
    "UnsafeCellValueError",
    "UnsupportedFormatError",
    "artifact",
    "band_counts",
    "build_report",
    "build_summary",
    "check_sheet_names",
    "columns_from",
    "exceptions_table",
    "policy_from_config",
    "reconciliation_table",
    "render",
    "render_artifacts",
    "render_count",
    "render_decimal",
    "strip_trailing_zeros",
    "render_html",
    "render_json",
    "render_xlsx",
    "review_queue_table",
    "summary_payload",
    "summary_table",
    "uses_streaming",
    "verify_result",
]
