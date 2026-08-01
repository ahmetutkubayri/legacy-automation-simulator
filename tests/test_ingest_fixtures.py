"""T9 ingestion of the four committed synthetic fixtures.

Every expected count is derived from the fixture contract rather than
asserted from memory: row counts and defect counts come from the constants
in ``scripts/generate_fixtures.py``, and the four SHA-256 values come from
``docs/DEVELOPMENT_PLAN.md``. Nothing here is a number someone typed because
it looked right.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from collections import Counter
from pathlib import Path

import pytest

from las.config import RunConfig, load_config
from las.ingest import (
    ACCOUNT_LINK_SCHEMA,
    ACCOUNT_SCHEMA,
    IngestOutcome,
    IngestResult,
    SourceFormat,
    feed_schema,
    read_source,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
PLAN_PATH = REPO_ROOT / "docs" / "DEVELOPMENT_PLAN.md"
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_fixtures.py"

_spec = importlib.util.spec_from_file_location("generate_fixtures", GENERATOR_PATH)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

FIXTURE_NAMES = ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv")


@pytest.fixture(scope="module")
def config() -> RunConfig:
    return load_config(SAMPLES_DIR / "config.yaml")


@pytest.fixture(scope="module")
def accounts() -> IngestResult:
    return read_source(SAMPLES_DIR / "accounts.csv", ACCOUNT_SCHEMA)


@pytest.fixture(scope="module")
def links() -> IngestResult:
    return read_source(SAMPLES_DIR / "account_links.csv", ACCOUNT_LINK_SCHEMA)


@pytest.fixture(scope="module")
def alpha(config: RunConfig) -> IngestResult:
    feed = next(f for f in config.feeds if f.name == "feed_alpha")
    return read_source(SAMPLES_DIR / feed.path, feed_schema(feed))


@pytest.fixture(scope="module")
def beta(config: RunConfig) -> IngestResult:
    feed = next(f for f in config.feeds if f.name == "feed_beta")
    return read_source(SAMPLES_DIR / feed.path, feed_schema(feed))


def documented_hashes() -> dict[str, str]:
    plan = PLAN_PATH.read_text(encoding="utf-8")
    found = {
        match.group(2).split("/")[-1]: match.group(1)
        for match in re.finditer(
            r"^([0-9a-f]{64})\s+(data/samples/\S+)$", plan, re.MULTILINE
        )
    }
    assert set(found) == set(FIXTURE_NAMES), "the plan must document all four fixtures"
    return found


# --- readability --------------------------------------------------------------


def test_all_four_fixtures_are_readable_through_their_schemas(
    accounts: IngestResult, links: IngestResult, alpha: IngestResult, beta: IngestResult
) -> None:
    for result in (accounts, links, alpha, beta):
        assert result.outcome is IngestOutcome.ROWS
        assert result.header_row == 1
        assert result.rows
    assert accounts.source_format is SourceFormat.CSV
    assert links.source_format is SourceFormat.CSV
    assert alpha.source_format is SourceFormat.XLSX
    assert beta.source_format is SourceFormat.CSV
    assert alpha.worksheet == "records"


def test_configured_feed_paths_are_the_committed_fixtures(config: RunConfig) -> None:
    for feed in config.feeds:
        assert (SAMPLES_DIR / feed.path).is_file()


# --- header resolution against the real fixture headers -----------------------


def test_master_data_headers_resolve_exactly(
    accounts: IngestResult, links: IngestResult
) -> None:
    assert accounts.headers == tuple(gen.ACCOUNT_HEADERS)
    assert links.headers == tuple(gen.LINK_HEADERS)
    # These schemas refuse extras, so an empty tuple proves no surplus column.
    assert accounts.unmapped_headers == ()
    assert links.unmapped_headers == ()


def test_feed_headers_resolve_through_the_config_aliases(
    alpha: IngestResult, beta: IngestResult
) -> None:
    # Canonical fields appear in physical column order, which differs per feed.
    assert alpha.headers == ("record_id", "external_ref", "amount", "unit", "period")
    assert beta.headers == ("period", "amount", "external_ref", "record_id", "unit")


def test_the_one_unmapped_column_per_feed_is_reported(
    alpha: IngestResult, beta: IngestResult
) -> None:
    """Each feed carries a source-name column that is not a canonical field."""
    assert alpha.unmapped_headers == ("reported by",)
    assert beta.unmapped_headers == ("src name",)
    # Retained, so the matching stage can still reach it later.
    assert alpha.rows[0].extras["reported by"].startswith("Demo Account ")
    assert beta.rows[0].extras["src name"].startswith("Demo Account ")


def test_unmapped_headers_correspond_to_the_generator_columns() -> None:
    """The surplus columns are exactly the generator's source-name columns."""
    assert gen.FEED_ALPHA_HEADERS[-1] == "Reported By"
    assert gen.FEED_BETA_HEADERS[-1] == "SRC_NAME"


# --- row counts, derived from the generator contract --------------------------


def test_row_counts_match_the_generator_constants(
    accounts: IngestResult, links: IngestResult, alpha: IngestResult, beta: IngestResult
) -> None:
    assert accounts.row_count == gen.ACCOUNT_COUNT
    assert links.row_count == gen.LINK_COUNT
    assert alpha.row_count == gen.FEED_ALPHA_ROW_COUNT
    assert beta.row_count == gen.FEED_BETA_ROW_COUNT


def test_no_blank_rows_and_no_gaps_in_provenance(
    accounts: IngestResult, links: IngestResult, alpha: IngestResult, beta: IngestResult
) -> None:
    """Physical rows run contiguously from row 2, so nothing was dropped."""
    for result in (accounts, links, alpha, beta):
        assert result.skipped_blank_rows == 0
        rows = [row.provenance.source_row for row in result.rows]
        assert rows == list(range(2, 2 + result.row_count))


def test_provenance_carries_a_basename_and_never_a_path(
    accounts: IngestResult, alpha: IngestResult
) -> None:
    assert accounts.rows[0].provenance.source == "accounts.csv"
    assert alpha.rows[0].provenance.source == "feed_alpha.xlsx"
    for result in (accounts, alpha):
        for row in result.rows:
            assert "/" not in row.provenance.source
            assert "\\" not in row.provenance.source


def test_counts_are_deterministic_across_repeated_reads(config: RunConfig) -> None:
    feed = next(f for f in config.feeds if f.name == "feed_beta")
    schema = feed_schema(feed)
    first = read_source(SAMPLES_DIR / feed.path, schema)
    second = read_source(SAMPLES_DIR / feed.path, schema)
    assert first == second


# --- injected business defects survive ingestion ------------------------------


def test_malformed_record_ids_are_preserved(beta: IngestResult) -> None:
    ids = set(beta.column("record_id"))
    assert set(gen.INVALID_RECORD_ID_VALUES) <= ids
    assert len(gen.INVALID_RECORD_ID_VALUES) == gen.INVALID_RECORD_ID_COUNT


def test_orphan_external_references_are_preserved(
    beta: IngestResult, links: IngestResult
) -> None:
    mapped = set(links.column("external_ref"))
    orphans = {
        ref
        for ref in beta.column("external_ref")
        if ref is not None and ref not in mapped
    }
    assert len(orphans) == gen.ORPHAN_REFERENCE_COUNT
    assert all(ref.startswith("EXT-9") for ref in orphans)


def test_missing_amounts_are_preserved_as_missing(
    alpha: IngestResult, beta: IngestResult
) -> None:
    alpha_missing = sum(1 for value in alpha.column("amount") if value is None)
    beta_missing = sum(1 for value in beta.column("amount") if value is None)
    assert alpha_missing + beta_missing == gen.MISSING_VALUE_COUNT
    assert alpha_missing == beta_missing == gen.MISSING_VALUE_COUNT // 2


def test_invalid_amount_text_is_preserved_verbatim(alpha: IngestResult) -> None:
    amounts = set(alpha.column("amount"))
    assert set(gen.INVALID_AMOUNT_VALUES) <= amounts
    assert len(gen.INVALID_AMOUNT_VALUES) == gen.INVALID_AMOUNT_COUNT


def test_duplicate_feed_rows_are_both_retained(alpha: IngestResult) -> None:
    signatures = Counter(
        tuple(sorted(row.values.items())) + tuple(sorted(row.extras.items()))
        for row in alpha.rows
    )
    doubled = [signature for signature, count in signatures.items() if count == 2]
    assert len(doubled) == gen.DUPLICATE_RECORD_COUNT


def test_ambiguous_rows_keep_their_empty_reference_and_source_name(
    alpha: IngestResult,
) -> None:
    ambiguous = [
        row
        for row in alpha.rows
        if row.get("external_ref") is None and row.extras.get("reported by")
    ]
    assert len(ambiguous) == gen.AMBIGUOUS_MATCH_COUNT
    for row in ambiguous:
        assert row.extras["reported by"] == gen.AMBIGUOUS_SOURCE_NAME


def test_unit_mismatches_between_the_two_feeds_survive(
    alpha: IngestResult, beta: IngestResult
) -> None:
    alpha_units = dict(zip(alpha.column("record_id"), alpha.column("unit"), strict=True))
    beta_units = dict(zip(beta.column("record_id"), beta.column("unit"), strict=True))
    shared = set(alpha_units) & set(beta_units)
    mismatched = {rid for rid in shared if alpha_units[rid] != beta_units[rid]}
    assert len(mismatched) == gen.UNIT_MISMATCH_COUNT


def test_nothing_was_filtered_out_in_the_name_of_cleanliness(
    alpha: IngestResult, beta: IngestResult
) -> None:
    """Total ingested rows equal the generator's totals, defects included."""
    assert alpha.row_count + beta.row_count == (
        gen.FEED_ALPHA_ROW_COUNT + gen.FEED_BETA_ROW_COUNT
    )


# --- the committed fixtures are untouched -------------------------------------


def test_ingestion_leaves_every_fixture_byte_identical(config: RunConfig) -> None:
    before = {
        name: hashlib.sha256((SAMPLES_DIR / name).read_bytes()).hexdigest()
        for name in FIXTURE_NAMES
    }
    read_source(SAMPLES_DIR / "accounts.csv", ACCOUNT_SCHEMA)
    read_source(SAMPLES_DIR / "account_links.csv", ACCOUNT_LINK_SCHEMA)
    for feed in config.feeds:
        read_source(SAMPLES_DIR / feed.path, feed_schema(feed))
    after = {
        name: hashlib.sha256((SAMPLES_DIR / name).read_bytes()).hexdigest()
        for name in FIXTURE_NAMES
    }
    assert after == before


def test_committed_fixtures_still_match_the_documented_hashes() -> None:
    documented = documented_hashes()
    actual = {
        name: hashlib.sha256((SAMPLES_DIR / name).read_bytes()).hexdigest()
        for name in FIXTURE_NAMES
    }
    assert actual == documented
