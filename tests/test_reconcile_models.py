"""T12 models: the stable vocabulary, the neutral DTO, and deep immutability."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

from las.reconcile import (
    MATCH_REASONS,
    MESSAGE_BY_REASON,
    OUTCOME_RANK,
    SIDE_RANK,
    VALIDATION_REASONS,
    BatchReconcileResult,
    Contribution,
    GroupKey,
    GroupResult,
    LedgerRowError,
    ReconcileOutcome,
    ReconcileSummary,
    Side,
    SideAggregate,
    SystemLedgerRow,
    UnresolvedRow,
    summarize,
)
from reconcile_helpers import ACCOUNT_A, ACCOUNT_B, PERIOD_A, PERIOD_B, ledger_row


def contribution(
    side: Side = Side.FEED,
    amount: str = "10.50",
    unit: str = "EA",
    source: str = "probe.csv",
    source_row: int = 2,
    row_index: int = 0,
) -> Contribution:
    return Contribution(
        side=side,
        amount=Decimal(amount),
        unit=unit,
        source=source,
        source_row=source_row,
        row_index=row_index,
    )


# --- vocabulary ---------------------------------------------------------------


def test_there_are_exactly_two_neutral_sides() -> None:
    assert [side.value for side in Side] == ["feed", "system"]
    assert SIDE_RANK[Side.FEED] < SIDE_RANK[Side.SYSTEM]


def test_no_side_is_named_left_or_right() -> None:
    """Feed alpha and beta share the FEED side; there are no opposing feeds."""
    names = {side.name for side in Side} | {side.value for side in Side}
    assert not {"LEFT", "RIGHT", "left", "right"} & names


def test_the_outcome_set_is_exactly_the_approved_one() -> None:
    assert [outcome.value for outcome in ReconcileOutcome] == [
        "missing_in_feed",
        "missing_in_system",
        "unit_mismatch",
        "balanced",
        "within_tolerance",
        "mismatch",
    ]


def test_outcome_rank_encodes_the_approved_precedence() -> None:
    ordered = sorted(ReconcileOutcome, key=lambda outcome: OUTCOME_RANK[outcome])
    assert ordered == [
        ReconcileOutcome.MISSING_IN_FEED,
        ReconcileOutcome.MISSING_IN_SYSTEM,
        ReconcileOutcome.UNIT_MISMATCH,
        ReconcileOutcome.BALANCED,
        ReconcileOutcome.WITHIN_TOLERANCE,
        ReconcileOutcome.MISMATCH,
    ]


def test_every_reason_has_a_fixed_message_and_no_message_quotes_data() -> None:
    for reason, message in MESSAGE_BY_REASON.items():
        assert reason.islower() and " " not in reason
        assert message and not any(token in message for token in ("{", "}", "%s"))


def test_match_reasons_and_validation_reasons_stay_separate() -> None:
    """Which stage a row stalled at must never be blurred."""
    assert not set(MATCH_REASONS) & set(VALIDATION_REASONS)
    assert set(MATCH_REASONS) | set(VALIDATION_REASONS) <= set(MESSAGE_BY_REASON)


def test_the_vocabulary_mappings_are_read_only() -> None:
    for mapping in (MESSAGE_BY_REASON, SIDE_RANK, OUTCOME_RANK):
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping["injected"] = 1  # type: ignore[index]


# --- the group key ------------------------------------------------------------


def test_the_group_key_is_account_and_period_only() -> None:
    fields = {field.name for field in dataclasses.fields(GroupKey)}
    assert fields == {"account_ref", "period"}
    assert "unit" not in fields


def test_the_group_key_orders_by_account_then_period() -> None:
    keys = [
        GroupKey(ACCOUNT_B, PERIOD_A),
        GroupKey(ACCOUNT_A, PERIOD_B),
        GroupKey(ACCOUNT_A, PERIOD_A),
    ]
    assert [k.sort_key for k in sorted(keys, key=lambda k: k.sort_key)] == [
        (ACCOUNT_A, PERIOD_A),
        (ACCOUNT_A, PERIOD_B),
        (ACCOUNT_B, PERIOD_A),
    ]


def test_the_group_key_keeps_its_values_verbatim() -> None:
    """No trimming, no case correction — two different keys stay different."""
    assert GroupKey(" ACC-100000", PERIOD_A) != GroupKey("ACC-100000", PERIOD_A)
    assert GroupKey("acc-100000", PERIOD_A) != GroupKey("ACC-100000", PERIOD_A)


@pytest.mark.parametrize(("account", "period"), [("", PERIOD_A), (ACCOUNT_A, "")])
def test_a_blank_key_component_is_refused(account: str, period: str) -> None:
    with pytest.raises(LedgerRowError):
        GroupKey(account, period)


def test_the_group_key_is_frozen_and_hashable() -> None:
    key = GroupKey(ACCOUNT_A, PERIOD_A)
    assert {key: 1}[GroupKey(ACCOUNT_A, PERIOD_A)] == 1
    with pytest.raises(FrozenInstanceError):
        key.account_ref = ACCOUNT_B  # type: ignore[misc]


# --- the neutral system DTO ---------------------------------------------------


def test_the_ledger_row_carries_only_safe_reconciliation_fields() -> None:
    fields = {field.name for field in dataclasses.fields(SystemLedgerRow)}
    assert fields == {
        "account_ref",
        "period",
        "unit",
        "amount_text",
        "position",
        "record_id",
        "external_ref",
    }
    for forbidden in ("row", "record", "store", "screen", "driver", "path", "name"):
        assert not any(forbidden == field for field in fields)


def test_the_ledger_row_parses_its_amount_exactly_and_keeps_the_text() -> None:
    row = ledger_row(amount_text="105.90")
    assert row.amount == Decimal("105.90")
    assert row.amount_text == "105.90"
    assert isinstance(row.amount, Decimal)


def test_the_ledger_row_amount_never_passes_through_a_float() -> None:
    """A value no binary double can hold survives to the last digit."""
    row = ledger_row(amount_text="0.1")
    assert row.amount == Decimal("0.1")
    assert row.amount != Decimal(0.1)  # noqa: E721 - the point of the test


@pytest.mark.parametrize(
    "amount",
    ["", " ", "not-a-number", "1_000", "1 000", "1,000", "１0", "nan", "inf", "1e3", "1.2.3"],
)
def test_the_ledger_row_refuses_anything_but_plain_decimal_text(amount: str) -> None:
    with pytest.raises(LedgerRowError, match="plain decimal text|missing a required value"):
        ledger_row(amount_text=amount)


@pytest.mark.parametrize("field", ["account_ref", "period", "unit", "amount_text"])
def test_a_blank_ledger_field_is_refused_at_construction(field: str) -> None:
    with pytest.raises(LedgerRowError) as excinfo:
        ledger_row(**{field: "   "})  # type: ignore[arg-type]
    assert excinfo.value.field == field
    assert excinfo.value.side == "system"


@pytest.mark.parametrize("position", [-1, 1.5, "0", True, None])
def test_an_unusable_ledger_position_is_refused(position: object) -> None:
    with pytest.raises(LedgerRowError, match="position"):
        ledger_row(position=position)  # type: ignore[arg-type]


def test_a_ledger_error_never_quotes_the_offending_value() -> None:
    with pytest.raises(LedgerRowError) as excinfo:
        ledger_row(amount_text="a-secret-value-12345")
    assert "a-secret-value" not in str(excinfo.value)


def test_the_ledger_row_is_frozen() -> None:
    row = ledger_row()
    with pytest.raises(FrozenInstanceError):
        row.amount_text = "99.99"  # type: ignore[misc]


# --- aggregates ---------------------------------------------------------------


def test_a_side_aggregate_sorts_its_contributions_and_freezes_them() -> None:
    aggregate = SideAggregate(
        side=Side.FEED,
        contributions=[
            contribution(source_row=9, row_index=2),
            contribution(source_row=2, row_index=0),
        ],
        totals_by_unit={"EA": Decimal("21.00")},
    )
    assert [c.source_row for c in aggregate.contributions] == [2, 9]
    assert isinstance(aggregate.contributions, tuple)
    with pytest.raises(AttributeError):
        aggregate.contributions.append(contribution())  # type: ignore[attr-defined]


def test_a_side_aggregate_copies_and_freezes_its_totals() -> None:
    totals = {"KG": Decimal(2), "EA": Decimal(1)}
    aggregate = SideAggregate(
        side=Side.FEED, contributions=(contribution(),), totals_by_unit=totals
    )
    totals["BOX"] = Decimal(99)
    assert dict(aggregate.totals_by_unit) == {"EA": Decimal(1), "KG": Decimal(2)}
    assert isinstance(aggregate.totals_by_unit, MappingProxyType)
    with pytest.raises(TypeError):
        aggregate.totals_by_unit["BOX"] = Decimal(1)  # type: ignore[index]


def test_units_are_reported_in_one_deterministic_order() -> None:
    forward = SideAggregate(side=Side.FEED, totals_by_unit={"KG": Decimal(1), "EA": Decimal(2)})
    reverse = SideAggregate(side=Side.FEED, totals_by_unit={"EA": Decimal(2), "KG": Decimal(1)})
    assert forward.units == reverse.units == ("EA", "KG")


def test_a_total_exists_only_when_one_unit_is_reported() -> None:
    one = SideAggregate(
        side=Side.FEED, contributions=(contribution(),), totals_by_unit={"EA": Decimal(5)}
    )
    two = SideAggregate(
        side=Side.FEED,
        contributions=(contribution(), contribution(unit="KG")),
        totals_by_unit={"EA": Decimal(5), "KG": Decimal(3)},
    )
    assert one.total == Decimal(5)
    assert two.total is None


def test_presence_is_contribution_count_and_never_total_value() -> None:
    """A side that reported zero is present; a side that reported nothing is not."""
    zero = SideAggregate(
        side=Side.FEED,
        contributions=(contribution(amount="0"),),
        totals_by_unit={"EA": Decimal(0)},
    )
    absent = SideAggregate(side=Side.FEED)
    assert zero.is_present and zero.total == Decimal(0)
    assert not absent.is_present and absent.total is None


def test_contribution_order_ignores_input_sequence_position() -> None:
    """Two sources interleave by provenance, not by which batch came first."""
    rows = [
        contribution(source="beta.csv", source_row=3, row_index=1),
        contribution(source="alpha.xlsx", source_row=5, row_index=3),
    ]
    forward = SideAggregate(side=Side.FEED, contributions=rows)
    reverse = SideAggregate(side=Side.FEED, contributions=list(reversed(rows)))
    assert forward.contributions == reverse.contributions
    assert [c.source for c in forward.contributions] == ["alpha.xlsx", "beta.csv"]


# --- results and summaries ----------------------------------------------------


def group(outcome: ReconcileOutcome, reason: str, key: GroupKey | None = None) -> GroupResult:
    return GroupResult(
        key=key or GroupKey(ACCOUNT_A, PERIOD_A),
        feed=SideAggregate(side=Side.FEED, contributions=(contribution(),),
                           totals_by_unit={"EA": Decimal(1)}),
        system=SideAggregate(side=Side.SYSTEM,
                             contributions=(contribution(side=Side.SYSTEM),),
                             totals_by_unit={"EA": Decimal(1)}),
        outcome=outcome,
        reason=reason,
    )


def test_a_group_result_exposes_its_fixed_message() -> None:
    result = group(ReconcileOutcome.BALANCED, "totals_equal")
    assert result.message == MESSAGE_BY_REASON["totals_equal"]
    assert result.contribution_count == 2


def test_a_group_result_sorts_by_its_key() -> None:
    groups = [
        group(ReconcileOutcome.BALANCED, "totals_equal", GroupKey(ACCOUNT_B, PERIOD_A)),
        group(ReconcileOutcome.BALANCED, "totals_equal", GroupKey(ACCOUNT_A, PERIOD_B)),
    ]
    assert [g.sort_key for g in sorted(groups, key=lambda g: g.sort_key)] == [
        (ACCOUNT_A, PERIOD_B),
        (ACCOUNT_B, PERIOD_A),
    ]


def test_a_batch_result_freezes_its_sequences() -> None:
    groups = [group(ReconcileOutcome.BALANCED, "totals_equal")]
    unresolved: list[UnresolvedRow] = []
    result = BatchReconcileResult(
        groups=groups,
        unresolved=unresolved,
        summary=summarize(tuple(groups), (), feed_rows_in=1, system_rows_in=1),
    )
    groups.append(group(ReconcileOutcome.MISMATCH, "outside_variance_bands"))
    assert result.group_count == 1
    assert isinstance(result.groups, tuple)
    with pytest.raises(AttributeError):
        result.groups.append(groups[0])  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        result.summary = None  # type: ignore[misc]


def test_summary_mappings_are_sorted_copies_and_read_only() -> None:
    summary = ReconcileSummary(
        group_count=0,
        groups_by_outcome={"mismatch": 1, "balanced": 2},
        groups_by_reason={},
        contributions_by_side={"system": 1, "feed": 2},
        unresolved_by_reason={},
        feed_rows_in=2,
        system_rows_in=1,
    )
    assert list(summary.groups_by_outcome) == ["balanced", "mismatch"]
    assert list(summary.contributions_by_side) == ["feed", "system"]
    for name in ("groups_by_outcome", "contributions_by_side"):
        mapping = getattr(summary, name)
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping["injected"] = 1  # type: ignore[index]


def test_the_accounting_invariant_is_computed_not_asserted() -> None:
    summary = ReconcileSummary(
        group_count=1,
        groups_by_outcome={},
        groups_by_reason={},
        contributions_by_side={"feed": 3, "system": 4},
        unresolved_by_reason={"match_unmatched": 2},
        feed_rows_in=5,
        system_rows_in=4,
    )
    assert summary.feed_rows_accounted == 5
    assert summary.accounting_holds
    assert not dataclasses.replace(summary, feed_rows_in=6).accounting_holds
    assert not dataclasses.replace(summary, system_rows_in=9).accounting_holds


def test_with_outcome_filters_without_reordering() -> None:
    groups = (
        group(ReconcileOutcome.BALANCED, "totals_equal", GroupKey(ACCOUNT_A, PERIOD_A)),
        group(ReconcileOutcome.MISMATCH, "outside_variance_bands", GroupKey(ACCOUNT_A, PERIOD_B)),
        group(ReconcileOutcome.BALANCED, "totals_equal", GroupKey(ACCOUNT_B, PERIOD_A)),
    )
    result = BatchReconcileResult(
        groups=groups, unresolved=(),
        summary=summarize(groups, (), feed_rows_in=3, system_rows_in=3),
    )
    selected = result.with_outcome(ReconcileOutcome.BALANCED)
    assert [g.key.sort_key for g in selected] == [(ACCOUNT_A, PERIOD_A), (ACCOUNT_B, PERIOD_A)]


# --- unresolved rows ----------------------------------------------------------


def test_an_unresolved_row_keeps_safe_provenance_and_its_matching_verdict() -> None:
    row = UnresolvedRow(
        side=Side.FEED,
        reason="match_needs_review",
        source="probe.csv",
        source_row=7,
        row_index=5,
        match_outcome="needs_review",
    )
    assert row.message == MESSAGE_BY_REASON["match_needs_review"]
    assert row.sort_key == (0, "probe.csv", 7, 5)
    assert not isinstance(row.source, Path)


def test_every_public_model_is_a_frozen_dataclass() -> None:
    for cls in (
        GroupKey, SystemLedgerRow, Contribution, SideAggregate,
        GroupResult, UnresolvedRow, ReconcileSummary, BatchReconcileResult,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen"
