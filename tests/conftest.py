"""Shared fixtures for the test suite."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def sample_config_path() -> Path:
    """Path of the committed sample configuration."""
    return REPO_ROOT / "data" / "samples" / "config.yaml"


@pytest.fixture()
def base_config() -> dict:
    """Smallest valid RunConfig payload; tests mutate copies of it.

    The feed path is config-relative, matching the committed sample: relative
    paths resolve against the directory holding the configuration.
    """
    return {
        "seed": 1,
        "feeds": [{"name": "feed_a", "path": "feed_a.csv"}],
    }
