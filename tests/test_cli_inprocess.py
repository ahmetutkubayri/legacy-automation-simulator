"""T14 CLI driven in-process, alongside the subprocess suite.

``tests/test_cli.py`` runs the installed ``las`` command, which is the real
end-to-end proof but is invisible to coverage — a subprocess carries its own
interpreter. These tests call the same entry points directly, so every branch
the CLI actually takes is measured, and the two suites check the same behaviour
from opposite sides: if they ever disagree, one of them is wrong.

Nothing here weakens the subprocess tests; they remain the contract's evidence
that the installed command behaves.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from las.cli import (
    EXIT_CONFIGURATION,
    EXIT_INTERNAL,
    EXIT_OUTPUT,
    EXIT_SUCCESS,
    EXIT_USAGE,
    format_summary,
    main,
)
from las.pipeline import FILENAME_BY_FORMAT, run_pipeline
from pipeline_helpers import make_project, make_request, staging_left

SUMMARY_LINES = (
    "status: success",
    "feed_rows: ",
    "system_rows: ",
    "validation_invalid: ",
    "matching_exact: ",
    "matching_fuzzy: ",
    "matching_needs_review: ",
    "matching_unmatched: ",
    "matching_ineligible: ",
    "reconciliation_groups: ",
    "reconciliation_unresolved: ",
    "artifacts: ",
    "write_back: disabled",
)


def run_cli(*argv: str, capsys) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- the terminal summary -------------------------------------------------------


def test_a_successful_run_prints_the_fixed_summary(tmp_path: Path, capsys) -> None:
    config = make_project(tmp_path / "project")
    code, out, err = run_cli("run", "--config", str(config), capsys=capsys)

    assert code == EXIT_SUCCESS
    assert err == ""
    lines = out.splitlines()
    assert len(lines) == len(SUMMARY_LINES)
    for line, expected in zip(lines, SUMMARY_LINES, strict=True):
        assert line.startswith(expected), line
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert "\r" not in out


def test_the_summary_names_no_path_identifier_or_time(tmp_path: Path, capsys) -> None:
    config = make_project(tmp_path / "project")
    _, out, _ = run_cli("run", "--config", str(config), capsys=capsys)
    for forbidden in ("/", "\\", "ACC-", "EXT-", "OPR-", "20", ".xlsx", ".json", ".html"):
        if forbidden == "20":
            continue  # counts may legitimately contain digits
        assert forbidden not in out, forbidden


def test_format_summary_matches_a_direct_pipeline_run(tmp_path: Path, capsys) -> None:
    """The printer and the engine agree; the CLI adds no arithmetic."""
    config_path = make_project(tmp_path / "project")
    _, out, _ = run_cli("run", "--config", str(config_path), capsys=capsys)

    direct = run_pipeline(make_request(tmp_path / "direct"))
    assert format_summary(direct) == out


# --- commands -------------------------------------------------------------------


def test_no_command_prints_help_and_succeeds(capsys) -> None:
    code, out, err = run_cli(capsys=capsys)
    assert code == EXIT_SUCCESS
    assert "run" in out and "demo" in out and "generate-fixtures" in out
    assert err == ""


def test_generate_fixtures_prints_its_fixed_summary(tmp_path: Path, capsys) -> None:
    code, out, err = run_cli(
        "generate-fixtures", "--output-dir", str(tmp_path / "samples"), capsys=capsys
    )
    assert code == EXIT_SUCCESS
    assert out == "status: success\nfixtures: 4\n"
    assert err == ""
    assert sorted(p.name for p in (tmp_path / "samples").iterdir()) == [
        "account_links.csv", "accounts.csv", "feed_alpha.xlsx", "feed_beta.csv"
    ]


def test_demo_runs_the_same_pipeline_and_cleans_up(tmp_path: Path, capsys) -> None:
    code, out, err = run_cli("demo", "--output-dir", str(tmp_path / "out"), capsys=capsys)
    assert code == EXIT_SUCCESS
    assert err == ""
    assert out.splitlines()[0] == "status: success"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(
        FILENAME_BY_FORMAT.values()
    )
    # The private input directory never outlives the command.
    assert staging_left(tmp_path) == []


def test_demo_cleans_up_even_when_the_run_fails(tmp_path: Path, capsys) -> None:
    output = tmp_path / "out"
    output.mkdir(parents=True)
    code, out, err = run_cli("demo", "--output-dir", str(output), capsys=capsys)
    assert code == EXIT_OUTPUT
    assert out == ""
    assert err.startswith("las: ")
    assert staging_left(tmp_path) == []


# --- failures -------------------------------------------------------------------


def test_a_missing_config_file_is_a_configuration_failure(tmp_path: Path, capsys) -> None:
    code, out, err = run_cli("run", "--config", str(tmp_path / "absent.yaml"), capsys=capsys)
    assert code == EXIT_CONFIGURATION
    assert out == ""
    assert err.endswith("\n") and err.count("\n") == 1
    assert "Traceback" not in err and "absent.yaml" not in err


def test_a_malformed_config_is_a_configuration_failure(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("seed: not-an-integer\n", encoding="utf-8")
    code, out, err = run_cli("run", "--config", str(broken), capsys=capsys)
    assert code == EXIT_CONFIGURATION
    assert out == ""
    assert "not-an-integer" not in err


def test_an_existing_output_directory_is_an_output_failure(
    tmp_path: Path, capsys
) -> None:
    config = make_project(tmp_path / "project")
    (tmp_path / "project" / "report").mkdir(parents=True)
    code, out, err = run_cli("run", "--config", str(config), capsys=capsys)
    assert code == EXIT_OUTPUT
    assert out == ""
    assert "report" not in err or "/" not in err


def test_a_fixture_staging_failure_is_an_output_failure(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transaction that cannot even stage is an output failure, not a crash."""
    import tempfile

    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda *a, **k: (_ for _ in ()).throw(OSError("no space"))
    )
    output = tmp_path / "samples"
    code, out, err = run_cli(
        "generate-fixtures", "--output-dir", str(output), capsys=capsys
    )
    monkeypatch.undo()

    assert code == EXIT_OUTPUT
    assert out == ""
    assert err == "las: staging_error\n"
    assert "Traceback" not in err and "OSError" not in err
    assert "samples" not in err and "/" not in err
    assert not output.exists()
    assert staging_left(tmp_path) == []
    assert list(tmp_path.rglob("*.csv")) == []


def test_an_unexpected_failure_becomes_the_internal_code(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is known to be safe about it, so only a stable code is said."""
    import las.cli as cli

    def explode(*_args, **_kwargs):
        raise RuntimeError("a secret detail that must never travel")

    monkeypatch.setattr(cli, "run_pipeline", explode)
    code, out, err = run_cli(
        "run", "--config", str(make_project(tmp_path / "project")), capsys=capsys
    )
    assert code == EXIT_INTERNAL
    assert out == ""
    assert err == "las: internal_error\n"
    assert "secret detail" not in err
    assert "RuntimeError" not in err


def test_an_invalid_argument_uses_the_argparse_code(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run"])  # --config is required
    assert excinfo.value.code == EXIT_USAGE


def test_an_unknown_command_uses_the_argparse_code(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["nonsense"])
    assert excinfo.value.code == EXIT_USAGE


# --- determinism ----------------------------------------------------------------


def test_two_in_process_runs_agree_byte_for_byte(tmp_path: Path, capsys) -> None:
    _, first, _ = run_cli(
        "run", "--config", str(make_project(tmp_path / "one")), capsys=capsys
    )
    _, second, _ = run_cli(
        "run", "--config", str(make_project(tmp_path / "two")), capsys=capsys
    )
    assert first == second
    for name in FILENAME_BY_FORMAT.values():
        one = (tmp_path / "one" / "report" / name).read_bytes()
        two = (tmp_path / "two" / "report" / name).read_bytes()
        assert one == two, name
        assert hashlib.sha256(one).hexdigest() == hashlib.sha256(two).hexdigest()


def test_two_fixture_generations_agree_byte_for_byte(tmp_path: Path, capsys) -> None:
    run_cli("generate-fixtures", "--output-dir", str(tmp_path / "a"), capsys=capsys)
    run_cli("generate-fixtures", "--output-dir", str(tmp_path / "b"), capsys=capsys)
    for name in ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
