"""Audit trail behavior: files, correlation IDs, schema, and safety."""

from __future__ import annotations

import enum
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from las.audit import AUDIT_FILENAME, AuditError, AuditTrail, ensure_relative_path, new_run_id

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")


@pytest.fixture(autouse=True)
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run every test from a scratch directory so 'output' stays relative."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def read_events(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


# --- directory and file creation -------------------------------------------


def test_run_directory_and_audit_file_are_created() -> None:
    with AuditTrail() as trail:
        assert trail.run_dir == Path("output") / trail.run_id
        assert trail.path == trail.run_dir / AUDIT_FILENAME
        assert trail.path.is_file()


def test_custom_output_root_is_respected() -> None:
    with AuditTrail(output_root="output/nested") as trail:
        assert trail.path.is_file()
        assert trail.path.parts[0] == "output"


# --- schema and round-trip ---------------------------------------------------


def test_events_round_trip_with_full_schema() -> None:
    with AuditTrail() as trail:
        trail.record("ingest", "feed_loaded", {"rows": 10, "feed": "feed_alpha"})
    events = read_events(trail.path)
    assert [e["event"] for e in events] == ["run_started", "feed_loaded", "run_ended"]
    loaded = events[1]
    assert set(loaded) == {"ts", "run_id", "stage", "event", "payload"}
    assert loaded["stage"] == "ingest"
    assert loaded["payload"] == {"rows": 10, "feed": "feed_alpha"}


def test_payload_types_survive_round_trip() -> None:
    payload = {
        "count": 3,
        "ratio": 0.5,
        "flag": True,
        "missing": None,
        "nested": {"items": [1, "two", 3.0]},
    }
    with AuditTrail() as trail:
        trail.record("validate", "rule_fired", payload)
    assert read_events(trail.path)[1]["payload"] == payload


def test_run_ended_reports_event_count() -> None:
    with AuditTrail() as trail:
        trail.record("stage_a", "event_a")
        trail.record("stage_b", "event_b")
    ended = read_events(trail.path)[-1]
    assert ended["event"] == "run_ended"
    assert ended["payload"] == {"events_written": 3}  # run_started + 2
    assert trail.events_written == 4  # ... + the run_ended event itself


# --- correlation IDs ---------------------------------------------------------


def test_correlation_id_is_stable_within_a_run() -> None:
    with AuditTrail() as trail:
        trail.record("match", "candidate_scored", {"score": 0.9})
        trail.record("match", "candidate_scored", {"score": 0.4})
    run_ids = {event["run_id"] for event in read_events(trail.path)}
    assert run_ids == {trail.run_id}


def test_separate_runs_get_different_correlation_ids() -> None:
    with AuditTrail() as first:
        pass
    with AuditTrail() as second:
        pass
    assert first.run_id != second.run_id


def test_run_id_format() -> None:
    assert re.fullmatch(r"RUN-\d{8}T\d{6}Z-[0-9a-f]{6}", new_run_id())


def test_invalid_explicit_run_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="run_id"):
        AuditTrail(run_id="bad/id")


# --- append behavior ---------------------------------------------------------


def test_reopening_same_run_appends_instead_of_truncating() -> None:
    with AuditTrail(run_id="RUN-fixed") as trail:
        trail.record("stage_a", "first_session")
    first_count = len(read_events(trail.path))

    with AuditTrail(run_id="RUN-fixed") as again:
        again.record("stage_a", "second_session")

    events = read_events(again.path)
    assert len(events) > first_count
    names = [event["event"] for event in events]
    assert "first_session" in names
    assert "second_session" in names


# --- timestamps --------------------------------------------------------------


def test_timestamps_are_utc_isoformat() -> None:
    with AuditTrail() as trail:
        trail.record("stage_a", "event_a")
    for event in read_events(trail.path):
        assert TS_PATTERN.fullmatch(event["ts"]), event["ts"]
        parsed = datetime.fromisoformat(event["ts"])
        assert parsed.utcoffset() is not None
        assert parsed.astimezone(UTC) == parsed


# --- payload safety ----------------------------------------------------------


class LeakyObject:
    """Object whose repr/str would leak a sentinel value if ever called."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __repr__(self) -> str:
        self.calls.append("repr")
        return "LEAK_SENTINEL_MUST_NOT_APPEAR"

    def __str__(self) -> str:
        self.calls.append("str")
        return "LEAK_SENTINEL_MUST_NOT_APPEAR"


def test_secret_bearing_object_is_rejected_without_calling_repr_or_str() -> None:
    leaky = LeakyObject()
    with AuditTrail() as trail:
        with pytest.raises(AuditError, match="LeakyObject") as excinfo:
            trail.record("stage_a", "event_a", {"credential": leaky})
        assert "LEAK_SENTINEL_MUST_NOT_APPEAR" not in str(excinfo.value)
    assert leaky.calls == []
    assert "LEAK_SENTINEL_MUST_NOT_APPEAR" not in trail.path.read_text(encoding="utf-8")


def test_unsupported_nested_object_is_rejected_with_location() -> None:
    with AuditTrail() as trail, pytest.raises(AuditError, match=r"payload\.outer\[1\]\.inner"):
        trail.record("stage_a", "event_a", {"outer": [1, {"inner": object()}]})


def test_non_string_dict_keys_are_rejected() -> None:
    with AuditTrail() as trail, pytest.raises(AuditError, match="keys must be strings"):
        trail.record("stage_a", "event_a", {"nested": {1: "one"}})


@pytest.mark.parametrize("bad_float", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_rejected(bad_float: float) -> None:
    with AuditTrail() as trail, pytest.raises(AuditError, match="non-finite float"):
        trail.record("stage_a", "event_a", {"value": bad_float})


def test_recursive_payload_is_rejected() -> None:
    loop: list = []
    loop.append(loop)
    with AuditTrail() as trail, pytest.raises(AuditError, match="depth"):
        trail.record("stage_a", "event_a", {"loop": loop})


def test_excessively_deep_payload_is_rejected() -> None:
    deep: dict = {"leaf": 1}
    for _ in range(25):
        deep = {"level": deep}
    with AuditTrail() as trail, pytest.raises(AuditError, match="depth"):
        trail.record("stage_a", "event_a", deep)


def test_rejected_event_leaves_no_partial_line() -> None:
    with AuditTrail() as trail:
        trail.record("stage_a", "good_event")
        before = trail.path.read_text(encoding="utf-8")
        with pytest.raises(AuditError):
            trail.record("stage_a", "bad_event", {"obj": object()})
        after = trail.path.read_text(encoding="utf-8")
    assert after == before  # nothing appended, not even a fragment
    for line in after.splitlines():
        json.loads(line)  # every existing line is still complete JSON


def test_safe_standard_types_serialize_deterministically() -> None:
    class Status(enum.Enum):
        ACTIVE = "internal-value-not-recorded"

    payload = {
        "day": date(2026, 1, 2),
        "moment": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "token": UUID("12345678-1234-5678-1234-567812345678"),
        "state": Status.ACTIVE,
        "pair": (1, 2),
    }
    with AuditTrail() as trail:
        trail.record("stage_a", "event_a", payload)
    recorded = read_events(trail.path)[1]["payload"]
    assert recorded == {
        "day": "2026-01-02",
        "moment": "2026-01-02T03:04:05+00:00",
        "token": "12345678-1234-5678-1234-567812345678",
        "state": "Status.ACTIVE",
        "pair": [1, 2],
    }
    assert "internal-value-not-recorded" not in trail.path.read_text(encoding="utf-8")


def test_non_mapping_payload_raises() -> None:
    with AuditTrail() as trail, pytest.raises(AuditError, match="mapping"):
        trail.record("stage_a", "event_a", [1, 2, 3])  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_stage_or_event_raises(bad: str) -> None:
    with AuditTrail() as trail:
        with pytest.raises(AuditError, match="stage"):
            trail.record(bad, "event_a")
        with pytest.raises(AuditError, match="event"):
            trail.record("stage_a", bad)


# --- lifecycle ---------------------------------------------------------------


def test_record_without_entering_raises() -> None:
    trail = AuditTrail()
    with pytest.raises(AuditError, match="not open"):
        trail.record("stage_a", "event_a")


def test_record_after_close_raises() -> None:
    with AuditTrail() as trail:
        pass
    with pytest.raises(AuditError, match="not open"):
        trail.record("stage_a", "event_a")


def test_close_is_idempotent() -> None:
    trail = AuditTrail()
    with trail:
        pass
    trail.close()
    events = read_events(trail.path)
    assert [e["event"] for e in events].count("run_ended") == 1
    assert not trail.is_open


def test_close_before_entering_is_a_no_op() -> None:
    AuditTrail().close()  # must not raise or create files
    assert not Path("output").exists()


def test_exception_inside_block_records_run_failed_and_propagates() -> None:
    with pytest.raises(RuntimeError, match="boom"), AuditTrail() as trail:
        trail.record("stage_a", "event_a")
        raise RuntimeError("boom")
    names = [event["event"] for event in read_events(trail.path)]
    assert names[-2:] == ["run_failed", "run_ended"]
    failed = read_events(trail.path)[-2]
    assert failed["payload"] == {"error_type": "RuntimeError"}


# --- relative-path enforcement ----------------------------------------------


@pytest.mark.parametrize(
    "bad_root",
    ["C:\\runs", "/var/runs", "\\\\host\\share\\runs", "../escape"],
)
def test_non_relative_output_root_is_rejected(bad_root: str) -> None:
    with pytest.raises(ValueError):
        AuditTrail(output_root=bad_root)


def test_ensure_relative_path_accepts_plain_relative() -> None:
    assert ensure_relative_path("output/run") == Path("output") / "run"
