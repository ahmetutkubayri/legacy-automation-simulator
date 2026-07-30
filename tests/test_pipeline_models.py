"""T14 models: filenames, stage order, and deep immutability."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from las.pipeline import (
    FILENAME_BY_FORMAT,
    STAGE_ORDER,
    ArtifactNameError,
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
from las.report import MEDIA_TYPE_BY_FORMAT, OutputFormat

DIGEST = "a" * 64


def artifact(fmt: OutputFormat = OutputFormat.JSON, **kwargs) -> WrittenArtifact:
    base = {
        "output_format": fmt,
        "filename": FILENAME_BY_FORMAT[fmt],
        "media_type": MEDIA_TYPE_BY_FORMAT[fmt],
        "byte_count": 10,
        "sha256": DIGEST,
    }
    return WrittenArtifact(**{**base, **kwargs})


def summary(**kwargs) -> RunSummary:
    base = {
        "feed_rows": 1, "system_rows": 1, "validation_invalid": 0,
        "matching_counts": {"exact": 1}, "reconciliation_groups": 1,
        "reconciliation_counts": {"balanced": 1}, "reconciliation_unresolved": 0,
    }
    return RunSummary(**{**base, **kwargs})


# --- filenames ------------------------------------------------------------------


def test_the_three_filenames_are_the_documented_constants() -> None:
    assert FILENAME_BY_FORMAT[OutputFormat.XLSX] == "reconciliation-report.xlsx"
    assert FILENAME_BY_FORMAT[OutputFormat.JSON] == "reconciliation-summary.json"
    assert FILENAME_BY_FORMAT[OutputFormat.HTML] == "reconciliation-summary.html"


def test_every_format_has_exactly_one_filename() -> None:
    assert set(FILENAME_BY_FORMAT) == set(OutputFormat)
    assert len(set(FILENAME_BY_FORMAT.values())) == len(OutputFormat)


def test_filenames_are_plain_child_names() -> None:
    for name in FILENAME_BY_FORMAT.values():
        assert Path(name).name == name
        assert not Path(name).is_absolute()
        assert ".." not in Path(name).parts
        assert "/" not in name and "\\" not in name


def test_filenames_carry_no_time_seed_or_identity() -> None:
    from datetime import UTC, datetime

    year = str(datetime.now(UTC).year)
    for name in FILENAME_BY_FORMAT.values():
        assert year not in name
        assert not any(character.isdigit() for character in name)


def test_filenames_are_windows_and_unix_safe() -> None:
    for name in FILENAME_BY_FORMAT.values():
        assert not any(c in name for c in '<>:"/\\|?*')
        assert name == name.strip() and not name.endswith(".")
        assert Path(name).stem.upper() not in {
            "CON", "PRN", "AUX", "NUL", "COM1", "LPT1"
        }


def test_the_filename_map_is_read_only() -> None:
    assert isinstance(FILENAME_BY_FORMAT, MappingProxyType)
    with pytest.raises(TypeError):
        FILENAME_BY_FORMAT[OutputFormat.JSON] = "x"  # type: ignore[index]


def test_an_unknown_format_has_no_authorized_filename() -> None:
    with pytest.raises(ArtifactNameError, match="no authorized filename"):
        filename_for("pdf")  # type: ignore[arg-type]


# --- stages ----------------------------------------------------------------------


def test_the_seven_stages_in_documented_order() -> None:
    assert [stage.value for stage in STAGE_ORDER] == [
        "extract", "ingest", "validate", "match", "reconcile", "report", "verify"
    ]
    assert list(Stage) == list(STAGE_ORDER)
    assert len(STAGE_ORDER) == 7


def test_configuration_and_publication_are_not_stages() -> None:
    names = {stage.value for stage in Stage}
    for mechanic in ("config", "publish", "write", "audit", "log", "summary"):
        assert mechanic not in names


# --- artifacts -------------------------------------------------------------------


def test_an_artifact_must_use_its_authorized_filename() -> None:
    with pytest.raises(ArtifactNameError, match="does not authorize"):
        artifact(filename="whatever.json")


def test_an_artifact_filename_may_not_contain_a_separator() -> None:
    with pytest.raises(ArtifactNameError):
        artifact(filename="nested/reconciliation-summary.json")


@pytest.mark.parametrize(("count", "digest"), [(-1, DIGEST), (10, "short")])
def test_an_artifact_needs_a_count_and_a_full_digest(count: int, digest: str) -> None:
    with pytest.raises(ArtifactNameError):
        artifact(byte_count=count, sha256=digest)


def test_an_artifact_holds_no_path_or_bytes() -> None:
    names = {f.name for f in dataclasses.fields(WrittenArtifact)}
    assert names == {"output_format", "filename", "media_type", "byte_count", "sha256"}
    for forbidden in ("path", "directory", "content", "bytes", "payload"):
        assert not any(forbidden in name for name in names)


# --- the run result ---------------------------------------------------------------


def test_write_back_has_exactly_one_disabled_mode() -> None:
    assert [mode.value for mode in WriteBackMode] == ["disabled"]


def test_a_run_result_defaults_to_write_back_disabled() -> None:
    result = RunResult(
        status=RunStatus.SUCCESS,
        stages=tuple(StageState(stage) for stage in STAGE_ORDER),
        summary=summary(),
        artifacts=(artifact(),),
    )
    assert result.write_back_mode is WriteBackMode.DISABLED
    assert result.write_back_attempted == 0
    assert result.write_back_applied == 0
    assert result.artifact_count == 1
    assert result.completed_stages == STAGE_ORDER


def test_a_run_result_freezes_its_sequences() -> None:
    stages = [StageState(Stage.EXTRACT)]
    artifacts = [artifact()]
    result = RunResult(
        status=RunStatus.SUCCESS, stages=stages, summary=summary(), artifacts=artifacts
    )
    stages.append(StageState(Stage.INGEST))
    artifacts.append(artifact(OutputFormat.HTML))
    assert len(result.stages) == 1 and len(result.artifacts) == 1
    with pytest.raises(FrozenInstanceError):
        result.status = RunStatus.FAILED  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.artifacts.append(artifact())  # type: ignore[attr-defined]


def test_a_summary_copies_and_freezes_its_mappings() -> None:
    counts = {"exact": 1}
    item = summary(matching_counts=counts)
    counts["injected"] = 9
    assert "injected" not in item.matching_counts
    assert isinstance(item.matching_counts, MappingProxyType)
    with pytest.raises(TypeError):
        item.matching_counts["x"] = 1  # type: ignore[index]


def test_a_run_result_retains_nothing_unsafe() -> None:
    """The field *set* is closed, and every reachable value is a primitive.

    A name search is useless here — ``feed_rows`` is a count, not a row — so
    the check is the exact field set plus the types actually reachable.
    """
    assert {f.name for f in dataclasses.fields(RunResult)} == {
        "status", "stages", "summary", "artifacts",
        "write_back_mode", "write_back_attempted", "write_back_applied",
    }
    assert {f.name for f in dataclasses.fields(RunSummary)} == {
        "feed_rows", "system_rows", "validation_invalid", "matching_counts",
        "reconciliation_groups", "reconciliation_counts", "reconciliation_unresolved",
    }

    result = RunResult(
        status=RunStatus.SUCCESS,
        stages=tuple(StageState(stage) for stage in STAGE_ORDER),
        summary=summary(),
        artifacts=(artifact(),),
    )

    def assert_safe(value: object, where: str) -> None:
        assert isinstance(
            value, str | int | bool | tuple | MappingProxyType | dataclasses.dataclass
            if False else str | int | bool | tuple | MappingProxyType
        ) or dataclasses.is_dataclass(value), f"{where} holds {type(value).__name__}"

    for name, value in vars(result).items():
        assert_safe(value, f"result.{name}")
    for name, value in vars(result.summary).items():
        assert_safe(value, f"summary.{name}")
    for item in result.artifacts:
        for name, value in vars(item).items():
            assert_safe(value, f"artifact.{name}")
            assert not isinstance(value, Path | bytes | bytearray)


def test_equal_business_inputs_give_equal_results() -> None:
    def build() -> RunResult:
        return RunResult(
            status=RunStatus.SUCCESS,
            stages=tuple(StageState(stage) for stage in STAGE_ORDER),
            summary=summary(),
            artifacts=(artifact(),),
        )

    assert build() == build()


def test_every_public_model_is_a_frozen_dataclass() -> None:
    for cls in (StageState, WrittenArtifact, RunSummary, RunResult):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, cls.__name__


def test_stage_and_run_statuses_are_closed_sets() -> None:
    assert {s.value for s in StageStatus} == {"completed", "failed"}
    assert {s.value for s in RunStatus} == {"success", "failed"}
