"""Pure value-level checks. No I/O, no clock, no randomness, no mutation.

Each function takes canonical text exactly as T9 produced it and returns a
stable issue code, or ``None`` when the value passes. Nothing here trims,
case-folds, rounds, or otherwise repairs a value — validation reports, it
never edits.

Why the regular expressions are strict
--------------------------------------
``decimal.Decimal`` is more forgiving than a deterministic validator should
be. It accepts surrounding whitespace (silent trimming), underscore digit
separators (separator guessing), and non-ASCII digits such as full-width
forms — so ``Decimal("１_2 ")`` would quietly become ``12``. Every value is
therefore screened by an explicit ASCII pattern *before* ``Decimal`` sees it.

The same trap exists in the configured identifier patterns: Python's ``\\d``
matches Unicode digits, so ``OPR-`` followed by full-width digits satisfies
``^OPR-\\d{8}$`` by default. Patterns are compiled with :data:`re.ASCII` to
close that hole without rewriting the configured pattern text.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from las.validate.models import (
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_AMOUNT_NOT_FINITE,
    CODE_AMOUNT_OUT_OF_RANGE,
    CODE_PERIOD_FORMAT_INVALID,
    CODE_PERIOD_MONTH_INVALID,
    CODE_UNIT_NOT_SUPPORTED,
)

#: Plain decimal text and nothing else: ASCII digits, an optional leading
#: sign, and at most one decimal point.
#:
#: Exponent notation is deliberately absent. No repository source authorizes
#: it — the development plan says nothing about amount syntax, the
#: configuration defines only ``amount_min``/``amount_max``, and every amount
#: the fixture generator emits is plain decimal text (``round(..., 2)``,
#: ``f"{amount:.2f}"``, or the declared invalid literals). Accepting exponents
#: would be inventing a capability the synthetic contract never defines.
#:
#: Underscores, whitespace, thousands separators and non-ASCII digits are all
#: excluded too, because ``Decimal`` would otherwise accept them silently.
AMOUNT_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")

#: The special forms ``Decimal`` understands but a measurement cannot be.
#: Matched deliberately, so they earn their own code instead of looking like
#: ordinary malformed text.
NON_FINITE_PATTERN = re.compile(r"[+-]?(?:nan|inf(?:inity)?)", re.IGNORECASE)

#: ``YYYY-MM``, ASCII digits only. No year restriction and no dependence on
#: today's date, so a result never changes with the calendar.
PERIOD_PATTERN = r"[0-9]{4}-[0-9]{2}"

_MONTHS = range(1, 13)


def is_blank(text: str | None) -> bool:
    """True when a cell carries no business value.

    T9 already maps an empty CSV field and an absent spreadsheet cell to
    ``None``. Whitespace-only text is content as far as ingestion is
    concerned, but it carries no *value*, so validation treats it as absent:
    a required field reports it missing, and domain rules skip it rather than
    piling a second, redundant issue on the same defect.
    """
    return text is None or not text.strip()


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a configured pattern with ASCII semantics (see module docs)."""
    return re.compile(pattern, re.ASCII)


def check_identifier(text: str, pattern: re.Pattern[str], code: str) -> str | None:
    """Full-string identifier match. No partial matches, no normalization."""
    return None if pattern.fullmatch(text) else code


def parse_amount(text: str | None) -> Decimal | None:
    """The exact decimal a value denotes, or ``None`` when it denotes none.

    This is the single place the project turns amount text into a number, so
    that every stage measures the same thing. The grammar is the strict one
    described above — plain ASCII decimal notation, nothing else — and parsing
    goes straight to :class:`~decimal.Decimal`, never through a binary float,
    so no value is rounded and the caller's original string stays
    authoritative.

    Range is deliberately not considered: how large an amount may be is a
    policy question, whereas this function answers only what the text says.
    Nothing about the offending value is reported, because a caller that needs
    to explain a failure has its own fixed vocabulary for that.
    """
    if text is None:
        return None
    if NON_FINITE_PATTERN.fullmatch(text):
        return None
    if not AMOUNT_PATTERN.fullmatch(text):
        return None
    # The two guards below are unreachable while AMOUNT_PATTERN stays as
    # narrow as it is: plain decimal text always parses, and always parses to
    # a finite value. They remain so that loosening the grammar can never turn
    # into a crash or let a non-finite value through unreported.
    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover - defence in depth
        return None
    if not value.is_finite():  # pragma: no cover - defence in depth
        return None
    return value


def check_amount(text: str, *, minimum: Decimal, maximum: Decimal) -> str | None:
    """Classify an amount as malformed, non-finite, out of range, or fine.

    Non-finite text is recognised first so that it earns its own code rather
    than looking like ordinary malformed text; everything else is decided by
    the shared :func:`parse_amount` grammar, then by the configured range.
    """
    if NON_FINITE_PATTERN.fullmatch(text):
        return CODE_AMOUNT_NOT_FINITE
    value = parse_amount(text)
    if value is None:
        return CODE_AMOUNT_NOT_DECIMAL
    if value < minimum or value > maximum:
        return CODE_AMOUNT_OUT_OF_RANGE
    return None


def check_unit(text: str, allowed: frozenset[str]) -> str | None:
    """Exact membership. No case folding, no trimming, no alias guessing."""
    return None if text in allowed else CODE_UNIT_NOT_SUPPORTED


def check_period(text: str, pattern: re.Pattern[str]) -> str | None:
    """Shape first, then calendar month, so the two failures stay distinct."""
    if not pattern.fullmatch(text):
        return CODE_PERIOD_FORMAT_INVALID
    if int(text[5:7]) not in _MONTHS:
        return CODE_PERIOD_MONTH_INVALID
    return None
