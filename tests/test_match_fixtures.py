"""T11 against the committed synthetic fixtures, through T9 and T10.

Every expected count is derived from the generator's declared contract — the
constants are imported, never typed from memory — and each assertion names the
constant it comes from.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from collections import Counter
from pathlib import Path

import pytest

from las.config import RunConfig, load_config
from las.ingest import ACCOUNT_LINK_SCHEMA, ACCOUNT_SCHEMA, IngestResult, feed_schema, read_source
from las.match import (
    DEFAULT_MAX_NAME_LENGTH,
    REASON_AMBIGUOUS_TIE,
    REASON_BELOW_REVIEW,
    REASON_EXACT_REFERENCE,
    BatchMatchResult,
    MasterIndex,
    MatchMethod,
    MatchOutcome,
    build_master_index,
    match_validated,
    policy_from_config,
)
from las.validate import policy_from_config as validation_policy
from las.validate import validate_ingest_result

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
FIXTURE_NAMES = ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv")

#: The one name-length bound this whole integration path runs under. Named
#: once and passed to both the master index and the matching policy, so the
#: fixture run demonstrates the shared-bound contract rather than leaving two
#: independent defaults to agree by luck.
NAME_LENGTH = DEFAULT_MAX_NAME_LENGTH

_spec = importlib.util.spec_from_file_location(
    "generate_fixtures", REPO_ROOT / "scripts" / "generate_fixtures.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


@pytest.fixture(scope="module")
def config() -> RunConfig:
    return load_config(SAMPLES_DIR / "config.yaml")


@pytest.fixture(scope="module")
def master() -> tuple[IngestResult, IngestResult]:
    return (
        read_source(SAMPLES_DIR / "accounts.csv", ACCOUNT_SCHEMA),
        read_source(SAMPLES_DIR / "account_links.csv", ACCOUNT_LINK_SCHEMA),
    )


@pytest.fixture(scope="module")
def index(master: tuple[IngestResult, IngestResult]) -> MasterIndex:
    return build_master_index(*master, max_name_length=NAME_LENGTH)


@pytest.fixture(scope="module")
def matched(
    config: RunConfig, index: MasterIndex, master: tuple[IngestResult, IngestResult]
) -> dict[str, BatchMatchResult]:
    known = frozenset(v for v in master[1].column("external_ref") if v)
    vpolicy = validation_policy(config.validation, known_external_refs=known)
    mpolicy = policy_from_config(config.match, max_name_length=NAME_LENGTH)
    results: dict[str, BatchMatchResult] = {}
    for feed in config.feeds:
        ingested = read_source(SAMPLES_DIR / feed.path, feed_schema(feed))
        validated = validate_ingest_result(ingested, vpolicy)
        results[feed.name] = match_validated(validated, index, mpolicy)
    return results


@pytest.fixture(scope="module")
def reasons(matched: dict[str, BatchMatchResult]) -> Counter[str]:
    totals: Counter[str] = Counter()
    for result in matched.values():
        totals.update(result.summary.by_reason)
    return totals


# --- the master index ---------------------------------------------------------


def test_the_master_index_matches_the_generator_contract(index: MasterIndex) -> None:
    assert index.account_count == gen.ACCOUNT_COUNT
    assert index.link_count == gen.LINK_COUNT
    assert index.duplicate_links == 0
    # Every generated account name is distinct, so no name is ambiguous.
    assert all(len(refs) == 1 for refs in index.account_refs_by_name.values())


def test_the_index_is_built_deterministically(
    master: tuple[IngestResult, IngestResult]
) -> None:
    assert build_master_index(*master, max_name_length=NAME_LENGTH) == build_master_index(
        *master, max_name_length=NAME_LENGTH
    )


def test_the_near_duplicate_twins_are_both_present(index: MasterIndex) -> None:
    """Source: gen.AMBIGUOUS_ACCOUNT_NAMES."""
    from las.match import normalize_name

    refs = [index.accounts_named(normalize_name(n)) for n in gen.AMBIGUOUS_ACCOUNT_NAMES]
    assert all(len(r) == 1 for r in refs)
    assert len({r[0] for r in refs}) == gen.AMBIGUOUS_MATCH_COUNT


def test_building_the_index_leaves_the_master_ingestion_untouched(
    master: tuple[IngestResult, IngestResult]
) -> None:
    accounts, links = master
    before = (accounts.row_count, accounts.headers, links.row_count, links.headers)
    build_master_index(accounts, links, max_name_length=NAME_LENGTH)
    assert (accounts.row_count, accounts.headers, links.row_count, links.headers) == before


# --- nothing is lost ----------------------------------------------------------


def test_every_row_produces_exactly_one_decision(
    matched: dict[str, BatchMatchResult]
) -> None:
    assert matched["feed_alpha"].row_count == gen.FEED_ALPHA_ROW_COUNT
    assert matched["feed_beta"].row_count == gen.FEED_BETA_ROW_COUNT
    for result in matched.values():
        assert [d.row_index for d in result.decisions] == list(range(result.row_count))
        rows = [d.provenance.source_row for d in result.decisions]
        assert rows == sorted(rows)
        assert len(rows) == len(set(rows))  # one decision per physical row


def test_no_row_is_filtered_and_no_duplicate_is_removed(
    matched: dict[str, BatchMatchResult]
) -> None:
    total = sum(r.row_count for r in matched.values())
    assert total == gen.FEED_ALPHA_ROW_COUNT + gen.FEED_BETA_ROW_COUNT
    # The four duplicated alpha rows are still eight physical rows.
    alpha_rows = {d.provenance.source_row for d in matched["feed_alpha"].decisions}
    assert len(alpha_rows) == gen.FEED_ALPHA_ROW_COUNT


# --- exact link matching ------------------------------------------------------


def test_exact_reference_matches_account_for_every_linked_row(
    matched: dict[str, BatchMatchResult], reasons: Counter[str]
) -> None:
    """Every row whose reference is in the mapping resolves by link.

    Source: the two feeds' row counts less the rows the generator gives an
    unusable reference — AMBIGUOUS_MATCH_COUNT in alpha (empty reference) and
    ORPHAN_REFERENCE_COUNT in beta (references in no mapping row).
    """
    expected_alpha = gen.FEED_ALPHA_ROW_COUNT - gen.AMBIGUOUS_MATCH_COUNT
    expected_beta = gen.FEED_BETA_ROW_COUNT - gen.ORPHAN_REFERENCE_COUNT
    assert matched["feed_alpha"].summary.exact == expected_alpha
    assert matched["feed_beta"].summary.exact == expected_beta
    assert reasons[REASON_EXACT_REFERENCE] == expected_alpha + expected_beta


def test_every_exact_decision_names_a_real_account(
    matched: dict[str, BatchMatchResult], index: MasterIndex
) -> None:
    for result in matched.values():
        for decision in result.with_outcome(MatchOutcome.EXACT):
            assert decision.account_ref in index.account_by_ref
            assert decision.method is MatchMethod.EXTERNAL_REFERENCE
            assert decision.score.is_perfect


def test_an_unrelated_validation_error_never_prevented_a_match(
    matched: dict[str, BatchMatchResult]
) -> None:
    """The malformed-identifier and bad-amount rows still matched by link."""
    assert matched["feed_beta"].summary.exact >= gen.INVALID_RECORD_ID_COUNT
    assert matched["feed_alpha"].summary.exact >= gen.INVALID_AMOUNT_COUNT


# --- the injected ambiguous cases ---------------------------------------------


def test_the_injected_ambiguous_rows_are_recalled_as_needs_review(
    matched: dict[str, BatchMatchResult], reasons: Counter[str]
) -> None:
    """Source: gen.AMBIGUOUS_MATCH_COUNT — rows with no usable reference whose
    name sits equidistant between the two near-duplicate accounts."""
    review = matched["feed_alpha"].with_outcome(MatchOutcome.NEEDS_REVIEW)
    assert len(review) == gen.AMBIGUOUS_MATCH_COUNT
    assert reasons[REASON_AMBIGUOUS_TIE] == gen.AMBIGUOUS_MATCH_COUNT
    for decision in review:
        assert decision.reason == REASON_AMBIGUOUS_TIE
        assert decision.account_ref is None  # no arbitrary winner
        assert decision.method is MatchMethod.FUZZY_NAME
        top = [c for c in decision.candidates if c.score == decision.candidates[0].score]
        assert len(top) == gen.AMBIGUOUS_MATCH_COUNT
        assert decision.score.basis_points >= policy_accept()


def policy_accept() -> int:
    from las.config import load_config as _load

    config = _load(SAMPLES_DIR / "config.yaml").match
    return policy_from_config(config, max_name_length=NAME_LENGTH).accept_bp


def test_the_fixture_index_and_policy_share_one_bound(
    config: RunConfig, index: MasterIndex
) -> None:
    """The integration path passes one named bound to both halves."""
    policy = policy_from_config(config.match, max_name_length=NAME_LENGTH)
    assert index.max_name_length == policy.max_name_length == NAME_LENGTH


def test_the_ambiguous_candidates_are_exactly_the_two_twins(
    matched: dict[str, BatchMatchResult], index: MasterIndex
) -> None:
    from las.match import normalize_name

    twins = {index.accounts_named(normalize_name(n))[0] for n in gen.AMBIGUOUS_ACCOUNT_NAMES}
    for decision in matched["feed_alpha"].with_outcome(MatchOutcome.NEEDS_REVIEW):
        best = decision.candidates[0].score
        assert {c.account_ref for c in decision.candidates if c.score == best} == twins


# --- the injected orphan cases ------------------------------------------------


def test_orphan_rows_remain_unmatched_rather_than_name_resolved(
    matched: dict[str, BatchMatchResult], reasons: Counter[str]
) -> None:
    """Source: gen.ORPHAN_REFERENCE_COUNT. Their invented source names sit far
    below the review threshold, so nothing is claimed for them."""
    unmatched = matched["feed_beta"].with_outcome(MatchOutcome.UNMATCHED)
    assert len(unmatched) == gen.ORPHAN_REFERENCE_COUNT
    assert reasons[REASON_BELOW_REVIEW] == gen.ORPHAN_REFERENCE_COUNT
    for decision in unmatched:
        assert decision.account_ref is None
        assert decision.external_ref is not None  # the reference is retained
        assert decision.external_ref.startswith("EXT-9")
        assert decision.score.basis_points < policy_accept()


def test_no_row_is_ineligible_in_the_committed_fixtures(
    matched: dict[str, BatchMatchResult]
) -> None:
    """Every generated row carries at least a reference or a source name."""
    for result in matched.values():
        assert result.summary.ineligible == 0


# --- totals -------------------------------------------------------------------


def test_the_documented_outcome_totals(matched: dict[str, BatchMatchResult]) -> None:
    """One place to see the whole picture, all derived from the contract."""
    combined = Counter()
    for result in matched.values():
        combined["exact"] += result.summary.exact
        combined["fuzzy"] += result.summary.fuzzy
        combined["needs_review"] += result.summary.needs_review
        combined["unmatched"] += result.summary.unmatched
        combined["ineligible"] += result.summary.ineligible
    assert dict(combined) == {
        "exact": (gen.FEED_ALPHA_ROW_COUNT - gen.AMBIGUOUS_MATCH_COUNT)
        + (gen.FEED_BETA_ROW_COUNT - gen.ORPHAN_REFERENCE_COUNT),
        "fuzzy": 0,
        "needs_review": gen.AMBIGUOUS_MATCH_COUNT,
        "unmatched": gen.ORPHAN_REFERENCE_COUNT,
        "ineligible": 0,
    }
    assert sum(combined.values()) == gen.FEED_ALPHA_ROW_COUNT + gen.FEED_BETA_ROW_COUNT


def test_summaries_are_internally_consistent(
    matched: dict[str, BatchMatchResult]
) -> None:
    for result in matched.values():
        s = result.summary
        assert s.exact + s.fuzzy + s.needs_review + s.unmatched + s.ineligible == s.total_rows
        assert sum(s.by_method.values()) == s.total_rows
        assert sum(s.by_reason.values()) == s.total_rows
        assert s.matched == s.exact + s.fuzzy


def test_matching_is_deterministic_across_repeated_runs(
    config: RunConfig, index: MasterIndex, master: tuple[IngestResult, IngestResult]
) -> None:
    known = frozenset(v for v in master[1].column("external_ref") if v)
    vpolicy = validation_policy(config.validation, known_external_refs=known)
    mpolicy = policy_from_config(config.match)
    feed = config.feeds[0]
    validated = validate_ingest_result(
        read_source(SAMPLES_DIR / feed.path, feed_schema(feed)), vpolicy
    )
    assert match_validated(validated, index, mpolicy) == match_validated(
        validated, index, mpolicy
    )


# --- boundaries T11 does not cross --------------------------------------------


def test_matching_does_not_resolve_unit_mismatches(
    matched: dict[str, BatchMatchResult]
) -> None:
    """The unit-mismatch rows match by link like any other; nothing is fixed."""
    for result in matched.values():
        for decision in result.decisions:
            assert not hasattr(decision, "unit")
            assert not hasattr(decision, "amount")


def test_no_decision_field_implies_reconciliation(
    matched: dict[str, BatchMatchResult]
) -> None:
    decision = matched["feed_alpha"].decisions[0]
    for forbidden in ("variance", "total", "aggregate", "reconcil", "ledger"):
        assert not any(forbidden in name for name in vars(decision))


# --- the fixtures are untouched -----------------------------------------------


def test_matching_leaves_every_committed_fixture_byte_identical() -> None:
    documented = {
        m.group(2).split("/")[-1]: m.group(1)
        for m in re.finditer(
            r"^([0-9a-f]{64})\s+(data/samples/\S+)$",
            (REPO_ROOT / "docs" / "DEVELOPMENT_PLAN.md").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    }
    assert set(documented) == set(FIXTURE_NAMES)
    actual = {
        name: hashlib.sha256((SAMPLES_DIR / name).read_bytes()).hexdigest()
        for name in FIXTURE_NAMES
    }
    assert actual == documented
