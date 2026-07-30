"""End-to-end orchestration: the composition root of the whole project.

This is the one package allowed to import every layer, because composing them
is the only thing it does. No layer imports it back, so the dependency graph
still runs one way and every lower package remains testable on its own.

What a run does
---------------
Seven stages — ``EXTRACT → INGEST → VALIDATE → MATCH → RECONCILE → REPORT →
VERIFY`` — then one atomic directory transaction. Every stage before ``VERIFY``
is pure: nothing is created on disk until the gate has passed, so a failing run
leaves the filesystem exactly as it found it. The full contract is written down
in ``docs/PIPELINE_CONTRACT.md``.

What it deliberately does not do
--------------------------------
It never mutates the simulated system. T8's write-back is real, tested and
dry-run by default, but nothing in the repository says which reconciliation
outcome should drive which record status — so this pipeline reports
``DISABLED``, attempts zero updates and applies zero. It also implements no
business rule of its own: every outcome, count and verdict is the answer the
owning layer gave.
"""

from las.pipeline.adapters import (
    BATCH_ID_LENGTH,
    BATCH_ID_PREFIX,
    adapt_ledger,
    batch_id,
    batch_ids,
    physical_key,
)
from las.pipeline.engine import (
    AUDIT_STAGE,
    EVENT_ARTIFACTS_COMMITTED,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_STAGE_COMPLETED,
    EVENT_STARTED,
    MATCHING_OUTCOME_FIELDS,
    PipelineRequest,
    all_stages_completed,
    run_pipeline,
    stage_order,
)
from las.pipeline.errors import (
    ArtifactNameError,
    ArtifactSetError,
    ArtifactWriteError,
    CommitError,
    ConfigurationError,
    ConfiguredInputError,
    DuplicateFeedError,
    ErrorCategory,
    ExtractionError,
    FixtureTransactionError,
    InternalPipelineError,
    LedgerAdapterError,
    OutputConflictError,
    OutputError,
    OutputParentError,
    PipelineError,
    SimulatorInitializationError,
    StageContractError,
    StagedByteError,
    StagingError,
    VerificationError,
)
from las.pipeline.models import (
    FILENAME_BY_FORMAT,
    STAGE_ORDER,
    RunResult,
    RunStatus,
    RunSummary,
    Stage,
    StageState,
    StageStatus,
    WriteBackMode,
    WrittenArtifact,
    filename_for,
)
from las.pipeline.writer import (
    FIXTURE_FILENAMES,
    STAGING_PREFIX,
    check_destination,
    publish,
    publish_fixtures,
)

__all__ = [
    "AUDIT_STAGE",
    "BATCH_ID_LENGTH",
    "BATCH_ID_PREFIX",
    "EVENT_ARTIFACTS_COMMITTED",
    "EVENT_COMPLETED",
    "EVENT_FAILED",
    "EVENT_STAGE_COMPLETED",
    "EVENT_STARTED",
    "FILENAME_BY_FORMAT",
    "FIXTURE_FILENAMES",
    "MATCHING_OUTCOME_FIELDS",
    "STAGE_ORDER",
    "STAGING_PREFIX",
    "ArtifactNameError",
    "ArtifactSetError",
    "ArtifactWriteError",
    "CommitError",
    "ConfigurationError",
    "ConfiguredInputError",
    "DuplicateFeedError",
    "ErrorCategory",
    "ExtractionError",
    "FixtureTransactionError",
    "InternalPipelineError",
    "LedgerAdapterError",
    "OutputConflictError",
    "OutputError",
    "OutputParentError",
    "PipelineError",
    "PipelineRequest",
    "RunResult",
    "RunStatus",
    "RunSummary",
    "SimulatorInitializationError",
    "Stage",
    "StageContractError",
    "StageState",
    "StageStatus",
    "StagedByteError",
    "StagingError",
    "VerificationError",
    "WriteBackMode",
    "WrittenArtifact",
    "adapt_ledger",
    "all_stages_completed",
    "batch_id",
    "batch_ids",
    "physical_key",
    "check_destination",
    "filename_for",
    "publish",
    "publish_fixtures",
    "run_pipeline",
    "stage_order",
]
