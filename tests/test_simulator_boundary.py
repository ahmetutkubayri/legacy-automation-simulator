"""Architecture tests: independence, no recovery logic, centralized faults."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_DIR = REPO_ROOT / "src" / "las" / "simulator"

#: Packages the simulator must never import (driver and pipeline layers
#: depend on the simulator — never the reverse).
FORBIDDEN_PREFIXES = (
    "las.driver",
    "las.pipeline",
    "las.ingest",
    "las.validate",
    "las.match",
    "las.reconcile",
    "las.report",
    "las.cli",
)


def iter_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_simulator_modules_import_no_forbidden_packages() -> None:
    python_files = sorted(SIMULATOR_DIR.glob("*.py"))
    assert python_files, "simulator package not found"
    for path in python_files:
        for module in iter_imports(path):
            assert not module.startswith(FORBIDDEN_PREFIXES), (
                f"{path.name} imports forbidden module '{module}'"
            )


def test_importing_simulator_loads_no_forbidden_modules() -> None:
    """Import the simulator in a clean interpreter and inspect sys.modules.

    A subprocess is required: within the test process other tests may
    legitimately have imported las.cli or future pipeline modules already.
    """
    probe = (
        "import sys; import las.simulator; "
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
        f"importing the simulator pulled in: {result.stdout.strip()}"
    )


_RECOVERY_WORDS = re.compile(
    r"\b(retry|retries|retried|retrying|backoff|recover|recovers|recovery)\b",
    re.IGNORECASE,
)


def test_simulator_contains_no_recovery_logic() -> None:
    """The simulator produces faults; handling them is the driver's job."""
    for path in sorted(SIMULATOR_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = _RECOVERY_WORDS.search(text)
        assert match is None, f"{path.name} mentions '{match.group(0) if match else ''}'"


def test_randomness_is_centralized_in_faults_module() -> None:
    """Only faults.py may touch randomness; decisions are centralized."""
    for path in sorted(SIMULATOR_DIR.glob("*.py")):
        modules = iter_imports(path)
        text = path.read_text(encoding="utf-8")
        if path.name == "faults.py":
            assert "random" in modules
        else:
            assert "random" not in modules, f"{path.name} imports random"
            assert "random." not in text, f"{path.name} uses randomness directly"
