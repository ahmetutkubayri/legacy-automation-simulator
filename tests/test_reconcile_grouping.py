"""T12 input alignment, row eligibility, grouping, and the accounting invariant."""

from __future__ import annotations

import contextlib
import dataclasses
from decimal import Decimal

import pytest

from las.match import MatchOutcome
from las.reconcile import (
    ACCEPTED_OUTCOMES,
    REASON_INVALID_AMOUNT,
    REASON_INVALID_PERIOD,
    REASON_INVALID_UNIT,
    REASON_MATCH_INELIGIBLE,
    REASON_MATCH_NEEDS_REVIEW,
    REASON_MATCH_UNMATCHED,
    DuplicateFeedBatchError,
    DuplicateInputIdentityError,
    GroupKey,
    InputContractError,
    MatchDecisionContractError,
    ReconcileExecutionError,
    RowAlignmentError,
    Side,
    check_batches,
    exact_difference,
    exact_sum,
    feed_contributions,
    reconcile,
    system_contributions,
    totals_by_unit,
)
from las.validate import (
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_DUPLICATE_KEY,
    CODE_RECORD_ID_FORMAT_INVALID,
    CODE_UNIT_NOT_SUPPORTED,
    MESSAGE_BY_CODE,
    SEVERITY_BY_CODE,
    Severity,
    ValidationIssue,
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


def issue(
    code: str = CODE_AMOUNT_NOT_DECIMAL,
    field: str | None = "amount",
    source_row: int = 2,
) -> ValidationIssue:
    """A real T10 issue: its severity and message come from the code itself."""
    return ValidationIssue(
        code=code,
        severity=SEVERITY_BY_CODE[code],
        message=MESSAGE_BY_CODE[code],
        source="probe.csv",
        source_row=source_row,
        field=field,
    )


# --- input alignment ----------------------------------------------------------


def test_an_aligned_batch_is_accepted() -> None:
    check_batches([make_batch([feed_row(2), feed_row(3)])])


def test_zero_rows_on_both_sides_is_a_valid_empty_reconciliation() -> None:
    result = reconcile([make_batch([])], (), POLICY)
    assert result.groups == ()
    assert result.unresolved == ()
    assert result.summary.group_count == 0
    assert result.summary.accounting_holds


def test_no_batches_at_all_is_still_accounted() -> None:
    result = reconcile([], (), POLICY)
    assert result.summary.feed_rows_in == 0
    assert result.summary.accounting_holds


def test_mismatched_row_and_decision_counts_are_refused() -> None:
    batch = make_batch([feed_row(2), feed_row(3)])
    truncated = dataclasses.replace(
        batch,
        matching=dataclasses.replace(
            batch.matching, decisions=batch.matching.decisions[:1]
        ),
    )
    with pytest.raises(RowAlignmentError, match="different number of rows and decisions"):
        check_batches([truncated])


def test_unequal_sequences_are_never_zipped_into_silence() -> None:
    """The longer sequence must not be truncated away without a word."""
    batch = make_batch([feed_row(2), feed_row(3), feed_row(4)])
    truncated = dataclasses.replace(
        batch,
        validation=dataclasses.replace(
            batch.validation, rows=batch.validation.rows[:2]
        ),
    )
    with pytest.raises(RowAlignmentError):
        reconcile([truncated], (), POLICY)


def test_a_decision_sitting_at_the_wrong_index_is_refused() -> None:
    batch = make_batch([feed_row(2), feed_row(3)])
    shifted = dataclasses.replace(
        batch,
        matching=dataclasses.replace(
            batch.matching,
            decisions=(
                dataclasses.replace(batch.matching.decisions[0], row_index=1),
                batch.matching.decisions[1],
            ),
        ),
    )
    with pytest.raises(RowAlignmentError, match="row index it claims"):
        check_batches([shifted])


def test_a_decision_belonging_to_another_row_is_refused() -> None:
    batch = make_batch([feed_row(2)])
    other = dataclasses.replace(
        batch.matching.decisions[0],
        provenance=dataclasses.replace(
            batch.matching.decisions[0].provenance, source_row=99
        ),
    )
    swapped = dataclasses.replace(
        batch, matching=dataclasses.replace(batch.matching, decisions=(other,))
    )
    with pytest.raises(RowAlignmentError, match="belongs to a different row"):
        check_batches([swapped])


def test_a_decision_from_another_worksheet_is_refused() -> None:
    batch = make_batch([feed_row(2, worksheet="records")])
    elsewhere = dataclasses.replace(
        batch.matching.decisions[0],
        provenance=dataclasses.replace(
            batch.matching.decisions[0].provenance, worksheet="other"
        ),
    )
    swapped = dataclasses.replace(
        batch, matching=dataclasses.replace(batch.matching, decisions=(elsewhere,))
    )
    with pytest.raises(RowAlignmentError, match="belongs to a different row"):
        check_batches([swapped])


def test_differing_source_labels_between_stages_are_refused() -> None:
    batch = make_batch([feed_row(2)])
    relabelled = dataclasses.replace(
        batch, matching=dataclasses.replace(batch.matching, source="other.csv")
    )
    with pytest.raises(RowAlignmentError, match="different source labels"):
        check_batches([relabelled])


def test_the_same_feed_batch_cannot_be_supplied_twice() -> None:
    """One batch must not silently contribute twice under one identity."""
    batch = make_batch([feed_row(2)], source="probe.csv")
    with pytest.raises(DuplicateFeedBatchError, match="supplied more than once"):
        check_batches([batch, batch])


def test_two_distinct_feed_sources_are_accepted_on_the_one_feed_side() -> None:
    alpha = make_batch([feed_row(2, source="alpha.xlsx")], source="alpha.xlsx")
    beta = make_batch([feed_row(2, source="beta.csv")], source="beta.csv")
    check_batches([alpha, beta])
    result = reconcile([alpha, beta], (ledger_row(),), POLICY)
    assert result.summary.contributions_by_side[Side.FEED.value] == 2


def test_inputs_are_unchanged_after_an_alignment_failure() -> None:
    batch = make_batch([feed_row(2), feed_row(3)])
    broken = dataclasses.replace(
        batch,
        matching=dataclasses.replace(
            batch.matching, decisions=batch.matching.decisions[:1]
        ),
    )
    before = (
        tuple(batch.validation.rows),
        tuple(batch.matching.decisions),
        batch.validation.source,
    )
    with pytest.raises(RowAlignmentError):
        reconcile([broken], (ledger_row(),), POLICY)
    assert (
        tuple(batch.validation.rows),
        tuple(batch.matching.decisions),
        batch.validation.source,
    ) == before


# --- accepted matching outcomes -----------------------------------------------


def test_the_accepted_outcomes_are_exactly_exact_and_fuzzy() -> None:
    assert ACCEPTED_OUTCOMES == (MatchOutcome.EXACT, MatchOutcome.FUZZY)


@pytest.mark.parametrize("outcome", [MatchOutcome.EXACT, MatchOutcome.FUZZY])
def test_an_accepted_outcome_contributes(outcome: MatchOutcome) -> None:
    batch = make_batch([feed_row(2)], outcomes=[outcome])
    contributions, unresolved = feed_contributions([batch])
    assert len(contributions) == 1
    assert unresolved == ()
    assert contributions[0][0] == GroupKey(ACCOUNT_A, PERIOD_A)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (MatchOutcome.NEEDS_REVIEW, REASON_MATCH_NEEDS_REVIEW),
        (MatchOutcome.UNMATCHED, REASON_MATCH_UNMATCHED),
        (MatchOutcome.INELIGIBLE, REASON_MATCH_INELIGIBLE),
    ],
)
def test_a_refused_outcome_never_contributes_and_is_restated(
    outcome: MatchOutcome, reason: str
) -> None:
    batch = make_batch([feed_row(2)], outcomes=[outcome], accounts=[None])
    contributions, unresolved = feed_contributions([batch])
    assert contributions == ()
    assert len(unresolved) == 1
    assert unresolved[0].reason == reason
    assert unresolved[0].match_outcome == outcome.value


def test_a_matched_decision_without_an_account_fails_safely() -> None:
    batch = make_batch([feed_row(2)], outcomes=[MatchOutcome.EXACT], accounts=[None])
    with pytest.raises(MatchDecisionContractError, match="names no account") as excinfo:
        feed_contributions([batch])
    assert excinfo.value.row == 2
    assert excinfo.value.side == "feed"


def test_a_matching_verdict_is_never_promoted_or_downgraded() -> None:
    rows = [feed_row(2), feed_row(3), feed_row(4)]
    outcomes = [MatchOutcome.NEEDS_REVIEW, MatchOutcome.UNMATCHED, MatchOutcome.EXACT]
    batch = make_batch(rows, outcomes=outcomes, accounts=[None, None, ACCOUNT_A])
    result = reconcile([batch], (), POLICY)
    assert {row.match_outcome for row in result.unresolved} == {
        "needs_review",
        "unmatched",
    }
    assert result.summary.contributions_by_side[Side.FEED.value] == 1


# --- reconciliation-field eligibility -----------------------------------------


@pytest.mark.parametrize("amount", [None, "", "   ", "not-a-number", "1e3", "1_000"])
def test_an_unusable_amount_excludes_the_row_but_keeps_it(amount: str | None) -> None:
    batch = make_batch([feed_row(2, amount=amount)])
    contributions, unresolved = feed_contributions([batch])
    assert contributions == ()
    assert [row.reason for row in unresolved] == [REASON_INVALID_AMOUNT]
    assert unresolved[0].account_ref == ACCOUNT_A


def test_an_amount_error_from_validation_excludes_the_row() -> None:
    """An out-of-range amount parses, but T10 said not to trust it."""
    batch = make_batch([feed_row(2, amount="-42.50")], issues_by_row=[(issue(),)])
    _, unresolved = feed_contributions([batch])
    assert [row.reason for row in unresolved] == [REASON_INVALID_AMOUNT]


@pytest.mark.parametrize("unit", [None, "", "   "])
def test_a_missing_unit_excludes_the_row(unit: str | None) -> None:
    batch = make_batch([feed_row(2, unit=unit)])
    _, unresolved = feed_contributions([batch])
    assert [row.reason for row in unresolved] == [REASON_INVALID_UNIT]


def test_an_unsupported_unit_excludes_the_row() -> None:
    batch = make_batch(
        [feed_row(2, unit="PALLET")],
        issues_by_row=[(issue(code=CODE_UNIT_NOT_SUPPORTED, field="unit"),)],
    )
    _, unresolved = feed_contributions([batch])
    assert [row.reason for row in unresolved] == [REASON_INVALID_UNIT]


@pytest.mark.parametrize("period", [None, "", "   "])
def test_a_missing_period_excludes_the_row(period: str | None) -> None:
    batch = make_batch([feed_row(2, period=period)])
    _, unresolved = feed_contributions([batch])
    assert [row.reason for row in unresolved] == [REASON_INVALID_PERIOD]


def test_the_field_check_order_is_fixed() -> None:
    """A row failing several checks always reports the same one."""
    batch = make_batch([feed_row(2, amount=None, unit=None, period=None)])
    _, unresolved = feed_contributions([batch])
    assert [row.reason for row in unresolved] == [REASON_INVALID_AMOUNT]


def test_an_unrelated_validation_error_never_excludes_a_usable_row() -> None:
    """A malformed record identifier says nothing about the arithmetic."""
    batch = make_batch(
        [feed_row(2, record_id="REC-1")],
        issues_by_row=[(issue(code=CODE_RECORD_ID_FORMAT_INVALID, field="record_id"),)],
    )
    contributions, unresolved = feed_contributions([batch])
    assert len(contributions) == 1
    assert unresolved == ()


def test_a_warning_never_excludes_a_row() -> None:
    """Duplicate warnings are visible, and change nothing about the totals."""
    warning = issue(code=CODE_DUPLICATE_KEY, field="record_id")
    assert warning.severity is Severity.WARN
    batch = make_batch([feed_row(2)], issues_by_row=[(warning,)])
    contributions, unresolved = feed_contributions([batch])
    assert len(contributions) == 1
    assert unresolved == ()
    # The warning is still there to be seen; it just changed no arithmetic.
    assert batch.validation.issues == (warning,)


# --- grouping -----------------------------------------------------------------


def test_the_same_account_in_different_periods_forms_different_groups() -> None:
    batch = make_batch([feed_row(2, period=PERIOD_A), feed_row(3, period=PERIOD_B)])
    contributions, _ = feed_contributions([batch])
    assert {key for key, _ in contributions} == {
        GroupKey(ACCOUNT_A, PERIOD_A),
        GroupKey(ACCOUNT_A, PERIOD_B),
    }


def test_different_accounts_in_one_period_form_different_groups() -> None:
    batch = make_batch([feed_row(2), feed_row(3)], accounts=[ACCOUNT_A, ACCOUNT_B])
    contributions, _ = feed_contributions([batch])
    assert {key.account_ref for key, _ in contributions} == {ACCOUNT_A, ACCOUNT_B}


def test_different_units_stay_in_one_group_because_unit_is_not_in_the_key() -> None:
    batch = make_batch([feed_row(2, unit="EA"), feed_row(3, unit="KG")])
    contributions, _ = feed_contributions([batch])
    assert {key for key, _ in contributions} == {GroupKey(ACCOUNT_A, PERIOD_A)}
    assert {c.unit for _, c in contributions} == {"EA", "KG"}


def test_grouping_is_independent_of_row_order() -> None:
    rows = [feed_row(2, period=PERIOD_B), feed_row(3, period=PERIOD_A), feed_row(4)]
    forward, _ = feed_contributions([make_batch(rows)])
    backward, _ = feed_contributions([make_batch(list(reversed(rows)))])
    assert sorted(k.sort_key for k, _ in forward) == sorted(k.sort_key for k, _ in backward)


# --- duplicates ---------------------------------------------------------------


def test_two_identical_business_rows_remain_two_contributions() -> None:
    batch = make_batch([feed_row(2), feed_row(3)])
    contributions, _ = feed_contributions([batch])
    assert len(contributions) == 2
    assert {c.source_row for _, c in contributions} == {2, 3}
    assert totals_by_unit([c for _, c in contributions]) == {"EA": Decimal("21.00")}


def test_reversing_duplicate_order_does_not_change_the_total() -> None:
    rows = [feed_row(2, amount="10.50"), feed_row(3, amount="10.50")]
    forward, _ = feed_contributions([make_batch(rows)])
    reverse, _ = feed_contributions([make_batch(list(reversed(rows)))])
    assert totals_by_unit([c for _, c in forward]) == totals_by_unit(
        [c for _, c in reverse]
    )


def test_there_is_no_first_row_wins_behaviour() -> None:
    batch = make_batch([feed_row(2, amount="1"), feed_row(3, amount="2")])
    contributions, _ = feed_contributions([batch])
    assert totals_by_unit([c for _, c in contributions]) == {"EA": Decimal(3)}


# --- the system side ----------------------------------------------------------


def test_every_supplied_ledger_row_becomes_exactly_one_contribution() -> None:
    rows = (ledger_row(position=0), ledger_row(position=1, amount_text="1.00"))
    contributions = system_contributions(rows)
    assert len(contributions) == 2
    assert all(c.side is Side.SYSTEM for _, c in contributions)
    assert [c.source_row for _, c in contributions] == [0, 1]


def test_system_contributions_use_the_supplied_account_verbatim() -> None:
    contributions = system_contributions((ledger_row(account_ref=ACCOUNT_B),))
    assert contributions[0][0] == GroupKey(ACCOUNT_B, PERIOD_A)


# --- the accounting invariant -------------------------------------------------


def test_every_feed_row_is_accounted_for_exactly_once() -> None:
    rows = [feed_row(2), feed_row(3, amount=None), feed_row(4), feed_row(5)]
    outcomes = [
        MatchOutcome.EXACT,
        MatchOutcome.EXACT,
        MatchOutcome.NEEDS_REVIEW,
        MatchOutcome.UNMATCHED,
    ]
    batch = make_batch(
        rows, outcomes=outcomes, accounts=[ACCOUNT_A, ACCOUNT_A, None, None]
    )
    result = reconcile([batch], (ledger_row(),), POLICY)
    summary = result.summary

    assert summary.feed_rows_in == 4
    assert summary.feed_contributions == 1
    assert summary.unresolved_count == 3
    assert summary.feed_rows_accounted == 4
    assert summary.accounting_holds
    assert dict(summary.unresolved_by_reason) == {
        REASON_INVALID_AMOUNT: 1,
        REASON_MATCH_NEEDS_REVIEW: 1,
        REASON_MATCH_UNMATCHED: 1,
    }


def test_no_row_appears_in_two_places_at_once() -> None:
    rows = [feed_row(2), feed_row(3, unit=None), feed_row(4)]
    batch = make_batch(
        rows,
        outcomes=[MatchOutcome.EXACT, MatchOutcome.EXACT, MatchOutcome.INELIGIBLE],
        accounts=[ACCOUNT_A, ACCOUNT_A, None],
    )
    result = reconcile([batch], (), POLICY)
    contributing = {
        (c.source, c.source_row)
        for group in result.groups
        for c in group.feed.contributions
    }
    unresolved = {(row.source, row.source_row) for row in result.unresolved}
    assert not contributing & unresolved
    assert len(contributing) + len(unresolved) == 3


def test_the_system_accounting_matches_what_was_supplied() -> None:
    system = tuple(ledger_row(position=index) for index in range(5))
    result = reconcile([make_batch([feed_row(2)])], system, POLICY)
    assert result.summary.system_rows_in == 5
    assert result.summary.system_contributions == 5
    assert result.summary.accounting_holds


# =============================================================================
# physical input identity: replay is refused, business duplicates are not
# =============================================================================


def test_a_batch_identity_defaults_to_its_source_label() -> None:
    batch = make_batch([feed_row(2)], source="alpha.csv")
    assert batch.batch_id == "alpha.csv"
    assert make_batch([feed_row(2)], source="alpha.csv").batch_id == batch.batch_id


def test_a_batch_identity_can_be_declared_explicitly() -> None:
    inner = make_batch([feed_row(2)], source="alpha.csv")
    named = dataclasses.replace(inner, batch_id="first-pass")
    assert named.batch_id == "first-pass"
    assert named.source == "alpha.csv"  # provenance is untouched


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_batch_identity_is_refused(value: str) -> None:
    inner = make_batch([feed_row(2)])
    with pytest.raises(InputContractError, match="non-blank label"):
        dataclasses.replace(inner, batch_id=value)


def test_the_same_batch_replayed_under_one_identity_fails() -> None:
    batch = make_batch([feed_row(2)])
    with pytest.raises(DuplicateFeedBatchError, match="supplied more than once"):
        reconcile([batch, batch], (ledger_row(),), POLICY)


def test_an_empty_batch_replayed_is_still_caught() -> None:
    """Nothing to double, but the replay is still a mistake worth naming."""
    batch = make_batch([])
    with pytest.raises(DuplicateFeedBatchError):
        reconcile([batch, batch], (), POLICY)


def test_the_same_physical_feed_row_twice_in_one_batch_fails() -> None:
    row = feed_row(2, amount="10.00")
    with pytest.raises(DuplicateInputIdentityError, match="physical feed row"):
        reconcile([make_batch([row, row])], (ledger_row(),), POLICY)


def test_the_same_physical_feed_row_across_two_batches_fails() -> None:
    """Different batch labels do not make one file row into two rows."""
    alpha = make_batch([feed_row(2, source="alpha.csv")], source="alpha.csv")
    replay = make_batch([feed_row(2, source="alpha.csv")], source="second-pass")
    with pytest.raises(DuplicateInputIdentityError, match="physical feed row"):
        reconcile([alpha, replay], (ledger_row(),), POLICY)


def test_the_same_row_number_on_different_worksheets_is_two_rows() -> None:
    alpha = make_batch(
        [feed_row(2, source="book.xlsx", worksheet="one")], source="one"
    )
    beta = make_batch([feed_row(2, source="book.xlsx", worksheet="two")], source="two")
    result = reconcile([alpha, beta], (ledger_row(),), POLICY)
    assert result.summary.contributions_by_side[Side.FEED.value] == 2


def test_business_duplicates_at_different_physical_rows_both_contribute() -> None:
    """Identical business values, two physical rows: two real observations."""
    result = reconcile(
        [make_batch([feed_row(2, amount="10.50"), feed_row(3, amount="10.50")])],
        (ledger_row(amount_text="21.00"),),
        POLICY,
    )
    group = result.groups[0]
    assert len(group.feed.contributions) == 2
    assert group.feed.total == Decimal("21.00")
    assert group.outcome.value == "balanced"


def test_replay_detection_does_not_depend_on_input_order() -> None:
    first = make_batch([feed_row(2, source="alpha.csv")], source="alpha.csv")
    second = make_batch([feed_row(2, source="alpha.csv")], source="second-pass")
    for order in ([first, second], [second, first]):
        with pytest.raises(DuplicateInputIdentityError):
            reconcile(order, (ledger_row(),), POLICY)


def test_identity_is_declared_rather_than_object_based() -> None:
    """Two separately built rows with the same provenance are one identity."""
    left = make_batch([feed_row(2, source="alpha.csv")], source="a")
    right = make_batch([feed_row(2, source="alpha.csv")], source="b")
    assert left.validation.rows[0] is not right.validation.rows[0]
    with pytest.raises(DuplicateInputIdentityError):
        reconcile([left, right], (), POLICY)


# --- the system side -----------------------------------------------------------


def test_the_same_system_row_supplied_twice_fails() -> None:
    row = ledger_row(position=0)
    with pytest.raises(DuplicateInputIdentityError, match="ledger record"):
        reconcile([make_batch([feed_row(2)])], (row, row), POLICY)


def test_two_system_rows_sharing_a_record_id_fail() -> None:
    rows = (
        ledger_row(position=0, record_id="OPR-88000000", amount_text="1.00"),
        ledger_row(position=1, record_id="OPR-88000000", amount_text="2.00"),
    )
    with pytest.raises(DuplicateInputIdentityError, match="ledger record") as excinfo:
        reconcile([make_batch([feed_row(2)])], rows, POLICY)
    assert excinfo.value.record_id == "OPR-88000000"
    assert excinfo.value.side == "system"


def test_two_system_rows_sharing_a_position_fail() -> None:
    rows = (
        ledger_row(position=0, record_id="OPR-88000000"),
        ledger_row(position=0, record_id="OPR-88000001"),
    )
    with pytest.raises(DuplicateInputIdentityError, match="same position") as excinfo:
        reconcile([make_batch([feed_row(2)])], rows, POLICY)
    assert excinfo.value.row == 0


def test_system_rows_without_record_ids_are_identified_by_position() -> None:
    rows = (
        ledger_row(position=0, record_id=None, amount_text="5.00"),
        ledger_row(position=1, record_id=None, amount_text="5.00"),
    )
    result = reconcile([make_batch([feed_row(2)])], rows, POLICY)
    assert result.summary.system_contributions == 2


def test_distinct_system_records_with_identical_values_both_contribute() -> None:
    rows = (
        ledger_row(position=0, record_id="OPR-88000000", amount_text="5.00"),
        ledger_row(position=1, record_id="OPR-88000001", amount_text="5.00"),
    )
    result = reconcile([make_batch([feed_row(2)])], rows, POLICY)
    assert result.groups[0].system.total == Decimal("10.00")
    assert len(result.groups[0].system.contributions) == 2


def test_system_replay_detection_does_not_depend_on_order() -> None:
    first = ledger_row(position=0, record_id="OPR-88000000")
    second = ledger_row(position=1, record_id="OPR-88000000")
    for order in ((first, second), (second, first)):
        with pytest.raises(DuplicateInputIdentityError):
            reconcile([make_batch([feed_row(2)])], order, POLICY)


# --- failure is complete, and leaves everything alone --------------------------


def test_replay_fails_before_any_partial_result_exists() -> None:
    """The error arrives instead of a result, never alongside one."""
    row = feed_row(2)
    outcome = None
    with contextlib.suppress(DuplicateInputIdentityError):
        outcome = reconcile([make_batch([row, row])], (ledger_row(),), POLICY)
    assert outcome is None


def test_a_replay_error_exposes_no_row_amount_or_name() -> None:
    row = feed_row(2, amount="123456.78")
    with pytest.raises(DuplicateInputIdentityError) as excinfo:
        reconcile([make_batch([row, row])], (), POLICY)
    message = str(excinfo.value)
    for leak in ("123456.78", "EXT-10000", "OPR-00000001", "Decimal", "IngestedRow", "0x"):
        assert leak not in message


def test_inputs_are_unchanged_after_a_replay_failure() -> None:
    row = feed_row(2)
    batch = make_batch([row, row])
    system = (ledger_row(),)
    before = (tuple(batch.validation.rows), tuple(batch.matching.decisions), system)
    with pytest.raises(DuplicateInputIdentityError):
        reconcile([batch], system, POLICY)
    assert (
        tuple(batch.validation.rows),
        tuple(batch.matching.decisions),
        system,
    ) == before


def test_the_accounting_invariant_still_holds_for_valid_input() -> None:
    alpha = make_batch(
        [feed_row(2, source="alpha.csv"), feed_row(3, source="alpha.csv")],
        source="alpha.csv",
    )
    beta = make_batch([feed_row(2, source="beta.csv")], source="beta.csv")
    system = (ledger_row(position=0), ledger_row(position=1))
    summary = reconcile([alpha, beta], system, POLICY).summary
    assert summary.feed_rows_in == 3
    assert summary.system_rows_in == 2
    assert summary.accounting_holds


def test_the_batch_identity_never_enters_the_group_key() -> None:
    named = dataclasses.replace(make_batch([feed_row(2)]), batch_id="whatever")
    plain = make_batch([feed_row(2)])
    assert reconcile([named], (ledger_row(),), POLICY).groups[0].key == reconcile(
        [plain], (ledger_row(),), POLICY
    ).groups[0].key


# --- the exact-summation primitive ---------------------------------------------


def test_exact_sum_of_nothing_is_zero() -> None:
    assert exact_sum([]) == Decimal(0)


def test_exact_sum_keeps_every_digit() -> None:
    values = [Decimal("0." + "1" * 200), Decimal("0." + "0" * 200 + "5")]
    assert exact_sum(values) == Decimal("0." + "1" * 200 + "5")


def test_exact_sum_handles_mixed_signs_and_scales() -> None:
    assert exact_sum([Decimal("1000"), Decimal("-0.001"), Decimal("0.001")]) == Decimal(1000)


def test_exact_difference_is_left_minus_right() -> None:
    assert exact_difference(Decimal("1"), Decimal("0." + "9" * 60)) == Decimal(
        "0." + "0" * 59 + "1"
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_value_cannot_take_part_in_a_total(value: str) -> None:
    with pytest.raises(ReconcileExecutionError, match="non-finite"):
        exact_sum([Decimal(1), Decimal(value)])


def test_a_contribution_amount_must_be_a_finite_decimal() -> None:
    from las.reconcile import Contribution

    for bad in (Decimal("NaN"), Decimal("Infinity"), 1.5, "10.50", None):
        with pytest.raises(InputContractError, match="finite decimal"):
            Contribution(
                side=Side.FEED, amount=bad, unit="EA",
                source="probe.csv", source_row=2, row_index=0,
            )
