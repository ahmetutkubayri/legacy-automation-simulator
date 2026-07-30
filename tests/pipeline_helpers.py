"""Shared, non-test fixtures for the T14 suites.

Deliberately not named ``test_*``: several pipeline modules import it, and
importing shared data from another test module would couple those together.

The builders here assemble a *config-relative* project in a temporary
directory: the four committed fixtures copied beside a configuration whose feed
paths are bare filenames. That is exactly the layout the resolution rule
describes, and it keeps every test free of the repository's own directory
shape.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from las.config import RunConfig, load_config
from las.pipeline import PipelineRequest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
FIXTURE_NAMES = ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv")


def make_project(root: Path, *, output_dir: str = "report", feeds=None) -> Path:
    """Copy the committed fixtures beside a config-relative configuration."""
    root.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_NAMES:
        shutil.copy2(SAMPLES_DIR / name, root / name)

    raw = yaml.safe_load((SAMPLES_DIR / "config.yaml").read_text(encoding="utf-8"))
    raw["output_dir"] = output_dir
    for feed in raw["feeds"]:
        feed["path"] = Path(feed["path"]).name
    if feeds is not None:
        raw["feeds"] = feeds
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
    return config_path


def make_request(root: Path, **kwargs) -> PipelineRequest:
    config_path = make_project(root, **kwargs)
    return PipelineRequest(config=load_config(config_path), config_dir=root)


def load_project_config(root: Path, **kwargs) -> RunConfig:
    return load_config(make_project(root, **kwargs))


def staging_left(root: Path) -> list[str]:
    """Any staging or demo directory the transaction should have removed."""
    return sorted(
        entry.name
        for entry in root.rglob("*")
        if entry.is_dir() and entry.name.startswith((".las-staging-", ".las-demo-"))
    )
