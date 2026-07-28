"""T12 engine: outcome precedence, exact arithmetic, and deterministic order."""

from __future__ import annotations

import decimal
import random
from contextlib import contextmanager
from decimal import Decimal

import pytest

from las.match import MatchOutcome
from las.reconcile import (
    REASON_MISSING_IN_FEED,
    REASON_MISSING_IN_SYSTEM,
    REASON_MULTIPLE_UNITS_ON_ONE_SIDE,
    REASON_OUTSIDE_VARIANCE_BANDS,
    REASON_TOTALS_EQUAL,
    REASON_UNIT_DIFFERS_ACROSS_SIDES,
    REASON_WITHIN_VARIANCE_BAND,
    GroupKey,
    ReconcileOutcome,
    Side,
    exact_sum,
    reconcile,
)
from reconcile_helpers import (
    ACCOUNT_A,
    ACCOUNT_B,
    PERIOD_A,
    PERIOD_B,
    feed_row,
    ledger_row,
    make_batch,
    make_policy,
)

POLICY = make_policy()


def run(
    feed_specs: list[dict] | None = None,
    system_specs: list[dict] | None = None,
    policy=POLICY,
):
    """One reconciliation from terse per-row descriptions."""
    feed_specs = [] if feed_specs is None else feed_specs
    system_specs = [] if system_specs is None else system_specs
    rows = [
        feed_row(
            2 + position,
            amount=spec.get("amount", "10.00"),
            unit=spec.get("unit", "EA"),
            period=spec.get("period", PERIOD_A),
        )
        for position, spec in enumerate(feed_specs)
    ]
    batch = make_batch(
        rows,
        outcomes=[spec.get("outcome", MatchOutcome.EXACT) for spec in feed_specs],
        accounts=[spec.get("account", ACCOUNT_A) for spec in feed_specs],
    )
    system = tuple(
        ledger_row(
            account_ref=spec.get("account", ACCOUNT_A),
            period=spec.get("period", PERIOD_A),
            unit=spec.get("unit", "EA"),
            amount_text=spec.get("amount", "10.00"),
            position=position,
        )
        for position, spec in enumerate(system_specs)
    )
    return reconcile([batch], system, policy)


def only(result):
    assert len(result.groups) == 1, [g.key.sort_key for g in result.groups]
    return result.groups[0]


# --- presence, decided first ---------------------------------------------------


def test_a_group_with_no_feed_contribution_is_missing_in_feed() -> None:
    group = only(run(system_specs=[{}]))
    assert group.outcome is ReconcileOutcome.MISSING_IN_FEED
    assert group.reason == REASON_MISSING_IN_FEED
    assert group.signed_difference is None
    assert group.absolute_difference is None
    assert group.variance_band is None


def test_a_group_with_no_system_contribution_is_missing_in_system() -> None:
    group = only(run(feed_specs=[{}]))
    assert group.outcome is ReconcileOutcome.MISSING_IN_SYSTEM
    assert group.reason == REASON_MISSING_IN_SYSTEM
    assert group.signed_difference is None


def test_a_present_side_totalling_zero_is_not_an_absent_side() -> None:
    """Presence is contribution count; a reported zero is a real report."""
    group = only(run(feed_specs=[{"amount": "0"}], system_specs=[{"amount": "0"}]))
    assert group.feed.is_present and group.system.is_present
    assert group.feed.total == group.system.total == Decimal(0)
    assert group.outcome is ReconcileOutcome.BALANCED


def test_one_side_absent_while_the_other_totals_zero_is_still_missing() -> None:
    group = only(run(feed_specs=[{"amount": "0"}]))
    assert group.outcome is ReconcileOutcome.MISSING_IN_SYSTEM
    assert group.feed.total == Decimal(0)
    assert group.system.total is None


def test_a_missing_side_outranks_a_unit_conflict() -> None:
    """Presence is settled before units are ever compared."""
    group = only(run(feed_specs=[{"unit": "EA"}, {"unit": "KG"}]))
    assert group.outcome is ReconcileOutcome.MISSING_IN_SYSTEM
    assert len(group.feed.units) == 2  # a conflict that never got to matter


# --- units, decided before arithmetic ------------------------------------------


def test_the_same_unit_on_both_sides_compares_normally() -> None:
    group = only(run(feed_specs=[{"unit": "KG"}], system_specs=[{"unit": "KG"}]))
    assert group.outcome is ReconcileOutcome.BALANCED


def test_different_units_across_sides_is_a_unit_mismatch() -> None:
    group = only(run(feed_specs=[{"unit": "EA"}], system_specs=[{"unit": "KG"}]))
    assert group.outcome is ReconcileOutcome.UNIT_MISMATCH
    assert group.reason == REASON_UNIT_DIFFERS_ACROSS_SIDES


def test_several_units_within_the_feed_side_is_a_unit_mismatch() -> None:
    group = only(
        run(feed_specs=[{"unit": "EA"}, {"unit": "KG"}], system_specs=[{"unit": "EA"}])
    )
    assert group.outcome is ReconcileOutcome.UNIT_MISMATCH
    assert group.reason == REASON_MULTIPLE_UNITS_ON_ONE_SIDE


def test_several_units_within_the_system_side_is_a_unit_mismatch() -> None:
    group = only(
        run(feed_specs=[{"unit": "EA"}], system_specs=[{"unit": "EA"}, {"unit": "BOX"}])
    )
    assert group.outcome is ReconcileOutcome.UNIT_MISMATCH
    assert group.reason == REASON_MULTIPLE_UNITS_ON_ONE_SIDE


def test_a_unit_conflict_publishes_no_variance_of_any_kind() -> None:
    group = only(
        run(
            feed_specs=[{"unit": "EA", "amount": "100"}],
            system_specs=[{"unit": "KG", "amount": "1"}],
        )
    )
    assert group.signed_difference is None
    assert group.absolute_difference is None
    assert group.variance_band is None
    assert group.feed.total is not None  # per-unit subtotals still stand
    assert dict(group.feed.totals_by_unit) == {"EA": Decimal(100)}
    assert dict(group.system.totals_by_unit) == {"KG": Decimal(1)}


def test_incompatible_units_are_never_summed_into_one_total() -> None:
    group = only(
        run(
            feed_specs=[{"unit": "EA", "amount": "3"}, {"unit": "KG", "amount": "4"}],
            system_specs=[{"unit": "EA", "amount": "7"}],
        )
    )
    assert group.feed.total is None
    assert dict(group.feed.totals_by_unit) == {"EA": Decimal(3), "KG": Decimal(4)}
    assert Decimal(7) not in group.feed.totals_by_unit.values()


def test_units_are_compared_verbatim_without_case_or_space_repair() -> None:
    group = only(run(feed_specs=[{"unit": "ea"}], system_specs=[{"unit": "EA"}]))
    assert group.outcome is ReconcileOutcome.UNIT_MISMATCH


# --- variance bands ------------------------------------------------------------


@pytest.mark.parametrize(
    ("feed_amount", "expected_outcome", "expected_band", "expected_reason"),
    [
        ("10.00", ReconcileOutcome.BALANCED, Decimal(0), REASON_TOTALS_EQUAL),
        ("10.0001", ReconcileOutcome.WITHIN_TOLERANCE, Decimal(1), REASON_WITHIN_VARIANCE_BAND),
        ("11.00", ReconcileOutcome.WITHIN_TOLERANCE, Decimal(1), REASON_WITHIN_VARIANCE_BAND),
        ("11.0001", ReconcileOutcome.WITHIN_TOLERANCE, Decimal(5), REASON_WITHIN_VARIANCE_BAND),
        ("15.00", ReconcileOutcome.WITHIN_TOLERANCE, Decimal(5), REASON_WITHIN_VARIANCE_BAND),
        ("15.0001", ReconcileOutcome.MISMATCH, None, REASON_OUTSIDE_VARIANCE_BANDS),
    ],
    ids=["0", "0.0001", "1", "1.0001", "5", "5.0001"],
)
def test_every_band_boundary_against_a_ten_unit_system_total(
    feed_amount: str,
    expected_outcome: ReconcileOutcome,
    expected_band: Decimal | None,
    expected_reason: str,
) -> None:
    group = only(run(feed_specs=[{"amount": feed_amount}], system_specs=[{}]))
    assert group.outcome is expected_outcome
    assert group.variance_band == expected_band
    assert group.reason == expected_reason


def test_the_signed_difference_is_feed_minus_system() -> None:
    over = only(run(feed_specs=[{"amount": "12.50"}], system_specs=[{"amount": "10.00"}]))
    under = only(run(feed_specs=[{"amount": "10.00"}], system_specs=[{"amount": "12.50"}]))
    assert over.signed_difference == Decimal("2.50")
    assert under.signed_difference == Decimal("-2.50")
    assert over.absolute_difference == under.absolute_difference == Decimal("2.50")


def test_a_negative_signed_difference_bands_the_same_as_a_positive_one() -> None:
    over = only(run(feed_specs=[{"amount": "16.00"}], system_specs=[{"amount": "10.00"}]))
    under = only(run(feed_specs=[{"amount": "10.00"}], system_specs=[{"amount": "16.00"}]))
    assert over.outcome is under.outcome is ReconcileOutcome.MISMATCH
    assert over.absolute_difference == under.absolute_difference == Decimal(6)


def test_both_sides_at_zero_are_balanced_rather_than_undefined() -> None:
    """No denominator exists anywhere, so a zero total is not a special case."""
    group = only(run(feed_specs=[{"amount": "0"}], system_specs=[{"amount": "0.00"}]))
    assert group.outcome is ReconcileOutcome.BALANCED
    assert group.signed_difference == Decimal(0)
    assert group.variance_band == Decimal(0)


def test_a_zero_system_total_against_a_feed_total_needs_no_division() -> None:
    group = only(run(feed_specs=[{"amount": "3.00"}], system_specs=[{"amount": "0"}]))
    assert group.outcome is ReconcileOutcome.WITHIN_TOLERANCE
    assert group.absolute_difference == Decimal(3)
    assert group.variance_band == Decimal(5)


# --- exact decimal arithmetic --------------------------------------------------


def test_sums_are_exact_rather_than_binary() -> None:
    """The classic float trap: 0.1 + 0.2 must be exactly 0.3 here."""
    group = only(
        run(
            feed_specs=[{"amount": "0.1"}, {"amount": "0.2"}],
            system_specs=[{"amount": "0.3"}],
        )
    )
    assert group.feed.total == Decimal("0.3")
    assert group.signed_difference == Decimal(0)
    assert group.outcome is ReconcileOutcome.BALANCED


def test_nothing_is_quantized_or_rounded_on_the_way_through() -> None:
    group = only(
        run(feed_specs=[{"amount": "10.123456789"}], system_specs=[{"amount": "10.00"}])
    )
    assert group.feed.total == Decimal("10.123456789")
    assert group.signed_difference == Decimal("0.123456789")


def test_very_large_amounts_stay_exact() -> None:
    big = "123456789012345678901234567890.12"
    group = only(run(feed_specs=[{"amount": big}], system_specs=[{"amount": big}]))
    assert group.feed.total == Decimal(big)
    assert group.outcome is ReconcileOutcome.BALANCED


def test_integer_and_decimal_notation_add_up_together() -> None:
    group = only(
        run(
            feed_specs=[{"amount": "1"}, {"amount": "2.50"}, {"amount": ".5"}],
            system_specs=[{"amount": "4.00"}],
        )
    )
    assert group.feed.total == Decimal("4.00")
    assert group.outcome is ReconcileOutcome.BALANCED


def test_a_sum_past_the_default_context_is_not_silently_truncated() -> None:
    """The default 28-digit context would have rounded this; ours must not."""
    from decimal import getcontext

    big = "123456789012345678901234567890.12"  # 32 significant digits
    assert getcontext().prec < 32
    group = only(run(feed_specs=[{"amount": big}], system_specs=[{"amount": "0.02"}]))
    assert group.feed.total == Decimal(big)
    assert group.signed_difference == Decimal("123456789012345678901234567890.10")


def test_a_valid_amount_is_never_rejected_for_having_many_digits() -> None:
    """No hidden precision ceiling: T10 accepts it, so T12 must total it."""
    long_amount = "0." + "7" * 300
    group = only(run(feed_specs=[{"amount": long_amount}], system_specs=[{"amount": long_amount}]))
    assert group.outcome is ReconcileOutcome.BALANCED
    assert group.feed.total == Decimal(long_amount)
    assert group.signed_difference == Decimal(0)


def test_repeated_runs_over_the_same_input_are_equal() -> None:
    specs = [{"amount": "1.10"}, {"amount": "2.20", "unit": "KG"}]
    first = run(feed_specs=specs, system_specs=[{"amount": "3.30"}])
    second = run(feed_specs=specs, system_specs=[{"amount": "3.30"}])
    assert first == second


# --- determinism ---------------------------------------------------------------


def test_groups_are_ordered_by_account_then_period() -> None:
    result = run(
        feed_specs=[
            {"account": ACCOUNT_B, "period": PERIOD_A},
            {"account": ACCOUNT_A, "period": PERIOD_B},
            {"account": ACCOUNT_A, "period": PERIOD_A},
        ]
    )
    assert [group.key.sort_key for group in result.groups] == [
        (ACCOUNT_A, PERIOD_A),
        (ACCOUNT_A, PERIOD_B),
        (ACCOUNT_B, PERIOD_A),
    ]


def test_shuffling_the_input_rows_changes_nothing() -> None:
    specs = [
        {"account": ACCOUNT_A, "period": PERIOD_A, "amount": "1.00"},
        {"account": ACCOUNT_B, "period": PERIOD_B, "amount": "2.00"},
        {"account": ACCOUNT_A, "period": PERIOD_B, "amount": "3.00"},
        {"account": ACCOUNT_A, "period": PERIOD_A, "amount": "4.00"},
    ]
    system = [{"account": ACCOUNT_A, "period": PERIOD_A, "amount": "5.00"}]
    baseline = run(feed_specs=specs, system_specs=system)
    shuffler = random.Random(12)
    for _ in range(10):
        shuffler.shuffle(specs)
        shuffler.shuffle(system)
        assert [g.key.sort_key for g in run(feed_specs=specs, system_specs=system).groups] == [
            g.key.sort_key for g in baseline.groups
        ]


def test_supplying_the_two_feeds_in_either_order_gives_the_same_result() -> None:
    """Both feeds are the FEED side, so which arrives first cannot matter."""
    alpha = make_batch(
        [feed_row(2, source="alpha.xlsx", amount="4.00")], source="alpha.xlsx"
    )
    beta = make_batch([feed_row(2, source="beta.csv", amount="6.00")], source="beta.csv")
    system = (ledger_row(amount_text="10.00"),)

    forward = reconcile([alpha, beta], system, POLICY)
    backward = reconcile([beta, alpha], system, POLICY)
    assert forward == backward
    assert only(forward).outcome is ReconcileOutcome.BALANCED
    assert only(forward).feed.total == Decimal("10.00")


def test_both_feeds_land_on_the_one_feed_side() -> None:
    alpha = make_batch([feed_row(2, source="alpha.xlsx")], source="alpha.xlsx")
    beta = make_batch([feed_row(2, source="beta.csv")], source="beta.csv")
    group = only(reconcile([alpha, beta], (ledger_row(),), POLICY))
    assert {c.side for c in group.feed.contributions} == {Side.FEED}
    assert {c.source for c in group.feed.contributions} == {"alpha.xlsx", "beta.csv"}
    assert group.system.contributions[0].side is Side.SYSTEM


def test_contributions_are_ordered_by_provenance_not_arrival() -> None:
    alpha = make_batch(
        [feed_row(9, source="alpha.xlsx"), feed_row(3, source="alpha.xlsx")],
        source="alpha.xlsx",
    )
    beta = make_batch([feed_row(5, source="beta.csv")], source="beta.csv")
    group = only(reconcile([beta, alpha], (ledger_row(),), POLICY))
    assert [(c.source, c.source_row) for c in group.feed.contributions] == [
        ("alpha.xlsx", 3),
        ("alpha.xlsx", 9),
        ("beta.csv", 5),
    ]


def test_unresolved_rows_are_ordered_deterministically() -> None:
    alpha = make_batch(
        [feed_row(7, source="alpha.xlsx"), feed_row(2, source="alpha.xlsx")],
        source="alpha.xlsx",
        outcomes=[MatchOutcome.UNMATCHED, MatchOutcome.NEEDS_REVIEW],
        accounts=[None, None],
    )
    beta = make_batch(
        [feed_row(4, source="beta.csv")],
        source="beta.csv",
        outcomes=[MatchOutcome.INELIGIBLE],
        accounts=[None],
    )
    result = reconcile([beta, alpha], (), POLICY)
    assert [(row.source, row.source_row) for row in result.unresolved] == [
        ("alpha.xlsx", 2),
        ("alpha.xlsx", 7),
        ("beta.csv", 4),
    ]


# --- the batch result ----------------------------------------------------------


def test_a_mixed_batch_reports_every_outcome_it_produced() -> None:
    result = run(
        feed_specs=[
            {"period": PERIOD_A, "amount": "10.00"},            # balanced
            {"period": PERIOD_B, "amount": "10.50"},            # within tolerance
            {"account": ACCOUNT_B, "amount": "99.00"},          # mismatch
            {"account": ACCOUNT_B, "period": PERIOD_B, "unit": "KG"},  # unit mismatch
        ],
        system_specs=[
            {"period": PERIOD_A, "amount": "10.00"},
            {"period": PERIOD_B, "amount": "10.00"},
            {"account": ACCOUNT_B, "amount": "10.00"},
            {"account": ACCOUNT_B, "period": PERIOD_B, "unit": "EA"},
            {"account": ACCOUNT_B, "period": "2026-03", "amount": "1.00"},  # missing in feed
        ],
    )
    assert dict(result.summary.groups_by_outcome) == {
        "balanced": 1,
        "within_tolerance": 1,
        "mismatch": 1,
        "unit_mismatch": 1,
        "missing_in_feed": 1,
    }
    assert result.summary.group_count == 5
    assert sum(result.summary.groups_by_reason.values()) == 5
    assert result.summary.accounting_holds


def test_every_group_carries_exactly_one_outcome_and_one_reason() -> None:
    result = run(
        feed_specs=[{"amount": "1"}, {"account": ACCOUNT_B, "unit": "KG"}],
        system_specs=[{"amount": "99"}, {"account": ACCOUNT_B, "unit": "EA"}],
    )
    for group in result.groups:
        assert isinstance(group.outcome, ReconcileOutcome)
        assert isinstance(group.reason, str) and group.message
    assert len({(g.key, g.outcome) for g in result.groups}) == len(result.groups)


def test_a_group_key_appears_at_most_once() -> None:
    result = run(
        feed_specs=[{}, {}, {"period": PERIOD_B}],
        system_specs=[{}, {"period": PERIOD_B}],
    )
    keys = [group.key for group in result.groups]
    assert len(keys) == len(set(keys))
    assert set(keys) == {GroupKey(ACCOUNT_A, PERIOD_A), GroupKey(ACCOUNT_A, PERIOD_B)}


# =============================================================================
# exact arithmetic with no precision ceiling
# =============================================================================


@pytest.mark.parametrize("digits", [101, 150, 300])
def test_amounts_of_any_length_total_exactly(digits: int) -> None:
    """T10 caps no number of digits, so neither may T12."""
    amount = "0." + "1" * digits
    group = only(run(feed_specs=[{"amount": amount}], system_specs=[{"amount": amount}]))
    assert group.feed.total == Decimal(amount)
    assert group.system.total == Decimal(amount)
    assert group.signed_difference == Decimal(0)
    assert group.outcome is ReconcileOutcome.BALANCED


def test_two_valid_amounts_whose_exact_sum_outgrows_both() -> None:
    """Each operand is short; only their sum spans many digits.

    The expected value is written out literally: computing it with ``+`` would
    round it under the default twenty-eight-digit context, which is the very
    thing this test exists to rule out.
    """
    first, second = "0." + "1" * 60, "0." + "0" * 60 + "1" * 60
    group = only(
        run(feed_specs=[{"amount": first}, {"amount": second}], system_specs=[{"amount": "0"}])
    )
    assert group.feed.total == Decimal("0." + "1" * 120)
    assert Decimal(first) + Decimal(second) != Decimal("0." + "1" * 120)


def test_a_sum_that_carries_into_an_additional_digit() -> None:
    group = only(
        run(
            feed_specs=[{"amount": "0." + "9" * 120}, {"amount": "0." + "0" * 119 + "1"}],
            system_specs=[{"amount": "1.00"}],
        )
    )
    assert group.feed.total == Decimal(1)
    assert group.signed_difference == Decimal(0)
    assert group.outcome is ReconcileOutcome.BALANCED


def test_exact_cancellation_to_zero_across_long_amounts() -> None:
    amount = "0." + "9" * 150
    group = only(run(feed_specs=[{"amount": amount}], system_specs=[{"amount": amount}]))
    assert group.signed_difference == Decimal(0)
    assert group.absolute_difference == Decimal(0)
    assert group.variance_band == Decimal(0)
    assert group.outcome is ReconcileOutcome.BALANCED


def test_a_long_negative_signed_difference_stays_exact() -> None:
    """``1e-151 - 1`` is a hundred and fifty-one nines, every one of them kept."""
    group = only(
        run(feed_specs=[{"amount": "0." + "0" * 150 + "1"}], system_specs=[{"amount": "1"}])
    )
    assert group.signed_difference == Decimal("-0." + "9" * 151)
    assert group.signed_difference < 0
    assert group.absolute_difference == Decimal("0." + "9" * 151)
    assert group.outcome is ReconcileOutcome.WITHIN_TOLERANCE


def test_many_short_rows_whose_total_grows_in_precision() -> None:
    """Every contribution is three digits; the total spans a hundred and one."""
    amounts = ["0." + "0" * offset + "1" for offset in range(0, 130, 10)]
    group = only(
        run(
            feed_specs=[{"amount": amount} for amount in amounts],
            system_specs=[{"amount": "0"}],
        )
    )
    expected = Decimal(0)
    for amount in amounts:
        expected += Decimal(amount)  # under the default context this would round
    assert group.feed.total == exact_sum([Decimal(a) for a in amounts])
    assert len(group.feed.total.as_tuple().digits) > 100
    assert group.feed.total != expected  # the default context truly does round


def test_long_arithmetic_is_repeatable() -> None:
    specs = [{"amount": "0." + "3" * 200}, {"amount": "0." + "7" * 180}]
    system = [{"amount": "0." + "1" * 250}]
    assert run(feed_specs=specs, system_specs=system) == run(
        feed_specs=specs, system_specs=system
    )


# --- independence from the process-wide decimal context ------------------------


@contextmanager
def global_context(prec: int, rounding: str):
    """Bend the process-wide decimal context, then put it back."""
    saved = decimal.getcontext()
    decimal.setcontext(decimal.Context(prec=prec, rounding=rounding))
    try:
        yield
    finally:
        decimal.setcontext(saved)


def test_results_do_not_change_with_the_global_precision() -> None:
    specs = [{"amount": "0." + "4" * 120}, {"amount": "0." + "5" * 120}]
    system = [{"amount": "0.99"}]
    baseline = run(feed_specs=specs, system_specs=system)
    for prec in (1, 3, 28, 400):
        with global_context(prec, decimal.ROUND_HALF_EVEN):
            assert run(feed_specs=specs, system_specs=system) == baseline


def test_results_do_not_change_with_the_global_rounding_mode() -> None:
    specs = [{"amount": "10.005"}, {"amount": "0.004"}]
    system = [{"amount": "10.009"}]
    baseline = run(feed_specs=specs, system_specs=system)
    assert baseline.groups[0].outcome is ReconcileOutcome.BALANCED
    for rounding in (
        decimal.ROUND_UP, decimal.ROUND_DOWN, decimal.ROUND_CEILING,
        decimal.ROUND_FLOOR, decimal.ROUND_05UP,
    ):
        with global_context(3, rounding):
            assert run(feed_specs=specs, system_specs=system) == baseline


def test_the_package_declares_no_precision_constant_any_more() -> None:
    """The hidden ceiling is gone from the public surface as well."""
    import las.reconcile as package

    assert not hasattr(package, "RECONCILE_CONTEXT")
    assert not hasattr(package, "RECONCILE_PRECISION")
    assert not any("PRECISION" in name or "CONTEXT" in name for name in package.__all__)
