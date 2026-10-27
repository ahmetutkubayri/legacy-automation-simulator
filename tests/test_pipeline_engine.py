"""T14 engine: stage order, the VERIFY gate, failure atomicity, determinism."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from las.audit import AuditTrail
from las.pipeline import (
    STAGE_ORDER,
    ConfiguredInputError,
    DuplicateFeedError,
    ExtractionError,
    LedgerAdapterError,
    RunStatus,
    SimulatorInitializationError,
    Stage,
    StageContractError,
    StageStatus,
    VerificationError,
    WriteBackMode,
    run_pipeline,
    stage_order,
)
from las.pipeline.engine import (
    EVENT_ARTIFACTS_COMMITTED,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_STAGE_COMPLETED,
    EVENT_STARTED,
)
from pipeline_helpers import make_request, staging_left


def nothing_left(root: Path, output: Path) -> bool:
    return not output.exists() and not staging_left(root)


# --- the happy path ------------------------------------------------------------------


def test_a_run_completes_every_stage_in_order(tmp_path: Path) -> None:
    result = run_pipeline(make_request(tmp_path / "project"))
    assert result.status is RunStatus.SUCCESS
    assert [state.stage for state in result.stages] == list(STAGE_ORDER)
    assert all(state.status is StageStatus.COMPLETED for state in result.stages)
    assert stage_order() == STAGE_ORDER


def test_a_run_publishes_the_three_authorized_filenames(tmp_path: Path) -> None:
    root = tmp_path / "project"
    result = run_pipeline(make_request(root))
    assert sorted(item.filename for item in result.artifacts) == [
        "reconciliation-report.xlsx",
        "reconciliation-summary.html",
        "reconciliation-summary.json",
    ]
    assert sorted(p.name for p in (root / "report").iterdir()) == sorted(
        item.filename for item in result.artifacts
    )


def test_write_back_is_disabled_with_zero_updates(tmp_path: Path) -> None:
    result = run_pipeline(make_request(tmp_path / "project"))
    assert result.write_back_mode is WriteBackMode.DISABLED
    assert result.write_back_attempted == 0
    assert result.write_back_applied == 0


def test_the_simulator_is_never_mutated(tmp_path: Path) -> None:
    from las.simulator import RecordStore

    root = tmp_path / "project"
    request = make_request(root)
    before = RecordStore.from_fixtures(root).snapshot()
    run_pipeline(request)
    after = RecordStore.from_fixtures(root).snapshot()
    assert after == before


def test_no_temporary_directory_survives(tmp_path: Path) -> None:
    run_pipeline(make_request(tmp_path / "project"))
    assert not staging_left(tmp_path)


# --- failure atomicity -----------------------------------------------------------------


def broken(monkeypatch, target: str, error: Exception) -> None:
    from las.pipeline import engine

    def explode(*args, **kwargs):
        raise error

    monkeypatch.setattr(engine, target, explode)


@pytest.mark.parametrize(
    ("target", "error", "expected"),
    [
        ("RecordStore", RuntimeError("no store"), SimulatorInitializationError),
        ("extract_query_results", RuntimeError("no rows"), ExtractionError),
        ("build_master_index", RuntimeError("bad master"), StageContractError),
        ("adapt_ledger", LedgerAdapterError("unmapped"), LedgerAdapterError),
        ("reconcile", RuntimeError("refused"), StageContractError),
        ("build_report", RuntimeError("no report"), StageContractError),
        ("render_artifacts", RuntimeError("no bytes"), StageContractError),
    ],
)
def test_a_failing_stage_publishes_nothing(
    tmp_path: Path, monkeypatch, target: str, error: Exception, expected: type
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    broken(monkeypatch, target, error)
    with pytest.raises(expected):
        run_pipeline(request)
    assert nothing_left(tmp_path, root / "report")


def test_a_missing_feed_fails_before_any_output(tmp_path: Path) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    (root / "feed_beta.csv").unlink()
    with pytest.raises(ConfiguredInputError):
        run_pipeline(request)
    assert nothing_left(tmp_path, root / "report")


def test_a_missing_master_file_fails_before_any_output(tmp_path: Path) -> None:
    """EXTRACT reads the same master data the store is built from, so it is
    the stage that notices first — which is exactly the right place."""
    root = tmp_path / "project"
    request = make_request(root)
    (root / "accounts.csv").unlink()
    with pytest.raises(SimulatorInitializationError):
        run_pipeline(request)
    assert nothing_left(tmp_path, root / "report")


def test_a_duplicate_configured_feed_fails_before_aggregation(tmp_path: Path) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    original = request.config.feeds[0]
    config = request.config.model_copy(
        update={"feeds": [original, original.model_copy(update={"name": "feed_twin"})]}
    )
    with pytest.raises(DuplicateFeedError):
        run_pipeline(dataclasses.replace(request, config=config))
    assert nothing_left(tmp_path, root / "report")


def test_an_incomplete_extraction_is_refused(tmp_path: Path, monkeypatch) -> None:
    from las.driver import ExtractionResult
    from las.pipeline import engine

    root = tmp_path / "project"
    request = make_request(root)
    real = engine.extract_query_results

    def partial(driver, *args, **kwargs):
        result = real(driver, *args, **kwargs)
        return dataclasses.replace(result, complete=False)

    monkeypatch.setattr(engine, "extract_query_results", partial)
    assert ExtractionResult is not None
    with pytest.raises(ExtractionError, match="complete"):
        run_pipeline(request)
    assert nothing_left(tmp_path, root / "report")


# --- the VERIFY gate ---------------------------------------------------------------------


def test_verify_refuses_a_short_adapted_ledger(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import engine

    root = tmp_path / "project"
    request = make_request(root)
    real = engine.adapt_ledger
    monkeypatch.setattr(engine, "adapt_ledger", lambda rows, index: real(rows, index)[:-1])
    with pytest.raises(VerificationError, match="row for row"):
        run_pipeline(request)
    assert nothing_left(tmp_path, root / "report")


def test_verify_refuses_a_short_artifact_set(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import engine

    root = tmp_path / "project"
    request = make_request(root)
    real = engine.render_artifacts
    monkeypatch.setattr(
        engine, "render_artifacts", lambda result, policy: real(result, policy)[:2]
    )
    with pytest.raises(VerificationError, match="configured formats"):
        run_pipeline(request)
    assert nothing_left(tmp_path, root / "report")


def test_verify_refuses_artifacts_out_of_configured_order(
    tmp_path: Path, monkeypatch
) -> None:
    from las.pipeline import engine

    root = tmp_path / "project"
    request = make_request(root)
    real = engine.render_artifacts
    monkeypatch.setattr(
        engine, "render_artifacts",
        lambda result, policy: tuple(reversed(real(result, policy))),
    )
    with pytest.raises(VerificationError, match="configured format order"):
        run_pipeline(request)
    assert nothing_left(tmp_path, root / "report")


def test_verify_runs_before_any_directory_exists(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import engine

    root = tmp_path / "project"
    request = make_request(root)
    seen: list[bool] = []
    real = engine._verify

    def watch(*args, **kwargs):
        seen.append((root / "report").exists())
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "_verify", watch)
    run_pipeline(request)
    assert seen == [False]


# --- audit -------------------------------------------------------------------------------


def read_events(root: Path) -> list[dict]:
    import json

    lines = []
    for path in root.rglob("audit.jsonl"):
        lines.extend(json.loads(line) for line in path.read_text().splitlines())
    return lines


def test_a_successful_run_emits_the_documented_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    with AuditTrail(output_root="operational") as trail:
        run_pipeline(request, trail=trail)
    events = [e["event"] for e in read_events(audit_root) if e["stage"] == "pipeline"]
    assert events == [
        EVENT_STARTED,
        *[EVENT_STAGE_COMPLETED] * len(STAGE_ORDER),
        EVENT_ARTIFACTS_COMMITTED,
        EVENT_COMPLETED,
    ]


def test_stage_events_arrive_in_stage_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    with AuditTrail(output_root="operational") as trail:
        run_pipeline(request, trail=trail)
    stages = [
        e["payload"]["stage"]
        for e in read_events(audit_root)
        if e["event"] == EVENT_STAGE_COMPLETED
    ]
    assert stages == [stage.value for stage in STAGE_ORDER]


def test_only_approved_payload_keys_are_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = {
        "command", "seed", "stage", "count", "artifact_count", "feed_rows",
        "system_rows", "group_count", "unresolved_count", "error_code", "status",
    }
    root = tmp_path / "project"
    request = make_request(root)
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    with AuditTrail(output_root="operational") as trail:
        run_pipeline(request, trail=trail)
    for event in read_events(audit_root):
        if event["stage"] == "pipeline":
            assert set(event["payload"]) <= approved, event["payload"]


def test_a_failure_emits_pipeline_failed_and_no_completion(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    broken(monkeypatch, "extract_query_results", RuntimeError("no rows"))
    with AuditTrail(output_root="operational") as trail, pytest.raises(ExtractionError):
        run_pipeline(request, trail=trail)
    events = [e for e in read_events(audit_root) if e["stage"] == "pipeline"]
    names = [e["event"] for e in events]
    assert names.count(EVENT_FAILED) == 1
    assert EVENT_COMPLETED not in names
    failure = next(e for e in events if e["event"] == EVENT_FAILED)
    assert failure["payload"] == {"stage": Stage.EXTRACT.value,
                                  "error_code": "extraction_error"}


@pytest.mark.parametrize(
    ("target", "stage"),
    [
        ("validation_policy", Stage.VALIDATE),
        ("validate_ingest_result", Stage.VALIDATE),
        ("match_policy", Stage.MATCH),
        ("match_validated", Stage.MATCH),
    ],
)
def test_validation_and_matching_failures_are_safe_and_audited(
    tmp_path: Path, monkeypatch, target: str, stage: Stage
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    broken(monkeypatch, target, RuntimeError("unsafe lower-layer detail"))
    with AuditTrail(output_root="operational") as trail, pytest.raises(StageContractError):
        run_pipeline(request, trail=trail)
    events = [e for e in read_events(audit_root) if e["stage"] == "pipeline"]
    failures = [e for e in events if e["event"] == EVENT_FAILED]
    assert len(failures) == 1
    assert failures[0]["payload"] == {"stage": stage.value, "error_code": "stage_contract_error"}
    assert not any(e["event"] in {EVENT_COMPLETED, EVENT_ARTIFACTS_COMMITTED} for e in events)
    assert "unsafe lower-layer detail" not in str(events)
    assert nothing_left(tmp_path, root / "report")


def test_simulator_change_fails_verify_before_publication(tmp_path: Path, monkeypatch) -> None:
    from las.simulator import RecordStore

    root = tmp_path / "project"
    request = make_request(root)
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    snapshots = iter([object(), object()])
    monkeypatch.setattr(RecordStore, "snapshot", lambda self: next(snapshots))
    with AuditTrail(output_root="operational") as trail, pytest.raises(VerificationError):
        run_pipeline(request, trail=trail)
    events = [e for e in read_events(audit_root) if e["stage"] == "pipeline"]
    assert events[-1]["event"] == EVENT_FAILED
    assert events[-1]["payload"] == {"stage": Stage.VERIFY.value,
                                     "error_code": "verification_error"}
    assert not any(e["event"] in {EVENT_COMPLETED, EVENT_ARTIFACTS_COMMITTED} for e in events)
    assert nothing_left(tmp_path, root / "report")


@pytest.mark.parametrize("target", ["validate_ingest_result", "match_validated"])
def test_validation_and_matching_preserve_existing_pipeline_errors(
    tmp_path: Path, monkeypatch, target: str
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    error = ConfiguredInputError("already classified")
    broken(monkeypatch, target, error)
    with pytest.raises(ConfiguredInputError) as excinfo:
        run_pipeline(request)
    assert excinfo.value is error
    assert nothing_left(tmp_path, root / "report")


@pytest.mark.parametrize(
    "aliases",
    [{"Amount": "unit"}, {"---": "amount"}, {"Quantity": "amount", "quantity": "unit"}],
)
def test_invalid_alias_schema_is_audited_as_configured_input(
    tmp_path: Path, monkeypatch, aliases: dict
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    request.config.feeds[0].header_aliases = aliases
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    with AuditTrail(output_root="operational") as trail, pytest.raises(ConfiguredInputError):
        run_pipeline(request, trail=trail)
    events = [e for e in read_events(audit_root) if e["stage"] == "pipeline"]
    failures = [e for e in events if e["event"] == EVENT_FAILED]
    assert len(failures) == 1
    assert failures[0]["payload"] == {"stage": Stage.INGEST.value,
                                      "error_code": "configured_input_error"}
    assert not any(e["event"] in {EVENT_COMPLETED, EVENT_ARTIFACTS_COMMITTED} for e in events)
    assert nothing_left(tmp_path, root / "report")


def test_no_audit_payload_carries_a_path_or_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    request = make_request(root)
    monkeypatch.chdir(tmp_path)
    audit_root = tmp_path / "operational"
    with AuditTrail(output_root="operational") as trail:
        run_pipeline(request, trail=trail)
    blob = "".join(str(e["payload"]) for e in read_events(audit_root))
    for forbidden in (str(tmp_path), "feed_alpha.xlsx", "ACC-", "/", "\\", ".las-"):
        assert forbidden not in blob, forbidden


# --- determinism -----------------------------------------------------------------------------


def test_two_equivalent_runs_give_equal_results(tmp_path: Path) -> None:
    one = run_pipeline(make_request(tmp_path / "one"))
    two = run_pipeline(make_request(tmp_path / "two"))
    assert one == two


def test_feed_configuration_order_changes_nothing(tmp_path: Path) -> None:
    forward = run_pipeline(make_request(tmp_path / "forward"))
    request = make_request(tmp_path / "reversed")
    reordered = request.config.model_copy(
        update={"feeds": list(reversed(request.config.feeds))}
    )
    backward = run_pipeline(dataclasses.replace(request, config=reordered))
    assert forward.summary == backward.summary
    assert [a.sha256 for a in forward.artifacts] == [a.sha256 for a in backward.artifacts]


def test_a_second_run_into_the_same_directory_conflicts(tmp_path: Path) -> None:
    from las.pipeline import OutputConflictError

    root = tmp_path / "project"
    request = make_request(root)
    first = run_pipeline(request)
    digests = {a.filename: a.sha256 for a in first.artifacts}
    with pytest.raises(OutputConflictError):
        run_pipeline(request)
    import hashlib

    for name, digest in digests.items():
        assert hashlib.sha256((root / "report" / name).read_bytes()).hexdigest() == digest
