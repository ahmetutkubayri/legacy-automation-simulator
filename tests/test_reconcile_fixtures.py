"""T12 against the committed synthetic fixtures, through T9, T10 and T11.

Every expected count is derived from the generator's declared contract or from
the pipeline's own earlier stages — the constants are imported, never typed
from memory — and each assertion names where its number comes from.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from las.config import RunConfig, load_config
from las.ingest import (
    ACCOUNT_LINK_SCHEMA,
    ACCOUNT_SCHEMA,
    IngestResult,
    feed_schema,
    read_source,
)
from las.match import (
    MasterIndex,
    MatchOutcome,
    build_master_index,
    match_validated,
)
from las.match import (
    policy_from_config as match_policy,
)
from las.reconcile import (
    REASON_INVALID_AMOUNT,
    REASON_MATCH_NEEDS_REVIEW,
    REASON_MATCH_UNMATCHED,
    BatchReconcileResult,
    FeedBatch,
    ReconcileOutcome,
    SystemLedgerRow,
    policy_from_config,
    reconcile,
)
from las.validate import policy_from_config as validation_policy
from las.validate import validate_ingest_result
from reconcile_helpers import ledger_rows_for_fixtures

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
FIXTURE_NAMES = ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv")

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
    return build_master_index(*master)


@pytest.fixture(scope="module")
def batches(
    config: RunConfig, index: MasterIndex, master: tuple[IngestResult, IngestResult]
) -> tuple[FeedBatch, ...]:
    """Both feeds, each carried through T9, T10 and T11, aligned."""
    known = frozenset(value for value in master[1].column("external_ref") if value)
    vpolicy = validation_policy(config.validation, known_external_refs=known)
    mpolicy = match_policy(config.match)
    built: list[FeedBatch] = []
    for feed in config.feeds:
        ingested = read_source(REPO_ROOT / feed.path, feed_schema(feed))
        validated = validate_ingest_result(ingested, vpolicy)
        built.append(
            FeedBatch(validation=validated, matching=match_validated(validated, index, mpolicy))
        )
    return tuple(built)


@pytest.fixture(scope="module")
def system_rows(index: MasterIndex) -> tuple[SystemLedgerRow, ...]:
    return ledger_rows_for_fixtures(SAMPLES_DIR, index)


@pytest.fixture(scope="module")
def result(
    config: RunConfig,
    batches: tuple[FeedBatch, ...],
    system_rows: tuple[SystemLedgerRow, ...],
) -> BatchReconcileResult:
    return reconcile(batches, system_rows, policy_from_config(config.report))


# --- the two sides ------------------------------------------------------------


def test_both_feeds_are_supplied_as_one_side(batches: tuple[FeedBatch, ...]) -> None:
    """Source: config.feeds — two feeds, neither of them a side of its own."""
    assert len(batches) == 2
    assert {batch.source for batch in batches} == {"feed_alpha.xlsx", "feed_beta.csv"}


def test_the_system_side_is_the_whole_derived_ledger(
    system_rows: tuple[SystemLedgerRow, ...]
) -> None:
    """Source: gen.LINK_COUNT mapping rows times the ledger's six periods."""
    from las.simulator.store import LEDGER_PERIODS

    assert len(system_rows) == gen.LINK_COUNT * len(LEDGER_PERIODS)
    assert all(row.account_ref.startswith("ACC-") for row in system_rows)
    assert all(isinstance(row.amount, Decimal) for row in system_rows)


def test_every_system_row_crossed_over_as_the_neutral_dto(
    system_rows: tuple[SystemLedgerRow, ...]
) -> None:
    """What T14's adapter will produce: no simulator object travels."""
    for row in system_rows:
        assert isinstance(row, SystemLedgerRow)
        assert type(row).__module__ == "las.reconcile.models"
        assert isinstance(row.amount_text, str)


# --- row accounting -----------------------------------------------------------


def test_every_committed_feed_row_is_accounted_for_exactly_once(
    result: BatchReconcileResult,
) -> None:
    """Source: gen.FEED_ALPHA_ROW_COUNT + gen.FEED_BETA_ROW_COUNT."""
    expected = gen.FEED_ALPHA_ROW_COUNT + gen.FEED_BETA_ROW_COUNT
    summary = result.summary
    assert summary.feed_rows_in == expected
    assert summary.feed_contributions + summary.unresolved_count == expected
    assert summary.feed_rows_accounted == expected
    assert summary.accounting_holds


def test_no_feed_row_is_both_a_contribution_and_unresolved(
    result: BatchReconcileResult,
) -> None:
    contributing = [
        (c.source, c.source_row)
        for group in result.groups
        for c in group.feed.contributions
    ]
    unresolved = [(row.source, row.source_row) for row in result.unresolved]
    assert not set(contributing) & set(unresolved)
    assert len(contributing) + len(unresolved) == result.summary.feed_rows_in


def test_every_system_row_contributes_exactly_once(
    result: BatchReconcileResult, system_rows: tuple[SystemLedgerRow, ...]
) -> None:
    assert result.summary.system_rows_in == len(system_rows)
    assert result.summary.system_contributions == len(system_rows)
    positions = [
        c.source_row for group in result.groups for c in group.system.contributions
    ]
    assert len(positions) == len(set(positions)) == len(system_rows)


# --- eligibility, straight from T11's verdicts --------------------------------


def test_the_accepted_matches_are_the_exact_links_less_bad_amounts(
    result: BatchReconcileResult, batches: tuple[FeedBatch, ...]
) -> None:
    """T11 accepted 204; the rows with unusable amounts cannot be added up.

    Source: gen.INVALID_AMOUNT_COUNT (negative or unparseable) plus
    gen.MISSING_VALUE_COUNT (an empty amount, two per feed).
    """
    accepted = sum(
        1
        for batch in batches
        for decision in batch.matching.decisions
        if decision.outcome in (MatchOutcome.EXACT, MatchOutcome.FUZZY)
    )
    bad_amounts = gen.INVALID_AMOUNT_COUNT + gen.MISSING_VALUE_COUNT
    assert result.summary.unresolved_by_reason[REASON_INVALID_AMOUNT] == bad_amounts
    assert result.summary.feed_contributions == accepted - bad_amounts


def test_needs_review_rows_stay_unresolved(result: BatchReconcileResult) -> None:
    """Source: gen.AMBIGUOUS_MATCH_COUNT — never promoted into a total."""
    assert (
        result.summary.unresolved_by_reason[REASON_MATCH_NEEDS_REVIEW]
        == gen.AMBIGUOUS_MATCH_COUNT
    )


def test_unmatched_rows_stay_unresolved(result: BatchReconcileResult) -> None:
    """Source: gen.ORPHAN_REFERENCE_COUNT — never resolved by reconciliation."""
    assert (
        result.summary.unresolved_by_reason[REASON_MATCH_UNMATCHED]
        == gen.ORPHAN_REFERENCE_COUNT
    )


def test_the_unresolved_reasons_are_exactly_those_three(
    result: BatchReconcileResult,
) -> None:
    assert set(result.summary.unresolved_by_reason) == {
        REASON_INVALID_AMOUNT,
        REASON_MATCH_NEEDS_REVIEW,
        REASON_MATCH_UNMATCHED,
    }


def test_a_malformed_record_id_never_blocked_a_row(
    result: BatchReconcileResult, batches: tuple[FeedBatch, ...]
) -> None:
    """Source: gen.INVALID_RECORD_ID_COUNT — all in beta, all still matched.

    Their identifiers are invalid but their amounts, units and periods are
    fine, so they contribute like any other row.
    """
    invalid_ids = {value for value in gen.INVALID_RECORD_ID_VALUES}
    offenders = [
        validated.row.provenance.source_row
        for batch in batches
        for validated in batch.validation.rows
        if validated.row.values.get("record_id") in invalid_ids
    ]
    assert len(offenders) == gen.INVALID_RECORD_ID_COUNT
    contributing = {
        c.source_row for group in result.groups for c in group.feed.contributions
    }
    assert set(offenders) <= contributing


# --- the injected unit mismatches ---------------------------------------------


def test_the_injected_unit_mismatches_are_all_recalled(
    result: BatchReconcileResult, batches: tuple[FeedBatch, ...]
) -> None:
    """Source: gen.UNIT_MISMATCH_COUNT — one record in both feeds, EA vs KG.

    Both feeds are the same side, so the pair lands in one account-and-period
    group carrying two units. Unit is not in the key precisely so that this is
    visible as a verdict instead of splitting into two tidy groups.
    """
    injected = {f"OPR-{30000000 + index}" for index in range(gen.UNIT_MISMATCH_COUNT)}
    keys_with_injected = set()
    for batch in batches:
        for validated, decision in zip(
            batch.validation.rows, batch.matching.decisions, strict=True
        ):
            if validated.row.values.get("record_id") in injected:
                keys_with_injected.add(
                    (decision.account_ref, validated.row.values["period"])
                )

    assert len(injected) == gen.UNIT_MISMATCH_COUNT
    by_key = {(g.key.account_ref, g.key.period): g for g in result.groups}
    for key in keys_with_injected:
        assert by_key[key].outcome is ReconcileOutcome.UNIT_MISMATCH


def test_the_injected_pairs_kept_both_units_without_converting(
    result: BatchReconcileResult, batches: tuple[FeedBatch, ...]
) -> None:
    injected = {f"OPR-{30000000 + index}" for index in range(gen.UNIT_MISMATCH_COUNT)}
    seen_units: Counter[str] = Counter()
    for batch in batches:
        for validated in batch.validation.rows:
            if validated.row.values.get("record_id") in injected:
                seen_units[validated.row.values["unit"]] += 1
    # The generator writes EA into alpha and KG into beta for each pair.
    assert seen_units == Counter(
        {"EA": gen.UNIT_MISMATCH_COUNT, "KG": gen.UNIT_MISMATCH_COUNT}
    )


def test_no_unit_mismatch_group_publishes_a_variance(
    result: BatchReconcileResult,
) -> None:
    groups = result.with_outcome(ReconcileOutcome.UNIT_MISMATCH)
    assert groups, "the committed fixtures must exercise this path"
    for group in groups:
        assert group.signed_difference is None
        assert group.absolute_difference is None
        assert group.variance_band is None


def test_no_group_ever_summed_across_units(result: BatchReconcileResult) -> None:
    for group in result.groups:
        for side in (group.feed, group.system):
            if len(side.totals_by_unit) > 1:
                assert side.total is None


# --- outcomes -----------------------------------------------------------------


def test_every_group_has_one_outcome_and_the_counts_agree(
    result: BatchReconcileResult,
) -> None:
    summary = result.summary
    assert summary.group_count == len(result.groups)
    assert sum(summary.groups_by_outcome.values()) == summary.group_count
    assert sum(summary.groups_by_reason.values()) == summary.group_count


def test_groups_missing_a_feed_side_are_ledger_only_periods(
    result: BatchReconcileResult,
) -> None:
    """The ledger covers every mapping row in six periods; the feeds do not."""
    missing = result.with_outcome(ReconcileOutcome.MISSING_IN_FEED)
    assert missing
    for group in missing:
        assert not group.feed.contributions
        assert group.system.contributions
        assert group.signed_difference is None


def test_the_committed_run_exercises_presence_units_and_arithmetic(
    result: BatchReconcileResult,
) -> None:
    produced = set(result.summary.groups_by_outcome)
    assert {"missing_in_feed", "unit_mismatch"} <= produced
    assert produced & {"balanced", "within_tolerance", "mismatch"}


def test_groups_are_ordered_by_account_then_period(
    result: BatchReconcileResult,
) -> None:
    keys = [group.key.sort_key for group in result.groups]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def test_reconciling_twice_gives_an_equal_result(
    config: RunConfig,
    batches: tuple[FeedBatch, ...],
    system_rows: tuple[SystemLedgerRow, ...],
) -> None:
    policy = policy_from_config(config.report)
    assert reconcile(batches, system_rows, policy) == reconcile(
        batches, system_rows, policy
    )


def test_supplying_the_feeds_in_the_other_order_gives_an_equal_result(
    config: RunConfig,
    batches: tuple[FeedBatch, ...],
    system_rows: tuple[SystemLedgerRow, ...],
) -> None:
    policy = policy_from_config(config.report)
    assert reconcile(batches, system_rows, policy) == reconcile(
        tuple(reversed(batches)), system_rows, policy
    )


def test_shuffling_the_system_rows_gives_an_equal_result(
    config: RunConfig,
    batches: tuple[FeedBatch, ...],
    system_rows: tuple[SystemLedgerRow, ...],
) -> None:
    import random

    shuffled = list(system_rows)
    random.Random(5).shuffle(shuffled)
    policy = policy_from_config(config.report)
    assert reconcile(batches, tuple(shuffled), policy) == reconcile(
        batches, system_rows, policy
    )


# --- boundaries T12 does not cross --------------------------------------------


def test_reconciliation_changed_no_matching_decision(
    result: BatchReconcileResult, batches: tuple[FeedBatch, ...], index: MasterIndex,
    config: RunConfig,
) -> None:
    known = frozenset(
        value
        for batch in batches
        for decision in batch.matching.decisions
        for value in (decision.external_ref,)
        if value
    )
    assert known  # the decisions still carry what matching put there
    before = [
        (d.outcome, d.reason, d.account_ref)
        for batch in batches
        for d in batch.matching.decisions
    ]
    reconcile(batches, (), policy_from_config(config.report))
    after = [
        (d.outcome, d.reason, d.account_ref)
        for batch in batches
        for d in batch.matching.decisions
    ]
    assert before == after


def test_reconciliation_changed_no_source_row(batches: tuple[FeedBatch, ...],
                                              config: RunConfig) -> None:
    before = [
        (dict(validated.row.values), validated.row.provenance)
        for batch in batches
        for validated in batch.validation.rows
    ]
    reconcile(batches, (), policy_from_config(config.report))
    after = [
        (dict(validated.row.values), validated.row.provenance)
        for batch in batches
        for validated in batch.validation.rows
    ]
    assert before == after


def test_reconciliation_changed_no_validation_issue(
    batches: tuple[FeedBatch, ...], config: RunConfig
) -> None:
    before = [
        (issue.code, issue.severity, issue.field, issue.source_row)
        for batch in batches
        for issue in batch.validation.issues
    ]
    reconcile(batches, (), policy_from_config(config.report))
    after = [
        (issue.code, issue.severity, issue.field, issue.source_row)
        for batch in batches
        for issue in batch.validation.issues
    ]
    assert before == after


def test_no_result_object_implies_reporting_or_a_pipeline(
    result: BatchReconcileResult,
) -> None:
    for forbidden in ("report", "sheet", "workbook", "html", "json", "pipeline", "stage"):
        assert not any(forbidden in name for name in vars(result))
        assert not any(forbidden in name for name in vars(result.groups[0]))


def test_no_unit_conversion_happened_anywhere(result: BatchReconcileResult) -> None:
    """Units survive verbatim; the ledger's three are all that appear."""
    from las.simulator.store import LEDGER_UNITS

    seen = {
        c.unit
        for group in result.groups
        for side in (group.feed, group.system)
        for c in side.contributions
    }
    assert seen <= set(LEDGER_UNITS)


# --- the fixtures are untouched -----------------------------------------------


def test_reconciling_leaves_every_committed_fixture_byte_identical() -> None:
    documented = {
        match.group(2).split("/")[-1]: match.group(1)
        for match in re.finditer(
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
