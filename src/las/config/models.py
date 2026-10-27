"""Pydantic models describing every tunable of a pipeline run.

All thresholds, identifier schemes, and vocabulary here are invented for
this project. Every model rejects unknown keys so that a typo in a config
file fails fast instead of silently disabling behavior, and every value
that later stages rely on (bands, thresholds, aliases, fault profiles)
lives here rather than as magic constants in stage code.
"""

from __future__ import annotations

import re
from math import isfinite
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Canonical field names that every feed's columns are mapped onto.
#: ``record_id`` identifies an operational record (``OPR-########``);
#: ``external_ref`` is the identifier supplied by an external feed
#: (``EXT-#####``), resolved elsewhere to master-data account IDs
#: (``ACC-######``, called ``account_ref`` only in master data);
#: ``amount``/``unit``/``period`` carry the measured value.
CANONICAL_FIELDS: frozenset[str] = frozenset(
    {"record_id", "external_ref", "amount", "unit", "period"}
)

#: The only email domain permitted anywhere in configuration values.
ALLOWED_EMAIL_DOMAIN = "example.com"

_RECIPIENT_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@example\.com")

ReportFormat = Literal["xlsx", "json", "html"]


def _ensure_relative_path(value: str) -> str:
    """Reject absolute, drive-anchored, UNC, and parent-escaping paths."""
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute() or posix.is_absolute() or windows.drive:
        raise ValueError(
            "must be a relative path inside the project; absolute and "
            "network paths are not allowed"
        )
    if ".." in windows.parts:
        raise ValueError("must not contain '..' segments")
    return value


class StrictModel(BaseModel):
    """Base model: unknown keys are configuration errors."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SimulatorConfig(StrictModel):
    """Behavior of the simulated legacy system.

    Only the two window settings the pipeline actually passes to
    ``LegacySystem``. Fault profiles, fault seeds and busy delays belong to
    ``las.simulator`` and are reached by constructing a ``FaultInjector`` or a
    ``LegacySystem`` directly; the CLI wires none of them, so the schema no
    longer accepts keys it would silently ignore.
    """

    visible_rows: int = Field(default=12, ge=1, le=500)
    buffer_rows: int = Field(default=3, ge=0, le=50)


class FeedConfig(StrictModel):
    """One external data source providing operational records."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    path: str
    header_aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def _path_is_relative_tabular_file(cls, value: str) -> str:
        _ensure_relative_path(value)
        suffix = PureWindowsPath(value).suffix.lower()
        if suffix not in {".csv", ".xlsx"}:
            raise ValueError(
                f"must point to a .csv or .xlsx file, got '{suffix or 'no extension'}'"
            )
        return value

    @field_validator("header_aliases")
    @classmethod
    def _aliases_target_canonical_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key for key in value):
            raise ValueError("alias keys must be non-empty header names")
        unknown = sorted(set(value.values()) - CANONICAL_FIELDS)
        if unknown:
            allowed = ", ".join(sorted(CANONICAL_FIELDS))
            raise ValueError(
                f"alias target(s) {unknown} are not canonical fields (allowed: {allowed})"
            )
        return value


class ValidationConfig(StrictModel):
    """Rule parameters for validating ingested operational records."""

    required_fields: list[str] = Field(
        default_factory=lambda: ["record_id", "external_ref", "amount"]
    )
    record_id_pattern: str = r"^OPR-\d{8}$"
    external_ref_pattern: str = r"^EXT-\d{5}$"
    amount_min: float = 0.0
    amount_max: float = 1_000_000.0
    duplicate_key_fields: list[str] = Field(default_factory=lambda: ["record_id"])

    @field_validator("required_fields", "duplicate_key_fields")
    @classmethod
    def _fields_are_canonical(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("must list at least one canonical field")
        if len(set(value)) != len(value):
            raise ValueError("must not contain duplicate field names")
        unknown = sorted(set(value) - CANONICAL_FIELDS)
        if unknown:
            allowed = ", ".join(sorted(CANONICAL_FIELDS))
            raise ValueError(f"unknown field(s) {unknown} (canonical fields: {allowed})")
        return value

    @field_validator("record_id_pattern", "external_ref_pattern")
    @classmethod
    def _pattern_compiles(cls, value: str) -> str:
        try:
            re.compile(value, re.ASCII)
        except (re.error, ValueError) as exc:
            raise ValueError(f"is not a valid regular expression: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _amount_range_ordered(self) -> ValidationConfig:
        # NaN is unordered, so the bounds must positively satisfy this relation.
        if not self.amount_min < self.amount_max:
            raise ValueError("amount_min must be strictly less than amount_max")
        return self


class MatchConfig(StrictModel):
    """Thresholds steering exact/fuzzy entity matching.

    Scores at or above ``fuzzy_threshold`` are accepted automatically;
    scores in ``[review_threshold, fuzzy_threshold)`` are routed to the
    needs-review bucket; anything below is unmatched. Nothing is ever
    silently matched below ``fuzzy_threshold``.
    """

    fuzzy_threshold: float = Field(default=0.87, gt=0.0, le=1.0)
    review_threshold: float = Field(default=0.70, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> MatchConfig:
        if self.review_threshold > self.fuzzy_threshold:
            raise ValueError("review_threshold must be <= fuzzy_threshold")
        return self


class ReportConfig(StrictModel):
    """Output formats, synthetic recipients, and variance banding.

    These four values are everything reporting takes from configuration.
    Sheet names, column names, column order and every colour belong to
    the contract in ``docs/REPORTING_CONTRACT.md``, not to config, so
    that two runs of this project produce the same report shape.

    ``formats`` fixes both which artifacts are produced and the order
    they are returned in. ``recipients`` is report *metadata*: it is
    shown on the summary sheet and in both summary documents, and no
    mail is ever sent. ``streaming_row_threshold`` selects write-only
    workbook construction when any detail sheet reaches it.

    ``variance_bands`` are **absolute amount-difference upper bounds**,
    expressed in the shared validated unit of the reconciliation group
    they judge. They are not percentages: no denominator is involved and
    no division is ever performed, so a group whose totals are zero needs
    no special case. Reconciliation converts each value once through
    ``Decimal(str(value))`` and compares exactly.

    The ladder ascends strictly from exactly ``0``, and every nonzero
    upper edge is **inclusive**. Read against the sample ``[0.0, 1.0,
    5.0]``: a difference of ``0`` is balanced; ``0 < d <= 1`` and
    ``1 < d <= 5`` are within tolerance, selecting the ``1`` and ``5``
    bands respectively; and ``d > 5`` is a mismatch, inside no band.

    They live here because reporting colours by the same ladder it
    reconciles against; it is one set of bounds, read in both places.
    """

    formats: list[ReportFormat] = Field(
        default_factory=lambda: ["xlsx", "json", "html"], min_length=1
    )
    recipients: list[str] = Field(default_factory=lambda: ["ops-team@example.com"])
    variance_bands: list[float] = Field(
        default_factory=lambda: [0.0, 1.0, 5.0],
        min_length=1,
        description=(
            "Absolute amount-difference upper bounds in the group's shared unit, "
            "ascending from zero, with inclusive upper edges. Not percentages."
        ),
    )
    streaming_row_threshold: int = Field(default=50_000, ge=1_000, le=1_048_575)

    @field_validator("formats")
    @classmethod
    def _formats_unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("must not contain duplicate formats")
        return value

    @field_validator("recipients")
    @classmethod
    def _recipients_use_synthetic_domain(cls, value: list[str]) -> list[str]:
        for recipient in value:
            if not _RECIPIENT_PATTERN.fullmatch(recipient):
                raise ValueError(
                    f"recipient '{recipient}' is not allowed; recipients must be "
                    f"synthetic addresses on the {ALLOWED_EMAIL_DOMAIN} domain"
                )
        return value

    @field_validator("variance_bands")
    @classmethod
    def _bands_ascending(cls, value: list[float]) -> list[float]:
        if any(not isfinite(band) for band in value):
            raise ValueError("variance bands must be finite")
        if any(band < 0 for band in value):
            raise ValueError("variance bands must be non-negative")
        if any(later <= earlier for earlier, later in zip(value, value[1:], strict=False)):
            raise ValueError("variance bands must be strictly ascending")
        if value[0] != 0:
            raise ValueError("the first variance band must be exactly zero")
        return value


class RunConfig(StrictModel):
    """Root configuration for one pipeline run."""

    seed: int = Field(ge=0)
    output_dir: str = "output"
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    feeds: list[FeedConfig] = Field(min_length=1)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    match: MatchConfig = Field(default_factory=MatchConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)

    @field_validator("output_dir")
    @classmethod
    def _output_dir_is_relative(cls, value: str) -> str:
        return _ensure_relative_path(value)

    @model_validator(mode="after")
    def _feed_names_unique(self) -> RunConfig:
        names = [feed.name for feed in self.feeds]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise ValueError(f"feeds must have unique names; duplicated: {', '.join(duplicated)}")
        return self
