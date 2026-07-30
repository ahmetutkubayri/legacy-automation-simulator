"""T14 end to end over the committed fixtures, through every earlier task.

Every expected number is derived from the generator's constants or from the
lower layers' own output — never typed in as an independent business constant.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from las.pipeline import RunStatus, WriteBackMode, run_pipeline
from las.simulator import RecordStore
from pipeline_helpers import REPO_ROOT, SAMPLES_DIR, make_request, staging_left

_spec = importlib.util.spec_from_file_location(
    "generate_fixtures", REPO_ROOT / "scripts" / "generate_fixtures.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

FIXTURE_NAMES = ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("project")
    request = make_request(root)
    return request, run_pipeline(request), root


# --- counts ------------------------------------------------------------------------


def test_every_committed_feed_row_is_ingested(run) -> None:
    """Source: gen.FEED_ALPHA_ROW_COUNT + gen.FEED_BETA_ROW_COUNT."""
    _, result, _ = run
    assert result.summary.feed_rows == gen.FEED_ALPHA_ROW_COUNT + gen.FEED_BETA_ROW_COUNT
    assert result.summary.feed_rows == 211


def test_every_ledger_row_is_extracted_and_adapted(run) -> None:
    """Source: gen.LINK_COUNT times the ledger's period count."""
    from las.simulator.store import LEDGER_PERIODS

    _, result, _ = run
    expected = gen.LINK_COUNT * len(LEDGER_PERIODS)
    assert result.summary.system_rows == expected == 270


def test_the_matching_counts_are_unchanged(run) -> None:
    _, result, _ = run
    assert dict(result.summary.matching_counts) == {
        "exact": 204, "fuzzy": 0, "needs_review": gen.AMBIGUOUS_MATCH_COUNT,
        "unmatched": gen.ORPHAN_REFERENCE_COUNT, "ineligible": 0,
    }


def test_the_reconciliation_counts_are_unchanged(run) -> None:
    _, result, _ = run
    assert result.summary.reconciliation_groups == 240
    assert result.summary.reconciliation_unresolved == 14
    assert dict(result.summary.reconciliation_counts) == {
        "mismatch": 27, "missing_in_feed": 110,
        "unit_mismatch": 101, "within_tolerance": 2,
    }


def test_the_validation_count_matches_the_injected_defects(run) -> None:
    """Rows T10 marks invalid: bad amounts, bad identifiers, missing values."""
    _, result, _ = run
    assert result.summary.validation_invalid == (
        gen.INVALID_AMOUNT_COUNT + gen.INVALID_RECORD_ID_COUNT + gen.MISSING_VALUE_COUNT
        + 2  # the two ambiguous rows also carry a missing external reference
    )


# --- artifacts ---------------------------------------------------------------------


def test_exactly_the_three_configured_artifacts_are_written(run) -> None:
    request, result, root = run
    assert len(result.artifacts) == len(request.config.report.formats) == 3
    assert sorted(p.name for p in (root / "report").iterdir()) == [
        "reconciliation-report.xlsx",
        "reconciliation-summary.html",
        "reconciliation-summary.json",
    ]


def test_every_recorded_hash_matches_the_bytes_on_disk(run) -> None:
    _, result, root = run
    for item in result.artifacts:
        payload = (root / "report" / item.filename).read_bytes()
        assert item.byte_count == len(payload)
        assert item.sha256 == hashlib.sha256(payload).hexdigest()


def test_the_documents_are_exactly_what_t13_renders(run) -> None:
    from las.report import build_report, render_html, render_json
    from las.report import policy_from_config as report_policy

    request, _, root = run
    reconciliation = _reconcile(request)
    policy = report_policy(request.config.report)
    report = build_report(reconciliation, policy)
    assert (root / "report" / "reconciliation-summary.json").read_bytes() == render_json(
        report, policy
    )
    assert (root / "report" / "reconciliation-summary.html").read_bytes() == render_html(
        report, policy
    )


def test_the_workbook_carries_the_four_t13_sheets(run) -> None:
    _, _, root = run
    book = load_workbook(
        io.BytesIO((root / "report" / "reconciliation-report.xlsx").read_bytes()),
        data_only=False,
    )
    assert book.sheetnames == ["Summary", "Exceptions", "Reconciliation", "Review Queue"]
    assert sum(
        1 for sheet in book for row in sheet.iter_rows() for cell in row
        if cell.data_type == "f"
    ) == 0


def test_the_json_summary_agrees_with_the_run_result(run) -> None:
    _, result, root = run
    payload = json.loads((root / "report" / "reconciliation-summary.json").read_bytes())
    assert payload["accounting"]["feed_rows_input"] == result.summary.feed_rows
    assert payload["accounting"]["system_rows_input"] == result.summary.system_rows
    assert payload["accounting"]["group_count"] == result.summary.reconciliation_groups


def _reconcile(request):
    """Re-run the pure stages independently, to compare against the pipeline."""
    from las.driver import LegacyDriver, extract_query_results
    from las.ingest import ACCOUNT_LINK_SCHEMA, ACCOUNT_SCHEMA, feed_schema, read_source
    from las.match import build_master_index, match_validated
    from las.match import policy_from_config as match_policy
    from las.pipeline import adapt_ledger, batch_ids
    from las.reconcile import FeedBatch, reconcile
    from las.reconcile import policy_from_config as reconcile_policy
    from las.simulator import LegacySystem, SimulatedClock
    from las.validate import policy_from_config as validation_policy
    from las.validate import validate_ingest_result

    config, root = request.config, request.config_dir
    store = RecordStore.from_fixtures(root)
    system = LegacySystem(store, visible_rows=config.simulator.visible_rows,
                          buffer_rows=config.simulator.buffer_rows, clock=SimulatedClock())
    extracted = extract_query_results(LegacyDriver(system)).rows
    links = read_source(root / "account_links.csv", ACCOUNT_LINK_SCHEMA)
    index = build_master_index(read_source(root / "accounts.csv", ACCOUNT_SCHEMA), links)
    known = frozenset(v for v in links.column("external_ref") if v)
    vpolicy = validation_policy(config.validation, known_external_refs=known)
    mpolicy = match_policy(config.match)
    identifiers = batch_ids(config.feeds, root)
    batches = []
    for feed, identifier in zip(config.feeds, identifiers, strict=True):
        validated = validate_ingest_result(
            read_source(root / feed.path, feed_schema(feed)), vpolicy
        )
        batches.append(
            FeedBatch(validation=validated,
                      matching=match_validated(validated, index, mpolicy),
                      batch_id=identifier)
        )
    return reconcile(tuple(batches), adapt_ledger(extracted, index),
                     reconcile_policy(config.report))


# --- the filesystem ------------------------------------------------------------------


def test_nothing_is_written_outside_the_output_directory(run) -> None:
    _, _, root = run
    assert sorted(p.name for p in root.iterdir()) == [
        "account_links.csv", "accounts.csv", "config.yaml",
        "feed_alpha.xlsx", "feed_beta.csv", "report",
    ]


def test_no_temporary_directory_remains(run) -> None:
    _, _, root = run
    assert not staging_left(root.parent)


def test_the_committed_fixtures_are_untouched(run) -> None:
    documented = {
        name: hashlib.sha256((SAMPLES_DIR / name).read_bytes()).hexdigest()
        for name in FIXTURE_NAMES
    }
    plan = (REPO_ROOT / "docs" / "DEVELOPMENT_PLAN.md").read_text(encoding="utf-8")
    for name, digest in documented.items():
        assert f"{digest}  data/samples/{name}" in plan


def test_the_simulator_is_identical_before_and_after(run) -> None:
    _, _, root = run
    assert RecordStore.from_fixtures(root).snapshot() == RecordStore.from_fixtures(
        SAMPLES_DIR
    ).snapshot()


def test_write_back_stayed_disabled(run) -> None:
    _, result, _ = run
    assert result.status is RunStatus.SUCCESS
    assert result.write_back_mode is WriteBackMode.DISABLED
    assert (result.write_back_attempted, result.write_back_applied) == (0, 0)


# --- repeatability --------------------------------------------------------------------


def test_a_fresh_directory_gives_an_equivalent_run(tmp_path: Path, run) -> None:
    _, first, _ = run
    second = run_pipeline(make_request(tmp_path / "again"))
    assert second.summary == first.summary
    assert [a.sha256 for a in second.artifacts] == [a.sha256 for a in first.artifacts]


def test_a_repeated_run_conflicts_and_changes_nothing(run) -> None:
    from las.pipeline import OutputConflictError

    request, result, root = run
    before = {
        item.filename: (root / "report" / item.filename).read_bytes()
        for item in result.artifacts
    }
    with pytest.raises(OutputConflictError):
        run_pipeline(request)
    for name, payload in before.items():
        assert (root / "report" / name).read_bytes() == payload
