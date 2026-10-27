"""Invalid stage configuration is rejected through the installed CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from pipeline_helpers import installed_launcher, make_project, staging_left


@pytest.mark.parametrize(
    ("section", "settings"),
    [
        ("validation", {"amount_max": float("nan")}),
        ("validation", {"record_id_pattern": r"(?u)^\d+$"}),
        ("report", {"variance_bands": [1.0, 5.0]}),
        ("report", {"variance_bands": [0.0, float("inf")]}),
    ],
)
def test_invalid_stage_policy_config_exits_three_without_output(
    tmp_path: Path, section: str, settings: dict
) -> None:
    root = tmp_path / "project"
    config = make_project(root)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data[section].update(settings)
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = subprocess.run(
        [str(installed_launcher()), "run", "--config", str(config)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "las: configuration_error\n"
    assert not (root / "report").exists()
    assert not staging_left(root)


def test_negative_demo_seed_is_a_configuration_error(tmp_path: Path) -> None:
    output = tmp_path / "report"
    result = subprocess.run(
        [str(installed_launcher()), "demo", "--output-dir", str(output), "--seed", "-1"],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "las: configuration_error\n"
    assert not output.exists()
    assert not staging_left(tmp_path)


@pytest.mark.parametrize(
    "aliases",
    [{"Amount": "unit"}, {"---": "amount"}, {"Quantity": "amount", "quantity": "unit"}],
)
def test_invalid_alias_schema_exits_three_without_output(tmp_path: Path, aliases: dict) -> None:
    root = tmp_path / "project"
    config = make_project(root)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["feeds"][0]["header_aliases"] = aliases
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = subprocess.run(
        [str(installed_launcher()), "run", "--config", str(config)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "las: configured_input_error\n"
    assert not (root / "report").exists()
    assert not staging_left(root)


@pytest.mark.parametrize("content", [b"\xff", "seed: 1\n".encode("utf-16")])
def test_non_utf8_config_exits_three_without_output(tmp_path: Path, content: bytes) -> None:
    config = tmp_path / "config.yaml"
    config.write_bytes(content)
    result = subprocess.run(
        [str(installed_launcher()), "run", "--config", str(config)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "las: configuration_error\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.yaml"]
