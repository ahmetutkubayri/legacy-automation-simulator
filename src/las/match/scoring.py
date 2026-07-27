"""A small, explicit, integer similarity score.

Why this and not a library
--------------------------
The development plan asks for "fuzzy scoring with config thresholds" without
naming an algorithm, so the algorithm is defined here rather than borrowed.
``difflib.SequenceMatcher`` is deliberately not used: its ratio depends on a
matching-block heuristic whose contract is not specified in terms this project
can pin, and it returns a binary float. A short edit distance is easier to
state exactly, to test, and to reason about.

The definition
--------------
``distance(a, b)`` is the Levenshtein distance with unit costs — insertion 1,
deletion 1, substitution 1 — computed over the two *normalized* names.

``similarity_bp(a, b)`` converts it to basis points on a fixed 0–10,000 scale::

    m = max(len(a), len(b))
    similarity = 10000 * (m - distance) / m,  rounded half up
    similarity = 10000                        when m == 0

The arithmetic is entirely integer: the rounding is ``(x + m // 2) // m``, so
no binary float is involved anywhere and equality is exact. Scores are
symmetric because Levenshtein distance is, and identical inputs always give
exactly 10,000.

Bounds
------
The dynamic program is two rolling rows of ``len(b) + 1`` integers, driven by
two ``for`` loops over finite strings. There is no recursion and no ``while``.
Input length is bounded upstream by
:data:`las.match.normalize.MAX_NAME_LENGTH`, so the work per comparison is
bounded too.
"""

from __future__ import annotations

#: The fixed inclusive score range, in basis points.
SCORE_MIN_BP = 0
SCORE_MAX_BP = 10_000


def edit_distance(left: str, right: str) -> int:
    """Levenshtein distance with unit insert, delete and substitute costs."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for row, left_character in enumerate(left, start=1):
        current = [row]
        for column, right_character in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,  # deletion
                    current[column - 1] + 1,  # insertion
                    previous[column - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def similarity_bp(left: str, right: str) -> int:
    """Similarity of two normalized names, in basis points from 0 to 10,000."""
    longest = max(len(left), len(right))
    if longest == 0:
        return SCORE_MAX_BP
    remaining = longest - edit_distance(left, right)
    # Integer round-half-up; no float ever enters the calculation.
    return (SCORE_MAX_BP * remaining + longest // 2) // longest
