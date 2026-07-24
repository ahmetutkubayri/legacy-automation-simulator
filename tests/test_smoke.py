"""Smoke tests: package imports, version wiring, and CLI entry point."""

import pytest

import las
from las.cli import build_parser, main


def test_package_has_version() -> None:
    assert isinstance(las.__version__, str)
    assert las.__version__


def test_cli_version_flag_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert las.__version__ in capsys.readouterr().out


def test_cli_without_arguments_prints_help_and_succeeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 0
    assert "Legacy Automation Simulator" in capsys.readouterr().out
