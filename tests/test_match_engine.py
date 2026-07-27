"""T11 engine: precedence, bands, ambiguity, eligibility, purity."""

from __future__ import annotations

import random

import pytest

from las.match import (
    REASON_AMBIGUOUS_EXACT_NAMES,
    REASON_AMBIGUOUS_TIE,
    REASON_BELOW_ACCEPTANCE,
    REASON_BELOW_REVIEW,
    REASON_EXACT_REFERENCE,
    REASON_FUZZY_ACCEPTED,
    REASON_NO_CANDIDATES,
    REASON_NO_MATCHING_INPUT,
    MatchMethod,
    MatchOutcome,
    build_master_index,
    match_row,
    match_rows,
)
from match_helpers import (
    NEAR_TWIN_NAME,
    PLAIN_ACCOUNTS,
    TWIN_A,
    TWIN_B,
    feed_row,
    make_index,
    make_pair,
    make_policy,
)

#: Built together from one bound: matching rejects a pair that disagrees.
INDEX, POLICY = make_pair()


def decide(**kwargs):
    return match_row(feed_row(**kwargs), 0, INDEX, POLICY)


# --- precedence ---------------------------------------------------------------


def test_a_resolvable_reference_matches_exactly() -> None:
    got = decide(external_ref="EXT-10000")
    assert got.outcome is MatchOutcome.EXACT
    assert got.method is MatchMethod.EXTERNAL_REFERENCE
    assert got.reason == REASON_EXACT_REFERENCE
    assert got.account_ref == "ACC-100000"
    assert got.external_ref == "EXT-10000"
    assert got.score.is_perfect
    assert got.candidates == (got.candidates[0],)


def test_a_reference_match_is_never_overridden_by_name_evidence() -> None:
    """The name points at a different account; the link still wins."""
    got = decide(external_ref="EXT-10000", source_name="Demo Account 000002")
    assert got.account_ref == "ACC-100000"
    assert got.method is MatchMethod.EXTERNAL_REFERENCE


def test_an_unknown_reference_falls_back_to_the_name() -> None:
    got = decide(external_ref="EXT-90000", source_name="Demo Account 000001")
    assert got.outcome is MatchOutcome.EXACT
    assert got.method is MatchMethod.EXACT_NAME
    assert got.account_ref == "ACC-100001"
    assert got.external_ref == "EXT-90000"  # retained for evidence


def test_a_malformed_reference_falls_back_to_the_name() -> None:
    """Matching never trims or corrects an identifier; it moves on."""
    got = decide(external_ref="ext-10000", source_name="Demo Account 000002")
    assert got.method is MatchMethod.EXACT_NAME
    assert got.account_ref == "ACC-100002"


def test_identifier_matching_is_case_sensitive_and_untrimmed() -> None:
    for reference in ("ext-10000", " EXT-10000", "EXT-10000 "):
        got = decide(external_ref=reference, source_name=None)
        assert got.outcome is MatchOutcome.INELIGIBLE
        assert got.reason == REASON_NO_MATCHING_INPUT


def test_a_missing_reference_falls_back_to_the_name() -> None:
    got = decide(external_ref=None, source_name="Demo Account 000000")
    assert got.method is MatchMethod.EXACT_NAME
    assert got.account_ref == "ACC-100000"


# --- eligibility --------------------------------------------------------------


def test_a_row_with_neither_reference_nor_name_is_ineligible() -> None:
    got = decide(external_ref=None, source_name=None)
    assert got.outcome is MatchOutcome.INELIGIBLE
    assert got.method is MatchMethod.NONE
    assert got.reason == REASON_NO_MATCHING_INPUT
    assert got.account_ref is None
    assert got.candidates == ()


def test_an_unusable_name_with_no_reference_is_ineligible() -> None:
    for name in ("", "   ", "Demo\x00Account", "Demo‮Account", "x" * 300):
        got = decide(external_ref=None, source_name=name)
        assert got.outcome is MatchOutcome.INELIGIBLE, name


def test_an_unrelated_field_error_never_blocks_matching() -> None:
    """A bad amount, unit, period or record identifier is not a matching field."""
    got = decide(
        external_ref="EXT-10000",
        amount="not-a-number",
        unit="PALLET",
        period="2026-13",
        record_id="REC-1",
    )
    assert got.outcome is MatchOutcome.EXACT
    assert got.account_ref == "ACC-100000"


def test_a_malformed_identifier_still_allows_the_name_path() -> None:
    got = decide(external_ref="REC-1", source_name="Demo Account 000001")
    assert got.outcome is MatchOutcome.EXACT
    assert got.method is MatchMethod.EXACT_NAME


# --- exact-name ambiguity -----------------------------------------------------


def test_two_accounts_sharing_a_name_are_never_arbitrarily_picked() -> None:
    index = build_master_index(
        [
            __import__("match_helpers").account_row("ACC-100000", "Shared Name 000001", 2),
            __import__("match_helpers").account_row("ACC-100001", "Shared Name 000001", 3),
        ],
        [],
        max_name_length=POLICY.max_name_length,
    )
    got = match_row(feed_row(external_ref=None, source_name="Shared Name 000001"), 0, index, POLICY)
    assert got.outcome is MatchOutcome.NEEDS_REVIEW
    assert got.method is MatchMethod.EXACT_NAME
    assert got.reason == REASON_AMBIGUOUS_EXACT_NAMES
    assert got.account_ref is None
    assert [c.account_ref for c in got.candidates] == ["ACC-100000", "ACC-100001"]


# --- fuzzy bands --------------------------------------------------------------


def test_a_clear_winner_above_the_threshold_is_a_fuzzy_match() -> None:
    # One character longer than exactly one account name, and two edits from
    # every other, so the best candidate is unique rather than tied.
    got = decide(external_ref=None, source_name="Demo Account 000000X")
    assert got.outcome is MatchOutcome.FUZZY
    assert got.method is MatchMethod.FUZZY_NAME
    assert got.reason == REASON_FUZZY_ACCEPTED
    assert got.account_ref == "ACC-100000"
    assert got.score.basis_points >= POLICY.accept_bp


def test_a_tie_above_the_review_threshold_needs_review() -> None:
    got = decide(external_ref=None, source_name=NEAR_TWIN_NAME)
    assert got.outcome is MatchOutcome.NEEDS_REVIEW
    assert got.reason == REASON_AMBIGUOUS_TIE
    assert got.account_ref is None
    assert [c.account_ref for c in got.candidates[:2]] == [TWIN_A[0], TWIN_B[0]]
    assert got.candidates[0].score == got.candidates[1].score


def test_a_unique_best_inside_the_review_band_needs_review() -> None:
    policy = make_policy(accept_bp=9600, review_bp=5000)
    got = match_row(
        feed_row(external_ref=None, source_name="Demo Account 000000X"), 0, INDEX, policy
    )
    assert got.outcome is MatchOutcome.NEEDS_REVIEW
    assert got.reason == REASON_BELOW_ACCEPTANCE
    assert got.account_ref is None


def test_a_best_below_the_review_threshold_is_unmatched() -> None:
    got = decide(external_ref=None, source_name="Unlisted Source 1")
    assert got.outcome is MatchOutcome.UNMATCHED
    assert got.reason == REASON_BELOW_REVIEW
    assert got.account_ref is None
    assert got.score.basis_points < POLICY.review_bp


def test_a_tie_below_the_review_threshold_is_unmatched_not_ambiguous() -> None:
    """Two equally poor candidates are poor, not worth a person's attention."""
    index = make_index(accounts=(("ACC-100000", "Alpha One"), ("ACC-100001", "Alpha Two")))
    got = match_row(
        feed_row(external_ref=None, source_name="Zzzzzz Qqqqqq"), 0, index, POLICY
    )
    assert got.candidates[0].score == got.candidates[1].score  # a genuine tie
    assert got.outcome is MatchOutcome.UNMATCHED
    assert got.reason == REASON_BELOW_REVIEW


def test_an_index_with_no_comparable_accounts_yields_no_candidates() -> None:
    index = build_master_index([], [], max_name_length=POLICY.max_name_length)
    got = match_row(feed_row(external_ref=None, source_name="Demo"), 0, index, POLICY)
    assert got.outcome is MatchOutcome.UNMATCHED
    assert got.reason == REASON_NO_CANDIDATES
    assert got.candidates == ()


# --- candidate ordering -------------------------------------------------------


def test_candidates_are_ordered_by_score_then_name_then_reference() -> None:
    got = decide(external_ref=None, source_name=NEAR_TWIN_NAME)
    scores = [c.score.basis_points for c in got.candidates]
    assert scores == sorted(scores, reverse=True)
    top = [c for c in got.candidates if c.score == got.candidates[0].score]
    assert [c.account_ref for c in top] == sorted(c.account_ref for c in top)


def test_candidate_order_is_independent_of_master_row_order() -> None:
    accounts = [*PLAIN_ACCOUNTS, TWIN_A, TWIN_B]
    shuffler = random.Random(21)
    probe = feed_row(external_ref=None, source_name=NEAR_TWIN_NAME)
    baseline = match_row(probe, 0, make_index(tuple(accounts)), POLICY)
    for _ in range(10):
        shuffler.shuffle(accounts)
        got = match_row(
            feed_row(external_ref=None, source_name=NEAR_TWIN_NAME),
            0,
            make_index(tuple(accounts)),
            POLICY,
        )
        assert got == baseline


def test_a_candidate_cap_trims_presentation_without_changing_the_verdict() -> None:
    uncapped = match_row(
        feed_row(external_ref=None, source_name=NEAR_TWIN_NAME), 0, INDEX, make_policy()
    )
    capped = match_row(
        feed_row(external_ref=None, source_name=NEAR_TWIN_NAME),
        0,
        INDEX,
        make_policy(max_candidates=1),
    )
    assert len(capped.candidates) == 1
    assert len(uncapped.candidates) == 5
    assert capped.outcome is uncapped.outcome is MatchOutcome.NEEDS_REVIEW
    assert capped.reason == uncapped.reason == REASON_AMBIGUOUS_TIE
    assert capped.score == uncapped.score


def test_a_candidate_cap_cannot_hide_a_tie() -> None:
    """The decision is computed over every candidate, then the list is trimmed."""
    capped = match_row(
        feed_row(external_ref=None, source_name=NEAR_TWIN_NAME),
        0,
        INDEX,
        make_policy(max_candidates=1),
    )
    assert capped.outcome is MatchOutcome.NEEDS_REVIEW


# --- batches ------------------------------------------------------------------


def test_an_empty_batch_is_handled() -> None:
    got = match_rows([], INDEX, POLICY)
    assert got.decisions == ()
    assert got.summary.total_rows == 0
    assert got.source == "unknown"


def test_every_input_row_produces_exactly_one_decision_in_order() -> None:
    rows = [
        feed_row(2, external_ref="EXT-10000"),
        feed_row(3, external_ref=None, source_name=None),
        feed_row(4, external_ref=None, source_name=NEAR_TWIN_NAME),
        feed_row(5, external_ref=None, source_name="Unlisted Source 1"),
    ]
    got = match_rows(rows, INDEX, POLICY)
    assert got.row_count == 4
    assert [d.row_index for d in got.decisions] == [0, 1, 2, 3]
    assert [d.provenance.source_row for d in got.decisions] == [2, 3, 4, 5]
    assert [d.outcome for d in got.decisions] == [
        MatchOutcome.EXACT,
        MatchOutcome.INELIGIBLE,
        MatchOutcome.NEEDS_REVIEW,
        MatchOutcome.UNMATCHED,
    ]
    s = got.summary
    assert s.exact + s.fuzzy + s.needs_review + s.unmatched + s.ineligible == 4


def test_duplicate_source_rows_stay_distinct_by_provenance() -> None:
    rows = [feed_row(2), feed_row(3)]
    got = match_rows(rows, INDEX, POLICY)
    assert [d.provenance.source_row for d in got.decisions] == [2, 3]
    assert got.decisions[0] != got.decisions[1]


def test_source_and_worksheet_come_from_provenance_unless_supplied() -> None:
    rows = [feed_row(2, source="feed_probe.xlsx", worksheet="records")]
    assert match_rows(rows, INDEX, POLICY).worksheet == "records"
    explicit = match_rows(rows, INDEX, POLICY, source="s", worksheet="w")
    assert (explicit.source, explicit.worksheet) == ("s", "w")


# --- determinism and purity ---------------------------------------------------


def test_repeated_matching_of_the_same_input_is_equal() -> None:
    rows = [feed_row(2), feed_row(3, external_ref=None, source_name=NEAR_TWIN_NAME)]
    assert match_rows(rows, INDEX, POLICY) == match_rows(rows, INDEX, POLICY)


def test_matching_never_mutates_the_rows_it_is_given() -> None:
    row = feed_row(2, external_ref=None, source_name=NEAR_TWIN_NAME)
    before = (dict(row.values), dict(row.extras), row.provenance)
    match_rows([row], INDEX, POLICY)
    assert (dict(row.values), dict(row.extras), row.provenance) == before
    with pytest.raises(TypeError):
        row.values["external_ref"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        row.extras["reported by"] = "changed"  # type: ignore[index]


def test_matching_never_mutates_the_index_or_the_policy() -> None:
    before = (INDEX.accounts, INDEX.link_count, POLICY.accept_bp, POLICY.source_name_keys)
    match_rows([feed_row(2, external_ref=None, source_name=NEAR_TWIN_NAME)], INDEX, POLICY)
    assert (INDEX.accounts, INDEX.link_count, POLICY.accept_bp, POLICY.source_name_keys) == before


def test_the_second_declared_source_name_key_is_used_when_the_first_is_absent() -> None:
    got = match_row(
        feed_row(external_ref=None, source_name="Demo Account 000001", name_key="src name"),
        0,
        INDEX,
        POLICY,
    )
    assert got.account_ref == "ACC-100001"


def test_an_unknown_extras_key_is_not_treated_as_a_source_name() -> None:
    got = match_row(
        feed_row(external_ref=None, source_name="Demo Account 000001", name_key="something else"),
        0,
        INDEX,
        POLICY,
    )
    assert got.outcome is MatchOutcome.INELIGIBLE
