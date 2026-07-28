"""The immutable parameter set one reporting pass runs against.

What comes from configuration, and what does not
------------------------------------------------
:class:`las.config.ReportConfig` supplies four things and only four: the output
formats and their order, the recipients, the variance ladder, and the streaming
threshold. Sheet names, column names, column order and every colour are part of
the *contract*, not the configuration — they live here as fixed values so that
two runs of this project produce the same report shape, and so that
``docs/REPORTING_CONTRACT.md`` can describe one layout rather than a family of
them.

Colours are exact eight-digit ARGB for the workbook and exact six-digit hex for
HTML, and every one is validated on the way in. A colour that is nearly right
is a colour that is wrong.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from las.config import ReportConfig
from las.reconcile import (
    MATCH_REASONS,
    MESSAGE_BY_REASON,
    VALIDATION_REASONS,
    ReconcileOutcome,
    to_decimal_bound,
)
from las.report.errors import (
    ReportPolicyError,
    SheetDefinitionError,
    StyleDefinitionError,
    UnsupportedFormatError,
)
from las.report.models import (
    SHEET_ORDER,
    Column,
    OutputFormat,
    SheetId,
    columns_from,
    strip_trailing_zeros,
)

_ARGB = re.compile(r"[0-9A-F]{8}")
_HEX = re.compile(r"#[0-9A-F]{6}")

# --- columns ------------------------------------------------------------------

#: The Summary sheet's long-form shape: one metric per row, never a wide table,
#: so adding a metric never moves a column.
SUMMARY_COLUMN_NAMES: tuple[str, ...] = ("section", "metric", "value")

#: Shared by Reconciliation and Exceptions. Exceptions is a filtered view of the
#: same rows, so giving it the same columns keeps the two readable side by side.
GROUP_COLUMN_NAMES: tuple[str, ...] = (
    "account_ref",
    "period",
    "outcome",
    "reason_code",
    "feed_units",
    "system_units",
    "feed_total",
    "system_total",
    "signed_difference",
    "absolute_difference",
    "variance_band_upper",
    "feed_contribution_count",
    "system_contribution_count",
)

#: The Review Queue's columns. Four of them — match_method, external_ref,
#: period and unit — are always blank today, because T12's unresolved record
#: does not carry them. They are declared anyway so the sheet's shape is fixed
#: by the contract rather than by what one release happens to expose.
REVIEW_QUEUE_COLUMN_NAMES: tuple[str, ...] = (
    "source",
    "worksheet",
    "source_row",
    "row_index",
    "unresolved_reason",
    "match_outcome",
    "match_method",
    "account_ref",
    "external_ref",
    "period",
    "unit",
)

COLUMN_NAMES_BY_SHEET: Mapping[SheetId, tuple[str, ...]] = MappingProxyType(
    {
        SheetId.SUMMARY: SUMMARY_COLUMN_NAMES,
        SheetId.EXCEPTIONS: GROUP_COLUMN_NAMES,
        SheetId.RECONCILIATION: GROUP_COLUMN_NAMES,
        SheetId.REVIEW_QUEUE: REVIEW_QUEUE_COLUMN_NAMES,
    }
)

# --- vocabulary order ---------------------------------------------------------

#: Reading order for outcomes in every summary. Deliberately *not* the enum's
#: declaration order, which runs decision-precedence first: a reader wants the
#: settled outcomes before the unsettled ones.
OUTCOME_DISPLAY_ORDER: tuple[ReconcileOutcome, ...] = (
    ReconcileOutcome.BALANCED,
    ReconcileOutcome.WITHIN_TOLERANCE,
    ReconcileOutcome.MISMATCH,
    ReconcileOutcome.UNIT_MISMATCH,
    ReconcileOutcome.MISSING_IN_FEED,
    ReconcileOutcome.MISSING_IN_SYSTEM,
)

#: The outcomes the Exceptions sheet selects: everything that is not settled.
EXCEPTION_OUTCOMES: tuple[ReconcileOutcome, ...] = (
    ReconcileOutcome.MISMATCH,
    ReconcileOutcome.UNIT_MISMATCH,
    ReconcileOutcome.MISSING_IN_FEED,
    ReconcileOutcome.MISSING_IN_SYSTEM,
)

#: Group reason codes, in T12's own declaration order — every reason that is
#: not an unresolved-row reason.
GROUP_REASON_ORDER: tuple[str, ...] = tuple(
    reason
    for reason in MESSAGE_BY_REASON
    if reason not in MATCH_REASONS and reason not in VALIDATION_REASONS
)

#: Unresolved reason codes, matching first and then validation, as T12 declares
#: them. Which stage a row stalled at stays visible in the report too.
UNRESOLVED_REASON_ORDER: tuple[str, ...] = tuple(MATCH_REASONS) + tuple(VALIDATION_REASONS)

# --- styles -------------------------------------------------------------------

HEADER_FILL_ARGB = "FF1F4E78"
HEADER_FONT_ARGB = "FFFFFFFF"

#: One fill per outcome, applied across a whole Reconciliation data row. Every
#: group carries exactly one T12 outcome, so no row can qualify for two.
OUTCOME_FILL_ARGB: Mapping[ReconcileOutcome, str] = MappingProxyType(
    {
        ReconcileOutcome.BALANCED: "FFE2F0D9",
        ReconcileOutcome.WITHIN_TOLERANCE: "FFFFF2CC",
        ReconcileOutcome.MISMATCH: "FFF4CCCC",
        ReconcileOutcome.UNIT_MISMATCH: "FFFCE5CD",
        ReconcileOutcome.MISSING_IN_FEED: "FFDDEBF7",
        ReconcileOutcome.MISSING_IN_SYSTEM: "FFE4DFEC",
    }
)

#: The HTML palette. The outcome colours are the workbook's, without the alpha
#: byte, so the two artifacts read as one report.
HTML_HEADER_BACKGROUND = "#1F4E78"
HTML_HEADER_TEXT = "#FFFFFF"
HTML_TEXT = "#222222"
HTML_BORDER = "#B7B7B7"
HTML_PAGE_BACKGROUND = "#FFFFFF"
HTML_FONT_FAMILY = "Arial, sans-serif"
HTML_OUTCOME_BACKGROUND: Mapping[ReconcileOutcome, str] = MappingProxyType(
    {outcome: f"#{argb[2:]}" for outcome, argb in OUTCOME_FILL_ARGB.items()}
)

#: The HTML document's title, and the workbook's fixed properties. None of
#: these is derived from a clock, a machine, a user or a source filename.
DOCUMENT_TITLE = "Legacy Automation Simulator — Reconciliation Summary"
WORKBOOK_CREATOR = "Legacy Automation Simulator"
WORKBOOK_TITLE = "Reconciliation Report"
WORKBOOK_SUBJECT = "Deterministic reconciliation output"
WORKBOOK_DESCRIPTION = "Synthetic portfolio reconciliation report"
WORKBOOK_EPOCH = (2000, 1, 1, 0, 0, 0)


def _check_argb(value: str, name: str) -> str:
    if not isinstance(value, str) or not _ARGB.fullmatch(value):
        raise StyleDefinitionError(
            "a workbook colour must be exactly eight uppercase hexadecimal digits",
            operation="check_argb",
            column=name,
        )
    return value


def _check_hex(value: str, name: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise StyleDefinitionError(
            "an HTML colour must be a six-digit hexadecimal value",
            operation="check_hex",
            column=name,
        )
    return value


@dataclass(frozen=True)
class ReportPolicy:
    """Immutable, deeply frozen reporting parameters."""

    formats: tuple[OutputFormat, ...]
    recipients: tuple[str, ...]
    variance_bands: tuple[Decimal, ...]
    streaming_row_threshold: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", tuple(self.formats))
        object.__setattr__(self, "recipients", tuple(self.recipients))
        object.__setattr__(self, "variance_bands", tuple(self.variance_bands))

        if not self.formats:
            raise UnsupportedFormatError("at least one output format must be declared")
        seen: set[OutputFormat] = set()
        for output_format in self.formats:
            if not isinstance(output_format, OutputFormat):
                raise UnsupportedFormatError(
                    "an output format outside the authorized set was requested"
                )
            if output_format in seen:
                raise UnsupportedFormatError(
                    "an output format was declared twice",
                    output_format=output_format.value,
                )
            seen.add(output_format)

        for recipient in self.recipients:
            if not isinstance(recipient, str) or not recipient.strip():
                raise ReportPolicyError("a recipient must be a non-blank label")

        if not self.variance_bands:
            raise ReportPolicyError("at least one variance band must be declared")
        for band in self.variance_bands:
            if not isinstance(band, Decimal) or not band.is_finite() or band < 0:
                raise ReportPolicyError("variance bands must be finite and non-negative")
        if any(
            later <= earlier
            for earlier, later in zip(
                self.variance_bands, self.variance_bands[1:], strict=False
            )
        ):
            raise ReportPolicyError("variance bands must ascend strictly")

        if isinstance(self.streaming_row_threshold, bool) or not isinstance(
            self.streaming_row_threshold, int
        ):
            raise ReportPolicyError("streaming_row_threshold must be an integer")
        if self.streaming_row_threshold < 1:
            raise ReportPolicyError("streaming_row_threshold must be at least one")

        # The fixed part of the contract is validated too, so a careless edit
        # to a colour or a sheet name fails loudly rather than shipping.
        _check_argb(HEADER_FILL_ARGB, "header_fill")
        _check_argb(HEADER_FONT_ARGB, "header_font")
        for outcome, argb in OUTCOME_FILL_ARGB.items():
            _check_argb(argb, outcome.value)
        for name, value in (
            ("header_background", HTML_HEADER_BACKGROUND),
            ("header_text", HTML_HEADER_TEXT),
            ("text", HTML_TEXT),
            ("border", HTML_BORDER),
            ("page_background", HTML_PAGE_BACKGROUND),
        ):
            _check_hex(value, name)
        check_sheet_names(SHEET_ORDER)

    # -- the fixed contract, exposed through the policy --------------------

    @property
    def sheet_order(self) -> tuple[SheetId, ...]:
        return SHEET_ORDER

    def columns_for(self, sheet: SheetId) -> tuple[Column, ...]:
        return columns_from(COLUMN_NAMES_BY_SHEET[sheet], sheet=sheet.value)

    def header_style(self) -> tuple[str, str, bool]:
        """Fill, font colour, bold — the same on all four sheets."""
        return (HEADER_FILL_ARGB, HEADER_FONT_ARGB, True)

    def fill_for(self, outcome: ReconcileOutcome) -> str:
        return OUTCOME_FILL_ARGB[outcome]

    def html_background_for(self, outcome: ReconcileOutcome) -> str:
        return HTML_OUTCOME_BACKGROUND[outcome]

    def is_exception(self, outcome: ReconcileOutcome) -> bool:
        return outcome in EXCEPTION_OUTCOMES

    def streams(self, row_count: int) -> bool:
        """Whether a detail sheet of this size is built write-only.

        Inclusive at the boundary: a sheet with exactly the threshold number of
        data rows already streams.
        """
        return row_count >= self.streaming_row_threshold


def check_sheet_names(sheets: Iterable[SheetId]) -> None:
    """Refuse a sheet set a workbook cannot hold."""
    seen: set[str] = set()
    for sheet in sheets:
        name = sheet.value
        if not name or not name.strip():
            raise SheetDefinitionError("a sheet name must not be blank")
        if len(name) > 31:
            raise SheetDefinitionError(
                "a sheet name exceeds the workbook limit", sheet=name
            )
        if any(character in name for character in r"[]:*?/\\"):
            raise SheetDefinitionError(
                "a sheet name uses a character workbooks forbid", sheet=name
            )
        if name in seen:
            raise SheetDefinitionError("a sheet name is declared twice", sheet=name)
        seen.add(name)


def policy_from_config(config: ReportConfig) -> ReportPolicy:
    """Build a reporting policy from the four configured values."""
    formats: list[OutputFormat] = []
    for name in config.formats:
        try:
            formats.append(OutputFormat(name))
        except ValueError:
            raise UnsupportedFormatError(
                "an output format outside the authorized set was requested"
            ) from None
    return ReportPolicy(
        formats=tuple(formats),
        recipients=tuple(config.recipients),
        variance_bands=tuple(
            strip_trailing_zeros(
                to_decimal_bound(value, f"variance_bands[{position}]")
            )
            for position, value in enumerate(config.variance_bands)
        ),
        streaming_row_threshold=config.streaming_row_threshold,
    )
