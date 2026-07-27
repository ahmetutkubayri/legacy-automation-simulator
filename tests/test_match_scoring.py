"""T11 similarity scoring: exact integers, bounded work, no recursion."""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

from las.match import SCORE_MAX_BP, SCORE_MIN_BP, edit_distance, similarity_bp

SCORING_SOURCE = Path(__file__).resolve().parents[1] / "src" / "las" / "match" / "scoring.py"


@pytest.mark.parametrize(
    ("left", "right", "distance"),
    [
        ("", "", 0),
        ("abc", "abc", 0),
        ("abc", "abcd", 1),
        ("abcd", "abc", 1),
        ("abc", "abd", 1),
        ("abc", "xyz", 3),
        ("", "abc", 3),
        ("abc", "", 3),
        ("kitten", "sitting", 3),
        ("synthetic twin account 90000", "synthetic twin account 900001", 1),
    ],
)
def test_edit_distance_uses_unit_insert_delete_substitute_costs(
    left: str, right: str, distance: int
) -> None:
    assert edit_distance(left, right) == distance


def test_one_insertion_deletion_and_substitution_each_cost_one() -> None:
    assert edit_distance("abcd", "abcde") == 1  # insertion
    assert edit_distance("abcde", "abcd") == 1  # deletion
    assert edit_distance("abcd", "abce") == 1  # substitution


def test_distance_is_symmetric() -> None:
    for left, right in (("abc", "abcd"), ("kitten", "sitting"), ("", "abc")):
        assert edit_distance(left, right) == edit_distance(right, left)


def test_identical_names_score_perfectly() -> None:
    assert similarity_bp("demo account 000000", "demo account 000000") == SCORE_MAX_BP


def test_two_empty_names_are_identical() -> None:
    assert similarity_bp("", "") == SCORE_MAX_BP


def test_empty_against_non_empty_scores_zero() -> None:
    assert similarity_bp("", "demo") == SCORE_MIN_BP
    assert similarity_bp("demo", "") == SCORE_MIN_BP


def test_completely_different_names_score_zero() -> None:
    assert similarity_bp("abc", "xyz") == SCORE_MIN_BP


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("abc", "abcd", 7500),
        ("abc", "abd", 6667),
        ("synthetic twin account 90000", "synthetic twin account 900001", 9655),
        ("synthetic twin account 90000", "synthetic twin account 900002", 9655),
    ],
)
def test_the_documented_arithmetic(left: str, right: str, expected: int) -> None:
    """10000 * (max_len - distance) / max_len, rounded half up, in integers."""
    longest = max(len(left), len(right))
    manual = (SCORE_MAX_BP * (longest - edit_distance(left, right)) + longest // 2) // longest
    assert similarity_bp(left, right) == manual == expected


def test_scores_are_symmetric() -> None:
    for left, right in (("abc", "abcd"), ("demo account", "demo acount"), ("", "x")):
        assert similarity_bp(left, right) == similarity_bp(right, left)


def test_scores_are_bounded_integers() -> None:
    shuffler = random.Random(3)
    alphabet = "abcdefghij "
    for _ in range(200):
        left = "".join(shuffler.choice(alphabet) for _ in range(shuffler.randint(0, 20)))
        right = "".join(shuffler.choice(alphabet) for _ in range(shuffler.randint(0, 20)))
        score = similarity_bp(left, right)
        assert isinstance(score, int)
        assert not isinstance(score, bool)
        assert SCORE_MIN_BP <= score <= SCORE_MAX_BP


def test_repeated_calls_are_identical() -> None:
    for _ in range(5):
        assert similarity_bp("demo account 000001", "demo account 000002") == similarity_bp(
            "demo account 000001", "demo account 000002"
        )


def test_scoring_never_touches_a_binary_float() -> None:
    """No float appears in the module's code at all."""
    code = SCORING_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(code)
    floats = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == []
    assert "/" not in code.split('"""')[-1] or "//" in code


def test_a_long_but_bounded_input_is_handled() -> None:
    left = "demo account " * 15
    right = "demo account " * 15 + "x"
    assert 0 <= similarity_bp(left.strip(), right.strip()) <= SCORE_MAX_BP


def test_scoring_uses_no_recursion_and_no_unbounded_loop() -> None:
    tree = ast.parse(SCORING_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.While), "scoring must not use a while loop"
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                assert call.func.id != node.name, f"{node.name} recurses"


def test_score_ordering_is_independent_of_comparison_order() -> None:
    names = ["demo account 000002", "demo account 000001", "demo account 000010"]
    probe = "demo account 000001"
    forward = sorted(((similarity_bp(probe, n), n) for n in names), reverse=True)
    backward = sorted(((similarity_bp(probe, n), n) for n in reversed(names)), reverse=True)
    assert forward == backward
