"""Architecture and static-safety tests for the ingestion package."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from ingest_helpers import schema
from las.ingest import MalformedRowError, read_csv

REPO_ROOT = Path(__file__).resolve().parents[1]
INGEST_DIR = REPO_ROOT / "src" / "las" / "ingest"

#: Layers that depend on ingestion, and which ingestion must never import.
FORBIDDEN_PREFIXES = (
    "las.simulator",
    "las.driver",
    "las.pipeline",
    "las.validate",
    "las.match",
    "las.reconcile",
    "las.report",
    "las.cli",
)

#: The only ``las`` namespaces ingestion may reach for. ``las.audit`` is
#: deliberately absent: the development plan does not place audit events in
#: this task, so ingestion takes no dependency on it.
ALLOWED_LAS_PREFIXES = ("las.ingest", "las.config")

#: Third-party modules ingestion must not pull in. Only pinned openpyxl and
#: the standard library are permitted, so pandas stays out even though the
#: project declares it for a later stage.
FORBIDDEN_THIRD_PARTY = ("pandas", "numpy", "yaml", "pydantic")


def ingest_sources() -> list[Path]:
    files = sorted(INGEST_DIR.glob("*.py"))
    assert files, "the ingest package was not found"
    return files


def iter_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


# --- layering -----------------------------------------------------------------


def test_ingest_imports_no_forbidden_layer() -> None:
    for path in ingest_sources():
        for module in iter_imports(path):
            assert not module.startswith(FORBIDDEN_PREFIXES), (
                f"{path.name} imports forbidden module '{module}'"
            )
            if module == "las" or module.startswith("las."):
                assert module.startswith(ALLOWED_LAS_PREFIXES), (
                    f"{path.name} imports unexpected las module '{module}'"
                )


def test_ingest_does_not_depend_on_pandas_or_other_extras() -> None:
    for path in ingest_sources():
        for module in iter_imports(path):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_THIRD_PARTY, (
                f"{path.name} imports '{module}'"
            )


def test_importing_ingest_loads_no_forbidden_modules() -> None:
    """Import in a clean interpreter: nothing later in the pipeline appears."""
    probe = (
        "import sys; import las.ingest; "
        f"loaded = [m for m in sys.modules if m.startswith({FORBIDDEN_PREFIXES!r})]; "
        "print(','.join(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == "", (
        f"importing ingest pulled in: {result.stdout.strip()}"
    )


def test_importing_ingest_does_not_load_pandas() -> None:
    """pandas must never be loaded, however indirectly.

    numpy is deliberately *not* asserted against here: the pinned openpyxl
    imports it on its own, before any of this package is involved, which the
    companion test below pins so the omission cannot be mistaken for an
    oversight. pandas has no such excuse.
    """
    probe = (
        "import sys; import las.ingest; "
        "print('pandas' if 'pandas' in sys.modules else '')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == ""


def test_numpy_arrives_through_openpyxl_and_not_through_ingest() -> None:
    """Records why numpy is exempt from the runtime import check.

    If a future openpyxl stops importing numpy, this test fails and the
    exemption above should be tightened rather than quietly kept.
    """
    probe = (
        "import sys; import openpyxl; "
        "print('numpy' if 'numpy' in sys.modules else '')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == "numpy", (
        "openpyxl no longer pulls in numpy; tighten the runtime import check"
    )
    # And ingest itself never names numpy, which the static check enforces.
    for path in ingest_sources():
        assert "numpy" not in path.read_text(encoding="utf-8")


def test_openpyxl_is_used_only_by_the_reader_module() -> None:
    for path in ingest_sources():
        uses_openpyxl = any(
            module.split(".")[0] == "openpyxl" for module in iter_imports(path)
        )
        if uses_openpyxl:
            assert path.name == "readers.py", (
                f"{path.name} should not need openpyxl directly"
            )


# --- read-only guarantees -----------------------------------------------------


def test_ingest_never_writes_or_saves() -> None:
    for path in ingest_sources():
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            ".save(",
            ".write_text(",
            ".write_bytes(",
            ".writerow",
            ".writelines",
            "shutil",
            "os.remove",
            "os.rename",
            "unlink",
            "mkdir",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_files_are_only_ever_opened_for_reading() -> None:
    """Every open() call must be an explicit read mode."""
    for path in ingest_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name not in {"open", "load_workbook"}:
                continue
            if name == "open":
                # Handles both the builtin open(path, mode) and the
                # Path.open(mode) method form, where the mode shifts position.
                modes = [
                    arg.value
                    for arg in node.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ]
                modes += [
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "mode"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ]
                assert modes, f"{path.name}: open() must state a mode explicitly"
                for mode in modes:
                    assert mode == "r", f"{path.name}: open() mode '{mode}' is not read-only"
            else:
                read_only = [
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "read_only" and isinstance(kw.value, ast.Constant)
                ]
                assert read_only == [True], (
                    f"{path.name}: load_workbook must pass read_only=True"
                )


def test_workbooks_are_opened_without_trusting_cached_formula_values() -> None:
    text = (INGEST_DIR / "readers.py").read_text(encoding="utf-8")
    assert "data_only=False" in text


# --- determinism and safety ---------------------------------------------------


def test_ingest_has_no_real_time_dependency() -> None:
    for path in ingest_sources():
        text = path.read_text(encoding="utf-8")
        assert ("import " + "time") not in text, f"{path.name} imports real time"
        assert ("time" + ".sleep") not in text, f"{path.name} sleeps"
        assert "datetime.now" not in text, f"{path.name} reads the wall clock"
        assert "date.today" not in text, f"{path.name} reads today's date"


def test_ingest_introduces_no_randomness() -> None:
    for path in ingest_sources():
        text = path.read_text(encoding="utf-8")
        assert "random" not in text, f"{path.name} mentions randomness"
        assert "uuid" not in text, f"{path.name} mentions uuid"


def test_ingest_never_serializes_arbitrary_objects() -> None:
    for path in ingest_sources():
        text = path.read_text(encoding="utf-8")
        assert ("repr" + "(") not in text, f"{path.name} calls repr()"
        assert "!r" not in text, f"{path.name} formats with !r"
        assert ("str" + "(") not in text, f"{path.name} calls str() on a value"
        assert "pickle" not in text, f"{path.name} mentions pickle"


def test_ingest_has_no_unbounded_loops() -> None:
    for path in ingest_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                # A while is acceptable only with a concrete, shrinking test;
                # the sole one trims trailing padding off a header row.
                assert path.name == "readers.py", f"{path.name} has a while loop"
                assert not (
                    isinstance(node.test, ast.Constant) and node.test.value is True
                ), f"{path.name} has an unbounded loop"


def test_no_absolute_paths_appear_in_ingest_sources() -> None:
    # The needles are assembled from fragments so this file does not itself
    # contain the literals a pre-publication path scan looks for. A UNC
    # literal in source needs four backslashes; two are just an escaped
    # separator, which the normalization docs legitimately show.
    needles = ("/Us" + "ers/", "/ho" + "me/", "C" + ":" + "\\", "\\\\" + "\\\\")
    for path in ingest_sources():
        text = path.read_text(encoding="utf-8")
        for forbidden in needles:
            assert forbidden not in text, f"{path.name} contains a path literal"


# --- behavioural: no path ever escapes into results or errors ------------------


def test_results_and_errors_expose_only_a_basename(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "directory"
    nested.mkdir(parents=True)
    good = nested / "fine.csv"
    good.write_text(
        "record_id,external_ref,amount\nOPR-00000001,EXT-10000,1\n",
        encoding="utf-8",
        newline="",
    )
    bad = nested / "broken.csv"
    bad.write_text(
        "record_id,external_ref,amount\nOPR-00000001,EXT-10000\n",
        encoding="utf-8",
        newline="",
    )

    result = read_csv(good, schema())
    assert result.source == "fine.csv"
    assert str(nested) not in result.source
    for row in result.rows:
        assert str(nested) not in row.provenance.source

    with pytest.raises(MalformedRowError) as excinfo:
        read_csv(bad, schema())
    message = str(excinfo.value)
    assert "broken.csv" in message
    assert str(nested) not in message
    assert "deep" not in message


def test_every_public_result_type_is_frozen() -> None:
    import dataclasses

    from las.ingest import (
        FieldSpec,
        HeaderResolution,
        IngestedRow,
        IngestResult,
        IngestSchema,
        RowProvenance,
    )

    for cls in (
        RowProvenance,
        IngestedRow,
        IngestResult,
        FieldSpec,
        IngestSchema,
        HeaderResolution,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen"


def test_the_declared_public_surface_is_importable() -> None:
    import las.ingest as package

    # Constants first, then types and functions -- the convention the driver
    # and simulator packages already follow.
    assert len(package.__all__) == len(set(package.__all__)), "__all__ has duplicates"
    for name in package.__all__:
        assert hasattr(package, name), f"__all__ names missing attribute {name}"
