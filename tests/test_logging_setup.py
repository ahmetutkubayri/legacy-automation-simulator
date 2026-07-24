"""Logging setup behavior: destinations, levels, and idempotent re-init."""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from las.logging_setup import LOG_FILENAME, LOGGER_NAME, reset_logging, setup_logging

RUN_DIR = Path("output") / "RUN-logtest"


@pytest.fixture(autouse=True)
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Scratch cwd per test; always release file handlers afterwards."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    reset_logging()


def read_log() -> str:
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()
    return (RUN_DIR / LOG_FILENAME).read_text(encoding="utf-8")


def test_per_run_log_file_is_created() -> None:
    logger = setup_logging(RUN_DIR)
    logger.info("pipeline stage reached")
    content = read_log()
    assert "pipeline stage reached" in content
    assert "INFO" in content
    assert LOGGER_NAME in content


def test_console_receives_formatted_output() -> None:
    stream = io.StringIO()
    logger = setup_logging(RUN_DIR, console_stream=stream)
    logger.info("visible progress message")
    output = stream.getvalue()
    assert "visible progress message" in output
    assert "INFO" in output
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", output)


def test_reinitialization_does_not_duplicate_handlers() -> None:
    stream = io.StringIO()
    setup_logging(RUN_DIR, console_stream=stream)
    logger = setup_logging(RUN_DIR, console_stream=stream)

    # Count only our managed handlers: pytest's log-capture handlers may
    # also be attached to this logger during the test run.
    managed = [h for h in logger.handlers if getattr(h, "_las_managed_handler", False)]
    assert len(managed) == 2  # exactly one console + one file handler
    logger.info("emitted-once marker")
    assert stream.getvalue().count("emitted-once marker") == 1
    assert read_log().count("emitted-once marker") == 1


def test_console_level_filters_while_file_keeps_debug() -> None:
    stream = io.StringIO()
    logger = setup_logging(RUN_DIR, console_level="WARNING", console_stream=stream)
    logger.debug("debug detail")
    logger.warning("loud warning")

    console_output = stream.getvalue()
    assert "debug detail" not in console_output
    assert "loud warning" in console_output

    file_output = read_log()
    assert "debug detail" in file_output
    assert "loud warning" in file_output


def test_child_logger_messages_carry_module_name() -> None:
    stream = io.StringIO()
    setup_logging(RUN_DIR, console_stream=stream)
    logging.getLogger("las.ingest").info("feed parsed")
    assert "las.ingest" in stream.getvalue()
    assert "las.ingest" in read_log()


def test_invalid_console_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="console_level"):
        setup_logging(RUN_DIR, console_level="CHATTY")


def test_absolute_run_dir_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="relative"):
        setup_logging(tmp_path / "output")


def test_audit_file_and_run_log_stay_separate() -> None:
    from las.audit import AuditTrail

    with AuditTrail(run_id="RUN-logtest") as trail:
        logger = setup_logging(trail.run_dir)
        logger.info("human readable line")
        trail.record("stage_a", "machine_event")

    log_content = read_log()
    audit_content = trail.path.read_text(encoding="utf-8")
    assert "machine_event" not in log_content
    assert "human readable line" not in audit_content
