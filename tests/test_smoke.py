"""Smoke tests: package imports, version wiring, and CLI entry point."""

import importlib.metadata

import pytest

import las
from las.cli import build_parser, main

#: The distribution this package is installed as, per ``[project].name``.
DISTRIBUTION = "legacy-automation-simulator"


def test_package_has_version() -> None:
    assert isinstance(las.__version__, str)
    assert las.__version__


def test_installed_metadata_reports_the_version_the_package_declares() -> None:
    """``__version__`` is the source; the installed metadata is derived from it.

    Setuptools reads the attribute at build time, so these can only disagree if
    the wiring breaks. Asserted against the attribute rather than a literal, so
    the check survives the next version bump untouched.
    """
    assert importlib.metadata.version(DISTRIBUTION) == las.__version__


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
