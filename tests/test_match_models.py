"""T11 result models: bounded scores, fixed messages, deep immutability."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from las.ingest import RowProvenance, SourceLabelError
from las.match import (
    PERFECT_SCORE,
    REASON_EXACT_REFERENCE,
    REASON_MESSAGES,
    SCORE_MAX_BP,
    SCORE_MIN_BP,
    BatchMatchResult,
    Candidate,
    MatchDecision,
    MatchMethod,
    MatchOutcome,
    MatchPolicy,
    MatchPolicyError,
    MatchSummary,
    Score,
    ScoreRangeError,
    summarize,
)
from match_helpers import make_index, make_policy

PROV = RowProvenance(source="probe.csv", source_row=7)


def decision(**kwargs) -> MatchDecision:
    options = {
        "provenance": PROV,
        "row_index": 0,
        "outcome": MatchOutcome.EXACT,
        "method": MatchMethod.EXTERNAL_REFERENCE,
        "reason": REASON_EXACT_REFERENCE,
    }
    options.update(kwargs)
    return MatchDecision(**options)


# --- scores -------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 5000, 9999, 10000])
def test_scores_inside_the_range_are_accepted(value: int) -> None:
    assert Score(value).basis_points == value


@pytest.mark.parametrize("value", [-1, 10001, 100000])
def test_scores_outside_the_range_are_rejected(value: int) -> None:
    with pytest.raises(ScoreRangeError, match="between"):
        Score(value)


@pytest.mark.parametrize("value", [1.5, "8700", None, True, [1]])
def test_a_score_must_be_a_plain_integer(value: object) -> None:
    with pytest.raises(ScoreRangeError, match="integer"):
        Score(value)  # type: ignore[arg-type]


def test_scores_compare_exactly_and_order() -> None:
    assert Score(8700) == Score(8700)
    assert Score(8699) < Score(8700)
    assert sorted([Score(3), Score(1), Score(2)]) == [Score(1), Score(2), Score(3)]
    assert PERFECT_SCORE.is_perfect is True
    assert Score(SCORE_MAX_BP - 1).is_perfect is False
    assert (SCORE_MIN_BP, SCORE_MAX_BP) == (0, 10000)


def test_a_score_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        Score(1).basis_points = 2  # type: ignore[misc]


# --- candidates ---------------------------------------------------------------


def test_a_candidate_carries_only_a_reference_and_a_score() -> None:
    candidate = Candidate(account_ref="ACC-100000", score=Score(8700))
    assert set(vars(candidate)) == {"account_ref", "score"}
    with pytest.raises(FrozenInstanceError):
        candidate.account_ref = "ACC-100001"  # type: ignore[misc]


def test_a_candidate_needs_an_account_reference() -> None:
    with pytest.raises(ScoreRangeError, match="account reference"):
        Candidate(account_ref="", score=PERFECT_SCORE)


# --- decisions ----------------------------------------------------------------


def test_a_decision_reports_its_fixed_message() -> None:
    got = decision()
    assert got.message == REASON_MESSAGES[REASON_EXACT_REFERENCE]
    assert got.is_matched is True


def test_every_reason_code_has_a_fixed_template() -> None:
    for code, message in REASON_MESSAGES.items():
        assert code == code.lower()
        assert " " not in code
        assert message and message == message.strip()


def test_reason_messages_are_read_only() -> None:
    with pytest.raises(TypeError):
        REASON_MESSAGES["injected"] = "x"  # type: ignore[index]


def test_an_unknown_reason_is_rejected() -> None:
    with pytest.raises(ScoreRangeError, match="unknown match reason"):
        decision(reason="invented_out_of_thin_air")


def test_a_negative_row_index_is_rejected() -> None:
    with pytest.raises(ScoreRangeError, match="zero-based"):
        decision(row_index=-1)


def test_decision_candidates_are_coerced_to_a_tuple_and_frozen() -> None:
    supplied = [Candidate("ACC-100000", Score(9000))]
    got = decision(candidates=supplied)
    assert isinstance(got.candidates, tuple)
    supplied.append(Candidate("ACC-100001", Score(1)))
    assert len(got.candidates) == 1
    with pytest.raises(AttributeError):
        got.candidates.append(Candidate("ACC-100002", Score(1)))  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        got.outcome = MatchOutcome.UNMATCHED  # type: ignore[misc]


def test_unmatched_and_ineligible_are_not_matched() -> None:
    for outcome in (MatchOutcome.UNMATCHED, MatchOutcome.INELIGIBLE,
                    MatchOutcome.NEEDS_REVIEW):
        assert decision(outcome=outcome, reason="no_usable_matching_input").is_matched is False


def test_a_decision_retains_provenance_and_no_path_object() -> None:
    got = decision()
    assert got.provenance.source == "probe.csv"
    assert got.provenance.source_row == 7
    for value in (getattr(got, name) for name in vars(got)):
        assert not isinstance(value, Path)


def test_a_decision_cannot_be_given_an_unsafe_source_label() -> None:
    with pytest.raises(SourceLabelError):
        RowProvenance(source="/" + "opt/secret/feed.csv", source_row=1)


# --- summaries ----------------------------------------------------------------


def test_summary_counts_every_outcome_and_orders_deterministically() -> None:
    decisions = (
        decision(row_index=0),
        decision(row_index=1, outcome=MatchOutcome.UNMATCHED,
                 method=MatchMethod.FUZZY_NAME, reason="below_review_threshold"),
        decision(row_index=2, outcome=MatchOutcome.NEEDS_REVIEW,
                 method=MatchMethod.FUZZY_NAME, reason="ambiguous_tie"),
    )
    got = summarize(decisions)
    assert (got.total_rows, got.exact, got.fuzzy) == (3, 1, 0)
    assert (got.needs_review, got.unmatched, got.ineligible) == (1, 1, 0)
    assert got.matched == 1
    assert list(got.by_method) == [m.value for m in MatchMethod]
    assert list(got.by_reason) == sorted(got.by_reason)
    assert sum(got.by_reason.values()) == 3


def test_an_empty_summary_still_lists_every_method() -> None:
    got = summarize(())
    assert got.total_rows == 0
    assert got.by_reason == {}
    assert set(got.by_method) == {m.value for m in MatchMethod}
    assert sum(got.by_method.values()) == 0


def test_summary_mappings_are_copied_and_read_only() -> None:
    methods = {"external_reference": 1}
    reasons = {"exact_reference_matched": 1}
    got = MatchSummary(
        total_rows=1, exact=1, fuzzy=0, needs_review=0, unmatched=0, ineligible=0,
        by_method=methods, by_reason=reasons,
    )
    methods["external_reference"] = 99
    reasons["injected"] = 1
    assert got.by_method == {"external_reference": 1}
    assert got.by_reason == {"exact_reference_matched": 1}
    for mapping in (got.by_method, got.by_reason):
        with pytest.raises(TypeError):
            mapping["injected"] = 1  # type: ignore[index]


# --- batch results ------------------------------------------------------------


def test_batch_result_coerces_caller_lists_and_offers_lookups() -> None:
    supplied = [decision(row_index=0),
                decision(row_index=1, outcome=MatchOutcome.UNMATCHED,
                         method=MatchMethod.FUZZY_NAME, reason="below_review_threshold")]
    got = BatchMatchResult(source="probe.csv", decisions=supplied,
                           summary=summarize(supplied))
    assert isinstance(got.decisions, tuple)
    supplied.append(decision(row_index=2))
    assert got.row_count == 2
    assert len(got.with_outcome(MatchOutcome.EXACT)) == 1
    assert len(got.with_reason("below_review_threshold")) == 1
    with pytest.raises(AttributeError):
        got.decisions.append(decision(row_index=3))  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        got.source = "other"  # type: ignore[misc]


# --- policy -------------------------------------------------------------------


def test_policy_bands_follow_the_configured_thresholds() -> None:
    policy = make_policy()
    assert policy.band_of(10000) == "accept"
    assert policy.band_of(8700) == "accept"
    assert policy.band_of(8699) == "review"
    assert policy.band_of(7000) == "review"
    assert policy.band_of(6999) == "below"


def test_policy_copies_its_caller_sequences() -> None:
    keys = ["reported by", "src name"]
    policy = make_policy(source_name_keys=keys)
    assert isinstance(policy.source_name_keys, tuple)
    keys.append("injected")
    assert policy.source_name_keys == ("reported by", "src name")
    with pytest.raises(FrozenInstanceError):
        policy.accept_bp = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"accept_bp": -1}, "between"),
        ({"accept_bp": 10001}, "between"),
        ({"accept_bp": 8700, "review_bp": 9000}, "must not exceed"),
        ({"accept_bp": 1.5}, "integer"),
        ({"review_bp": True}, "integer"),
        ({"source_name_keys": ()}, "at least one source-name key"),
        ({"source_name_keys": ("", "x")}, "must not be blank"),
        ({"source_name_keys": ("a", "a")}, "repeat"),
        ({"max_candidates": 0}, "at least one"),
        ({"max_candidates": 1.5}, "integer or None"),
    ],
)
def test_contradictory_policies_are_rejected(overrides: dict, expected: str) -> None:
    base = {"accept_bp": 8700, "review_bp": 7000}
    with pytest.raises(MatchPolicyError, match=expected):
        MatchPolicy(**{**base, **overrides})


def test_policy_from_config_converts_thresholds_exactly() -> None:
    from las.config import load_config
    from las.match import policy_from_config

    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "data" / "samples" / "config.yaml")
    policy = policy_from_config(config.match)
    assert policy.accept_bp == 8700  # 0.87 exactly, not 8699
    assert policy.review_bp == 7000
    assert policy.max_candidates is None


def test_threshold_conversion_rejects_values_outside_zero_to_one() -> None:
    from las.match import to_basis_points

    assert to_basis_points(0.87, "probe") == 8700
    assert to_basis_points(1.0, "probe") == 10000
    assert to_basis_points(0.0, "probe") == 0
    for bad in (1.5, -0.1):
        with pytest.raises(MatchPolicyError, match="between 0 and 1"):
            to_basis_points(bad, "probe")


def test_threshold_conversion_rejects_unusable_input() -> None:
    from las.match import to_basis_points

    with pytest.raises(MatchPolicyError, match="not a usable threshold"):
        to_basis_points("not-a-number", "probe")  # type: ignore[arg-type]


def test_the_index_helper_builds_the_expected_shape() -> None:
    assert make_index().account_count == 5
