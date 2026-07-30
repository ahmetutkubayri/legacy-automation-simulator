"""T14 writer: the directory transaction, and what it leaves behind on failure."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from las.pipeline import (
    FIXTURE_FILENAMES,
    STAGING_PREFIX,
    ArtifactSetError,
    ArtifactWriteError,
    CommitError,
    OutputConflictError,
    OutputParentError,
    StagedByteError,
    StagingError,
    check_destination,
    publish,
    publish_fixtures,
)
from las.report import OutputFormat
from las.report import artifact as make_artifact
from pipeline_helpers import staging_left

PAYLOADS = {
    OutputFormat.XLSX: b"PK\x03\x04binary-workbook",
    OutputFormat.JSON: b'{\n  "schema_version": "1.0"\n}\n',
    OutputFormat.HTML: b"<!DOCTYPE html>\n<html></html>\n",
}


def artifacts():
    return tuple(make_artifact(fmt, payload) for fmt, payload in PAYLOADS.items())


def nothing_left(root: Path, output: Path) -> bool:
    return not output.exists() and not staging_left(root)


# --- preconditions -----------------------------------------------------------------


def test_a_clean_destination_is_accepted(tmp_path: Path) -> None:
    check_destination(tmp_path / "report")


def test_an_existing_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "report").mkdir()
    with pytest.raises(OutputConflictError, match="already exists"):
        check_destination(tmp_path / "report")


def test_an_existing_nonempty_directory_is_refused_unread(tmp_path: Path) -> None:
    output = tmp_path / "report"
    output.mkdir()
    (output / "precious.txt").write_bytes(b"keep me")
    with pytest.raises(OutputConflictError):
        publish(output, artifacts())
    assert (output / "precious.txt").read_bytes() == b"keep me"
    assert sorted(p.name for p in output.iterdir()) == ["precious.txt"]


def test_an_existing_file_at_the_destination_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "report"
    output.write_bytes(b"not a directory")
    with pytest.raises(OutputConflictError):
        check_destination(output)


def test_a_missing_parent_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OutputParentError, match="not a directory"):
        check_destination(tmp_path / "absent" / "report")


def test_a_file_as_parent_is_refused(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"x")
    with pytest.raises(OutputParentError):
        check_destination(tmp_path / "file" / "report")


# --- the happy path -----------------------------------------------------------------


def test_publishing_writes_exactly_the_expected_files(tmp_path: Path) -> None:
    output = tmp_path / "report"
    written = publish(output, artifacts())
    assert sorted(p.name for p in output.iterdir()) == [
        "reconciliation-report.xlsx",
        "reconciliation-summary.html",
        "reconciliation-summary.json",
    ]
    assert len(written) == 3
    assert not staging_left(tmp_path)


def test_published_bytes_are_exact(tmp_path: Path) -> None:
    output = tmp_path / "report"
    publish(output, artifacts())
    for fmt, payload in PAYLOADS.items():
        from las.pipeline import filename_for

        assert (output / filename_for(fmt)).read_bytes() == payload


def test_the_descriptions_carry_the_real_digests(tmp_path: Path) -> None:
    output = tmp_path / "report"
    written = publish(output, artifacts())
    for item in written:
        on_disk = (output / item.filename).read_bytes()
        assert item.byte_count == len(on_disk)
        assert item.sha256 == hashlib.sha256(on_disk).hexdigest()


def test_artifacts_are_written_in_the_order_given(tmp_path: Path) -> None:
    written = publish(tmp_path / "report", artifacts())
    assert [item.output_format for item in written] == list(PAYLOADS)


def test_no_newline_conversion_happens(tmp_path: Path) -> None:
    output = tmp_path / "report"
    publish(output, artifacts())
    text = (output / "reconciliation-summary.json").read_bytes()
    assert b"\r\n" not in text and text.endswith(b"\n")


def test_nothing_is_written_outside_the_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "report"
    (tmp_path / "nested").mkdir()
    publish(output, artifacts())
    assert sorted(p.name for p in (tmp_path / "nested").iterdir()) == ["report"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["nested"]


# --- failure atomicity ----------------------------------------------------------------


def test_a_staging_creation_failure_leaves_nothing(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import writer

    monkeypatch.setattr(
        writer.tempfile, "mkdtemp", lambda **kwargs: (_ for _ in ()).throw(OSError())
    )
    output = tmp_path / "report"
    with pytest.raises(StagingError):
        publish(output, artifacts())
    assert nothing_left(tmp_path, output)


@pytest.mark.parametrize("failing", [0, 1, 2], ids=["first", "second", "third"])
def test_a_failing_artifact_write_leaves_nothing(
    tmp_path: Path, monkeypatch, failing: int
) -> None:
    from las.pipeline import writer

    calls = {"n": 0}
    original = writer._write_one

    def flaky(staging: Path, artifact):
        if calls["n"] == failing:
            calls["n"] += 1
            raise OSError("disk full")
        calls["n"] += 1
        return original(staging, artifact)

    monkeypatch.setattr(writer, "_write_one", flaky)
    output = tmp_path / "report"
    with pytest.raises(OSError):
        publish(output, artifacts())
    assert nothing_left(tmp_path, output)


@pytest.mark.parametrize("failing", [0, 1, 2], ids=["first", "second", "third"])
def test_a_write_error_is_typed_and_leaves_nothing(
    tmp_path: Path, monkeypatch, failing: int
) -> None:

    calls = {"n": 0}
    real_open = Path.open

    def flaky(self, *args, **kwargs):
        if "w" in (args[0] if args else kwargs.get("mode", "")):
            if calls["n"] == failing:
                calls["n"] += 1
                raise OSError("disk full")
            calls["n"] += 1
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky)
    output = tmp_path / "report"
    with pytest.raises(ArtifactWriteError):
        publish(output, artifacts())
    monkeypatch.undo()
    assert nothing_left(tmp_path, output)


def test_a_failed_read_back_leaves_nothing(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import writer

    monkeypatch.setattr(
        writer, "_verify_one",
        lambda path, artifact: (_ for _ in ()).throw(
            StagedByteError("a staged report artifact could not be read back")
        ),
    )
    output = tmp_path / "report"
    with pytest.raises(StagedByteError):
        publish(output, artifacts())
    assert nothing_left(tmp_path, output)


def test_a_corrupted_staged_byte_is_caught(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import writer

    original = writer._write_one

    def corrupt(staging: Path, artifact):
        path = original(staging, artifact)
        path.write_bytes(artifact.content + b"tampered")
        return path

    monkeypatch.setattr(writer, "_write_one", corrupt)
    output = tmp_path / "report"
    with pytest.raises(StagedByteError, match="byte count"):
        publish(output, artifacts())
    assert nothing_left(tmp_path, output)


def test_an_unexpected_staged_file_is_caught(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import writer

    original = writer._verify_set

    def intrude(staging: Path, expected):
        (staging / "stowaway.txt").write_bytes(b"x")
        return original(staging, expected)

    monkeypatch.setattr(writer, "_verify_set", intrude)
    output = tmp_path / "report"
    with pytest.raises(ArtifactSetError, match="exactly the expected"):
        publish(output, artifacts())
    assert nothing_left(tmp_path, output)


def test_a_failing_commit_leaves_nothing(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import writer

    monkeypatch.setattr(
        writer.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("cross-device"))
    )
    output = tmp_path / "report"
    with pytest.raises(CommitError):
        publish(output, artifacts())
    assert nothing_left(tmp_path, output)


def test_a_directory_appearing_during_staging_is_not_touched(
    tmp_path: Path, monkeypatch
) -> None:
    from las.pipeline import writer

    output = tmp_path / "report"
    original = writer._verify_set

    def race(staging: Path, expected):
        output.mkdir()
        (output / "theirs.txt").write_bytes(b"someone else")
        return original(staging, expected)

    monkeypatch.setattr(writer, "_verify_set", race)
    with pytest.raises(OutputConflictError):
        publish(output, artifacts())
    assert (output / "theirs.txt").read_bytes() == b"someone else"
    assert not staging_left(tmp_path)


# --- the staging name is private -------------------------------------------------------


def test_the_staging_name_never_appears_in_an_error(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import writer

    monkeypatch.setattr(
        writer.os, "rename", lambda *a: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(CommitError) as excinfo:
        publish(tmp_path / "report", artifacts())
    assert STAGING_PREFIX not in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)


def test_the_staging_name_never_affects_final_names(tmp_path: Path) -> None:
    one = publish(tmp_path / "a", artifacts())
    two = publish(tmp_path / "b", artifacts())
    assert [i.filename for i in one] == [i.filename for i in two]
    assert [i.sha256 for i in one] == [i.sha256 for i in two]


# --- the fixture transaction -------------------------------------------------------------


def test_publishing_fixtures_writes_exactly_four_files(tmp_path: Path) -> None:
    names = publish_fixtures(tmp_path / "samples", 42)
    assert sorted(names) == sorted(FIXTURE_FILENAMES)
    assert sorted(p.name for p in (tmp_path / "samples").iterdir()) == sorted(
        FIXTURE_FILENAMES
    )
    assert not staging_left(tmp_path)


def test_fixture_bytes_match_the_committed_fixtures(tmp_path: Path) -> None:
    from pipeline_helpers import SAMPLES_DIR

    publish_fixtures(tmp_path / "samples", 42)
    for name in FIXTURE_FILENAMES:
        assert (tmp_path / "samples" / name).read_bytes() == (
            SAMPLES_DIR / name
        ).read_bytes()


def test_an_existing_fixture_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "samples").mkdir()
    with pytest.raises(OutputConflictError):
        publish_fixtures(tmp_path / "samples", 42)


def test_a_failing_fixture_commit_leaves_nothing(tmp_path: Path, monkeypatch) -> None:
    from las.pipeline import writer

    monkeypatch.setattr(
        writer.os, "rename", lambda *a: (_ for _ in ()).throw(OSError())
    )
    output = tmp_path / "samples"
    with pytest.raises(CommitError):
        publish_fixtures(output, 42)
    assert nothing_left(tmp_path, output)


def test_fixture_generation_is_deterministic_per_seed(tmp_path: Path) -> None:
    publish_fixtures(tmp_path / "one", 7)
    publish_fixtures(tmp_path / "two", 7)
    for name in FIXTURE_FILENAMES:
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
