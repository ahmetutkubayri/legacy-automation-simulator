"""T14 CLI, driven through the installed ``las`` command in a subprocess.

Everything here runs the real entry point rather than calling ``main`` in
process, because the contract is about what an operator sees: the exit code,
what lands on stdout, what lands on stderr, and nothing else.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from pipeline_helpers import SAMPLES_DIR, installed_launcher, make_project, staging_left

FIXTURE_NAMES = ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv")
REPORT_NAMES = (
    "reconciliation-report.xlsx",
    "reconciliation-summary.html",
    "reconciliation-summary.json",
)


def las(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the installed command, never a module import.

    The launcher is resolved through the active scheme's scripts directory so
    the same call works on POSIX and Windows; see
    ``pipeline_helpers.installed_launcher``.
    """
    return subprocess.run(
        [str(installed_launcher()), *arguments],
        capture_output=True, text=True, timeout=180, cwd=cwd,
    )


# --- help and version ------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [("--help",), ("run", "--help"), ("demo", "--help"),
     ("generate-fixtures", "--help")],
)
def test_help_is_deterministic(arguments: tuple[str, ...]) -> None:
    first, second = las(*arguments), las(*arguments)
    assert first.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == ""


def test_the_three_commands_are_advertised() -> None:
    result = las("--help")
    for command in ("run", "demo", "generate-fixtures"):
        assert command in result.stdout


def test_no_unauthorized_flag_is_advertised() -> None:
    listing = las("--help").stdout + las("run", "--help").stdout
    for forbidden in ("--apply", "--dry-run", "--overwrite", "--output-dir\n"):
        assert forbidden not in listing.replace("  ", " ") or "run" not in listing


def test_run_takes_only_config() -> None:
    text = las("run", "--help").stdout
    assert "--config" in text
    for forbidden in ("--apply", "--dry-run", "--overwrite", "--output-dir"):
        assert forbidden not in text


def test_version_is_preserved() -> None:
    result = las("--version")
    assert result.returncode == 0
    assert result.stdout.startswith("las ")


# --- generate-fixtures -------------------------------------------------------------


def test_generate_fixtures_writes_four_files(tmp_path: Path) -> None:
    result = las("generate-fixtures", "--output-dir", str(tmp_path / "samples"))
    assert result.returncode == 0
    assert result.stdout == "status: success\nfixtures: 4\n"
    assert result.stderr == ""
    assert sorted(p.name for p in (tmp_path / "samples").iterdir()) == sorted(
        FIXTURE_NAMES
    )


def test_generated_fixtures_match_the_committed_bytes(tmp_path: Path) -> None:
    las("generate-fixtures", "--output-dir", str(tmp_path / "samples"))
    for name in FIXTURE_NAMES:
        assert (tmp_path / "samples" / name).read_bytes() == (
            SAMPLES_DIR / name
        ).read_bytes()


def test_generate_fixtures_is_deterministic_per_seed(tmp_path: Path) -> None:
    las("generate-fixtures", "--output-dir", str(tmp_path / "a"), "--seed", "9")
    las("generate-fixtures", "--output-dir", str(tmp_path / "b"), "--seed", "9")
    for name in FIXTURE_NAMES:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_a_different_seed_gives_different_fixtures(tmp_path: Path) -> None:
    las("generate-fixtures", "--output-dir", str(tmp_path / "a"), "--seed", "1")
    las("generate-fixtures", "--output-dir", str(tmp_path / "b"), "--seed", "2")
    assert (tmp_path / "a" / "accounts.csv").read_bytes() != (
        tmp_path / "b" / "accounts.csv"
    ).read_bytes()


# --- run ---------------------------------------------------------------------------


def test_a_successful_run_prints_the_fixed_summary(tmp_path: Path) -> None:
    config = make_project(tmp_path / "project")
    result = las("run", "--config", str(config))
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (
        "status: success\n"
        "feed_rows: 211\n"
        "system_rows: 270\n"
        "validation_invalid: 12\n"
        "matching_exact: 204\n"
        "matching_fuzzy: 0\n"
        "matching_needs_review: 2\n"
        "matching_unmatched: 5\n"
        "matching_ineligible: 0\n"
        "reconciliation_groups: 240\n"
        "reconciliation_unresolved: 14\n"
        "artifacts: 3\n"
        "write_back: disabled\n"
    )


def test_a_run_writes_exactly_the_three_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = make_project(root)
    las("run", "--config", str(config))
    assert sorted(p.name for p in (root / "report").iterdir()) == sorted(REPORT_NAMES)
    assert not staging_left(tmp_path)


def test_two_runs_produce_identical_summaries_and_bytes(tmp_path: Path) -> None:
    one = las("run", "--config", str(make_project(tmp_path / "one")))
    two = las("run", "--config", str(make_project(tmp_path / "two")))
    assert one.stdout == two.stdout
    for name in REPORT_NAMES:
        assert hashlib.sha256((tmp_path / "one" / "report" / name).read_bytes()).digest() \
            == hashlib.sha256((tmp_path / "two" / "report" / name).read_bytes()).digest()


def test_the_run_summary_prints_no_path_or_name(tmp_path: Path) -> None:
    root = tmp_path / "project"
    result = las("run", "--config", str(make_project(root)))
    for forbidden in (str(tmp_path), "feed_alpha", "ACC-", "reconciliation-report",
                      "/", "\\"):
        assert forbidden not in result.stdout, forbidden


def test_a_run_is_independent_of_the_working_directory(tmp_path: Path) -> None:
    config = make_project(tmp_path / "project")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = las("run", "--config", str(config), cwd=elsewhere)
    assert result.returncode == 0
    assert (tmp_path / "project" / "report").is_dir()
    assert sorted(p.name for p in elsewhere.iterdir()) == []


# --- demo ----------------------------------------------------------------------------


def test_demo_runs_the_same_pipeline(tmp_path: Path) -> None:
    result = las("demo", "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("status: success\n")
    assert result.stdout.endswith("write_back: disabled\n")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(REPORT_NAMES)


def test_demo_leaves_no_temporary_input_directory(tmp_path: Path) -> None:
    las("demo", "--output-dir", str(tmp_path / "out"))
    assert not staging_left(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]


def test_demo_prints_no_temporary_path(tmp_path: Path) -> None:
    result = las("demo", "--output-dir", str(tmp_path / "out"))
    for forbidden in (".las-demo", ".las-staging", "/tmp", "/var/folders"):
        assert forbidden not in result.stdout


def test_demo_is_deterministic_per_seed(tmp_path: Path) -> None:
    one = las("demo", "--output-dir", str(tmp_path / "a"), "--seed", "42")
    two = las("demo", "--output-dir", str(tmp_path / "b"), "--seed", "42")
    assert one.stdout == two.stdout
    for name in REPORT_NAMES:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


# --- exit codes ------------------------------------------------------------------------


def test_missing_config_argument_is_a_usage_error() -> None:
    result = las("run")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr


def test_an_unknown_command_is_a_usage_error() -> None:
    result = las("frobnicate")
    assert result.returncode == 2


def test_a_missing_config_file_exits_three(tmp_path: Path) -> None:
    result = las("run", "--config", str(tmp_path / "absent.yaml"))
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "las: configuration_error\n"


def test_a_malformed_config_exits_three(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("seed: not-a-number\n", encoding="utf-8")
    result = las("run", "--config", str(bad))
    assert result.returncode == 3
    assert result.stderr == "las: configuration_error\n"


def test_a_missing_feed_exits_three(tmp_path: Path) -> None:
    """A configured input that is not there is a configuration problem."""
    root = tmp_path / "project"
    config = make_project(root)
    (root / "feed_beta.csv").unlink()
    result = las("run", "--config", str(config))
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "las: configured_input_error\n"


def test_a_duplicate_configured_feed_exits_three(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = make_project(
        root,
        feeds=[
            {"name": "feed_alpha", "path": "feed_alpha.xlsx"},
            {"name": "feed_twin", "path": "feed_alpha.xlsx"},
        ],
    )
    result = las("run", "--config", str(config))
    assert result.returncode == 3
    assert result.stderr == "las: duplicate_feed_error\n"


def test_an_existing_output_directory_exits_four(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = make_project(root)
    (root / "report").mkdir()
    (root / "report" / "keep.txt").write_bytes(b"mine")
    result = las("run", "--config", str(config))
    assert result.returncode == 4
    assert result.stdout == ""
    assert result.stderr == "las: output_conflict\n"
    assert (root / "report" / "keep.txt").read_bytes() == b"mine"


def test_a_repeated_run_conflicts(tmp_path: Path) -> None:
    config = make_project(tmp_path / "project")
    assert las("run", "--config", str(config)).returncode == 0
    second = las("run", "--config", str(config))
    assert second.returncode == 4
    assert second.stderr == "las: output_conflict\n"


def test_a_missing_output_parent_exits_four(tmp_path: Path) -> None:
    result = las(
        "generate-fixtures", "--output-dir", str(tmp_path / "absent" / "samples")
    )
    assert result.returncode == 4
    assert result.stderr == "las: output_parent_error\n"


def test_an_existing_fixture_directory_exits_four(tmp_path: Path) -> None:
    (tmp_path / "samples").mkdir()
    result = las("generate-fixtures", "--output-dir", str(tmp_path / "samples"))
    assert result.returncode == 4
    assert result.stderr == "las: output_conflict\n"


# --- output discipline -------------------------------------------------------------------


def test_no_failure_prints_a_traceback(tmp_path: Path) -> None:
    for result in (
        las("run", "--config", str(tmp_path / "absent.yaml")),
        las("generate-fixtures", "--output-dir", str(tmp_path / "no" / "where")),
    ):
        assert "Traceback" not in result.stderr
        assert "File \"" not in result.stderr
        assert result.stderr.count("\n") == 1


def test_no_failure_leaks_a_path_or_exception_text(tmp_path: Path) -> None:
    result = las("run", "--config", str(tmp_path / "absent.yaml"))
    for forbidden in (str(tmp_path), "absent.yaml", "ValidationError", "OSError",
                      "pydantic", "0x"):
        assert forbidden not in result.stderr


def test_every_failure_line_has_the_fixed_shape(tmp_path: Path) -> None:
    import re

    root = tmp_path / "project"
    config = make_project(root)
    (root / "report").mkdir()
    for result in (
        las("run", "--config", str(tmp_path / "absent.yaml")),
        las("run", "--config", str(config)),
        las("generate-fixtures", "--output-dir", str(tmp_path / "no" / "where")),
    ):
        assert re.fullmatch(r"las: [a-z_]+\n", result.stderr), result.stderr
        assert result.stdout == ""


def test_no_command_writes_an_artifact_to_stdout(tmp_path: Path) -> None:
    outputs = [
        las("run", "--config", str(make_project(tmp_path / "project"))).stdout,
        las("demo", "--output-dir", str(tmp_path / "demo")).stdout,
        las("generate-fixtures", "--output-dir", str(tmp_path / "samples")).stdout,
    ]
    for text in outputs:
        assert "<html" not in text and "{" not in text and "PK" not in text


def test_summaries_carry_no_timestamp(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    year = str(datetime.now(UTC).year)
    result = las("demo", "--output-dir", str(tmp_path / "out"))
    assert year not in result.stdout
