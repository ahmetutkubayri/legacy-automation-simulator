"""The clarified documentation and the runtime policy must say the same thing.

The variance-band contract lives in three places — the development plan, the
``ReportConfig.variance_bands`` description, and the reconciliation policy that
actually decides. Documentation that drifts from behaviour is worse than none,
so these tests read the prose and check the code agrees with it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from las.config import ReportConfig, load_config
from las.reconcile import (
    GROUP_KEY_FIELDS,
    ReconcileOutcome,
    Side,
    policy_from_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
PLAN = (REPO_ROOT / "docs" / "DEVELOPMENT_PLAN.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def t12_clause() -> str:
    """The plan's T12 bullet, from its heading to the next bullet."""
    start = PLAN.index("**T12 Reconciliation**")
    end = PLAN.index("* **T13 Reporting**", start)
    return PLAN[start:end]


@pytest.fixture(scope="module")
def band_description() -> str:
    field = ReportConfig.model_fields["variance_bands"]
    assert field.description, "variance_bands must document its own meaning"
    return field.description + " " + (ReportConfig.__doc__ or "")


# --- the plan states the approved model ---------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "both feeds",
        "FEED",
        "SYSTEM",
        "SystemLedgerRow",
        "account_ref",
        "period",
        "T14",
        "duplicate",
        "inclusive",
    ],
)
def test_the_plan_states_each_part_of_the_contract(t12_clause: str, phrase: str) -> None:
    assert phrase in t12_clause


def test_the_plan_says_the_feeds_are_one_side_not_two(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "not opposing sides" in flat
    assert "two sources contributing to the same side" in flat
    assert "LEFT" not in flat and "RIGHT" not in flat


def test_the_plan_states_the_neutral_boundary_and_the_t14_adapter(
    t12_clause: str,
) -> None:
    flat = " ".join(t12_clause.split())
    assert "neutral immutable `SystemLedgerRow` DTO" in flat
    assert "imports neither the driver nor the simulator" in flat
    assert "T14 owns the adapter" in flat
    assert "public driver `ExtractedRow` values" in flat
    assert "existing master index" in flat


def test_the_plan_states_the_group_key_and_excludes_unit(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "`account_ref` **and** `period`" in flat
    assert "never with `unit` in the key" in flat
    assert "never by account alone" in flat


def test_the_plan_states_units_come_before_arithmetic(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "presence, then units, then arithmetic" in flat
    assert "publish no variance" in flat


def test_the_plan_states_duplicates_are_retained(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "Duplicate rows each contribute" in flat
    assert "nothing is deduplicated" in flat.lower()


def test_the_plan_states_absolute_decimal_bands(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "absolute amount differences" in flat
    assert "not percentages" in flat
    assert "no division" in flat
    assert "Decimal(str(value))" in flat
    assert "inclusive" in flat


def test_the_plan_states_the_accounting_invariant(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "accounted for exactly once" in flat


# --- the config description says the same thing --------------------------------


def test_the_config_description_calls_the_bands_absolute(band_description: str) -> None:
    assert "Absolute amount-difference upper bounds" in band_description
    assert "Not percentages" in band_description or "not percentages" in band_description


def test_the_config_description_states_inclusive_upper_edges(
    band_description: str,
) -> None:
    assert "inclusive" in band_description.lower()
    assert "ascending from zero" in band_description.lower()


def test_the_config_docstring_works_through_the_sample_ladder(
    band_description: str,
) -> None:
    for phrase in ("0 < d <= 1", "1 < d <= 5", "d > 5", "balanced", "mismatch"):
        assert phrase in band_description


def test_the_config_description_mentions_no_denominator(band_description: str) -> None:
    assert "no denominator" in band_description.lower()
    assert "percent of" not in band_description.lower()


# --- the runtime agrees with both ----------------------------------------------


def test_the_documented_field_name_and_defaults_are_unchanged() -> None:
    """The contract was clarified; nothing about the field itself moved."""
    assert "variance_bands" in ReportConfig.model_fields
    assert ReportConfig().variance_bands == [0.0, 1.0, 5.0]
    assert load_config(SAMPLES_DIR / "config.yaml").report.variance_bands == [
        0.0,
        1.0,
        5.0,
    ]


def test_the_runtime_ladder_matches_the_documented_worked_example() -> None:
    """Exactly the table the docstring prints, evaluated for real."""
    policy = policy_from_config(load_config(SAMPLES_DIR / "config.yaml").report)
    assert policy.band_for(Decimal("0")) == Decimal(0)          # balanced
    assert policy.band_for(Decimal("0.5")) == Decimal(1)        # 0 < d <= 1
    assert policy.band_for(Decimal("1")) == Decimal(1)          # inclusive edge
    assert policy.band_for(Decimal("3")) == Decimal(5)          # 1 < d <= 5
    assert policy.band_for(Decimal("5")) == Decimal(5)          # inclusive edge
    assert policy.band_for(Decimal("5.01")) is None             # d > 5, mismatch


def test_the_runtime_group_key_matches_the_documented_one() -> None:
    assert GROUP_KEY_FIELDS == ("account_ref", "period")


def test_the_runtime_sides_match_the_documented_ones() -> None:
    assert {side.name for side in Side} == {"FEED", "SYSTEM"}


def test_the_runtime_outcomes_match_the_documented_precedence() -> None:
    documented = ["missing", "unit", "balanced", "tolerance", "mismatch"]
    values = " ".join(outcome.value for outcome in ReconcileOutcome)
    for word in documented:
        assert word in values


def test_the_plan_still_documents_the_four_fixture_hashes() -> None:
    """The clarification must not have disturbed the determinism section."""
    import re

    hashes = re.findall(r"^([0-9a-f]{64})\s+data/samples/(\S+)$", PLAN, re.MULTILINE)
    assert len(hashes) == 4
    assert {name for _, name in hashes} == {
        "accounts.csv",
        "account_links.csv",
        "feed_alpha.xlsx",
        "feed_beta.csv",
    }


# --- the clarified replay and precision wording --------------------------------


def test_the_plan_separates_business_duplicates_from_replay(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "two real observations" in flat
    assert "replay" in flat
    assert "refused before aggregation" in flat
    assert "never part of the business group key" in flat


def test_the_plan_states_arithmetic_has_no_precision_ceiling(t12_clause: str) -> None:
    flat = " ".join(t12_clause.split())
    assert "no precision ceiling" in flat
    assert "every amount the T10 parser accepts is aggregatable" in flat
    assert "unbounded integer coefficients" in flat
    assert "process-wide decimal context" in flat


def test_the_runtime_matches_the_documented_replay_rule() -> None:
    """Business duplicate accepted, physical replay refused."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from las.reconcile import DuplicateInputIdentityError, reconcile
    from reconcile_helpers import feed_row, ledger_row, make_batch, make_policy

    policy = make_policy()
    duplicates = reconcile(
        [make_batch([feed_row(2, amount="1.00"), feed_row(3, amount="1.00")])],
        (ledger_row(amount_text="2.00"),),
        policy,
    )
    assert len(duplicates.groups[0].feed.contributions) == 2

    row = feed_row(2)
    with pytest.raises(DuplicateInputIdentityError):
        reconcile([make_batch([row, row])], (ledger_row(),), policy)


def test_the_runtime_matches_the_documented_precision_rule() -> None:
    from decimal import Decimal

    from las.reconcile import exact_sum

    long_value = Decimal("0." + "6" * 400)
    assert exact_sum([long_value, Decimal(0)]) == long_value
