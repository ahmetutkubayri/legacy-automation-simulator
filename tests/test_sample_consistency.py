"""The committed sample config and generated fixtures must stay consistent.

These tests only prove mutual consistency between ``data/samples/config.yaml``
and the committed fixture files — validation and ingestion logic belong to
later pipeline stages and are deliberately not duplicated here.
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from openpyxl import load_workbook

from las.config import FeedConfig, RunConfig, load_config
from las.pipeline import PipelineRequest
from pipeline_helpers import installed_launcher

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
CONFIG_PATH = SAMPLES_DIR / "config.yaml"

#: The artifact set one successful run publishes, per the T14 contract.
REPORT_NAMES = (
    "reconciliation-report.xlsx",
    "reconciliation-summary.html",
    "reconciliation-summary.json",
)


@pytest.fixture(scope="module")
def config() -> RunConfig:
    return load_config(CONFIG_PATH)


def feed_by_name(config: RunConfig, name: str) -> FeedConfig:
    return next(feed for feed in config.feeds if feed.name == name)


def alpha_rows() -> list[dict[str, str]]:
    sheet = load_workbook(SAMPLES_DIR / "feed_alpha.xlsx").active
    rows = list(sheet.iter_rows(values_only=True))
    headers = [str(cell) for cell in rows[0]]
    return [
        {
            header: "" if cell is None else str(cell)
            for header, cell in zip(headers, row, strict=True)
        }
        for row in rows[1:]
    ]


def beta_rows() -> list[dict[str, str]]:
    with (SAMPLES_DIR / "feed_beta.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def test_configured_paths_point_to_committed_fixtures(config: RunConfig) -> None:
    """Resolved exactly as a run resolves them: against the config's own directory.

    ``PipelineRequest.resolve`` is the production rule, so it is called rather
    than reimplemented — a test that rebased these paths on the repository root
    would pass against a configuration no ``las run`` can actually load.
    """
    request = PipelineRequest(config=config, config_dir=CONFIG_PATH.parent)
    for feed in config.feeds:
        assert request.resolve(feed.path).is_file(), feed.path


def test_the_committed_sample_runs_unmodified(tmp_path: Path) -> None:
    """The committed sample is a working project, not just a valid document.

    The whole sample directory is copied verbatim — configuration and fixtures
    together — and the installed command is pointed at the copy. Nothing
    rewrites a path on the way in, so this fails against any configuration
    whose feed paths are not resolvable from the configuration's own directory.

    A copy is used because the committed configuration publishes into
    ``output`` beside itself, and the repository is not a scratch directory.
    """
    project = tmp_path / "samples"
    shutil.copytree(SAMPLES_DIR, project)
    copied_config = project / "config.yaml"
    assert copied_config.read_bytes() == CONFIG_PATH.read_bytes()

    result = subprocess.run(
        [str(installed_launcher()), "run", "--config", str(copied_config)],
        capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("status: success\n")
    published = project / "output"
    assert sorted(item.name for item in published.iterdir()) == sorted(REPORT_NAMES)
    # The real tree is untouched: the run wrote only inside the copy.
    assert not (SAMPLES_DIR / "output").exists()


def test_record_id_pattern_matches_generated_ids(config: RunConfig) -> None:
    pattern = re.compile(config.validation.record_id_pattern)
    for row in alpha_rows():
        assert pattern.fullmatch(row["Record Id"]), row["Record Id"]
    well_formed = [r["REC_ID"] for r in beta_rows() if r["REC_ID"].startswith("OPR-")]
    assert well_formed
    for record_id in well_formed:
        assert pattern.fullmatch(record_id), record_id


def test_external_ref_pattern_matches_generated_references(config: RunConfig) -> None:
    pattern = re.compile(config.validation.external_ref_pattern)
    with (SAMPLES_DIR / "account_links.csv").open(encoding="utf-8", newline="") as handle:
        links = list(csv.DictReader(handle))
    assert links
    for link in links:
        assert pattern.fullmatch(link["external_ref"]), link["external_ref"]

    feed_references = [r["External Reference"] for r in alpha_rows() if r["External Reference"]]
    feed_references += [r["EXT_REF"] for r in beta_rows() if r["EXT_REF"]]
    assert feed_references
    for reference in feed_references:
        assert pattern.fullmatch(reference), reference


def test_header_aliases_exist_in_files_and_cover_required_fields(config: RunConfig) -> None:
    required = set(config.validation.required_fields)

    alpha_headers = set(alpha_rows()[0].keys())
    alpha_feed = feed_by_name(config, "feed_alpha")
    assert set(alpha_feed.header_aliases) <= alpha_headers
    assert required <= set(alpha_feed.header_aliases.values())

    beta_headers = set(beta_rows()[0].keys())
    beta_feed = feed_by_name(config, "feed_beta")
    assert set(beta_feed.header_aliases) <= beta_headers
    assert required <= set(beta_feed.header_aliases.values())
