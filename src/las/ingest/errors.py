"""Typed error hierarchy for the ingestion layer.

Every error carries only safe, generic context: the operation, a source
*label* (a basename or caller-supplied name, never a filesystem path), a
worksheet name, a one-based row number, and a one-based column position.

Deliberately absent from every message: absolute or relative paths, whole
rejected rows, cell values, and any serialization of arbitrary objects.
When a value's type is at fault the message names the *type*, never the
value — so a malformed cell can be reported without leaking its contents.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base class for every ingestion failure, carrying safe context only."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        source: str | None = None,
        worksheet: str | None = None,
        row: int | None = None,
        column: int | None = None,
    ) -> None:
        context: list[str] = []
        if operation is not None:
            context.append(f"operation={operation}")
        if source is not None:
            context.append(f"source={source}")
        if worksheet is not None:
            context.append(f"worksheet={worksheet}")
        if row is not None:
            context.append(f"row={row}")
        if column is not None:
            context.append(f"column={column}")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)
        self.operation = operation
        self.source = source
        self.worksheet = worksheet
        self.row = row
        self.column = column


class UnsupportedFormatError(IngestError):
    """The source's extension is not one this layer can read."""


class SourceLabelError(IngestError):
    """A caller-supplied source label is not safe to record.

    The message never echoes the rejected label, because the whole point of
    rejecting it is that it may carry a path, a host, or a username.
    """


class SourceReadError(IngestError):
    """The source is missing, unreadable, undecodable, or has no header row."""


class WorksheetSelectionError(IngestError):
    """The requested worksheet does not exist, or the workbook has none."""


class SchemaError(IngestError):
    """A schema or alias registry is self-contradictory as *defined*.

    Raised when the schema is built, not when a file is read: a schema that
    cannot be satisfied by any input is a programming error.
    """


class HeaderResolutionError(IngestError):
    """A source's header row cannot be mapped onto the schema."""


class MalformedRowError(IngestError):
    """A physical row's shape contradicts the header row.

    Structural only. A row of the right shape whose *values* are
    business-invalid is never a malformed row — those flow through
    untouched for the validation stage.
    """


class UnsupportedCellValueError(IngestError):
    """A cell holds a type outside the documented safe scalar allowlist."""


class FormulaCellError(IngestError):
    """A cell holds a formula; ingestion never evaluates or trusts caches."""
