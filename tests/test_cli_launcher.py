"""How the subprocess CLI suite finds the installed ``las`` command.

The regression these guard
--------------------------
``tests/test_cli.py`` used to build the launcher path from the *interpreter's*
directory: ``Path(sys.executable).parent / "las"``. That is right on POSIX by
coincidence — the interpreter and its console scripts share one ``bin``
directory — and wrong on Windows twice over: console scripts live in
``Scripts``, one level below the interpreter, and ``CreateProcess`` will not run
an extensionless file. Every subprocess test died before the CLI started.

What the resolver is, and is not
--------------------------------
It does **not** discover the scripts directory. ``sysconfig`` supplies that,
because ``sysconfig`` already knows the active scheme. The pure helper only
chooses the launcher *filename* beneath a directory it is handed, and that
choice depends on exactly one thing: whether the operating system is Windows.

So these tests hand it a directory and assert which name it picks. They never
assert anything about path *spelling*, because path flavour is the host's
business: on Windows a concrete ``Path`` is a ``WindowsPath``, on POSIX a
``PosixPath``, and the production resolver is correct precisely because it runs
on the host whose layout it is describing. A test that constructed a
foreign-flavour path on this host would be testing pathlib's cross-flavour
conversion, not the resolver.

Nothing here executes a fake launcher. The tests are about which path is
chosen; running an invented binary would prove nothing about that.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sysconfig
import tomllib
from pathlib import Path

import pytest

import pipeline_helpers
from pipeline_helpers import (
    LAUNCHER_STEM,
    REPO_ROOT,
    WINDOWS_OS_NAME,
    installed_launcher,
    launcher_path,
)

CLI_SUITE = REPO_ROOT / "tests" / "test_cli.py"
CLI_SOURCE = CLI_SUITE.read_text(encoding="utf-8")

#: A relative, platform-neutral stand-in for a scripts directory, for the
#: cases that need no filesystem at all.
RELATIVE_SCRIPTS_DIR = Path("runtime", "bin")


def resolver_identifiers() -> set[str]:
    """Every name the resolver's *code* mentions, with docstrings excluded.

    Prose is not behaviour: a docstring explaining ``which`` directory is used
    must not read as a call to ``shutil.which``.
    """
    names: set[str] = set()
    for function in (pipeline_helpers.launcher_path, pipeline_helpers.installed_launcher):
        tree = ast.parse(inspect.getsource(function).lstrip())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
    return names


def scripts_dir_holding_both(tmp_path: Path, leaf: str) -> Path:
    """A real directory containing *both* candidate launchers.

    Both names are present so a passing test proves the resolver *selected*
    one, rather than merely finding the only file there.
    """
    directory = tmp_path / "runtime" / leaf
    directory.mkdir(parents=True)
    (directory / "las").write_bytes(b"inert stand-in, never executed")
    (directory / "las.exe").write_bytes(b"inert stand-in, never executed")
    return directory


# --- the pure resolver, both layouts, from either host --------------------------


def test_posix_mode_chooses_the_extensionless_launcher() -> None:
    resolved = launcher_path(RELATIVE_SCRIPTS_DIR, os_name="posix")
    assert resolved.name == "las"
    assert resolved.suffix == ""
    assert resolved.parent == RELATIVE_SCRIPTS_DIR


def test_windows_mode_chooses_the_exe_launcher() -> None:
    resolved = launcher_path(RELATIVE_SCRIPTS_DIR, os_name=WINDOWS_OS_NAME)
    assert resolved.name == "las.exe"
    assert resolved.suffix == ".exe"
    assert resolved.parent == RELATIVE_SCRIPTS_DIR


def test_the_launcher_is_always_a_direct_child_of_the_given_directory() -> None:
    """The resolver adds a name; it never climbs, descends or wanders."""
    for os_name in ("posix", WINDOWS_OS_NAME):
        resolved = launcher_path(RELATIVE_SCRIPTS_DIR, os_name=os_name)
        assert resolved.parent == RELATIVE_SCRIPTS_DIR
        assert ".." not in resolved.parts
        assert resolved.stem == LAUNCHER_STEM


@pytest.mark.parametrize("os_name", ["posix", "java", "", "NT", "Nt"])
def test_only_the_exact_windows_name_earns_the_exe_suffix(os_name: str) -> None:
    """Case matters: ``os.name`` is ``nt`` exactly, never ``NT``."""
    assert launcher_path(RELATIVE_SCRIPTS_DIR, os_name=os_name).name == "las"


def test_the_resolver_defaults_to_the_running_platform() -> None:
    assert launcher_path(RELATIVE_SCRIPTS_DIR) == launcher_path(
        RELATIVE_SCRIPTS_DIR, os.name
    )


def test_the_resolver_touches_no_filesystem(tmp_path: Path) -> None:
    """Pure: it answers for a directory that does not exist, and creates nothing."""
    absent = tmp_path / "never-created"
    before = set(tmp_path.rglob("*"))
    assert launcher_path(absent, os_name="posix").parent == absent
    assert not absent.exists()
    assert set(tmp_path.rglob("*")) == before


def test_the_resolver_accepts_a_string_or_a_path(tmp_path: Path) -> None:
    assert launcher_path(str(tmp_path), os_name="posix") == launcher_path(
        tmp_path, os_name="posix"
    )


# --- simulated layouts, on disk, never executed ---------------------------------


def test_a_simulated_windows_scripts_directory_selects_the_exe(tmp_path: Path) -> None:
    scripts = scripts_dir_holding_both(tmp_path, "Scripts")
    resolved = launcher_path(scripts, os_name=WINDOWS_OS_NAME)

    assert resolved == scripts / "las.exe"
    assert resolved.is_file()
    assert resolved != scripts / "las", "the extensionless decoy must not win"


def test_a_simulated_posix_scripts_directory_selects_the_extensionless_file(
    tmp_path: Path,
) -> None:
    scripts = scripts_dir_holding_both(tmp_path, "bin")
    resolved = launcher_path(scripts, os_name="posix")

    assert resolved == scripts / "las"
    assert resolved.is_file()
    assert resolved != scripts / "las.exe", "the .exe decoy must not win"


def test_the_two_layouts_disagree_for_one_directory(tmp_path: Path) -> None:
    """The whole bug in one assertion: same directory, two different answers."""
    scripts = scripts_dir_holding_both(tmp_path, "Scripts")
    assert launcher_path(scripts, os_name="posix") != launcher_path(
        scripts, os_name=WINDOWS_OS_NAME
    )


def test_the_scripts_directory_is_not_the_interpreter_directory(
    tmp_path: Path,
) -> None:
    """The shape of the original defect, without naming any real machine.

    A Windows-like layout puts the launcher one level *below* the interpreter.
    Resolving from the interpreter's own directory — what the old helper did —
    lands on a file that is not there.
    """
    interpreter_dir = tmp_path / "runtime"
    scripts = scripts_dir_holding_both(tmp_path, "Scripts")
    assert scripts.parent == interpreter_dir

    old_style = interpreter_dir / LAUNCHER_STEM  # the bug, reconstructed
    assert not old_style.exists()

    resolved = launcher_path(scripts, os_name=WINDOWS_OS_NAME)
    assert resolved.is_file()
    assert resolved != old_style


# --- the active environment -----------------------------------------------------


def test_the_installed_launcher_exists_before_the_subprocess_suite_runs() -> None:
    """If this fails, every subprocess test would fail for one boring reason."""
    launcher = installed_launcher()
    assert launcher.is_file()
    assert launcher.stem == LAUNCHER_STEM


def test_the_installed_launcher_comes_from_the_configured_scripts_path() -> None:
    """``sysconfig`` is the authority, not the interpreter's own directory."""
    scripts = Path(sysconfig.get_path("scripts"))
    assert installed_launcher() == launcher_path(scripts)
    assert installed_launcher().parent == scripts


def test_an_absent_launcher_fails_clearly_and_searches_nowhere_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No silent substitution: a missing launcher is a loud, explained failure."""
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path))
    with pytest.raises(FileNotFoundError) as excinfo:
        installed_launcher()
    message = str(excinfo.value)
    assert "not installed" in message
    assert "pip install -e" in message
    assert LAUNCHER_STEM in message

    # And it never falls back to PATH, the home directory or the repository.
    # Matched against identifiers the code actually uses, not raw text: prose
    # in a docstring says nothing about behaviour.
    assert resolver_identifiers().isdisjoint(
        {"which", "environ", "getenv", "home", "cwd", "getcwd", "REPO_ROOT", "glob"}
    )


# --- the subprocess suite is still a subprocess suite ---------------------------


def cli_tree() -> ast.Module:
    return ast.parse(CLI_SOURCE)


def test_the_cli_suite_still_launches_a_subprocess() -> None:
    calls = [
        node
        for node in ast.walk(cli_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert len(calls) == 1, "one launcher, so one place can ever be wrong"


def test_the_cli_suite_never_uses_a_shell() -> None:
    for node in ast.walk(cli_tree()):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                assert keyword.arg != "shell", "shell=True is never needed here"
    assert "shell=True" not in CLI_SOURCE


def test_the_cli_suite_does_not_run_the_module_instead() -> None:
    for forbidden in ("-m", "las.cli", "runpy", "sys.executable"):
        assert forbidden not in CLI_SOURCE, forbidden


def test_the_cli_suite_imports_no_product_module() -> None:
    """A subprocess test that imported the CLI could accidentally call it."""
    imported: set[str] = set()
    for node in ast.walk(cli_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name == "las" or name.startswith("las.") for name in imported)


def test_the_cli_suite_calls_no_entry_point_in_process() -> None:
    for node in ast.walk(cli_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "main"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"main", "build_parser", "format_summary"}


def test_every_cli_test_goes_through_the_one_launcher_helper() -> None:
    """No test may quietly invoke something else."""
    tree = cli_tree()
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]

    def called_names(node: ast.AST) -> set[str]:
        return {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }

    reaching = {"las"}
    changed = True
    while changed:
        changed = False
        for function in functions:
            if function.name not in reaching and called_names(function) & reaching:
                reaching.add(function.name)
                changed = True

    tests = [f for f in functions if f.name.startswith("test_")]
    assert tests, "the suite must still contain tests"
    assert all(called_names(f) & reaching for f in tests)


def test_the_subprocess_suite_still_holds_its_original_tests() -> None:
    """The fix changed how the launcher is found, not what is asserted."""
    tests = [
        node
        for node in ast.walk(cli_tree())
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    assert len(tests) == 33, "33 functions, 36 cases once parametrised"


# --- the entry point itself -----------------------------------------------------


def test_the_entry_point_still_maps_las_to_the_approved_function() -> None:
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["scripts"] == {LAUNCHER_STEM: "las.cli:main"}


def test_the_declared_entry_point_is_importable_and_callable() -> None:
    module_name, _, attribute = "las.cli:main".partition(":")
    module = __import__(module_name, fromlist=[attribute])
    assert callable(getattr(module, attribute))


def test_the_installed_launcher_reports_the_package_version() -> None:
    """End to end: the resolved path really is this project's command."""
    from las import __version__

    result = subprocess.run(
        [str(installed_launcher()), "--version"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"{LAUNCHER_STEM} {__version__}"
