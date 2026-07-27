"""T11 name normalization: limited, deterministic, and never repairing."""

from __future__ import annotations

import pytest

from las.match import DEFAULT_MAX_NAME_LENGTH, is_comparable, normalize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Demo Account 000000", "demo account 000000"),
        ("demo account 000000", "demo account 000000"),
        ("DEMO ACCOUNT 000000", "demo account 000000"),
        ("  Demo Account 000000  ", "demo account 000000"),
        ("Demo   Account\t000000", "demo account 000000"),
        ("Demo Account 000000", "demo account 000000"),
        ("Ｄｅｍｏ Ａｃｃｏｕｎｔ 000000", "demo account 000000"),
        ("Synthetic Twin Account 900001", "synthetic twin account 900001"),
        ("Unlisted Source 3", "unlisted source 3"),
    ],
)
def test_ordinary_and_varied_spellings_normalize_alike(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_normalization_is_idempotent() -> None:
    for raw in ("Demo Account 000000", "  DEMO   account ", "Ｄｅｍｏ"):
        once = normalize_name(raw)
        assert once is not None
        assert normalize_name(once) == once


def test_punctuation_is_kept_and_compared_literally() -> None:
    """The fixtures carry no punctuation, so removing any would invent a rule."""
    assert normalize_name("Demo Account, 000000.") == "demo account, 000000."
    assert normalize_name("Demo-Account") == "demo-account"
    assert normalize_name("Demo Account") != normalize_name("Demo-Account")
    assert normalize_name("Demo Account 000000") != normalize_name("Demo Account 000000.")


def test_unicode_lookalikes_do_not_collapse_into_a_false_match() -> None:
    """NFKC folds compatibility forms, but never different letters."""
    cyrillic = normalize_name("Аccount")  # leading Cyrillic A
    latin = normalize_name("Account")
    assert cyrillic is not None and latin is not None
    assert cyrillic != latin


def test_compatibility_forms_do_fold_because_they_are_the_same_character() -> None:
    assert normalize_name("ﬁle") == normalize_name("file")


@pytest.mark.parametrize("raw", [None, "", "   ", "\t", "\n", "   "])
def test_absent_and_blank_names_are_not_comparable(raw: str | None) -> None:
    assert normalize_name(raw) is None
    assert is_comparable(raw) is False


@pytest.mark.parametrize(
    "raw",
    [
        "Demo\x00Account",
        "Demo\x1bAccount",
        "Demo​Account",
        "Demo‮Account",
        "Demo⁦Account",
        "﻿Demo Account",
    ],
)
def test_control_and_bidirectional_characters_make_a_name_unusable(raw: str) -> None:
    assert normalize_name(raw) is None
    assert is_comparable(raw) is False


def test_an_over_long_name_is_unusable_rather_than_truncated() -> None:
    assert normalize_name("a" * DEFAULT_MAX_NAME_LENGTH) == "a" * DEFAULT_MAX_NAME_LENGTH
    assert normalize_name("a" * (DEFAULT_MAX_NAME_LENGTH + 1)) is None


def test_the_length_cap_is_a_parameter_not_a_hidden_constant() -> None:
    """Callers set the bound; the module constant is only the default."""
    assert normalize_name("abcdef", max_length=6) == "abcdef"
    assert normalize_name("abcdef", max_length=5) is None
    assert is_comparable("abcdef", max_length=6) is True
    assert is_comparable("abcdef", max_length=5) is False


@pytest.mark.parametrize("raw", [123, 1.5, b"bytes", ["Demo"], {"n": "Demo"}, object()])
def test_non_text_input_is_unusable(raw: object) -> None:
    assert normalize_name(raw) is None  # type: ignore[arg-type]


def test_the_original_value_is_never_modified() -> None:
    raw = "  Demo   Account 000000  "
    before = raw
    normalize_name(raw)
    assert raw == before


def test_normalization_has_no_locale_dependence() -> None:
    """Case folding is Unicode-defined, not locale-defined."""
    assert normalize_name("STRASSE") == "strasse"
    assert normalize_name("İstanbul") == normalize_name("İstanbul")


def test_is_comparable_agrees_with_normalize_name() -> None:
    for raw in ("Demo Account", "", None, "a\x00b", "x" * 300):
        assert is_comparable(raw) is (normalize_name(raw) is not None)
