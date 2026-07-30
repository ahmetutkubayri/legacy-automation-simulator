"""Immutable public types for one pipeline run.

What a run result may remember
------------------------------
Counts, stable codes, fixed filenames, media types, byte counts and hashes.
Never a path, a config object, a feed or ledger row, a validation result, a
matching decision, a reconciliation object, a report model, artifact bytes, a
simulator, a driver, an exception or an audit object. A run result is a
*statement about* a run, not a handle on one.

Determinism
-----------
Equal business inputs give an equal :class:`RunResult`. Nothing operational
enters it — no run identifier, no timestamp, no temporary name, no absolute
path — so two runs of the same data compare equal even though their audit
envelopes differ.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from las.pipeline.errors import ArtifactNameError
from las.report import OutputFormat

#: The three artifact filenames, fixed by contract. No timestamp, no seed, no
#: run identifier, no counter, no source filename — two runs of the same
#: configuration write the same three names, and a reader knows them by heart.
FILENAME_BY_FORMAT: Mapping[OutputFormat, str] = MappingProxyType(
    {
        OutputFormat.XLSX: "reconciliation-report.xlsx",
        OutputFormat.JSON: "reconciliation-summary.json",
        OutputFormat.HTML: "reconciliation-summary.html",
    }
)


class Stage(StrEnum):
    """The seven business stages, in execution order."""

    EXTRACT = "extract"
    INGEST = "ingest"
    VALIDATE = "validate"
    MATCH = "match"
    RECONCILE = "reconcile"
    REPORT = "report"
    VERIFY = "verify"


#: Stage order, fixed. Configuration loading, logging, audit setup, publication
#: and result construction are orchestration mechanics, not stages.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.EXTRACT,
    Stage.INGEST,
    Stage.VALIDATE,
    Stage.MATCH,
    Stage.RECONCILE,
    Stage.REPORT,
    Stage.VERIFY,
)


class StageStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class WriteBackMode(StrEnum):
    """Whether this run may change the simulated system.

    Only one value exists in T14. The driver's dry-run-by-default write-back
    from T8 is deliberately not wired in: nothing in the repository says which
    reconciliation outcome should drive which record status, and a pipeline
    that guessed would be writing invented state into the system it is meant to
    be reconciling against.
    """

    DISABLED = "disabled"


def filename_for(output_format: OutputFormat) -> str:
    """The authorized filename for one format."""
    try:
        return FILENAME_BY_FORMAT[output_format]
    except KeyError:
        raise ArtifactNameError(
            "no authorized filename exists for this output format",
            output_format=getattr(output_format, "value", None),
        ) from None


@dataclass(frozen=True)
class StageState:
    """One stage's outcome."""

    stage: Stage
    status: StageStatus = StageStatus.COMPLETED


@dataclass(frozen=True)
class WrittenArtifact:
    """One published artifact, described without its bytes or its directory."""

    output_format: OutputFormat
    filename: str
    media_type: str
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        if self.filename != filename_for(self.output_format):
            raise ArtifactNameError(
                "an artifact carries a filename the contract does not authorize",
                output_format=self.output_format.value,
            )
        if "/" in self.filename or "\\" in self.filename:
            raise ArtifactNameError(
                "an artifact filename must be a direct child name",
                output_format=self.output_format.value,
            )
        if self.byte_count < 0 or len(self.sha256) != 64:
            raise ArtifactNameError(
                "an artifact must carry a byte count and a full digest",
                output_format=self.output_format.value,
            )


@dataclass(frozen=True)
class RunSummary:
    """Safe aggregate counts, and nothing that could identify a row."""

    feed_rows: int
    system_rows: int
    validation_invalid: int
    matching_counts: Mapping[str, int]
    reconciliation_groups: int
    reconciliation_counts: Mapping[str, int]
    reconciliation_unresolved: int

    def __post_init__(self) -> None:
        for name in ("matching_counts", "reconciliation_counts"):
            value = dict(getattr(self, name))
            object.__setattr__(self, name, MappingProxyType(dict(sorted(value.items()))))


@dataclass(frozen=True)
class RunResult:
    """Everything one completed run is willing to say about itself."""

    status: RunStatus
    stages: tuple[StageState, ...]
    summary: RunSummary
    artifacts: tuple[WrittenArtifact, ...]
    write_back_mode: WriteBackMode = WriteBackMode.DISABLED
    write_back_attempted: int = 0
    write_back_applied: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))

    @property
    def artifact_count(self) -> int:
        return len(self.artifacts)

    @property
    def completed_stages(self) -> tuple[Stage, ...]:
        return tuple(
            state.stage for state in self.stages if state.status is StageStatus.COMPLETED
        )
