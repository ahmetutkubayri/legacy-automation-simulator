"""Typed errors for the pipeline, each carrying a stable code and fixed message.

Every message is a constant. The only context that travels is categorical: a
stage name, an output format, a count. Paths — input or output — never appear,
because the whole point of resolving them once at the boundary is that nothing
downstream has to know where anything lives. Source rows, amounts, account and
source names, raw exception text, temporary directory names and object
representations never travel either.

``error_code`` is the value the CLI prints. It is a stable identifier, spelled
in ``lower_snake_case``, and it is the *only* thing a failing run puts on
stderr.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    """Which class of failure an error belongs to, and so which exit code.

    The categories map one-to-one onto the documented exit codes, so a new
    error cannot quietly acquire an exit code by resembling another one.
    """

    CONFIGURATION = "configuration"
    OUTPUT = "output"
    EXECUTION = "execution"
    INTERNAL = "internal"


class PipelineError(Exception):
    """Base class for pipeline failures, carrying safe context only."""

    #: Stable identifier printed by the CLI. Overridden by every subclass.
    error_code = "pipeline_error"
    #: Which exit-code class this failure belongs to.
    category = ErrorCategory.EXECUTION

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        output_format: str | None = None,
        count: int | None = None,
    ) -> None:
        context: list[str] = []
        if stage is not None:
            context.append(f"stage={stage}")
        if output_format is not None:
            context.append(f"format={output_format}")
        if count is not None:
            context.append(f"count={count}")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)
        self.stage = stage
        self.output_format = output_format
        self.count = count


# --- configuration and configured input (exit 3) -------------------------------


class ConfigurationError(PipelineError):
    """The run configuration could not be loaded or does not satisfy T14."""

    error_code = "configuration_error"
    category = ErrorCategory.CONFIGURATION


class ConfiguredInputError(ConfigurationError):
    """A configured master or feed input is missing or unusable."""

    error_code = "configured_input_error"


class DuplicateFeedError(ConfigurationError):
    """Two feed definitions describe the same physical input."""

    error_code = "duplicate_feed_error"


# --- output and the directory transaction (exit 4) -----------------------------


class OutputError(PipelineError):
    """The artifact directory transaction cannot be completed."""

    error_code = "output_error"
    category = ErrorCategory.OUTPUT


class OutputConflictError(OutputError):
    """The configured output directory already exists.

    Refused rather than merged, replaced or emptied. A directory that is
    already there may hold a previous run somebody still needs, and this task
    has no authority to decide otherwise.
    """

    error_code = "output_conflict"


class OutputParentError(OutputError):
    """The parent of the configured output directory is missing or not one."""

    error_code = "output_parent_error"


class StagingError(OutputError):
    """The private staging directory could not be created."""

    error_code = "staging_error"


class ArtifactWriteError(OutputError):
    """An artifact could not be written, flushed or closed in staging."""

    error_code = "artifact_write_error"


class StagedByteError(OutputError):
    """A staged file did not read back as the exact bytes that were rendered."""

    error_code = "staged_byte_error"


class ArtifactSetError(OutputError):
    """The staging directory does not hold exactly the expected files."""

    error_code = "artifact_set_error"


class CommitError(OutputError):
    """The staging directory could not be renamed into place."""

    error_code = "commit_error"


class FixtureTransactionError(OutputError):
    """The fixture directory transaction could not be completed."""

    error_code = "fixture_transaction_error"


# --- execution (exit 5) --------------------------------------------------------


class SimulatorInitializationError(PipelineError):
    """The simulated legacy system could not be constructed."""

    error_code = "simulator_initialization_error"


class ExtractionError(PipelineError):
    """The ledger could not be extracted completely through the driver."""

    error_code = "extraction_error"


class LedgerAdapterError(PipelineError):
    """An extracted row could not become a neutral system ledger row."""

    error_code = "ledger_adapter_error"


class StageContractError(PipelineError):
    """A lower layer refused the input this pipeline handed it."""

    error_code = "stage_contract_error"


class ArtifactNameError(PipelineError):
    """A rendered artifact has no authorized filename."""

    error_code = "artifact_name_error"


class VerificationError(PipelineError):
    """The final pure gate found the run inconsistent with itself.

    Raised before any output directory exists, so a run that cannot describe
    itself truthfully publishes nothing at all.
    """

    error_code = "verification_error"


# --- the boundary (exit 6) -----------------------------------------------------


class InternalPipelineError(PipelineError):
    """An exception outside every approved hierarchy reached the boundary.

    Carries no detail whatsoever — not the type name, not the message. An
    unexpected failure is exactly the case where nobody has decided yet what
    is safe to say about it.
    """

    error_code = "internal_error"
    category = ErrorCategory.INTERNAL
