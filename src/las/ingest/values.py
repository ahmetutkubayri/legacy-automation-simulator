"""Cell-value policy: one deterministic, cross-format text representation.

Both readers funnel every cell through :func:`cell_to_text`, so a CSV field
and the XLSX cell holding the same unambiguous value produce the identical
canonical string.

Retained as text, not as native scalars
--------------------------------------
CSV can only ever yield text, while XLSX yields real numbers, booleans and
timestamps. Rather than let the format decide a row's Python types, every
supported scalar is normalized to text with locale-independent rules. That
keeps cross-format results comparable and leaves *interpretation* — is this
a valid amount? — entirely to the validation stage.

Missing cells
-------------
A cell is missing when it holds ``None`` (an absent XLSX cell) or an empty
string (an empty CSV field, or an empty XLSX string). Both become ``None``,
so "no value" means one thing in both formats. Whitespace-only text is
*not* missing: it is content, preserved verbatim for validation to judge.

Safe scalar allowlist
---------------------
``None``, ``str``, ``bool``, ``int``, finite ``float``, ``datetime`` and
``date``. Anything else raises :class:`UnsupportedCellValueError`, whose
message names the offending *type* and never the value.

Normalization rules
-------------------
* ``str`` — preserved byte-for-byte. No stripping, no case folding, no
  numeric coercion: malformed business text must survive intact.
* ``bool`` — ``"TRUE"`` / ``"FALSE"`` (checked before ``int``, which
  ``bool`` subclasses).
* ``int`` — plain decimal digits, no separators.
* ``float`` — Python's shortest round-trip form, which is exact and
  locale-independent. Nothing is rounded or padded, so a spreadsheet number
  of ``12.5`` yields ``"12.5"``; a CSV text of ``"12.50"`` stays
  ``"12.50"``, because the trailing zero is real text the numeric domain
  cannot represent.
* ``datetime`` / ``date`` — ISO-8601 via ``isoformat()``.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from las.ingest.errors import UnsupportedCellValueError

#: Text recorded for the two boolean cell values.
TRUE_TEXT = "TRUE"
FALSE_TEXT = "FALSE"


def cell_to_text(
    value: Any,
    *,
    operation: str,
    source: str,
    worksheet: str | None = None,
    row: int | None = None,
    column: int | None = None,
) -> str | None:
    """Normalize one cell to canonical text, or ``None`` when it is missing.

    Raises :class:`UnsupportedCellValueError` for any type outside the
    documented allowlist.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value else None
    if isinstance(value, bool):
        return TRUE_TEXT if value else FALSE_TEXT
    if isinstance(value, int):
        return format(value, "d")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedCellValueError(
                "a non-finite number is not a supported cell value",
                operation=operation,
                source=source,
                worksheet=worksheet,
                row=row,
                column=column,
            )
        # Shortest round-trip form: exact, deterministic, locale-independent.
        return format(value, "")
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise UnsupportedCellValueError(
        f"cell value of type '{type(value).__name__}' is not supported",
        operation=operation,
        source=source,
        worksheet=worksheet,
        row=row,
        column=column,
    )
