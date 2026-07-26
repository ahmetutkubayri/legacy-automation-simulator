"""T10 pure value-level checks: blank, identifier, amount, unit, period."""

from __future__ import annotations

from decimal import Decimal

import pytest

from las.validate import (
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_AMOUNT_NOT_FINITE,
    CODE_AMOUNT_OUT_OF_RANGE,
    CODE_PERIOD_FORMAT_INVALID,
    CODE_PERIOD_MONTH_INVALID,
    CODE_RECORD_ID_FORMAT_INVALID,
    CODE_UNIT_NOT_SUPPORTED,
    DEFAULT_ALLOWED_UNITS,
    PERIOD_PATTERN,
    check_amount,
    check_identifier,
    check_period,
    check_unit,
    compile_pattern,
    is_blank,
)

RECORD_ID = compile_pattern(r"^OPR-\d{8}$")
EXTERNAL_REF = compile_pattern(r"^EXT-\d{5}$")
ACCOUNT_REF = compile_pattern(r"ACC-[0-9]{6}")
PERIOD = compile_pattern(PERIOD_PATTERN)
UNITS = frozenset(DEFAULT_ALLOWED_UNITS)
MIN, MAX = Decimal("0.0"), Decimal("1000000.0")


def amount(text: str) -> str | None:
    return check_amount(text, minimum=MIN, maximum=MAX)


# --- blankness ----------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", " ", "   ", "\t", "\n", " \t\n "])
def test_absent_and_whitespace_only_values_are_blank(value: str | None) -> None:
    assert is_blank(value) is True


@pytest.mark.parametrize("value", ["0", "a", " a ", "-", "EA"])
def test_content_is_not_blank(value: str) -> None:
    assert is_blank(value) is False


# --- identifiers --------------------------------------------------------------


def test_valid_record_identifier() -> None:
    assert check_identifier("OPR-00000001", RECORD_ID, CODE_RECORD_ID_FORMAT_INVALID) is None


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        ("OPR-0000001", "too few digits"),
        ("OPR-000000001", "too many digits"),
        ("REC-00000001", "wrong prefix"),
        ("OPR00000001", "missing hyphen"),
        ("opr-00000001", "lowercase"),
        (" OPR-00000001", "leading whitespace"),
        ("OPR-00000001 ", "trailing whitespace"),
        ("OPR-00000001\n", "trailing newline"),
        ("OPR-00000001\t", "trailing tab"),
        ("OPR-0000\x0000", "embedded control character"),
        ("OPR-０００００００１", "full-width digits"),
        ("OPR-1234۵5678", "extended Arabic-Indic digit"),
        ("XOPR-00000001", "prefixed"),
        ("OPR-00000001X", "suffixed"),
        ("", "empty"),
    ],
)
def test_invalid_record_identifiers(candidate: str, reason: str) -> None:
    assert (
        check_identifier(candidate, RECORD_ID, CODE_RECORD_ID_FORMAT_INVALID)
        == CODE_RECORD_ID_FORMAT_INVALID
    ), reason


def test_identifier_matching_is_never_partial() -> None:
    """A valid identifier embedded in longer text must still be rejected."""
    for candidate in ("prefix OPR-00000001", "OPR-00000001 suffix", "OPR-00000001OPR-00000002"):
        assert check_identifier(candidate, RECORD_ID, CODE_RECORD_ID_FORMAT_INVALID) is not None


def test_unicode_digits_are_rejected_despite_the_configured_pattern() -> None:
    """The configured pattern uses ``\\d``, which matches Unicode digits.

    Compiling with ASCII semantics is what closes that hole, so this test
    fails if the compilation flag is ever dropped.
    """
    import re

    permissive = re.compile(r"^OPR-\d{8}$")
    full_width = "OPR-１２３４５６７８"
    assert permissive.fullmatch(full_width) is not None  # the hole exists
    assert RECORD_ID.fullmatch(full_width) is None  # and is closed here


@pytest.mark.parametrize("candidate", ["EXT-10000", "EXT-00000", "EXT-99999"])
def test_valid_external_references(candidate: str) -> None:
    assert check_identifier(candidate, EXTERNAL_REF, "external_ref_format_invalid") is None


@pytest.mark.parametrize(
    "candidate",
    ["EXT-1000", "EXT-100000", "ext-10000", "EXT10000", " EXT-10000", "EXT-10000 ", "XT-10000"],
)
def test_invalid_external_references(candidate: str) -> None:
    assert check_identifier(candidate, EXTERNAL_REF, "external_ref_format_invalid") is not None


@pytest.mark.parametrize("candidate", ["ACC-100000", "ACC-000000"])
def test_valid_account_references(candidate: str) -> None:
    assert check_identifier(candidate, ACCOUNT_REF, "account_ref_format_invalid") is None


@pytest.mark.parametrize(
    "candidate",
    ["ACC-10000", "ACC-1000000", "acc-100000", "ACC100000", " ACC-100000", "ACC-10000A"],
)
def test_invalid_account_references(candidate: str) -> None:
    assert check_identifier(candidate, ACCOUNT_REF, "account_ref_format_invalid") is not None


# --- amounts ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["0", "0.0", "0.00", "1", "52", "123.45", "10.50", "1000000", "1000000.0", ".5", "5."]
)
def test_valid_amounts(text: str) -> None:
    assert amount(text) is None


def test_zero_is_accepted_because_the_contract_sets_the_floor_at_zero() -> None:
    assert amount("0") is None
    assert amount("0.000") is None


def test_leading_plus_sign_is_accepted() -> None:
    assert amount("+1.5") is None


@pytest.mark.parametrize(
    "text",
    ["1e3", "1E3", "1e+3", "1E+3", "1e-3", "1.5e2", "-1e3", "1e" + "9" * 30, "1e0"],
)
def test_exponent_notation_is_rejected(text: str) -> None:
    """No repository source authorizes exponent notation for amounts.

    The plan says nothing about amount syntax, the configuration defines only
    the bounds, and every amount the generator emits is plain decimal text. So
    accepting exponents would invent a capability the contract never defines.
    """
    assert amount(text) == CODE_AMOUNT_NOT_DECIMAL


def test_negative_amounts_are_out_of_range_not_malformed() -> None:
    """The configured floor is zero, so a negative value parses then fails range."""
    assert amount("-42.50") == CODE_AMOUNT_OUT_OF_RANGE
    assert amount("-0.01") == CODE_AMOUNT_OUT_OF_RANGE


def test_amounts_above_the_ceiling_are_out_of_range() -> None:
    assert amount("1000000.01") == CODE_AMOUNT_OUT_OF_RANGE
    assert amount("1000000000") == CODE_AMOUNT_OUT_OF_RANGE


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not-a-number", "letters"),
        ("12.34.56", "two decimal points"),
        ("", "empty"),
        (".", "bare point"),
        ("-", "bare sign"),
        ("+", "bare sign"),
        ("12,34", "comma decimal separator"),
        ("1 000", "internal space"),
        ("1_000", "underscore separator"),
        (" 1", "leading whitespace"),
        ("1 ", "trailing whitespace"),
        ("１２３", "full-width digits"),
        ("0x10", "hexadecimal"),
        ("1e", "incomplete exponent"),
        ("1.2.3e4", "malformed mantissa"),
        ("$10.50", "currency symbol"),
        ("10.50EA", "trailing unit"),
    ],
)
def test_malformed_amounts(text: str, reason: str) -> None:
    assert amount(text) == CODE_AMOUNT_NOT_DECIMAL, reason


@pytest.mark.parametrize(
    "text",
    ["nan", "NaN", "NAN", "inf", "Inf", "INF", "infinity", "Infinity", "-inf", "+inf", "-nan"],
)
def test_non_finite_amounts_get_their_own_code(text: str) -> None:
    assert amount(text) == CODE_AMOUNT_NOT_FINITE


def test_underscore_separator_is_rejected_even_though_decimal_accepts_it() -> None:
    """Decimal would read ``1_0`` as ten; that is separator guessing."""
    assert Decimal("1_0") == Decimal(10)  # the permissive behaviour exists
    assert amount("1_0") == CODE_AMOUNT_NOT_DECIMAL  # and is refused here


def test_surrounding_whitespace_is_rejected_even_though_decimal_accepts_it() -> None:
    """Decimal would silently trim; validation must not repair values."""
    assert Decimal(" 1 ") == Decimal(1)
    assert amount(" 1 ") == CODE_AMOUNT_NOT_DECIMAL


def test_exact_decimal_semantics_without_binary_float_rounding() -> None:
    """A value a float cannot hold exactly must still validate exactly."""
    assert amount("0.30000000000000004") is None
    assert amount("0.1") is None
    # The boundary is compared as a decimal, not as a float.
    assert check_amount("0.1", minimum=Decimal("0.1"), maximum=Decimal("0.1")) is None
    assert check_amount(
        "0.09999999999999999", minimum=Decimal("0.1"), maximum=Decimal("1")
    ) == CODE_AMOUNT_OUT_OF_RANGE


def test_amount_parsing_does_not_depend_on_locale() -> None:
    """Only the ASCII pattern decides; no locale-aware conversion is used."""
    assert amount("1234.56") is None
    assert amount("1.234,56") == CODE_AMOUNT_NOT_DECIMAL
    assert amount("1 234,56") == CODE_AMOUNT_NOT_DECIMAL


def test_range_boundaries_are_inclusive() -> None:
    assert check_amount("5", minimum=Decimal("5"), maximum=Decimal("10")) is None
    assert check_amount("10", minimum=Decimal("5"), maximum=Decimal("10")) is None
    assert check_amount("4.999", minimum=Decimal("5"), maximum=Decimal("10")) is not None


# --- units --------------------------------------------------------------------


@pytest.mark.parametrize("unit", DEFAULT_ALLOWED_UNITS)
def test_every_allowed_unit_is_accepted(unit: str) -> None:
    assert check_unit(unit, UNITS) is None


@pytest.mark.parametrize(
    ("unit", "reason"),
    [
        ("PALLET", "not in the set"),
        ("ea", "lower case"),
        ("Ea", "mixed case"),
        (" EA", "leading whitespace"),
        ("EA ", "trailing whitespace"),
        (" EA ", "surrounding whitespace"),
        ("ЕA", "Cyrillic lookalike"),
        ("ＥＡ", "full-width lookalike"),
        ("", "empty"),
        ("EA\n", "trailing newline"),
        ("KGS", "near miss"),
    ],
)
def test_unsupported_units(unit: str, reason: str) -> None:
    assert check_unit(unit, UNITS) == CODE_UNIT_NOT_SUPPORTED, reason


def test_allowed_units_have_one_deterministic_order() -> None:
    assert tuple(sorted(DEFAULT_ALLOWED_UNITS)) == DEFAULT_ALLOWED_UNITS
    assert DEFAULT_ALLOWED_UNITS == ("BOX", "EA", "KG")


# --- periods ------------------------------------------------------------------


@pytest.mark.parametrize(
    "period", ["2026-01", "2026-06", "2026-12", "1999-01", "0001-01", "9999-12"]
)
def test_valid_periods(period: str) -> None:
    assert check_period(period, PERIOD) is None


@pytest.mark.parametrize(
    ("period", "reason"),
    [
        ("2026-1", "single-digit month"),
        ("26-01", "two-digit year"),
        ("2026/01", "wrong separator"),
        ("2026-01-01", "day component"),
        ("202601", "no separator"),
        (" 2026-01", "leading whitespace"),
        ("2026-01 ", "trailing whitespace"),
        ("2026-01\n", "trailing newline"),
        ("", "empty"),
        ("abcd-ef", "letters"),
        ("２０２６-01", "full-width digits"),
    ],
)
def test_malformed_periods(period: str, reason: str) -> None:
    assert check_period(period, PERIOD) == CODE_PERIOD_FORMAT_INVALID, reason


@pytest.mark.parametrize("period", ["2026-00", "2026-13", "2026-99"])
def test_impossible_calendar_months_get_their_own_code(period: str) -> None:
    assert check_period(period, PERIOD) == CODE_PERIOD_MONTH_INVALID


def test_period_validation_never_consults_the_current_date() -> None:
    """A far-past and a far-future period are equally acceptable."""
    assert check_period("1970-01", PERIOD) is None
    assert check_period("2999-12", PERIOD) is None


def test_the_generator_periods_are_not_hard_coded_as_the_only_valid_ones() -> None:
    """Validating the six sample months only would over-fit the fixture."""
    assert check_period("2027-07", PERIOD) is None


def test_an_absurd_exponent_never_reaches_decimal_at_all() -> None:
    """Decimal would raise on this; the grammar refuses it first."""
    from decimal import InvalidOperation

    from las.validate import AMOUNT_PATTERN

    text = "1e" + "9" * 30
    with pytest.raises(InvalidOperation):
        Decimal(text)  # Decimal itself cannot build this value
    assert AMOUNT_PATTERN.fullmatch(text) is None  # so the grammar excludes it
    assert amount(text) == CODE_AMOUNT_NOT_DECIMAL  # reported, never raised


def test_the_grammar_admits_only_plain_ascii_decimal_text() -> None:
    """One place to read the whole accepted amount grammar."""
    from las.validate import AMOUNT_PATTERN

    for accepted in ("0", "1", "52", "123.45", "-42.50", "+1.5", ".5", "5."):
        assert AMOUNT_PATTERN.fullmatch(accepted) is not None, accepted
    for refused in ("1e3", "1_0", " 1", "1 ", "１", "0x10", "1,5", "1.2.3", ""):
        assert AMOUNT_PATTERN.fullmatch(refused) is None, refused


def test_a_very_long_numeral_is_handled_exactly_and_without_error() -> None:
    """Arbitrary precision, no binary float, no crash."""
    assert amount("9" * 10000) == CODE_AMOUNT_OUT_OF_RANGE
    assert check_amount(
        "9" * 400, minimum=Decimal(0), maximum=Decimal("9" * 400)
    ) is None
