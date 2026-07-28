"""Typed errors for the reporting subsystem.

Every message is a fixed template plus safe categorical context: an operation,
a sheet name, a column name, an output format, a row position, and invented
synthetic identifiers. Cell contents, source or account names, amounts, whole
rows, absolute paths and raw exception text never travel — those are exactly
the values a reporting error is most tempted to quote when explaining what it
could not render.
"""

from __future__ import annotations


class ReportError(Exception):
    """Base class for reporting failures, carrying safe context only."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        sheet: str | None = None,
        column: str | None = None,
        output_format: str | None = None,
        row: int | None = None,
        account_ref: str | None = None,
        period: str | None = None,
        outcome: str | None = None,
    ) -> None:
        context: list[str] = []
        if operation is not None:
            context.append(f"operation={operation}")
        if sheet is not None:
            context.append(f"sheet={sheet}")
        if column is not None:
            context.append(f"column={column}")
        if output_format is not None:
            context.append(f"format={output_format}")
        if row is not None:
            context.append(f"row={row}")
        if account_ref is not None:
            context.append(f"account_ref={account_ref}")
        if period is not None:
            context.append(f"period={period}")
        if outcome is not None:
            context.append(f"outcome={outcome}")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)
        self.operation = operation
        self.sheet = sheet
        self.column = column
        self.output_format = output_format
        self.row = row
        self.account_ref = account_ref
        self.period = period
        self.outcome = outcome


class ReportPolicyError(ReportError):
    """A reporting policy cannot be built from the supplied parameters."""


class UnsupportedFormatError(ReportPolicyError):
    """An output format outside the authorized set was requested."""


class ColumnDefinitionError(ReportPolicyError):
    """A column set is empty, repeats a name, or names a blank column."""


class SheetDefinitionError(ReportPolicyError):
    """A sheet name is blank, duplicated, or unusable in a workbook."""


class StyleDefinitionError(ReportPolicyError):
    """A style value is not an exact eight-digit ARGB colour."""


class ReportContractError(ReportError):
    """The reconciliation result does not satisfy the T13 input contract.

    Raised before any table or artifact exists. Reporting states what
    reconciliation decided; a result whose summary disagrees with its own
    detail cannot be stated faithfully, and repairing it here would invent a
    reconciliation this project never performed.
    """


class DuplicateReportRowError(ReportContractError):
    """Two report rows claim one identity that must be unique."""


class UnsafeCellValueError(ReportError):
    """A cell value cannot be rendered safely in the requested format."""


class RenderError(ReportError):
    """A renderer failed while producing an artifact.

    Carries the failing operation and the exception's *type name* only; the
    original message is dropped because it can quote a cell value.
    """
