"""Human-oriented logging: console output plus one per-run log file.

Kept deliberately separate from the JSONL audit trail in
:mod:`las.audit`: the audit file is machine-parsed evidence of decisions,
while these logs exist for people watching or replaying a run. The
console shows the configured level and the per-run file always captures
DEBUG detail.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

from las.audit.trail import ensure_relative_path

LOGGER_NAME = "las"
LOG_FILENAME = "run.log"

_MARKER = "_las_managed_handler"
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_ALLOWED_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def setup_logging(
    run_dir: str | Path,
    console_level: str = "INFO",
    console_stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the ``las`` logger for one run and return it.

    Calling this again (for example at the start of the next run) replaces
    the previously installed handlers instead of stacking duplicates, so
    every message is emitted exactly once per destination.
    """
    if console_level not in _ALLOWED_LEVELS:
        allowed = ", ".join(_ALLOWED_LEVELS)
        raise ValueError(f"console_level '{console_level}' is not one of: {allowed}")

    directory = ensure_relative_path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _remove_managed_handlers(logger)

    formatter = logging.Formatter(_FORMAT, _DATE_FORMAT)

    console = logging.StreamHandler(console_stream if console_stream is not None else sys.stderr)
    console.setLevel(getattr(logging, console_level))
    console.setFormatter(formatter)
    setattr(console, _MARKER, True)
    logger.addHandler(console)

    file_handler = logging.FileHandler(directory / LOG_FILENAME, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _MARKER, True)
    logger.addHandler(file_handler)

    return logger


def reset_logging() -> None:
    """Remove and close all handlers installed by :func:`setup_logging`."""
    _remove_managed_handlers(logging.getLogger(LOGGER_NAME))


def _remove_managed_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _MARKER, False):
            logger.removeHandler(handler)
            handler.close()
