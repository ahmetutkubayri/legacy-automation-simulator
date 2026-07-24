"""Append-only JSONL audit trail with one correlation ID per run.

The audit trail answers "what did the pipeline decide, and when" in a
machine-readable form: one JSON object per line, every line carrying the
same run correlation ID. Human-oriented progress logging lives in
:mod:`las.logging_setup` and is kept strictly separate from this file.

Payload safety: values are validated against a strict allowlist *before*
anything is written, so an event is either recorded exactly as given or
rejected with :class:`AuditError` — never partially written, and never
serialized through ``repr()``/``str()`` of arbitrary objects, which could
leak secrets, personal data, or absolute paths into the audit file.

Allowed payload values:

* ``None``, ``bool``, ``int``, finite ``float``, ``str``
* ``list`` / ``tuple`` of allowed values (recorded as JSON arrays)
* ``dict`` with ``str`` keys and allowed values
* ``datetime.date`` / ``datetime.datetime`` — recorded as ISO-8601 via
  ``isoformat()``
* ``uuid.UUID`` — recorded in canonical hyphenated hex form
* ``enum.Enum`` — recorded as ``"ClassName.MEMBER_NAME"`` (the member's
  *name*, never its value)

Anything else — including NaN/infinite floats, non-string dict keys, and
structures nested deeper than :data:`MAX_PAYLOAD_DEPTH` (which also
catches recursive structures) — raises :class:`AuditError`; the error
message names only the offending *type* and its location in the payload.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import TracebackType
from typing import Any, TextIO

AUDIT_FILENAME = "audit.jsonl"

#: Maximum allowed nesting depth of a payload; also the cycle guard.
MAX_PAYLOAD_DEPTH = 20

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class AuditError(Exception):
    """Raised when an audit event cannot be recorded faithfully."""


def ensure_relative_path(value: str | Path) -> Path:
    """Return ``value`` as a ``Path``, rejecting non-relative locations.

    Run artifacts must stay inside the project tree, so absolute paths,
    drive-anchored paths, network paths, and ``..`` escapes are all
    rejected regardless of the platform the path syntax belongs to.
    """
    text = str(value)
    windows = PureWindowsPath(text)
    posix = PurePosixPath(text)
    if windows.is_absolute() or posix.is_absolute() or windows.drive:
        raise ValueError(
            f"path '{text}' must be relative to the project directory; "
            "absolute and network paths are not allowed"
        )
    if ".." in windows.parts:
        raise ValueError(f"path '{text}' must not contain '..' segments")
    return Path(text)


def new_run_id() -> str:
    """Mint a unique correlation ID, e.g. ``RUN-20260101T120000Z-3f9c2a``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"RUN-{stamp}-{uuid.uuid4().hex[:6]}"


def _sanitize_value(value: Any, path: str, depth: int) -> Any:
    """Validate one payload value against the allowlist (see module docs).

    Never calls ``repr()`` or ``str()`` on arbitrary objects; error
    messages carry only the offending type's name and payload location.
    """
    if depth > MAX_PAYLOAD_DEPTH:
        raise AuditError(
            f"payload nesting at '{path}' exceeds the maximum depth of "
            f"{MAX_PAYLOAD_DEPTH}; recursive or excessively deep structures "
            "are not allowed"
        )
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AuditError(
                f"non-finite float at '{path}' is not allowed; audit lines must be strict JSON"
            )
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [
            _sanitize_value(item, f"{path}[{index}]", depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AuditError(
                    f"dictionary key of type '{type(key).__name__}' at '{path}' "
                    "is not allowed; payload keys must be strings"
                )
            sanitized[key] = _sanitize_value(item, f"{path}.{key}", depth + 1)
        return sanitized
    raise AuditError(f"unsupported payload value of type '{type(value).__name__}' at '{path}'")


class AuditTrail:
    """Context manager owning one run's audit file.

    Opens ``<output_root>/<run_id>/audit.jsonl`` in append mode, records a
    ``run_started`` event on entry and a ``run_ended`` event on close (or
    ``run_failed`` first, when the ``with`` block raises — carrying only
    the exception *class name*, never its message). Payloads are
    allowlist-sanitized before writing; a rejected event leaves the file
    untouched.
    """

    def __init__(self, output_root: str | Path = "output", run_id: str | None = None) -> None:
        self.output_root = ensure_relative_path(output_root)
        if run_id is not None and not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError(
                f"run_id '{run_id}' may only contain letters, digits, dots, "
                "underscores, and hyphens"
            )
        self.run_id = run_id if run_id is not None else new_run_id()
        self.run_dir = self.output_root / self.run_id
        self.path = self.run_dir / AUDIT_FILENAME
        self._handle: TextIO | None = None
        self._events_written = 0

    @property
    def events_written(self) -> int:
        return self._events_written

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def __enter__(self) -> AuditTrail:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self.record("run", "run_started")
        return self

    def record(self, stage: str, event: str, payload: Mapping[str, Any] | None = None) -> None:
        """Append one event, or raise :class:`AuditError` writing nothing."""
        if self._handle is None:
            raise AuditError("audit trail is not open; use it as a context manager")
        if not isinstance(stage, str) or not stage.strip():
            raise AuditError("stage must be a non-empty string")
        if not isinstance(event, str) or not event.strip():
            raise AuditError("event must be a non-empty string")
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise AuditError(f"payload must be a mapping, got {type(payload).__name__}")

        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "stage": stage,
            "event": event,
            "payload": _sanitize_value(payload, "payload", depth=0),
        }
        # Sanitization already guarantees JSON-safe content; allow_nan=False
        # keeps every line strict JSON even if that guarantee ever regressed.
        line = json.dumps(entry, ensure_ascii=False, allow_nan=False)

        self._handle.write(line + "\n")
        self._handle.flush()
        self._events_written += 1

    def close(self) -> None:
        """Record ``run_ended`` and release the file; safe to call twice."""
        if self._handle is None:
            return
        self.record("run", "run_ended", {"events_written": self._events_written})
        handle, self._handle = self._handle, None
        handle.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None and self._handle is not None:
            self.record("run", "run_failed", {"error_type": exc_type.__name__})
        self.close()
        return False
