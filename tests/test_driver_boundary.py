"""Architecture and static-safety tests for the driver package."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER_DIR = REPO_ROOT / "src" / "las" / "driver"
TESTS_DIR = REPO_ROOT / "tests"

FORBIDDEN_PREFIXES = (
    "las.pipeline",
    "las.ingest",
    "las.validate",
    "las.match",
    "las.reconcile",
    "las.report",
    "las.cli",
)

#: The driver may import only from these las namespaces (plus stdlib).
ALLOWED_LAS_PREFIXES = ("las.driver", "las.simulator")


def iter_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def driver_sources() -> list[Path]:
    files = sorted(DRIVER_DIR.glob("*.py"))
    assert files, "driver package not found"
    return files


def test_driver_imports_only_allowed_packages() -> None:
    for path in driver_sources():
        for module in iter_imports(path):
            assert not module.startswith(FORBIDDEN_PREFIXES), (
                f"{path.name} imports forbidden module '{module}'"
            )
            if module == "las" or module.startswith("las."):
                assert module.startswith(ALLOWED_LAS_PREFIXES), (
                    f"{path.name} imports unexpected las module '{module}'"
                )


def test_importing_driver_loads_no_forbidden_modules() -> None:
    probe = (
        "import sys; import las.driver; "
        f"loaded = [m for m in sys.modules if m.startswith({FORBIDDEN_PREFIXES!r})]; "
        "print(','.join(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert result.stdout.strip() == "", (
        f"importing the driver pulled in: {result.stdout.strip()}"
    )


def test_driver_has_no_unbounded_loops_or_real_time() -> None:
    for path in driver_sources():
        text = path.read_text(encoding="utf-8")
        assert ("while " + "True") not in text, f"{path.name} has an unbounded loop"
        assert ("import " + "time") not in text, f"{path.name} imports real time"
        assert ("time" + ".sleep") not in text, f"{path.name} sleeps on real time"


def test_driver_errors_never_use_arbitrary_serialization() -> None:
    for path in driver_sources():
        text = path.read_text(encoding="utf-8")
        assert ("repr" + "(") not in text, f"{path.name} calls repr()"
        assert "!r" not in text, f"{path.name} formats with !r"


def test_driver_has_no_self_recursive_functions() -> None:
    for path in driver_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == node.name
                ):
                    raise AssertionError(
                        f"{path.name}:{node.name} calls itself recursively"
                    )


def test_driver_tests_contain_no_real_sleeps() -> None:
    # The needle is built by concatenation so this file cannot match itself.
    needle = "time" + ".sleep"
    for path in sorted(TESTS_DIR.glob("test_driver_*.py")):
        text = path.read_text(encoding="utf-8")
        assert needle not in text, f"{path.name} sleeps on real time"
