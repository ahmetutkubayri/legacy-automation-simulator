"""T11 hardening: source-name conflicts, the explicit length cap, precedence.

Three contract gaps the first T11 pass left open:

* a row carrying a usable name under more than one configured key was resolved
  by whichever key came first in the policy — a deterministic but *arbitrary*
  choice between two different names — and a blank or unsafe value in an
  earlier key masked a perfectly good value in a later one;
* the name-length limit was a constant buried inside normalization, so no
  policy controlled it and no repository source authorized the value;
* nothing pinned that an exact reference still wins when the name columns
  disagree;
* the master index and the policy each took a cap separately, so a public
  caller could build them with different values and matching would run anyway,
  comparing keys made under two different rules.
"""

from __future__ import annotations

import dataclasses
import random
from types import MappingProxyType, SimpleNamespace

import pytest

from las.ingest import IngestedRow, RowProvenance
from las.match import (
    DEFAULT_MAX_NAME_LENGTH,
    MAX_CONFIGURABLE_NAME_LENGTH,
    NAME_LENGTH_MISMATCH_MESSAGE,
    REASON_CONFLICTING_SOURCE_NAMES,
    REASON_EXACT_NAME,
    REASON_EXACT_REFERENCE,
    REASON_NO_MATCHING_INPUT,
    MasterIndex,
    MasterIndexError,
    MatchError,
    MatchMethod,
    MatchOutcome,
    MatchPolicy,
    MatchPolicyError,
    NameLengthMismatchError,
    OverlongNameError,
    build_master_index,
    match_row,
    match_rows,
    match_validated,
    normalize_name,
)
from match_helpers import (
    PLAIN_ACCOUNTS,
    TWIN_A,
    TWIN_B,
    account_row,
    feed_row,
    make_index,
    make_pair,
    make_policy,
)

#: Built together from one bound, which is the contract these tests pin.
INDEX, POLICY = make_pair()
KEY_ONE, KEY_TWO = "reported by", "src name"
NAME_A, NAME_C = "Demo Account 000000", "Demo Account 000002"
ACCOUNT_A, ACCOUNT_C = "ACC-100000", "ACC-100002"


def row(extras: dict[str, str | None], reference: str | None = None) -> IngestedRow:
    return IngestedRow(
        provenance=RowProvenance(source="probe.csv", source_row=2),
        values={
            "amount": "10.50",
            "external_ref": reference,
            "period": "2026-01",
            "record_id": "OPR-00000001",
            "unit": "EA",
        },
        extras=extras,
    )


def decide(extras: dict[str, str | None], reference: str | None = None, policy=POLICY):
    return match_row(row(extras, reference), 0, INDEX, policy)


# =============================================================================
# 1. deterministic source-name selection
# =============================================================================


def test_a_single_key_under_either_name_is_used() -> None:
    assert decide({KEY_ONE: NAME_A}).account_ref == ACCOUNT_A
    assert decide({KEY_TWO: NAME_C}).account_ref == ACCOUNT_C


def test_identical_values_under_both_keys_collapse_to_one_input() -> None:
    got = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_A})
    assert got.outcome is MatchOutcome.EXACT
    assert got.reason == REASON_EXACT_NAME
    assert got.account_ref == ACCOUNT_A


def test_normalization_equivalent_values_collapse_to_one_input() -> None:
    """Different spellings of the same name are not a conflict."""
    got = decide({KEY_ONE: NAME_A, KEY_TWO: "  DEMO   account 000000 "})
    assert normalize_name(NAME_A) == normalize_name("  DEMO   account 000000 ")
    assert got.outcome is MatchOutcome.EXACT
    assert got.account_ref == ACCOUNT_A


def test_two_distinct_usable_names_are_a_conflict_not_a_choice() -> None:
    got = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C})
    assert got.outcome is MatchOutcome.INELIGIBLE
    assert got.method is MatchMethod.NONE
    assert got.reason == REASON_CONFLICTING_SOURCE_NAMES
    assert got.account_ref is None
    assert got.score is None
    assert got.candidates == ()


def test_a_conflict_never_repeats_either_raw_name() -> None:
    got = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C})
    rendered = " ".join(
        [got.reason, got.message, got.outcome.value, got.method.value,
         got.account_ref or "", got.external_ref or ""]
    )
    assert NAME_A not in rendered
    assert NAME_C not in rendered
    assert "Demo Account" not in rendered


def test_a_blank_value_never_masks_a_usable_one() -> None:
    assert decide({KEY_ONE: "   ", KEY_TWO: NAME_C}).account_ref == ACCOUNT_C
    assert decide({KEY_ONE: "", KEY_TWO: NAME_C}).account_ref == ACCOUNT_C
    assert decide({KEY_ONE: None, KEY_TWO: NAME_C}).account_ref == ACCOUNT_C
    assert decide({KEY_ONE: NAME_A, KEY_TWO: "   "}).account_ref == ACCOUNT_A


def test_an_unsafe_value_never_masks_a_usable_one() -> None:
    for unsafe in ("Demo\x00Account", "Demo‮Account", "a" * 400):
        assert decide({KEY_ONE: unsafe, KEY_TWO: NAME_C}).account_ref == ACCOUNT_C
        assert decide({KEY_ONE: NAME_A, KEY_TWO: unsafe}).account_ref == ACCOUNT_A


def test_two_unusable_values_leave_the_row_without_a_name() -> None:
    got = decide({KEY_ONE: "   ", KEY_TWO: "Demo\x00Account"})
    assert got.outcome is MatchOutcome.INELIGIBLE
    assert got.reason == REASON_NO_MATCHING_INPUT


@pytest.mark.parametrize(
    "extras",
    [
        {KEY_ONE: NAME_A, KEY_TWO: NAME_C},
        {KEY_TWO: NAME_C, KEY_ONE: NAME_A},
    ],
)
def test_extras_insertion_order_never_decides_anything(extras: dict) -> None:
    baseline = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C})
    assert decide(extras) == baseline


def test_reversed_extras_order_gives_an_identical_decision() -> None:
    forward = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C})
    reverse = decide({KEY_TWO: NAME_C, KEY_ONE: NAME_A})
    assert forward == reverse
    assert forward.reason == reverse.reason == REASON_CONFLICTING_SOURCE_NAMES


def test_the_conflict_verdict_survives_shuffled_master_order() -> None:
    accounts = [*PLAIN_ACCOUNTS, TWIN_A, TWIN_B]
    shuffler = random.Random(31)
    baseline = match_row(row({KEY_ONE: NAME_A, KEY_TWO: NAME_C}), 0, make_index(), POLICY)
    for _ in range(10):
        shuffler.shuffle(accounts)
        got = match_row(
            row({KEY_ONE: NAME_A, KEY_TWO: NAME_C}), 0, make_index(tuple(accounts)), POLICY
        )
        assert got == baseline


def test_a_selected_name_survives_shuffled_master_order() -> None:
    accounts = [*PLAIN_ACCOUNTS, TWIN_A, TWIN_B]
    shuffler = random.Random(32)
    baseline = match_row(row({KEY_TWO: NAME_C}), 0, make_index(), POLICY)
    for _ in range(10):
        shuffler.shuffle(accounts)
        assert match_row(
            row({KEY_TWO: NAME_C}), 0, make_index(tuple(accounts)), POLICY
        ) == baseline


def test_policy_key_order_is_immutable_and_caller_safe() -> None:
    keys = ["reported by", "src name"]
    policy = make_policy(source_name_keys=keys)
    assert isinstance(policy.source_name_keys, tuple)
    keys.reverse()
    keys.append("injected")
    assert policy.source_name_keys == ("reported by", "src name")
    with pytest.raises(AttributeError):
        policy.source_name_keys.append("injected")  # type: ignore[attr-defined]


def test_duplicate_configured_keys_are_rejected() -> None:
    with pytest.raises(MatchPolicyError, match="repeat"):
        make_policy(source_name_keys=("reported by", "reported by"))


def test_key_order_does_not_change_a_conflict_verdict() -> None:
    """Reversing the policy's own key order still reports a conflict."""
    forward = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C})
    reversed_policy = make_policy(source_name_keys=(KEY_TWO, KEY_ONE))
    backward = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C}, policy=reversed_policy)
    assert forward.reason == backward.reason == REASON_CONFLICTING_SOURCE_NAMES
    assert forward.account_ref is backward.account_ref is None


# =============================================================================
# 2. the explicit name-length safety cap
# =============================================================================


def test_the_cap_lives_on_the_policy_and_changes_behaviour_deterministically() -> None:
    """Both halves move together, because a mismatched pair is now refused.

    The master names here are ten characters, so a cap of ten indexes them and
    still refuses the nineteen-character source name.
    """
    short = (("ACC-100000", "Acct 00001"),)
    generous_index, generous = make_pair(
        max_name_length=DEFAULT_MAX_NAME_LENGTH, accounts=short, links=()
    )
    strict_index, strict = make_pair(max_name_length=10, accounts=short, links=())

    loose = match_row(row({KEY_ONE: "Acct 00001 Extends"}), 0, generous_index, generous)
    assert loose.outcome is MatchOutcome.UNMATCHED  # scored, and found wanting

    tight = match_row(row({KEY_ONE: "Acct 00001 Extends"}), 0, strict_index, strict)
    assert tight.outcome is MatchOutcome.INELIGIBLE
    assert tight.reason == REASON_NO_MATCHING_INPUT


def test_the_default_cap_is_only_a_default() -> None:
    assert make_policy().max_name_length == DEFAULT_MAX_NAME_LENGTH
    assert make_policy(max_name_length=64).max_name_length == 64


@pytest.mark.parametrize(
    "value", [0, -1, MAX_CONFIGURABLE_NAME_LENGTH + 1, 10**9]
)
def test_an_out_of_range_cap_is_rejected(value: int) -> None:
    with pytest.raises(MatchPolicyError, match="max_name_length"):
        make_policy(max_name_length=value)


@pytest.mark.parametrize("value", [1.5, "256", None, True])
def test_a_non_integer_cap_is_rejected(value: object) -> None:
    with pytest.raises(MatchPolicyError, match="max_name_length"):
        make_policy(max_name_length=value)  # type: ignore[arg-type]


def test_the_cap_is_accepted_at_both_boundaries() -> None:
    assert make_policy(max_name_length=1).max_name_length == 1
    assert (
        make_policy(max_name_length=MAX_CONFIGURABLE_NAME_LENGTH).max_name_length
        == MAX_CONFIGURABLE_NAME_LENGTH
    )


def test_an_over_limit_source_name_is_deterministically_ineligible() -> None:
    got = decide({KEY_ONE: "a" * (DEFAULT_MAX_NAME_LENGTH + 1)})
    assert got.outcome is MatchOutcome.INELIGIBLE
    assert got.reason == REASON_NO_MATCHING_INPUT
    assert got.account_ref is None


def test_an_over_limit_master_name_is_rejected_with_a_typed_error() -> None:
    with pytest.raises(OverlongNameError, match="exceeds the configured") as excinfo:
        build_master_index(
            [account_row("ACC-100000", "z" * 300)], [], max_name_length=256
        )
    assert excinfo.value.account_ref == "ACC-100000"
    assert "z" * 300 not in str(excinfo.value)
    assert "zzz" not in str(excinfo.value)


def test_the_same_bound_governs_master_and_source_names() -> None:
    name = "Demo Account 000000"  # 19 characters
    build_master_index([account_row("ACC-100000", name)], [], max_name_length=19)
    with pytest.raises(OverlongNameError):
        build_master_index([account_row("ACC-100000", name)], [], max_name_length=18)
    assert normalize_name(name, max_length=19) is not None
    assert normalize_name(name, max_length=18) is None


def test_a_blank_master_name_is_kept_rather_than_rejected() -> None:
    """A blank name is benign; only the safety cap fails loudly."""
    index = build_master_index(
        [account_row("ACC-100000", "   "), account_row("ACC-100001", NAME_A, 3)], []
    )
    assert index.account_count == 2
    assert [a.account_ref for a in index.comparable_accounts()] == ["ACC-100001"]


# =============================================================================
# 3. precedence under conflict
# =============================================================================


def test_a_linked_reference_wins_even_when_the_name_columns_conflict() -> None:
    got = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C}, reference="EXT-10000")
    assert got.outcome is MatchOutcome.EXACT
    assert got.method is MatchMethod.EXTERNAL_REFERENCE
    assert got.reason == REASON_EXACT_REFERENCE
    assert got.account_ref == ACCOUNT_A
    assert got.external_ref == "EXT-10000"


def test_an_unknown_reference_falls_back_only_to_an_unambiguous_name() -> None:
    """The contract authorizes name fallback; it does not authorize a guess."""
    resolved = decide({KEY_ONE: NAME_C}, reference="EXT-90000")
    assert resolved.outcome is MatchOutcome.EXACT
    assert resolved.method is MatchMethod.EXACT_NAME
    assert resolved.external_ref == "EXT-90000"

    conflicted = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C}, reference="EXT-90000")
    assert conflicted.outcome is MatchOutcome.INELIGIBLE
    assert conflicted.reason == REASON_CONFLICTING_SOURCE_NAMES


@pytest.mark.parametrize("reference", [None, "", "ext-10000", "REC-1", "EXT-1"])
def test_an_unusable_reference_plus_conflicting_names_is_ineligible(
    reference: str | None,
) -> None:
    got = decide({KEY_ONE: NAME_A, KEY_TWO: NAME_C}, reference=reference)
    assert got.outcome is MatchOutcome.INELIGIBLE
    assert got.reason == REASON_CONFLICTING_SOURCE_NAMES
    assert got.method is MatchMethod.NONE
    assert got.score is None  # never fuzzy-matched


def test_unrelated_validation_errors_never_block_a_usable_reference() -> None:
    bad = IngestedRow(
        provenance=RowProvenance(source="probe.csv", source_row=2),
        values={
            "amount": "not-a-number",
            "external_ref": "EXT-10000",
            "period": "2026-13",
            "record_id": "REC-1",
            "unit": "PALLET",
        },
        extras={KEY_ONE: NAME_A, KEY_TWO: NAME_C},
    )
    got = match_row(bad, 0, INDEX, POLICY)
    assert got.outcome is MatchOutcome.EXACT
    assert got.account_ref == ACCOUNT_A


def test_one_row_yields_exactly_one_decision_and_one_reason() -> None:
    rows = [
        feed_row(2, external_ref="EXT-10000"),
        row({KEY_ONE: NAME_A, KEY_TWO: NAME_C}),
        row({}, reference=None),
    ]
    result = match_rows(rows, INDEX, POLICY)
    assert result.row_count == len(rows)
    assert [d.row_index for d in result.decisions] == [0, 1, 2]
    for decision in result.decisions:
        assert isinstance(decision.reason, str)
    reasons = [d.reason for d in result.decisions]
    assert reasons == [
        REASON_EXACT_REFERENCE,
        REASON_CONFLICTING_SOURCE_NAMES,
        REASON_NO_MATCHING_INPUT,
    ]
    assert sum(result.summary.by_reason.values()) == len(rows)


def test_no_reason_code_is_ever_emitted_twice_for_one_row() -> None:
    result = match_rows(
        [row({KEY_ONE: NAME_A, KEY_TWO: NAME_C}), feed_row(3)], INDEX, POLICY
    )
    seen = [(d.row_index, d.reason) for d in result.decisions]
    assert len(seen) == len(set(seen))


def test_repeated_runs_under_conflict_are_equal() -> None:
    rows = [row({KEY_ONE: NAME_A, KEY_TWO: NAME_C})]
    assert match_rows(rows, INDEX, POLICY) == match_rows(rows, INDEX, POLICY)


# =============================================================================
# 4. one name-length bound, shared by the index and the policy
# =============================================================================

#: Ten-character master names, so an index can be built at either cap.
SHORT_ACCOUNTS = (("ACC-100000", "Acct 00001"), ("ACC-100001", "Acct 00002"))
SHORT_LINKS = (("EXT-10000", "ACC-100000"),)


def short_pair(index_cap: int, policy_cap: int) -> tuple[MasterIndex, MatchPolicy]:
    """An index and a policy built deliberately apart, to prove the refusal."""
    return (
        make_index(SHORT_ACCOUNTS, SHORT_LINKS, max_name_length=index_cap),
        make_policy(max_name_length=policy_cap),
    )


def _validated(rows: list[IngestedRow]) -> SimpleNamespace:
    """The minimum ``match_validated`` reads: rows, source, worksheet."""
    return SimpleNamespace(
        rows=tuple(SimpleNamespace(row=row) for row in rows),
        source="probe.csv",
        worksheet=None,
    )


def test_the_index_retains_the_bound_it_was_built_with() -> None:
    assert make_index(max_name_length=64).max_name_length == 64
    assert make_index().max_name_length == POLICY.max_name_length
    assert build_master_index([], []).max_name_length == DEFAULT_MAX_NAME_LENGTH


def test_the_retained_bound_is_immutable() -> None:
    index = make_index(max_name_length=64)
    with pytest.raises(dataclasses.FrozenInstanceError):
        index.max_name_length = 10  # type: ignore[misc]
    assert index.max_name_length == 64


@pytest.mark.parametrize("value", [0, -1, MAX_CONFIGURABLE_NAME_LENGTH + 1, 1.5, True])
def test_the_index_rejects_an_unusable_bound(value: object) -> None:
    """The same rule the policy applies, so the two cannot drift apart."""
    with pytest.raises(MasterIndexError, match="max_name_length"):
        build_master_index([], [], max_name_length=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, MAX_CONFIGURABLE_NAME_LENGTH + 1, 1.5, True])
def test_a_directly_constructed_index_rejects_an_unusable_bound(value: object) -> None:
    """The builder is not the only door, so the model guards itself too."""
    with pytest.raises(MasterIndexError, match="max_name_length"):
        MasterIndex(
            accounts=(),
            account_by_ref={},
            account_ref_by_link={},
            account_refs_by_name={},
            max_name_length=value,  # type: ignore[arg-type]
        )


def test_equal_bounds_match_normally() -> None:
    index, policy = make_pair(max_name_length=64)
    assert index.max_name_length == policy.max_name_length == 64
    got = match_row(row({KEY_ONE: NAME_A}), 0, index, policy)
    assert got.outcome is MatchOutcome.EXACT
    assert got.account_ref == ACCOUNT_A


@pytest.mark.parametrize(
    ("index_cap", "policy_cap"),
    [(DEFAULT_MAX_NAME_LENGTH, 10), (10, DEFAULT_MAX_NAME_LENGTH)],
    ids=["index_wider_than_policy", "policy_wider_than_index"],
)
def test_both_mismatch_directions_are_refused(index_cap: int, policy_cap: int) -> None:
    index, policy = short_pair(index_cap, policy_cap)
    with pytest.raises(NameLengthMismatchError):
        match_row(row({KEY_ONE: "Acct 00001"}), 0, index, policy)


def test_a_one_character_disagreement_is_enough() -> None:
    """Nothing is nearly equal: the two values are the same or they are not."""
    index, policy = short_pair(64, 65)
    with pytest.raises(NameLengthMismatchError):
        match_rows([row({KEY_ONE: "Acct 00001"})], index, policy)


def test_a_zero_row_batch_still_rejects_a_mismatched_contract() -> None:
    """No row means no chance to notice later, so the batch itself is refused."""
    index, policy = short_pair(DEFAULT_MAX_NAME_LENGTH, 10)
    with pytest.raises(NameLengthMismatchError):
        match_rows([], index, policy)


def test_every_entry_point_enforces_the_contract() -> None:
    index, policy = short_pair(DEFAULT_MAX_NAME_LENGTH, 10)
    probe = row({KEY_ONE: "Acct 00001"})

    with pytest.raises(NameLengthMismatchError):
        match_row(probe, 0, index, policy)
    with pytest.raises(NameLengthMismatchError):
        match_rows([probe], index, policy)
    with pytest.raises(NameLengthMismatchError):
        match_validated(_validated([probe]), index, policy)


def test_match_validated_fails_before_reading_the_batch() -> None:
    """The validation result is not even asked for its rows."""
    index, policy = short_pair(DEFAULT_MAX_NAME_LENGTH, 10)

    class Hostile:
        source = "probe.csv"
        worksheet = None

        @property
        def rows(self):  # pragma: no cover - must never run
            raise AssertionError("the batch must not be read")

    with pytest.raises(NameLengthMismatchError):
        match_validated(Hostile(), index, policy)


def test_the_failure_is_the_fixed_safe_message() -> None:
    index, policy = short_pair(DEFAULT_MAX_NAME_LENGTH, 10)
    with pytest.raises(NameLengthMismatchError) as excinfo:
        match_rows([], index, policy)

    message = str(excinfo.value)
    assert NAME_LENGTH_MISMATCH_MESSAGE in message
    assert excinfo.value.operation == "verify_match_contract"
    # No value of any kind travels: not the caps, not a name, not a row.
    for leak in ("256", "10", "Acct", "ACC-", "EXT-", "probe.csv", "row="):
        assert leak not in message
    for attribute in ("source", "row", "account_ref", "external_ref"):
        assert getattr(excinfo.value, attribute) is None


def test_the_failure_is_identical_from_every_entry_point() -> None:
    index, policy = short_pair(DEFAULT_MAX_NAME_LENGTH, 10)
    probe = row({KEY_ONE: "Acct 00001"})
    messages = set()
    for call in (
        lambda: match_row(probe, 0, index, policy),
        lambda: match_rows([probe], index, policy),
        lambda: match_validated(_validated([probe]), index, policy),
    ):
        with pytest.raises(NameLengthMismatchError) as excinfo:
            call()
        messages.add(str(excinfo.value))
    assert len(messages) == 1


def test_the_failure_is_repeatable_and_typed() -> None:
    index, policy = short_pair(10, DEFAULT_MAX_NAME_LENGTH)
    seen = set()
    for _ in range(5):
        with pytest.raises(NameLengthMismatchError) as excinfo:
            match_rows([row({KEY_ONE: "Acct 00001"})], index, policy)
        seen.add((type(excinfo.value).__name__, str(excinfo.value)))
    assert len(seen) == 1
    assert issubclass(NameLengthMismatchError, MatchError)


def test_neither_bound_is_silently_preferred() -> None:
    """Refusal, not a quiet choice of the smaller or the larger value."""
    index, policy = short_pair(10, DEFAULT_MAX_NAME_LENGTH)
    long_name = "Acct 00001 Extends"  # usable at 256, not at 10

    with pytest.raises(NameLengthMismatchError):
        match_row(row({KEY_ONE: long_name}), 0, index, policy)

    # Under either bound applied honestly, a decision does exist — and the two
    # decisions differ, which is exactly why picking one silently is wrong.
    narrow_index, narrow = make_pair(max_name_length=10, accounts=SHORT_ACCOUNTS, links=())
    wide_index, wide = make_pair(max_name_length=256, accounts=SHORT_ACCOUNTS, links=())
    narrow_outcome = match_row(row({KEY_ONE: long_name}), 0, narrow_index, narrow).outcome
    wide_outcome = match_row(row({KEY_ONE: long_name}), 0, wide_index, wide).outcome
    assert narrow_outcome is MatchOutcome.INELIGIBLE
    assert wide_outcome is MatchOutcome.UNMATCHED
    assert narrow_outcome is not wide_outcome


def test_nothing_is_mutated_before_the_refusal() -> None:
    index, policy = short_pair(DEFAULT_MAX_NAME_LENGTH, 10)
    extras = {KEY_ONE: "Acct 00001"}
    probe = row(dict(extras))
    before = (
        dict(probe.values),
        dict(probe.extras),
        index.accounts,
        dict(index.account_by_ref),
        dict(index.account_ref_by_link),
        dict(index.account_refs_by_name),
        index.max_name_length,
        policy.source_name_keys,
        policy.max_name_length,
    )
    batch = [probe]

    with pytest.raises(NameLengthMismatchError):
        match_rows(batch, index, policy)

    assert batch == [probe]
    assert (
        dict(probe.values),
        dict(probe.extras),
        index.accounts,
        dict(index.account_by_ref),
        dict(index.account_ref_by_link),
        dict(index.account_refs_by_name),
        index.max_name_length,
        policy.source_name_keys,
        policy.max_name_length,
    ) == before
    assert dict(probe.extras) == extras


def test_the_index_and_policy_stay_deeply_immutable_after_a_refusal() -> None:
    index, policy = short_pair(DEFAULT_MAX_NAME_LENGTH, 10)
    with pytest.raises(NameLengthMismatchError):
        match_rows([], index, policy)

    for mapping in (
        index.account_by_ref,
        index.account_ref_by_link,
        index.account_refs_by_name,
    ):
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping["ACC-999999"] = None  # type: ignore[index]
    assert isinstance(index.accounts, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        index.duplicate_links = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.max_name_length = 256  # type: ignore[misc]


def test_shuffled_master_order_is_unaffected_by_the_check() -> None:
    accounts = [*PLAIN_ACCOUNTS, TWIN_A, TWIN_B]
    shuffler = random.Random(41)
    index, policy = make_pair(max_name_length=64)
    baseline = match_row(row({KEY_ONE: NAME_A}), 0, index, policy)
    assert baseline.account_ref == ACCOUNT_A
    for _ in range(10):
        shuffler.shuffle(accounts)
        shuffled, shuffled_policy = make_pair(
            max_name_length=64, accounts=tuple(accounts)
        )
        assert match_row(row({KEY_ONE: NAME_A}), 0, shuffled, shuffled_policy) == baseline


def test_one_bound_governs_indexing_lookup_eligibility_and_scoring() -> None:
    """All four uses move together when the single bound moves.

    At nineteen the master name is indexed, the source name is eligible, and
    the exact-name lookup finds it. At eighteen the index refuses the same
    master name and normalization refuses the same source name — one bound,
    four consequences.
    """
    name = "Demo Account 000000"  # 19 characters
    index, policy = make_pair(max_name_length=19, accounts=((ACCOUNT_A, name),), links=())
    assert index.accounts_named(normalize_name(name, max_length=19)) == (ACCOUNT_A,)
    assert match_row(row({KEY_ONE: name}), 0, index, policy).account_ref == ACCOUNT_A

    with pytest.raises(OverlongNameError):
        build_master_index([account_row(ACCOUNT_A, name)], [], max_name_length=18)
    assert normalize_name(name, max_length=18) is None

    # Scoring inputs too: with the pair at eighteen, nothing is comparable.
    small_index, small_policy = make_pair(
        max_name_length=18, accounts=(("ACC-100000", "Short Name"),), links=()
    )
    scored = match_row(row({KEY_ONE: name}), 0, small_index, small_policy)
    assert scored.outcome is MatchOutcome.INELIGIBLE
    assert scored.reason == REASON_NO_MATCHING_INPUT


def test_the_helpers_build_both_halves_from_one_bound() -> None:
    """Not two defaults that happen to agree: one name, read by both."""
    import inspect

    import match_helpers

    index_default = inspect.signature(make_index).parameters["max_name_length"].default
    policy_default = inspect.signature(make_policy).parameters["max_name_length"].default
    assert index_default == policy_default == match_helpers.SHARED_MAX_NAME_LENGTH

    index, policy = make_pair(max_name_length=123)
    assert index.max_name_length == policy.max_name_length == 123
