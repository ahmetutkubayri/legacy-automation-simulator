"""The composition root: seven stages, one gate, one directory transaction.

This is the only module allowed to import every layer, because composing them
is all it does. It contains no business rule of its own — every outcome,
threshold, count and verdict comes from the layer that owns it, and the
pipeline's whole job is to hand each layer its input in the right order and to
refuse to publish anything if the result cannot describe itself truthfully.

Order
-----
``EXTRACT → INGEST → VALIDATE → MATCH → RECONCILE → REPORT → VERIFY``, then
publication. Every stage before ``VERIFY`` is pure: no directory is created and
no byte is written until the gate has passed, so a run that fails anywhere
leaves the filesystem exactly as it found it.

Report-only
-----------
Nothing here mutates the simulator. T8's write-back exists and is dry-run by
default, but the repository never says which reconciliation outcome should
drive which record status — so this pipeline reports ``DISABLED``, attempts
zero updates and applies zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from las.audit import AuditTrail
from las.config import RunConfig
from las.driver import LegacyDriver, extract_query_results
from las.ingest import (
    ACCOUNT_LINK_SCHEMA,
    ACCOUNT_SCHEMA,
    IngestError,
    feed_schema,
    read_source,
)
from las.match import build_master_index, match_validated
from las.match import policy_from_config as match_policy
from las.pipeline.adapters import adapt_ledger, batch_ids
from las.pipeline.errors import (
    ConfiguredInputError,
    ExtractionError,
    PipelineError,
    SimulatorInitializationError,
    StageContractError,
    VerificationError,
)
from las.pipeline.models import (
    STAGE_ORDER,
    RunResult,
    RunStatus,
    RunSummary,
    Stage,
    StageState,
    StageStatus,
    WriteBackMode,
    filename_for,
)
from las.pipeline.writer import publish
from las.reconcile import FeedBatch, reconcile
from las.reconcile import policy_from_config as reconcile_policy
from las.report import build_report, render_artifacts
from las.report import policy_from_config as report_policy
from las.simulator import LegacySystem, RecordStore, SimulatedClock
from las.validate import policy_from_config as validation_policy
from las.validate import validate_ingest_result

#: The five matching outcomes T11's summary counts, named once so the run
#: result and the terminal summary cannot drift apart.
MATCHING_OUTCOME_FIELDS: tuple[str, ...] = (
    "exact",
    "fuzzy",
    "needs_review",
    "unmatched",
    "ineligible",
)

#: Audit stage label and the five approved event names.
AUDIT_STAGE = "pipeline"
EVENT_STARTED = "pipeline_started"
EVENT_STAGE_COMPLETED = "pipeline_stage_completed"
EVENT_ARTIFACTS_COMMITTED = "pipeline_artifacts_committed"
EVENT_COMPLETED = "pipeline_completed"
EVENT_FAILED = "pipeline_failed"


@dataclass(frozen=True)
class PipelineRequest:
    """One resolved request: config, and the directory it was loaded from.

    Paths live here and nowhere else. Everything the configuration stores
    relatively is resolved against ``config_dir``, so a later working-directory
    change cannot move an input, and nothing downstream ever sees a path.
    """

    config: RunConfig
    config_dir: Path
    #: Where ``demo`` was told to publish. ``run`` never sets it, because its
    #: contract says the output directory comes only from configuration.
    output_override: Path | None = None

    def resolve(self, value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else self.config_dir / candidate

    @property
    def output_dir(self) -> Path:
        if self.output_override is not None:
            return self.output_override
        return self.resolve(self.config.output_dir)


class _Recorder:
    """Emits the approved audit events, or does nothing when none is wanted."""

    def __init__(self, trail: AuditTrail | None) -> None:
        self._trail = trail

    def emit(self, event: str, payload: Mapping[str, object] | None = None) -> None:
        if self._trail is not None:
            self._trail.record(AUDIT_STAGE, event, dict(payload or {}))


def _extract(config: RunConfig, samples_dir: Path):
    """Build one simulator and read the whole ledger through the driver."""
    try:
        store = RecordStore.from_fixtures(samples_dir)
        system = LegacySystem(
            store,
            visible_rows=config.simulator.visible_rows,
            buffer_rows=config.simulator.buffer_rows,
            clock=SimulatedClock(),
        )
    except Exception as exc:
        del exc
        raise SimulatorInitializationError(
            "the simulated legacy system could not be constructed",
            stage=Stage.EXTRACT.value,
        ) from None

    driver = LegacyDriver(system)
    try:
        # An unfiltered query selects the whole ledger; the T8 helper drives the
        # screens and scrolls the window for us, so the pipeline never touches
        # simulator internals.
        result = extract_query_results(driver)
    except Exception as exc:
        del exc
        raise ExtractionError(
            "the system ledger could not be extracted", stage=Stage.EXTRACT.value
        ) from None
    if not result.complete:
        raise ExtractionError(
            "the extraction did not prove itself complete", stage=Stage.EXTRACT.value
        )
    return store, result.rows


def _read(path: Path, schema, *, stage: Stage):
    try:
        return read_source(path, schema)
    except IngestError as exc:
        del exc
        raise ConfiguredInputError(
            "a configured input could not be read", stage=stage.value
        ) from None
    except OSError:
        raise ConfiguredInputError(
            "a configured input is missing or unreadable", stage=stage.value
        ) from None


def _verify(
    request: PipelineRequest,
    extracted_count: int,
    system_rows,
    batches,
    identifiers,
    result,
    report,
    artifacts,
) -> None:
    """The final pure gate. Nothing has touched the filesystem yet."""
    formats = request.config.report.formats

    def fail(message: str, **context) -> None:
        raise VerificationError(message, stage=Stage.VERIFY.value, **context)

    if extracted_count != len(system_rows):
        fail("the adapted ledger does not match the extraction row for row")
    if len({row.position for row in system_rows}) != len(system_rows):
        fail("two adapted ledger rows claim one position")
    if len({row.record_id for row in system_rows}) != len(system_rows):
        fail("two adapted ledger rows share one record identifier")
    if len(batches) != len(identifiers) or len(set(identifiers)) != len(identifiers):
        fail("the configured feeds are not represented exactly once")
    if not result.summary.accounting_holds:
        fail("the reconciliation accounting does not balance")
    if result.summary.group_count != len(result.groups):
        fail("the reconciliation summary disagrees with its groups")

    if report.reconciliation.row_count != len(result.groups):
        fail("the report does not carry every reconciliation group")
    if report.review_queue.row_count != len(result.unresolved):
        fail("the report does not carry every unresolved row")

    if len(artifacts) != len(formats):
        fail("the rendered artifacts do not match the configured formats",
             count=len(artifacts))
    if [item.output_format.value for item in artifacts] != list(formats):
        fail("the rendered artifacts are not in configured format order")
    if len({item.output_format for item in artifacts}) != len(artifacts):
        fail("a configured format was rendered twice")

    from las.report import MEDIA_TYPE_BY_FORMAT, SUFFIX_BY_FORMAT

    names: set[str] = set()
    output_dir = request.output_dir
    for item in artifacts:
        if item.media_type != MEDIA_TYPE_BY_FORMAT[item.output_format]:
            fail("an artifact carries the wrong media type",
                 output_format=item.output_format.value)
        if item.suffix != SUFFIX_BY_FORMAT[item.output_format]:
            fail("an artifact carries the wrong suffix",
                 output_format=item.output_format.value)
        if not item.content:
            fail("an artifact payload is empty", output_format=item.output_format.value)
        name = filename_for(item.output_format)
        if name in names:
            fail("two artifacts claim one filename")
        names.add(name)
        planned = output_dir / name
        if planned.parent != output_dir or planned.name != name:
            fail("a planned artifact path is not a direct child of the output directory")
        if Path(name).is_absolute() or ".." in Path(name).parts:
            fail("an artifact filename is not a plain child name")


def run_pipeline(
    request: PipelineRequest, *, trail: AuditTrail | None = None
) -> RunResult:
    """Execute every stage, gate the result, then publish it atomically."""
    config = request.config
    recorder = _Recorder(trail)
    stage = Stage.EXTRACT
    completed: list[StageState] = []

    try:
        recorder.emit(EVENT_STARTED, {"command": "run", "seed": config.seed})

        # 1. EXTRACT ---------------------------------------------------------
        samples_dir = request.resolve(str(Path(config.feeds[0].path).parent))
        store, extracted = _extract(config, samples_dir)
        before = store.snapshot()
        completed.append(StageState(Stage.EXTRACT))
        recorder.emit(EVENT_STAGE_COMPLETED,
                      {"stage": Stage.EXTRACT.value, "count": len(extracted)})

        # 2. INGEST ----------------------------------------------------------
        stage = Stage.INGEST
        identifiers = batch_ids(config.feeds, request.config_dir)
        accounts = _read(samples_dir / "accounts.csv", ACCOUNT_SCHEMA, stage=stage)
        links = _read(samples_dir / "account_links.csv", ACCOUNT_LINK_SCHEMA, stage=stage)
        try:
            index = build_master_index(accounts, links)
        except Exception as exc:
            del exc
            raise StageContractError(
                "the master data could not be indexed", stage=stage.value
            ) from None
        try:
            ingested = [
                _read(request.resolve(feed.path), feed_schema(feed), stage=stage)
                for feed in config.feeds
            ]
        except IngestError:
            raise ConfiguredInputError(
                "a configured feed schema is invalid", stage=stage.value
            ) from None
        feed_rows = sum(item.row_count for item in ingested)
        completed.append(StageState(Stage.INGEST))
        recorder.emit(EVENT_STAGE_COMPLETED,
                      {"stage": stage.value, "count": feed_rows})

        # 3. VALIDATE --------------------------------------------------------
        stage = Stage.VALIDATE
        known = frozenset(v for v in links.column("external_ref") if v)
        try:
            vpolicy = validation_policy(config.validation, known_external_refs=known)
            validated = [validate_ingest_result(item, vpolicy) for item in ingested]
        except PipelineError:
            raise
        except Exception:
            raise StageContractError(
                "the validation refused this run's input", stage=stage.value
            ) from None
        invalid = sum(item.summary.invalid_rows for item in validated)
        completed.append(StageState(Stage.VALIDATE))
        recorder.emit(EVENT_STAGE_COMPLETED, {"stage": stage.value, "count": invalid})

        # 4. MATCH -----------------------------------------------------------
        stage = Stage.MATCH
        try:
            mpolicy = match_policy(config.match)
            matched = [match_validated(item, index, mpolicy) for item in validated]
        except PipelineError:
            raise
        except Exception:
            raise StageContractError(
                "the matching refused this run's input", stage=stage.value
            ) from None
        matching_counts = {name: 0 for name in MATCHING_OUTCOME_FIELDS}
        for item in matched:
            for name in MATCHING_OUTCOME_FIELDS:
                matching_counts[name] += getattr(item.summary, name)
        completed.append(StageState(Stage.MATCH))
        recorder.emit(EVENT_STAGE_COMPLETED,
                      {"stage": stage.value, "count": sum(matching_counts.values())})

        # 5. RECONCILE -------------------------------------------------------
        stage = Stage.RECONCILE
        system_rows = adapt_ledger(extracted, index)
        batches = tuple(
            FeedBatch(validation=v, matching=m, batch_id=identifier)
            for v, m, identifier in zip(validated, matched, identifiers, strict=True)
        )
        try:
            result = reconcile(batches, system_rows, reconcile_policy(config.report))
        except PipelineError:
            raise
        except Exception as exc:
            del exc
            raise StageContractError(
                "the reconciliation refused this run's input", stage=stage.value
            ) from None
        completed.append(StageState(Stage.RECONCILE))
        recorder.emit(EVENT_STAGE_COMPLETED,
                      {"stage": stage.value, "group_count": result.summary.group_count})

        # 6. REPORT ----------------------------------------------------------
        stage = Stage.REPORT
        rpolicy = report_policy(config.report)
        try:
            report = build_report(result, rpolicy)
            artifacts = render_artifacts(result, rpolicy)
        except Exception as exc:
            del exc
            raise StageContractError(
                "the report could not be built from this reconciliation",
                stage=stage.value,
            ) from None
        completed.append(StageState(Stage.REPORT))
        recorder.emit(EVENT_STAGE_COMPLETED,
                      {"stage": stage.value, "artifact_count": len(artifacts)})

        # 7. VERIFY ----------------------------------------------------------
        stage = Stage.VERIFY
        _verify(request, len(extracted), system_rows, batches, identifiers,
                result, report, artifacts)
        # The simulator is untouched by a report-only run, and says so.
        if store.snapshot() != before:
            raise VerificationError(
                "the simulated system changed during a report-only run",
                stage=stage.value,
            )
        completed.append(StageState(Stage.VERIFY))
        recorder.emit(EVENT_STAGE_COMPLETED,
                      {"stage": stage.value, "artifact_count": len(artifacts)})

        # publication --------------------------------------------------------
        written = publish(request.output_dir, artifacts)
        recorder.emit(EVENT_ARTIFACTS_COMMITTED, {"artifact_count": len(written)})

        summary = RunSummary(
            feed_rows=feed_rows,
            system_rows=len(system_rows),
            validation_invalid=invalid,
            matching_counts=matching_counts,
            reconciliation_groups=result.summary.group_count,
            reconciliation_counts=dict(result.summary.groups_by_outcome),
            reconciliation_unresolved=result.summary.unresolved_count,
        )
        recorder.emit(
            EVENT_COMPLETED,
            {
                "status": RunStatus.SUCCESS.value,
                "feed_rows": feed_rows,
                "system_rows": len(system_rows),
                "group_count": result.summary.group_count,
                "unresolved_count": result.summary.unresolved_count,
                "artifact_count": len(written),
            },
        )
        return RunResult(
            status=RunStatus.SUCCESS,
            stages=tuple(completed),
            summary=summary,
            artifacts=written,
            write_back_mode=WriteBackMode.DISABLED,
            write_back_attempted=0,
            write_back_applied=0,
        )
    except PipelineError as error:
        recorder.emit(EVENT_FAILED,
                      {"stage": stage.value, "error_code": error.error_code})
        raise


def stage_order() -> tuple[Stage, ...]:
    """The seven stages, in the order this engine runs them."""
    return STAGE_ORDER


def all_stages_completed(states: Sequence[StageState]) -> bool:
    return [state.stage for state in states] == list(STAGE_ORDER) and all(
        state.status is StageStatus.COMPLETED for state in states
    )
