"""Immutable result types, severities, and the stable issue-code registry.

Stable codes
------------
Every issue carries one code from :data:`SEVERITY_BY_CODE`. Codes are part of
the public contract: reports and downstream stages key off them, so they are
invented once, spelled in ``lower_snake_case``, and never reworded. The
human-readable text lives in :data:`MESSAGE_BY_CODE` as a *fixed template* —
messages are never assembled from source values, so an issue can name a
defect without ever quoting the data that caused it.

Severities
----------
Two, as the development plan specifies. ``ERROR`` means the row cannot be
trusted; ``WARN`` means it is well-formed but something downstream should
look at it. They are never conflated: a warning alone never makes a row
invalid.

Deep immutability
-----------------
Frozen dataclasses are not enough on their own — a frozen field can still
hold a caller's live list. Every sequence field is coerced to a tuple and
every mapping field is copied and then wrapped read-only, so neither the
caller's original container nor the returned object can change afterwards.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from types import MappingProxyType

from las.ingest import IngestedRow, RowProvenance
from las.validate.errors import ValidationError


class Severity(StrEnum):
    """How much a given issue matters. Warnings never imply invalidity."""

    ERROR = "error"
    WARN = "warn"


#: Deterministic severity ordering for summaries and sorting.
SEVERITY_RANK: Mapping[Severity, int] = MappingProxyType(
    {Severity.ERROR: 0, Severity.WARN: 1}
)


class RowOutcome(StrEnum):
    """One row's verdict, kept distinct from the issues that produced it."""

    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"


# --- stable issue codes -------------------------------------------------------

CODE_REQUIRED_VALUE_MISSING = "required_value_missing"
CODE_RECORD_ID_FORMAT_INVALID = "record_id_format_invalid"
CODE_EXTERNAL_REF_FORMAT_INVALID = "external_ref_format_invalid"
CODE_ACCOUNT_REF_FORMAT_INVALID = "account_ref_format_invalid"
CODE_AMOUNT_NOT_DECIMAL = "amount_not_decimal"
CODE_AMOUNT_NOT_FINITE = "amount_not_finite"
CODE_AMOUNT_OUT_OF_RANGE = "amount_out_of_range"
CODE_UNIT_NOT_SUPPORTED = "unit_not_supported"
CODE_PERIOD_FORMAT_INVALID = "period_format_invalid"
CODE_PERIOD_MONTH_INVALID = "period_month_invalid"
CODE_EXTERNAL_REF_UNKNOWN = "external_ref_unknown"
CODE_DUPLICATE_KEY = "duplicate_key"

#: The single authority on which codes exist and how severe each one is.
SEVERITY_BY_CODE: Mapping[str, Severity] = MappingProxyType(
    {
        CODE_REQUIRED_VALUE_MISSING: Severity.ERROR,
        CODE_RECORD_ID_FORMAT_INVALID: Severity.ERROR,
        CODE_EXTERNAL_REF_FORMAT_INVALID: Severity.ERROR,
        CODE_ACCOUNT_REF_FORMAT_INVALID: Severity.ERROR,
        CODE_AMOUNT_NOT_DECIMAL: Severity.ERROR,
        CODE_AMOUNT_NOT_FINITE: Severity.ERROR,
        CODE_AMOUNT_OUT_OF_RANGE: Severity.ERROR,
        CODE_UNIT_NOT_SUPPORTED: Severity.ERROR,
        CODE_PERIOD_FORMAT_INVALID: Severity.ERROR,
        CODE_PERIOD_MONTH_INVALID: Severity.ERROR,
        # Well-formed but unresolved: whether a reference resolves, and which
        # copy of a duplicated key wins, are decisions for later stages.
        CODE_EXTERNAL_REF_UNKNOWN: Severity.WARN,
        CODE_DUPLICATE_KEY: Severity.WARN,
    }
)

#: Fixed message templates. No template interpolates a source value.
MESSAGE_BY_CODE: Mapping[str, str] = MappingProxyType(
    {
        CODE_REQUIRED_VALUE_MISSING: "required value is missing",
        CODE_RECORD_ID_FORMAT_INVALID: "record identifier format is invalid",
        CODE_EXTERNAL_REF_FORMAT_INVALID: "external reference format is invalid",
        CODE_ACCOUNT_REF_FORMAT_INVALID: "account reference format is invalid",
        CODE_AMOUNT_NOT_DECIMAL: "amount is not a valid decimal",
        CODE_AMOUNT_NOT_FINITE: "amount is not a finite number",
        CODE_AMOUNT_OUT_OF_RANGE: "amount is outside the permitted range",
        CODE_UNIT_NOT_SUPPORTED: "unit is not supported",
        CODE_PERIOD_FORMAT_INVALID: "period format is invalid",
        CODE_PERIOD_MONTH_INVALID: "period month is out of range",
        CODE_EXTERNAL_REF_UNKNOWN: "external reference is absent from the reference set",
        CODE_DUPLICATE_KEY: "duplicate key appears in more than one source row",
    }
)

#: Metadata keys: short invented identifiers.
_SAFE_METADATA_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")

#: Metadata text values. Deliberately excludes ``:``, ``/`` and ``\``, so a
#: path, a UNC name or a URL scheme cannot be spelled at all, and excludes
#: every non-printable and bidirectional character by construction.
_SAFE_METADATA_TEXT = re.compile(r"[A-Za-z0-9 ,._+-]{1,120}")

#: The complete allowlist of metadata value types. Anything else — a nested
#: list, dict or set, a Path, an arbitrary object — is rejected outright
#: rather than shallowly wrapped, and the rejection never serializes it.
_METADATA_VALUE_TYPES = (bool, int, str)


def _freeze_mapping(mapping: Mapping[str, object]) -> Mapping[str, object]:
    """Copy then proxy, so neither side can mutate the other afterwards."""
    return MappingProxyType(dict(mapping))


def _check_metadata(
    metadata: Mapping[str, str | int | bool | None],
) -> Mapping[str, str | int | bool | None]:
    """Copy, screen against the scalar allowlist, then freeze in key order.

    Errors name the offending key and the offending *type* — never the value,
    which is exactly the thing that might be unsafe to render.
    """
    cleaned: dict[str, str | int | bool | None] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not _SAFE_METADATA_KEY.fullmatch(key):
            raise ValidationError("issue metadata keys must be short lowercase names")
        if value is None or isinstance(value, bool | int):
            cleaned[key] = value
            continue
        if not isinstance(value, _METADATA_VALUE_TYPES):
            raise ValidationError(
                f"issue metadata value for '{key}' has type "
                f"'{type(value).__name__}', which is not allowed"
            )
        if not _SAFE_METADATA_TEXT.fullmatch(value):
            raise ValidationError(
                f"issue metadata text for '{key}' is not a safe categorical token"
            )
        cleaned[key] = value
    # Sorted, so the mapping has one order regardless of how it was built.
    return MappingProxyType(dict(sorted(cleaned.items())))


@dataclass(frozen=True)
class ValidationIssue:
    """One defect found in one row, described without quoting the data."""

    code: str
    severity: Severity
    message: str
    source: str
    source_row: int
    field: str | None = None
    worksheet: str | None = None
    metadata: Mapping[str, str | int | bool | None] = dc_field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.code not in SEVERITY_BY_CODE:
            raise ValidationError(f"unknown issue code '{self.code}'")
        if self.severity is not SEVERITY_BY_CODE[self.code]:
            raise ValidationError(
                f"issue code '{self.code}' does not carry that severity"
            )
        if self.message != MESSAGE_BY_CODE[self.code]:
            raise ValidationError(
                f"issue code '{self.code}' must use its fixed message template"
            )
        if self.source_row < 1:
            raise ValidationError("source_row is one-based")
        object.__setattr__(self, "metadata", _check_metadata(self.metadata))

    @classmethod
    def create(
        cls,
        code: str,
        provenance: RowProvenance,
        *,
        field_name: str | None = None,
        metadata: Mapping[str, str | int | bool | None] | None = None,
    ) -> ValidationIssue:
        """Build an issue from its code and the row's T9 provenance."""
        if code not in SEVERITY_BY_CODE:
            raise ValidationError(f"unknown issue code '{code}'")
        return cls(
            code=code,
            severity=SEVERITY_BY_CODE[code],
            message=MESSAGE_BY_CODE[code],
            source=provenance.source,
            source_row=provenance.source_row,
            field=field_name,
            worksheet=provenance.worksheet,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class ValidatedRow:
    """One source row plus its issues and verdict.

    ``row`` is the T9 :class:`~las.ingest.IngestedRow` exactly as ingested —
    already deeply immutable, and never rewritten here. A row appears once
    however many issues it collected.
    """

    row: IngestedRow
    index: int
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.index < 0:
            raise ValidationError("row index is zero-based and non-negative")

    @property
    def provenance(self) -> RowProvenance:
        return self.row.provenance

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity is Severity.WARN)

    @property
    def outcome(self) -> RowOutcome:
        if self.error_count:
            return RowOutcome.INVALID
        if self.warning_count:
            return RowOutcome.VALID_WITH_WARNINGS
        return RowOutcome.VALID

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


@dataclass(frozen=True)
class ValidationSummary:
    """Deterministic counts over one batch. Every mapping is read-only."""

    total_rows: int
    valid_rows: int
    rows_with_warnings: int
    invalid_rows: int
    total_issues: int
    issues_by_severity: Mapping[str, int] = dc_field(
        default_factory=lambda: MappingProxyType({})
    )
    issues_by_code: Mapping[str, int] = dc_field(
        default_factory=lambda: MappingProxyType({})
    )
    completed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues_by_severity", _freeze_mapping(self.issues_by_severity))
        object.__setattr__(self, "issues_by_code", _freeze_mapping(self.issues_by_code))


@dataclass(frozen=True)
class BatchValidationResult:
    """Everything one validation pass produced, in one deterministic order."""

    source: str
    rows: tuple[ValidatedRow, ...]
    issues: tuple[ValidationIssue, ...]
    summary: ValidationSummary
    rule_ids: tuple[str, ...] = ()
    worksheet: str | None = None

    def __post_init__(self) -> None:
        for name in ("rows", "issues", "rule_ids"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def is_clean(self) -> bool:
        """True when nothing at all was flagged, warnings included."""
        return not self.issues

    def rows_with_outcome(self, outcome: RowOutcome) -> tuple[ValidatedRow, ...]:
        return tuple(row for row in self.rows if row.outcome is outcome)

    def issues_with_code(self, code: str) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code == code)


def summarize(
    rows: Sequence[ValidatedRow],
    issues: Sequence[ValidationIssue],
    *,
    completed: bool = True,
) -> ValidationSummary:
    """Build a summary whose mappings are ordered deterministically.

    Severity counts follow :data:`SEVERITY_RANK`; code counts are sorted by
    code. Neither depends on dict insertion order or hash randomization, and
    every declared severity appears even when its count is zero.
    """
    by_severity = {
        severity.value: sum(1 for issue in issues if issue.severity is severity)
        for severity in sorted(Severity, key=lambda item: SEVERITY_RANK[item])
    }
    present = {issue.code for issue in issues}
    by_code = {
        code: sum(1 for issue in issues if issue.code == code) for code in sorted(present)
    }
    return ValidationSummary(
        total_rows=len(rows),
        valid_rows=sum(1 for row in rows if row.outcome is RowOutcome.VALID),
        rows_with_warnings=sum(
            1 for row in rows if row.outcome is RowOutcome.VALID_WITH_WARNINGS
        ),
        invalid_rows=sum(1 for row in rows if row.outcome is RowOutcome.INVALID),
        total_issues=len(issues),
        issues_by_severity=by_severity,
        issues_by_code=by_code,
        completed=completed,
    )
