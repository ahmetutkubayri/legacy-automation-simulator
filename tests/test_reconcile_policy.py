"""T12 policy: the variance ladder, its bounds, and its boundary semantics."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from las.config import ReportConfig, load_config
from las.reconcile import (
    GROUP_KEY_FIELDS,
    REQUIRED_FIRST_BAND,
    ReconcilePolicy,
    ReconcilePolicyError,
    VarianceBandError,
    policy_from_config,
    to_decimal_bound,
)
from reconcile_helpers import SAMPLE_BANDS, make_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"


# --- construction from configuration ------------------------------------------


def test_the_committed_configuration_builds_the_sample_ladder() -> None:
    config = load_config(SAMPLES_DIR / "config.yaml")
    policy = policy_from_config(config.report)
    assert policy.variance_bands == SAMPLE_BANDS
    assert policy.largest_band == Decimal(5)


def test_configured_floats_convert_exactly_rather_than_through_binary() -> None:
    """``0.1`` must become the decimal the author wrote, not the double."""
    policy = policy_from_config(ReportConfig(variance_bands=[0.0, 0.1, 0.3]))
    assert policy.variance_bands == (Decimal("0"), Decimal("0.1"), Decimal("0.3"))
    assert policy.variance_bands[1] == Decimal("0.1")
    assert str(policy.variance_bands[2]) == "0.3"


def test_bounds_convert_from_text_and_integers_too() -> None:
    assert to_decimal_bound(1, "probe") == Decimal(1)
    assert to_decimal_bound("2.5", "probe") == Decimal("2.5")


@pytest.mark.parametrize("value", ["not-a-number", "", None, [1]])
def test_an_unusable_bound_is_rejected(value: object) -> None:
    with pytest.raises(VarianceBandError, match="not a usable decimal bound"):
        to_decimal_bound(value, "probe")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
def test_a_non_finite_bound_is_rejected(value: str) -> None:
    with pytest.raises(VarianceBandError, match="finite"):
        to_decimal_bound(value, "probe")


def test_an_override_replaces_the_configured_ladder() -> None:
    config = load_config(SAMPLES_DIR / "config.yaml")
    policy = policy_from_config(config.report, variance_bands=[0.0, 2.0])
    assert policy.variance_bands == (Decimal(0), Decimal(2))


# --- ladder validity ----------------------------------------------------------


def test_the_first_band_must_be_exactly_zero() -> None:
    assert Decimal(0) == REQUIRED_FIRST_BAND
    with pytest.raises(VarianceBandError, match="first variance band must be exactly zero"):
        make_policy((Decimal(1), Decimal(5)))


def test_bands_must_ascend_strictly() -> None:
    for bands in (
        (Decimal(0), Decimal(5), Decimal(1)),
        (Decimal(0), Decimal(1), Decimal(1)),
    ):
        with pytest.raises(VarianceBandError, match="ascend strictly"):
            make_policy(bands)


def test_a_negative_band_is_rejected() -> None:
    with pytest.raises(VarianceBandError, match="not be negative"):
        make_policy((Decimal(0), Decimal(-1)))


def test_a_non_finite_band_is_rejected() -> None:
    with pytest.raises(VarianceBandError, match="finite"):
        make_policy((Decimal(0), Decimal("Infinity")))


def test_an_empty_ladder_is_rejected() -> None:
    with pytest.raises(VarianceBandError, match="at least one"):
        make_policy(())


@pytest.mark.parametrize("value", [0.0, 1, "1"])
def test_a_band_that_is_not_an_exact_decimal_is_rejected(value: object) -> None:
    """Floats never reach the ladder; conversion happens once, up front."""
    with pytest.raises(VarianceBandError, match="exact decimals"):
        ReconcilePolicy(variance_bands=(value,))  # type: ignore[arg-type]


def test_a_single_zero_band_means_only_exact_equality_is_tolerated() -> None:
    policy = make_policy((Decimal(0),))
    assert policy.band_for(Decimal(0)) == Decimal(0)
    assert policy.band_for(Decimal("0.0001")) is None


# --- boundary semantics -------------------------------------------------------


@pytest.mark.parametrize(
    ("difference", "expected"),
    [
        ("0", Decimal(0)),
        ("0.0001", Decimal(1)),
        ("1", Decimal(1)),
        ("1.0001", Decimal(5)),
        ("5", Decimal(5)),
        ("5.0001", None),
        ("1000000", None),
    ],
)
def test_every_band_boundary_for_the_sample_ladder(
    difference: str, expected: Decimal | None
) -> None:
    """Upper edges are inclusive; only past the largest bound is there no band."""
    assert make_policy().band_for(Decimal(difference)) == expected


def test_a_band_is_chosen_from_an_absolute_difference_only() -> None:
    with pytest.raises(ReconcilePolicyError, match="absolute difference"):
        make_policy().band_for(Decimal("-1"))


def test_band_selection_is_exact_rather_than_approximate() -> None:
    """A hair over an edge falls to the next band, not into it."""
    policy = make_policy()
    assert policy.band_for(Decimal("1.0000000000000000001")) == Decimal(5)
    assert policy.band_for(Decimal("5.0000000000000000001")) is None


# --- immutability and equality ------------------------------------------------


def test_the_policy_copies_its_caller_sequence() -> None:
    bands = [Decimal(0), Decimal(1)]
    policy = ReconcilePolicy(variance_bands=bands)
    bands.append(Decimal(9))
    assert policy.variance_bands == (Decimal(0), Decimal(1))
    assert isinstance(policy.variance_bands, tuple)


def test_the_policy_cannot_be_mutated() -> None:
    policy = make_policy()
    with pytest.raises(FrozenInstanceError):
        policy.variance_bands = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        policy.variance_bands.append(Decimal(9))  # type: ignore[attr-defined]


def test_equal_ladders_give_equal_policies() -> None:
    assert make_policy() == make_policy()
    assert make_policy() != make_policy((Decimal(0), Decimal(2)))


def test_the_group_key_order_is_declared_and_immutable() -> None:
    assert GROUP_KEY_FIELDS == ("account_ref", "period")
    assert isinstance(GROUP_KEY_FIELDS, tuple)
    assert "unit" not in GROUP_KEY_FIELDS
