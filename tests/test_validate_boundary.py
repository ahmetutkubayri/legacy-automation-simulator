"""Architecture and static-safety tests for the validation package."""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from las.validate import validate_rows
from validate_helpers import Explosive, make_policy, make_row

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_DIR = REPO_ROOT / "src" / "las" / "validate"

#: Layers that depend on validation, and which validation must never import.
FORBIDDEN_PREFIXES = (
    "las.simulator",
    "las.driver",
    "las.pipeline",
    "las.match",
    "las.reconcile",
    "las.report",
    "las.cli",
    # The development plan does not place audit events in this task.
    "las.audit",
)

#: The only ``las`` namespaces validation may reach for.
ALLOWED_LAS_PREFIXES = ("las.validate", "las.ingest", "las.config")

#: Third-party modules validation must not touch. openpyxl belongs to
#: ingestion, and pandas belongs to no layer written so far.
FORBIDDEN_THIRD_PARTY = ("pandas", "numpy", "openpyxl", "yaml", "pydantic", "csv")


def validate_sources() -> list[Path]:
    files = sorted(VALIDATE_DIR.glob("*.py"))
    assert files, "the validate package was not found"
    return files


def code_only(path: Path) -> str:
    """The module's executable code, with docstrings and comments removed.

    Scanning raw text would flag the package's own prose — a docstring that
    promises "no randomness" would trip a search for ``random``. Re-emitting
    the parsed tree leaves only code, so these checks mean what they say.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


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


def test_validate_imports_no_forbidden_layer() -> None:
    for path in validate_sources():
        for module in iter_imports(path):
            assert not module.startswith(FORBIDDEN_PREFIXES), (
                f"{path.name} imports forbidden module '{module}'"
            )
            if module == "las" or module.startswith("las."):
                assert module.startswith(ALLOWED_LAS_PREFIXES), (
                    f"{path.name} imports unexpected las module '{module}'"
                )


def test_validate_does_not_touch_tabular_or_dataframe_libraries() -> None:
    for path in validate_sources():
        for module in iter_imports(path):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_THIRD_PARTY, f"{path.name} imports '{module}'"


def test_importing_validate_loads_no_forbidden_layer() -> None:
    probe = (
        "import sys; import las.validate; "
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
        f"importing validate pulled in: {result.stdout.strip()}"
    )


def test_importing_validate_does_not_load_pandas() -> None:
    probe = (
        "import sys; import las.validate; "
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


# --- purity -------------------------------------------------------------------


def test_validate_performs_no_filesystem_access() -> None:
    for path in validate_sources():
        text = code_only(path)
        for forbidden in (
            "open(",
            ".read_text(",
            ".read_bytes(",
            ".write_text(",
            ".write_bytes(",
            "Path(",
            "os.path",
            "shutil",
            "load_workbook",
            "csv.reader",
            "csv.writer",
            "urllib",
            "socket",
            "requests",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_validate_has_no_current_time_dependency() -> None:
    for path in validate_sources():
        text = code_only(path)
        for forbidden in (
            "import " + "time",
            "datetime.now",
            "date.today",
            "utcnow",
            "monotonic",
            "perf_counter",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_validate_introduces_no_randomness() -> None:
    for path in validate_sources():
        text = code_only(path)
        assert "random" not in text, f"{path.name} uses randomness"
        assert "uuid" not in text, f"{path.name} uses uuid"


def test_validate_uses_no_dynamic_execution_or_plugin_discovery() -> None:
    for path in validate_sources():
        text = code_only(path)
        for forbidden in (
            "eval(",
            "exec(",
            "__import__",
            "importlib",
            "entry_points",
            "pkgutil",
            "getattr(__",
            "globals()",
            "locals()",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_validate_never_serializes_arbitrary_objects() -> None:
    for path in validate_sources():
        text = code_only(path)
        assert ("repr" + "(") not in text, f"{path.name} calls repr()"
        assert "!r" not in text, f"{path.name} formats with !r"
        assert "pickle" not in text, f"{path.name} mentions pickle"


def test_validate_stringifies_only_configuration_bounds() -> None:
    """``str()`` appears once, converting a configured float bound exactly.

    Everything else in this package is already text from ingestion, so no
    source value is ever stringified.
    """
    occurrences = {path.name: code_only(path).count("str(") for path in validate_sources()}
    assert occurrences.pop("policy.py") == 1
    assert all(count == 0 for count in occurrences.values()), occurrences
    assert "Decimal(str(value))" in code_only(VALIDATE_DIR / "policy.py")


def test_validate_has_no_unbounded_loops() -> None:
    for path in validate_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.While), f"{path.name} has a while loop"


def test_no_absolute_paths_appear_in_validate_sources() -> None:
    needles = ("/Us" + "ers/", "/ho" + "me/", "C" + ":" + "\\", "\\\\" + "\\\\")
    for path in validate_sources():
        text = code_only(path)
        for forbidden in needles:
            assert forbidden not in text, f"{path.name} contains a path literal"


# --- deep immutability of every public model -----------------------------------


def test_every_public_model_is_a_frozen_dataclass() -> None:
    from las.validate import (
        BatchRule,
        BatchValidationResult,
        FieldRule,
        RuleSet,
        ValidatedRow,
        ValidationIssue,
        ValidationPolicy,
        ValidationSummary,
    )

    for cls in (
        ValidationIssue,
        ValidatedRow,
        ValidationSummary,
        BatchValidationResult,
        ValidationPolicy,
        FieldRule,
        BatchRule,
        RuleSet,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen"


def test_every_public_collection_field_is_immutable_after_construction() -> None:
    """Walk a real result and assert nothing mutable is reachable."""
    policy = make_policy(known_external_refs=("EXT-10000",))
    result = validate_rows(
        [make_row(2, amount="oops"), make_row(3, amount="oops")], policy
    )

    def assert_immutable(value: object, where: str) -> None:
        assert not isinstance(value, list | set | bytearray), f"{where} is mutable"
        if isinstance(value, dict):
            raise AssertionError(f"{where} is a bare dict")

    for name in vars(result):
        assert_immutable(getattr(result, name), f"result.{name}")
    for name in vars(result.summary):
        assert_immutable(getattr(result.summary, name), f"summary.{name}")
    for row in result.rows:
        for name in vars(row):
            assert_immutable(getattr(row, name), f"row.{name}")
        for issue in row.issues:
            for name in vars(issue):
                assert_immutable(getattr(issue, name), f"issue.{name}")


def test_a_result_never_retains_a_path_object() -> None:
    result = validate_rows([make_row()], make_policy())
    for name in vars(result):
        assert not isinstance(getattr(result, name), Path)
    for row in result.rows:
        assert not isinstance(row.provenance.source, Path)


# --- validation never mutates its input ---------------------------------------


def test_validation_leaves_rows_values_extras_and_provenance_untouched() -> None:
    row = make_row(2, amount="oops", extras={"reported by": "Demo Account 000001"})
    snapshot = (dict(row.values), dict(row.extras), row.provenance)

    validate_rows([row], make_policy())

    assert (dict(row.values), dict(row.extras), row.provenance) == snapshot
    with pytest.raises(TypeError):
        row.values["amount"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        row.extras["injected"] = "x"  # type: ignore[index]


def test_no_message_or_metadata_ever_stringifies_a_value() -> None:
    """A hostile value must pass straight through without being serialized."""
    row = make_row(2, extras={"probe": Explosive()})  # type: ignore[dict-item]
    result = validate_rows([row], make_policy())
    assert result.is_clean
    for issue in result.issues:  # pragma: no cover - the batch is clean
        assert isinstance(issue.message, str)


def test_messages_come_only_from_the_fixed_template_table() -> None:
    from las.validate import MESSAGE_BY_CODE

    policy = make_policy(known_external_refs=("EXT-10000",))
    rows = [
        make_row(2, record_id="REC-1", amount="oops", unit="PALLET", period="2026-13"),
        make_row(3, external_ref="EXT-90000"),
        make_row(4, record_id=None),
    ]
    result = validate_rows(rows, policy)
    assert result.issues
    for issue in result.issues:
        assert issue.message == MESSAGE_BY_CODE[issue.code]


def test_no_issue_message_or_metadata_echoes_a_source_value() -> None:
    policy = make_policy(known_external_refs=("EXT-10000",))
    sentinel_values = {
        "record_id": "REC-SENTINEL-ID",
        "amount": "SENTINEL-AMOUNT",
        "unit": "SENTINEL-UNIT",
        "period": "SENTINEL-PERIOD",
        "external_ref": "EXT-99999",
    }
    result = validate_rows([make_row(2, **sentinel_values)], policy)
    assert result.issues
    for issue in result.issues:
        rendered = issue.message + " ".join(issue.metadata.values())
        for value in sentinel_values.values():
            assert value not in rendered


def test_the_declared_public_surface_is_importable_and_unique() -> None:
    import las.validate as package

    assert len(package.__all__) == len(set(package.__all__)), "__all__ has duplicates"
    for name in package.__all__:
        assert hasattr(package, name), f"__all__ names missing attribute {name}"
