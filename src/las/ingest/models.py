"""Immutable result types produced by the ingestion readers.

Immutability here is *deep*, not merely a frozen dataclass. Sequence fields
are coerced to tuples and mapping fields are copied and then wrapped in a
read-only proxy, so neither the caller's original container nor the returned
object can be changed after construction.

Nothing here holds a filesystem path. A row's provenance is deliberately
minimal — enough to point a person at the exact line of the exact source,
and nothing more.

Source-label policy
-------------------
A label names a source; it is never a location. By default it is the file's
basename. A caller-supplied label passes :func:`safe_source_label`, which
rejects anything that could smuggle a location or disguise text into results
and error messages: path separators, drive prefixes, URL schemes, control
and format characters, bare directory names, blank text, and absurd lengths.
Rejections never echo the offending label — that is precisely the string
that might carry a path or a username.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from types import MappingProxyType

from las.ingest.errors import SourceLabelError

#: Longest accepted source label.
MAX_SOURCE_LABEL_LENGTH = 128

#: Matches a Windows drive prefix or any URL scheme, e.g. ``C:``, ``https:``.
_SCHEME_OR_DRIVE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

#: Unicode categories that must never reach a label: control characters and
#: format characters such as zero-width joiners and bidirectional overrides.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def safe_source_label(label: str, *, operation: str = "source_label") -> str:
    """Return a label safe to record, or raise :class:`SourceLabelError`."""
    if not isinstance(label, str):
        raise SourceLabelError("a source label must be text", operation=operation)

    text = unicodedata.normalize("NFKC", label).strip()
    if not text:
        raise SourceLabelError("a source label must not be blank", operation=operation)
    if len(text) > MAX_SOURCE_LABEL_LENGTH:
        raise SourceLabelError(
            f"a source label must be at most {MAX_SOURCE_LABEL_LENGTH} characters",
            operation=operation,
        )
    if any(unicodedata.category(character) in _FORBIDDEN_CATEGORIES for character in text):
        raise SourceLabelError(
            "a source label must not contain control or format characters",
            operation=operation,
        )
    if "/" in text or "\\" in text:
        raise SourceLabelError(
            "a source label must not contain path separators", operation=operation
        )
    if _SCHEME_OR_DRIVE.match(text):
        raise SourceLabelError(
            "a source label must not begin with a drive prefix or URL scheme",
            operation=operation,
        )
    if text in {".", ".."}:
        raise SourceLabelError(
            "a source label must name a source, not a directory", operation=operation
        )
    return text


class SourceFormat(StrEnum):
    """The tabular formats this layer can read."""

    CSV = "csv"
    XLSX = "xlsx"


class IngestOutcome(StrEnum):
    """Structural shape of a successful ingestion.

    A file whose header row resolved but that carries no data rows is a
    legitimate, deterministic outcome rather than an error, and says so.
    """

    ROWS = "rows"
    HEADER_ONLY = "header_only"


def _freeze(mapping: Mapping[str, str | None]) -> Mapping[str, str | None]:
    """Copy then proxy, so neither side can mutate the other afterwards."""
    return MappingProxyType(dict(mapping))


def _freeze_sequence(values: Sequence[object]) -> tuple[object, ...]:
    """Coerce to a tuple, so a caller-supplied list cannot stay mutable."""
    return tuple(values)


@dataclass(frozen=True)
class RowProvenance:
    """Where one row came from, in safe terms only.

    ``source`` is a label — a basename or a caller-supplied name — never a
    path. ``source_row`` is the one-based *physical* row of the file or
    worksheet, so it matches what a person sees in a text editor or a
    spreadsheet application; for a CSV record spanning several physical
    lines it is the line the record starts on.
    """

    source: str
    source_row: int
    worksheet: str | None = None

    def __post_init__(self) -> None:
        # Enforced here as well as at the reader boundary, so a provenance
        # record can never be built with an unsafe label by any route.
        object.__setattr__(self, "source", safe_source_label(self.source))
        if self.source_row < 1:
            raise ValueError("provenance source_row is one-based")


@dataclass(frozen=True)
class IngestedRow:
    """One row, keyed by canonical field name, with unmapped columns kept apart.

    Values are canonical text or ``None`` for a missing cell. Business-invalid
    content is preserved exactly as read — judging it belongs to validation.
    """

    provenance: RowProvenance
    values: Mapping[str, str | None]
    # A factory, not a bare ``MappingProxyType({})``: Python 3.11's dataclasses
    # reject an unhashable default, and a mappingproxy only became hashable in
    # 3.12. ``__post_init__`` re-freezes either way, so this is the same value.
    extras: Mapping[str, str | None] = dc_field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _freeze(self.values))
        object.__setattr__(self, "extras", _freeze(self.extras))

    def get(self, field: str) -> str | None:
        """The canonical field's text, or ``None`` when missing or absent."""
        return self.values.get(field)


@dataclass(frozen=True)
class IngestResult:
    """Everything one successful read produced, plus safe scalar metadata."""

    source: str
    source_format: SourceFormat
    headers: tuple[str, ...]
    rows: tuple[IngestedRow, ...]
    header_row: int
    skipped_blank_rows: int
    unmapped_headers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    worksheet: str | None = None
    outcome: IngestOutcome = IngestOutcome.ROWS

    def __post_init__(self) -> None:
        for name in ("headers", "rows", "unmapped_headers", "warnings"):
            object.__setattr__(self, name, _freeze_sequence(getattr(self, name)))

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def column(self, field: str) -> tuple[str | None, ...]:
        """One canonical field's values across every row, in source order."""
        return tuple(row.get(field) for row in self.rows)
