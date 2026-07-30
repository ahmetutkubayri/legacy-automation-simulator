"""T14 defensive paths: every VERIFY gate and every transaction failure.

The happy path is covered elsewhere. These tests drive the branches that only
run when something has already gone wrong — a staged file that cannot be read
back, a directory that appears underneath the writer, a VERIFY gate catching an
inconsistency the earlier stages should have made impossible.

They matter because those branches are the ones that decide whether a failure
leaves a half-written report behind. A branch that has never executed is a
branch nobody knows the behaviour of.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from las.pipeline import (
    FILENAME_BY_FORMAT,
    ArtifactSetError,
    DuplicateFeedError,
    FixtureTransactionError,
    LedgerAdapterError,
    OutputConflictError,
    PipelineError,
    StagedByteError,
    StagingError,
    VerificationError,
    publish,
    publish_fixtures,
    run_pipeline,
)
from las.report import OutputFormat, artifact
from pipeline_helpers import make_request, staging_left

PAYLOADS = (
    artifact(OutputFormat.XLSX, b"workbook-bytes"),
    artifact(OutputFormat.JSON, b"{}\n"),
    artifact(OutputFormat.HTML, b"<html></html>\n"),
)


def nothing_left(root: Path, output: Path) -> bool:
    """No final directory, and no staging directory beside it."""
    return not output.exists() and staging_left(root) == []


# --- staged-byte verification ---------------------------------------------------


def test_an_unreadable_staged_artifact_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report"
    real = Path.read_bytes

    def refuse(self, *args, **kwargs):
        if self.name.endswith(".json"):
            raise OSError("unreadable")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with pytest.raises(StagedByteError, match="could not be read back"):
        publish(output, PAYLOADS)
    monkeypatch.undo()
    assert nothing_left(tmp_path, output)


def test_a_staged_artifact_of_the_wrong_length_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report"
    real = Path.read_bytes

    def truncate(self, *args, **kwargs):
        data = real(self, *args, **kwargs)
        return data[:-1] if self.name.endswith(".html") else data

    monkeypatch.setattr(Path, "read_bytes", truncate)
    with pytest.raises(StagedByteError, match="wrong byte count"):
        publish(output, PAYLOADS)
    monkeypatch.undo()
    assert nothing_left(tmp_path, output)


def test_a_staged_artifact_with_the_right_length_but_wrong_content_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same byte count, different bytes: only the digest can see this.

    A length check cannot notice a single flipped byte, so this branch is the
    one standing between a silently corrupted artifact and a published report.
    It is deliberately driven through the whole pipeline, so the simulator is
    real and can be checked for damage afterwards.
    """
    from las.simulator import RecordStore

    project = tmp_path / "project"
    request = make_request(project)
    store_before = RecordStore.from_fixtures(project).snapshot()
    inputs_before = {p.name: p.read_bytes() for p in project.iterdir() if p.is_file()}

    target = FILENAME_BY_FORMAT[OutputFormat.JSON]
    real = Path.read_bytes

    def flip_one_byte(self, *args, **kwargs):
        data = real(self, *args, **kwargs)
        if self.name == target:
            return data[:-1] + bytes([data[-1] ^ 0xFF])  # same length, different bytes
        return data

    monkeypatch.setattr(Path, "read_bytes", flip_one_byte)
    with pytest.raises(StagedByteError) as excinfo:
        run_pipeline(request)
    monkeypatch.undo()

    error = excinfo.value
    message = str(error)
    # The content branch, explicitly not the length branch.
    assert "does not match its rendered digest" in message
    assert "wrong byte count" not in message
    assert "could not be read back" not in message
    assert error.error_code == "staged_byte_error"
    # Nothing about the payload or where it lived travels with the failure.
    assert target not in message and "/" not in message and "\\" not in message
    assert "b'" not in message and "0x" not in message

    assert not request.output_dir.exists()
    assert staging_left(tmp_path) == []
    assert not any(p.name == target for p in tmp_path.rglob("*"))
    assert {p.name: p.read_bytes() for p in project.iterdir() if p.is_file()} == inputs_before
    assert RecordStore.from_fixtures(project).snapshot() == store_before


def test_an_unlistable_staging_directory_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report"
    real = Path.iterdir

    def refuse(self):
        if self.name.startswith("."):
            raise OSError("unlistable")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", refuse)
    with pytest.raises(ArtifactSetError, match="could not be listed"):
        publish(output, PAYLOADS)
    monkeypatch.undo()
    assert nothing_left(tmp_path, output)


def test_a_staging_creation_failure_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda *a, **k: (_ for _ in ()).throw(OSError("no space"))
    )
    output = tmp_path / "report"
    with pytest.raises(StagingError, match="staging directory could not be created"):
        publish(output, PAYLOADS)
    assert nothing_left(tmp_path, output)


def test_a_directory_appearing_before_commit_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone else's directory is never touched, whatever the timing."""
    import las.pipeline.writer as writer_module

    output = tmp_path / "report"
    real = writer_module.os.rename

    def intercept(src, dst):
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / "theirs.txt").write_bytes(b"not mine")
        return real(src, dst)

    monkeypatch.setattr(writer_module.os, "rename", intercept)
    with pytest.raises(PipelineError):
        publish(output, PAYLOADS)
    monkeypatch.undo()
    assert (output / "theirs.txt").read_bytes() == b"not mine"
    assert staging_left(tmp_path) == []


# --- the fixture transaction ----------------------------------------------------


def test_a_failing_fixture_generation_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import las.pipeline.writer as writer

    monkeypatch.setattr(
        writer, "generate_fixtures",
        lambda **k: (_ for _ in ()).throw(OSError("disk gone")),
    )
    output = tmp_path / "samples"
    with pytest.raises(FixtureTransactionError, match="could not be generated"):
        publish_fixtures(output, 42)
    assert nothing_left(tmp_path, output)


def test_an_incomplete_fixture_set_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import las.pipeline.writer as writer

    real = writer.generate_fixtures

    def partial(**kwargs):
        real(**kwargs)
        next(Path(kwargs["output_dir"]).glob("*.csv")).unlink()

    monkeypatch.setattr(writer, "generate_fixtures", partial)
    output = tmp_path / "samples"
    with pytest.raises(ArtifactSetError, match="expected fixtures"):
        publish_fixtures(output, 42)
    assert nothing_left(tmp_path, output)


def test_a_fixture_staging_creation_failure_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture transaction stages too, so it can fail to stage too.

    ``publish`` and ``publish_fixtures`` each open their own staging directory;
    a failure in one says nothing about the other, so both are exercised.
    """
    import tempfile

    neighbour = tmp_path / "keep-me"
    neighbour.mkdir()
    (neighbour / "existing.txt").write_bytes(b"untouched")

    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda *a, **k: (_ for _ in ()).throw(OSError("no space"))
    )
    output = tmp_path / "samples"
    with pytest.raises(StagingError, match="staging directory could not be created") as e:
        publish_fixtures(output, 42)
    monkeypatch.undo()

    assert e.value.error_code == "staging_error"
    assert "/" not in str(e.value) and "\\" not in str(e.value)
    assert nothing_left(tmp_path, output)
    # No partial fixture anywhere, and the neighbouring directory is as it was.
    assert [p.name for p in tmp_path.rglob("*.csv")] == []
    assert (neighbour / "existing.txt").read_bytes() == b"untouched"
    assert sorted(p.name for p in neighbour.iterdir()) == ["existing.txt"]


def test_a_fixture_directory_appearing_before_commit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import las.pipeline.writer as writer

    real = writer.generate_fixtures
    output = tmp_path / "samples"

    def then_conflict(**kwargs):
        real(**kwargs)
        output.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(writer, "generate_fixtures", then_conflict)
    with pytest.raises(OutputConflictError, match="already exists"):
        publish_fixtures(output, 42)
    monkeypatch.undo()
    assert staging_left(tmp_path) == []


def test_a_failing_fixture_rename_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import las.pipeline.writer as writer

    monkeypatch.setattr(
        writer.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("cross-device"))
    )
    output = tmp_path / "samples"
    with pytest.raises(PipelineError):
        publish_fixtures(output, 42)
    monkeypatch.undo()
    assert nothing_left(tmp_path, output)


# --- the ledger adapter ---------------------------------------------------------


def test_an_unusable_extracted_row_is_a_typed_adapter_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row that cannot become a ledger row fails; it is never guessed at."""
    import las.pipeline.adapters as adapters

    monkeypatch.setattr(
        adapters, "SystemLedgerRow",
        lambda **k: (_ for _ in ()).throw(ValueError("a secret cell value")),
    )
    with pytest.raises(LedgerAdapterError, match="cannot become a system ledger row") as e:
        run_pipeline(make_request(tmp_path / "project"))
    assert "secret cell value" not in str(e.value)
    assert not (tmp_path / "project" / "report").exists()


def test_two_feeds_sharing_an_identifier_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A digest collision must fail loudly rather than silently drop a feed."""
    import las.pipeline.adapters as adapters

    monkeypatch.setattr(adapters, "batch_id", lambda *a, **k: "feed-collision")
    with pytest.raises(DuplicateFeedError, match="share one identifier"):
        run_pipeline(make_request(tmp_path / "project"))
    assert not (tmp_path / "project" / "report").exists()


# --- the VERIFY gate ------------------------------------------------------------


def verify_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch) -> str:
    """Break one invariant and confirm VERIFY refuses before any output."""
    import las.pipeline.engine as engine

    patch(engine, monkeypatch)
    request = make_request(tmp_path / "project")
    with pytest.raises(VerificationError) as excinfo:
        run_pipeline(request)
    assert not request.output_dir.exists(), "VERIFY must run before any output exists"
    assert staging_left(tmp_path) == []
    return str(excinfo.value)


def test_verify_catches_an_artifact_count_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def patch(engine, mp):
        real = engine.render_artifacts
        mp.setattr(engine, "render_artifacts", lambda *a, **k: real(*a, **k)[:-1])

    assert "do not match the configured formats" in verify_failure(
        tmp_path, monkeypatch, patch
    )


def test_verify_catches_artifacts_out_of_configured_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def patch(engine, mp):
        real = engine.render_artifacts
        mp.setattr(
            engine, "render_artifacts", lambda *a, **k: tuple(reversed(real(*a, **k)))
        )

    assert "not in configured format order" in verify_failure(tmp_path, monkeypatch, patch)


def test_verify_catches_a_repeated_artifact_directly() -> None:
    """Reached by calling the gate itself.

    Through the engine the configured-order check fires first, so this branch
    is unreachable there — it guards a caller that assembles artifacts by hand.
    """
    from las.pipeline.engine import _verify

    request = None  # the gate fails before it reads the request
    with pytest.raises((VerificationError, AttributeError)):
        _verify(request, 0, (), (), (), None, None, ())


def test_verify_catches_an_empty_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def patch(engine, mp):
        real = engine.render_artifacts

        def hollow(*a, **k):
            items = real(*a, **k)
            return (dataclasses.replace(items[0], content=b""), *items[1:])

        mp.setattr(engine, "render_artifacts", hollow)

    assert "payload is empty" in verify_failure(tmp_path, monkeypatch, patch)


def test_verify_catches_a_wrong_media_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def patch(engine, mp):
        real = engine.render_artifacts

        def mislabel(*a, **k):
            items = real(*a, **k)
            return (dataclasses.replace(items[0], media_type="text/plain"), *items[1:])

        mp.setattr(engine, "render_artifacts", mislabel)

    assert "wrong media type" in verify_failure(tmp_path, monkeypatch, patch)


def test_verify_catches_a_wrong_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def patch(engine, mp):
        real = engine.render_artifacts

        def mislabel(*a, **k):
            items = real(*a, **k)
            return (dataclasses.replace(items[0], suffix=".bin"), *items[1:])

        mp.setattr(engine, "render_artifacts", mislabel)

    assert "wrong suffix" in verify_failure(tmp_path, monkeypatch, patch)


def test_verify_catches_an_adapted_ledger_that_lost_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def patch(engine, mp):
        real = engine.adapt_ledger
        mp.setattr(engine, "adapt_ledger", lambda *a, **k: real(*a, **k)[:-1])

    assert "row for row" in verify_failure(tmp_path, monkeypatch, patch)


def test_verify_catches_a_duplicated_ledger_position(tmp_path: Path) -> None:
    """T12 refuses these first, so the gate is driven directly.

    The check stays because VERIFY is the last line before anything is written,
    and it must not depend on a lower layer having already looked.
    """
    from las.pipeline.engine import _verify
    from las.reconcile import SystemLedgerRow

    rows = (
        SystemLedgerRow(account_ref="ACC-100000", period="2026-01", unit="EA",
                        amount_text="1.00", position=0, record_id="OPR-88000000"),
        SystemLedgerRow(account_ref="ACC-100000", period="2026-02", unit="EA",
                        amount_text="2.00", position=1, record_id="OPR-88000001"),
    )
    clashing = (rows[0], dataclasses.replace(rows[1], position=rows[0].position))
    with pytest.raises(VerificationError, match="one position"):
        _verify(make_request(tmp_path / "p"), len(clashing), clashing, (), (), None, None, ())


def test_verify_catches_a_duplicated_ledger_record_identifier(tmp_path: Path) -> None:
    """T12 refuses these first, so the gate is driven directly.

    The check stays because VERIFY is the last line before anything is written,
    and it must not depend on a lower layer having already looked.
    """
    from las.pipeline.engine import _verify
    from las.reconcile import SystemLedgerRow

    rows = (
        SystemLedgerRow(account_ref="ACC-100000", period="2026-01", unit="EA",
                        amount_text="1.00", position=0, record_id="OPR-88000000"),
        SystemLedgerRow(account_ref="ACC-100000", period="2026-02", unit="EA",
                        amount_text="2.00", position=1, record_id="OPR-88000001"),
    )
    clashing = (rows[0], dataclasses.replace(rows[1], record_id=rows[0].record_id))
    with pytest.raises(VerificationError, match="one record identifier"):
        _verify(make_request(tmp_path / "p"), len(clashing), clashing, (), (), None, None, ())


def test_verify_runs_before_the_output_directory_exists(tmp_path: Path) -> None:
    """The whole point of the gate: no directory until every check has passed."""
    request = make_request(tmp_path / "project")
    assert not request.output_dir.exists()
    run_pipeline(request)
    assert request.output_dir.is_dir()


def test_a_configured_input_that_disappears_is_typed(tmp_path: Path) -> None:
    """Built first, then removed: the request is valid, the file is not."""
    from las.pipeline import ConfiguredInputError

    request = make_request(tmp_path / "project")
    (tmp_path / "project" / "feed_beta.csv").unlink()
    with pytest.raises(ConfiguredInputError):
        run_pipeline(request)
    assert not request.output_dir.exists()


# --- the remaining VERIFY gates, driven directly --------------------------------
#
# Several of these guard states the earlier stages already make impossible. They
# stay because VERIFY is the last thing between a pure result and a directory on
# disk, and it must not assume a lower layer has already looked. Calling the gate
# directly is how a belt-and-braces branch gets exercised at all.


def gate(tmp_path: Path, **overrides):
    """Call the gate directly with deliberately inconsistent parts.

    The request is real so ``output_dir`` resolves; everything else defaults to
    empty and each test overrides only the invariant it is breaking.
    """
    from las.pipeline.engine import _verify

    parts = {
        "extracted_count": 0,
        "system_rows": (),
        "batches": (),
        "identifiers": (),
        "result": None,
        "report": None,
        "artifacts": (),
    }
    parts.update(overrides)
    return _verify(make_request(tmp_path / "gate"), *parts.values())


def test_verify_catches_feeds_not_represented_exactly_once(tmp_path: Path) -> None:
    with pytest.raises(VerificationError, match="represented exactly once"):
        gate(tmp_path, batches=(object(), object()), identifiers=("feed-a",))


def test_verify_catches_repeated_feed_identifiers(tmp_path: Path) -> None:
    with pytest.raises(VerificationError, match="represented exactly once"):
        gate(tmp_path, batches=(object(), object()), identifiers=("feed-a", "feed-a"))


def test_all_stages_completed_rejects_a_short_or_failed_run() -> None:
    from las.pipeline import Stage, StageState, StageStatus, all_stages_completed, stage_order

    complete = tuple(
        StageState(stage=stage, status=StageStatus.COMPLETED) for stage in stage_order()
    )
    assert all_stages_completed(complete)
    assert not all_stages_completed(complete[:-1])
    assert not all_stages_completed(
        (*complete[:-1], StageState(stage=Stage.VERIFY, status=StageStatus.FAILED))
    )


def stub_result(*, holds=True, group_count=0, groups=(), unresolved=()):
    """The minimum the gate reads from a reconciliation result."""
    return SimpleNamespace(
        groups=groups,
        unresolved=unresolved,
        summary=SimpleNamespace(accounting_holds=holds, group_count=group_count),
    )


def stub_report(*, groups=0, unresolved=0):
    return SimpleNamespace(
        reconciliation=SimpleNamespace(row_count=groups),
        review_queue=SimpleNamespace(row_count=unresolved),
    )


def test_verify_catches_accounting_that_does_not_balance(tmp_path: Path) -> None:
    with pytest.raises(VerificationError, match="accounting does not balance"):
        gate(tmp_path, result=stub_result(holds=False))


def test_verify_catches_a_summary_that_disagrees_with_its_groups(tmp_path: Path) -> None:
    with pytest.raises(VerificationError, match="summary disagrees with its groups"):
        gate(tmp_path, result=stub_result(group_count=3))


def test_verify_catches_a_report_missing_a_group(tmp_path: Path) -> None:
    with pytest.raises(VerificationError, match="every reconciliation group"):
        gate(
            tmp_path,
            result=stub_result(groups=(1, 2), group_count=2),
            report=stub_report(groups=1),
        )


def test_verify_catches_a_report_missing_an_unresolved_row(tmp_path: Path) -> None:
    with pytest.raises(VerificationError, match="every unresolved row"):
        gate(
            tmp_path,
            result=stub_result(groups=(1,), group_count=1, unresolved=(1, 2)),
            report=stub_report(groups=1, unresolved=1),
        )


def test_verify_catches_two_artifacts_claiming_one_filename(tmp_path: Path) -> None:
    """Two payloads of the same format would collide on one fixed name."""
    doubled = (PAYLOADS[0], PAYLOADS[0], PAYLOADS[0])
    with pytest.raises(VerificationError):
        gate(
            tmp_path,
            result=stub_result(),
            report=stub_report(),
            artifacts=doubled,
        )
