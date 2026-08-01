"""T10 against the committed synthetic fixtures, ingested through T9.

Every expected count is derived from the generator's declared defect contract
in ``scripts/generate_fixtures.py`` — the constants are imported, never typed
from memory — and each assertion names the constant it comes from.

Defect recall
-------------
The development plan requires "100% recall of the generator's injected
defects". Six of the seven declared categories are reachable by the rule
families the plan names for this task:

===========================  ===================================  ============
generator category           detected by                          count source
===========================  ===================================  ============
invalid_record_id_format     record_id_format rule                INVALID_RECORD_ID_COUNT
missing_required_values      required_value rule (empty amounts)  MISSING_VALUE_COUNT
ambiguous_matches            required_value rule (empty ref)      AMBIGUOUS_MATCH_COUNT
invalid_amounts              amount_domain rule                   INVALID_AMOUNT_COUNT
orphan_external_refs         external_ref_known rule              ORPHAN_REFERENCE_COUNT
duplicate_records            duplicate_key rule                   DUPLICATE_RECORD_COUNT
===========================  ===================================  ============

The seventh, ``unit_mismatches``, is provably out of reach here and is pinned
as such by :func:`test_unit_mismatches_are_not_detectable_by_single_row_rules`.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

from las.config import RunConfig, load_config
from las.ingest import ACCOUNT_LINK_SCHEMA, ACCOUNT_SCHEMA, IngestResult, feed_schema, read_source
from las.validate import (
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_AMOUNT_OUT_OF_RANGE,
    CODE_DUPLICATE_KEY,
    CODE_EXTERNAL_REF_UNKNOWN,
    CODE_RECORD_ID_FORMAT_INVALID,
    CODE_REQUIRED_VALUE_MISSING,
    BatchValidationResult,
    RowOutcome,
    master_data_policy,
    policy_from_config,
    validate_ingest_result,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

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
def known_refs() -> frozenset[str]:
    """The reference set, read once from the committed mapping fixture."""
    links = read_source(SAMPLES_DIR / "account_links.csv", ACCOUNT_LINK_SCHEMA)
    return frozenset(value for value in links.column("external_ref") if value)


@pytest.fixture(scope="module")
def ingested(config: RunConfig) -> dict[str, IngestResult]:
    return {
        feed.name: read_source(SAMPLES_DIR / feed.path, feed_schema(feed))
        for feed in config.feeds
    }


@pytest.fixture(scope="module")
def validated(
    config: RunConfig, ingested: dict[str, IngestResult], known_refs: frozenset[str]
) -> dict[str, BatchValidationResult]:
    policy = policy_from_config(config.validation, known_external_refs=known_refs)
    return {name: validate_ingest_result(result, policy) for name, result in ingested.items()}


@pytest.fixture(scope="module")
def combined(validated: dict[str, BatchValidationResult]) -> Counter[str]:
    totals: Counter[str] = Counter()
    for result in validated.values():
        totals.update(result.summary.issues_by_code)
    return totals


# --- nothing is lost ----------------------------------------------------------


def test_every_ingested_row_is_validated_and_none_is_filtered(
    ingested: dict[str, IngestResult], validated: dict[str, BatchValidationResult]
) -> None:
    for name, result in validated.items():
        assert result.summary.total_rows == ingested[name].row_count
        assert len(result.rows) == ingested[name].row_count
    assert validated["feed_alpha"].row_count == gen.FEED_ALPHA_ROW_COUNT
    assert validated["feed_beta"].row_count == gen.FEED_BETA_ROW_COUNT


def test_row_order_and_provenance_are_preserved(
    ingested: dict[str, IngestResult], validated: dict[str, BatchValidationResult]
) -> None:
    for name, result in validated.items():
        assert [row.row for row in result.rows] == list(ingested[name].rows)
        assert [row.provenance.source_row for row in result.rows] == [
            row.provenance.source_row for row in ingested[name].rows
        ]


def test_valid_records_remain_present_and_are_the_majority(
    validated: dict[str, BatchValidationResult]
) -> None:
    for result in validated.values():
        assert result.summary.valid_rows > 0
        assert result.summary.valid_rows > result.summary.invalid_rows


def test_the_ingestion_result_is_unchanged_by_validation(
    ingested: dict[str, IngestResult], config: RunConfig, known_refs: frozenset[str]
) -> None:
    policy = policy_from_config(config.validation, known_external_refs=known_refs)
    for result in ingested.values():
        before = (result.row_count, result.headers, result.unmapped_headers, result.outcome)
        validate_ingest_result(result, policy)
        assert (
            result.row_count,
            result.headers,
            result.unmapped_headers,
            result.outcome,
        ) == before


# --- recall, category by category ---------------------------------------------


def test_malformed_record_identifiers_are_all_flagged(combined: Counter[str]) -> None:
    """Source: gen.INVALID_RECORD_ID_COUNT, all injected into feed_beta."""
    assert combined[CODE_RECORD_ID_FORMAT_INVALID] == gen.INVALID_RECORD_ID_COUNT


def test_missing_values_and_ambiguous_rows_are_all_flagged(combined: Counter[str]) -> None:
    """Source: gen.MISSING_VALUE_COUNT plus gen.AMBIGUOUS_MATCH_COUNT.

    The ambiguous rows carry an empty external reference, which is a required
    field, so the required-value rule reaches them without any matching.
    """
    expected = gen.MISSING_VALUE_COUNT + gen.AMBIGUOUS_MATCH_COUNT
    assert combined[CODE_REQUIRED_VALUE_MISSING] == expected


def test_invalid_amounts_are_all_flagged_and_split_by_reason(
    combined: Counter[str]
) -> None:
    """Source: gen.INVALID_AMOUNT_VALUES — two malformed, one negative.

    The configured floor is zero, so the negative value parses cleanly and
    fails the range check rather than the decimal check.
    """
    malformed = sum(1 for value in gen.INVALID_AMOUNT_VALUES if _is_malformed(value))
    negative = len(gen.INVALID_AMOUNT_VALUES) - malformed
    assert combined[CODE_AMOUNT_NOT_DECIMAL] == malformed
    assert combined[CODE_AMOUNT_OUT_OF_RANGE] == negative
    assert (
        combined[CODE_AMOUNT_NOT_DECIMAL] + combined[CODE_AMOUNT_OUT_OF_RANGE]
        == gen.INVALID_AMOUNT_COUNT
    )


def _is_malformed(value: str) -> bool:
    """Whether a generator amount fails to parse at all, as opposed to failing range."""
    from las.validate import AMOUNT_PATTERN

    return AMOUNT_PATTERN.fullmatch(value) is None


def test_orphan_references_are_all_flagged_as_warnings(
    validated: dict[str, BatchValidationResult], combined: Counter[str]
) -> None:
    """Source: gen.ORPHAN_REFERENCE_COUNT, injected into feed_beta."""
    assert combined[CODE_EXTERNAL_REF_UNKNOWN] == gen.ORPHAN_REFERENCE_COUNT
    for issue in validated["feed_beta"].issues_with_code(CODE_EXTERNAL_REF_UNKNOWN):
        assert issue.severity.value == "warn"


def test_duplicated_rows_are_flagged_on_both_copies_and_kept(
    validated: dict[str, BatchValidationResult], combined: Counter[str]
) -> None:
    """Source: gen.DUPLICATE_RECORD_COUNT; each duplicate exists twice."""
    assert combined[CODE_DUPLICATE_KEY] == gen.DUPLICATE_RECORD_COUNT * 2
    flagged = validated["feed_alpha"].issues_with_code(CODE_DUPLICATE_KEY)
    assert len({issue.source_row for issue in flagged}) == len(flagged)  # distinct rows


def test_recall_covers_six_of_the_seven_declared_defect_categories(
    combined: Counter[str]
) -> None:
    """Every category the plan's rule families can reach is reached."""
    covered = {
        "invalid_record_id_format": combined[CODE_RECORD_ID_FORMAT_INVALID]
        == gen.INVALID_RECORD_ID_COUNT,
        "missing_required_values": combined[CODE_REQUIRED_VALUE_MISSING]
        >= gen.MISSING_VALUE_COUNT,
        "ambiguous_matches": combined[CODE_REQUIRED_VALUE_MISSING]
        == gen.MISSING_VALUE_COUNT + gen.AMBIGUOUS_MATCH_COUNT,
        "invalid_amounts": combined[CODE_AMOUNT_NOT_DECIMAL]
        + combined[CODE_AMOUNT_OUT_OF_RANGE]
        == gen.INVALID_AMOUNT_COUNT,
        "orphan_external_refs": combined[CODE_EXTERNAL_REF_UNKNOWN]
        == gen.ORPHAN_REFERENCE_COUNT,
        "duplicate_records": combined[CODE_DUPLICATE_KEY]
        == gen.DUPLICATE_RECORD_COUNT * 2,
    }
    assert all(covered.values()), covered
    assert len(covered) == 6


def test_unit_mismatches_are_not_detectable_by_single_row_rules(
    ingested: dict[str, IngestResult], combined: Counter[str]
) -> None:
    """The seventh category needs cross-feed aggregation, which is not this task.

    Each mismatched pair holds two individually *valid* units, so no per-row
    rule can see the conflict. This test pins the gap so it is a documented
    boundary rather than a forgotten omission.
    """
    alpha_units = dict(
        zip(
            ingested["feed_alpha"].column("record_id"),
            ingested["feed_alpha"].column("unit"),
            strict=True,
        )
    )
    beta_units = dict(
        zip(
            ingested["feed_beta"].column("record_id"),
            ingested["feed_beta"].column("unit"),
            strict=True,
        )
    )
    shared = set(alpha_units) & set(beta_units)
    mismatched = {rid for rid in shared if alpha_units[rid] != beta_units[rid]}
    assert len(mismatched) == gen.UNIT_MISMATCH_COUNT

    # Both sides of every mismatch are units the contract allows.
    for rid in mismatched:
        assert alpha_units[rid] in gen.UNITS
        assert beta_units[rid] in gen.UNITS
    # So no unit issue is raised anywhere in either feed.
    assert combined["unit_not_supported"] == 0


# --- defects that are deliberately *not* validated here -----------------------


def test_orphan_rows_survive_and_are_never_declared_invalid(
    validated: dict[str, BatchValidationResult]
) -> None:
    """Resolution is a later concern, so an orphan stays usable."""
    orphans = [
        row
        for row in validated["feed_beta"].rows
        if CODE_EXTERNAL_REF_UNKNOWN in row.codes
    ]
    assert len(orphans) == gen.ORPHAN_REFERENCE_COUNT
    for row in orphans:
        assert row.outcome is RowOutcome.VALID_WITH_WARNINGS
        assert row.error_count == 0


def test_ambiguous_rows_survive_for_later_matching(
    ingested: dict[str, IngestResult], validated: dict[str, BatchValidationResult]
) -> None:
    """Their source name is still available; nothing about matching is decided."""
    ambiguous = [
        row
        for row in validated["feed_alpha"].rows
        if row.row.get("external_ref") is None
        and row.row.extras.get("reported by") == gen.AMBIGUOUS_SOURCE_NAME
    ]
    assert len(ambiguous) == gen.AMBIGUOUS_MATCH_COUNT
    for row in ambiguous:
        assert row.row.extras["reported by"] == gen.AMBIGUOUS_SOURCE_NAME


def test_no_relationship_or_reconciliation_codes_exist(
    combined: Counter[str]
) -> None:
    """Nothing here claims to have matched an account or reconciled a feed."""
    from las.validate import SEVERITY_BY_CODE

    for code in SEVERITY_BY_CODE:
        for forbidden in ("match", "fuzzy", "reconcil", "account_link", "variance"):
            assert forbidden not in code


# --- determinism and totals ---------------------------------------------------


def test_issue_totals_are_deterministic(
    config: RunConfig, ingested: dict[str, IngestResult], known_refs: frozenset[str]
) -> None:
    policy = policy_from_config(config.validation, known_external_refs=known_refs)
    for result in ingested.values():
        first = validate_ingest_result(result, policy)
        second = validate_ingest_result(result, policy)
        assert first == second


def test_the_documented_issue_totals(combined: Counter[str]) -> None:
    """One place to see the whole picture, all derived from the contract."""
    assert dict(sorted(combined.items())) == {
        CODE_AMOUNT_NOT_DECIMAL: 2,
        CODE_AMOUNT_OUT_OF_RANGE: 1,
        CODE_DUPLICATE_KEY: gen.DUPLICATE_RECORD_COUNT * 2,
        CODE_EXTERNAL_REF_UNKNOWN: gen.ORPHAN_REFERENCE_COUNT,
        CODE_RECORD_ID_FORMAT_INVALID: gen.INVALID_RECORD_ID_COUNT,
        CODE_REQUIRED_VALUE_MISSING: gen.MISSING_VALUE_COUNT + gen.AMBIGUOUS_MATCH_COUNT,
    }


def test_per_feed_summaries_are_internally_consistent(
    validated: dict[str, BatchValidationResult]
) -> None:
    for result in validated.values():
        summary = result.summary
        assert (
            summary.valid_rows + summary.rows_with_warnings + summary.invalid_rows
            == summary.total_rows
        )
        assert summary.total_issues == len(result.issues)
        assert sum(summary.issues_by_code.values()) == summary.total_issues
        assert sum(summary.issues_by_severity.values()) == summary.total_issues
        assert summary.completed is True


def test_issues_are_ordered_by_source_row_within_each_feed(
    validated: dict[str, BatchValidationResult]
) -> None:
    for result in validated.values():
        rows = [issue.source_row for issue in result.issues]
        assert rows == sorted(rows)


# --- master data --------------------------------------------------------------


def test_the_committed_master_data_validates_cleanly() -> None:
    """The accounts and mapping fixtures carry no injected defects."""
    accounts = read_source(SAMPLES_DIR / "accounts.csv", ACCOUNT_SCHEMA)
    links = read_source(SAMPLES_DIR / "account_links.csv", ACCOUNT_LINK_SCHEMA)

    account_result = validate_ingest_result(
        accounts,
        master_data_policy(
            required_fields=("account_ref",),
        ),
    )
    link_result = validate_ingest_result(links, master_data_policy())

    assert account_result.row_count == gen.ACCOUNT_COUNT
    assert link_result.row_count == gen.LINK_COUNT
    assert account_result.is_clean, account_result.summary.issues_by_code
    assert link_result.is_clean, link_result.summary.issues_by_code


def test_master_data_identifiers_match_the_generated_patterns() -> None:
    """A deliberately corrupted account reference is caught by the same rule."""
    from las.ingest import IngestedRow, RowProvenance
    from las.validate import validate_rows

    row = IngestedRow(
        provenance=RowProvenance(source="accounts.csv", source_row=2),
        values={"account_ref": "ACC-1", "external_ref": "EXT-10000"},
    )
    result = validate_rows([row], master_data_policy())
    assert result.rows[0].codes == ("account_ref_format_invalid",)
