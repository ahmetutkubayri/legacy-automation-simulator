"""Loader behavior: file handling, YAML parsing, and error reporting."""

from pathlib import Path

import pytest

from las.config import ConfigError, RunConfig, load_config


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_sample_config_loads(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    assert isinstance(config, RunConfig)
    assert config.seed == 42
    assert config.simulator.fault_profile == "mild"
    assert [feed.name for feed in config.feeds] == ["feed_alpha", "feed_beta"]
    assert config.feeds[0].header_aliases["Record Id"] == "record_id"
    assert config.report.variance_bands == [0.0, 1.0, 5.0]


def test_load_accepts_string_path(sample_config_path: Path) -> None:
    assert load_config(str(sample_config_path)).seed == 42


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="not found") as excinfo:
        load_config(missing)
    assert "nope.yaml" in str(excinfo.value)


def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, "seed: [unclosed\nfeeds:")
    with pytest.raises(ConfigError, match="Malformed YAML"):
        load_config(path)


def test_empty_file_raises_config_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, "")
    with pytest.raises(ConfigError, match="empty"):
        load_config(path)


def test_non_mapping_top_level_raises_config_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="mapping") as excinfo:
        load_config(path)
    assert "list" in str(excinfo.value)


def test_unreadable_file_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "seed: 1\n")

    def fail_read(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("simulated read failure")

    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(ConfigError, match="could not be read"):
        load_config(path)


def test_schema_violations_report_dotted_field_paths(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        seed: -5
        feeds:
          - name: feed_a
            path: data/samples/feed_a.csv
        match:
          fuzzy_threshold: 2.0
        """,
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "2 invalid configuration fields" in message
    assert "seed" in message
    assert "match.fuzzy_threshold" in message


def test_unknown_key_reported_with_location(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        seed: 1
        feeds:
          - name: feed_a
            path: data/samples/feed_a.csv
        simulator:
          warp_speed: true
        """,
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "simulator.warp_speed" in str(excinfo.value)
