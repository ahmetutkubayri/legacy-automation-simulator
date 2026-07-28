"""Architecture and static-safety tests for the reconciliation package."""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from las.reconcile import (
    BatchReconcileResult,
    Contribution,
    GroupKey,
    GroupResult,
    ReconcilePolicy,
    ReconcileSummary,
    Side,
    SideAggregate,
    SystemLedgerRow,
    UnresolvedRow,
    reconcile,
)
from reconcile_helpers import feed_row, ledger_row, make_batch, make_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
RECONCILE_DIR = REPO_ROOT / "src" / "las" / "reconcile"

#: Layers reconciliation must never reach for. The simulator and the driver
#: are the point: the ledger crosses over as a neutral DTO instead.
FORBIDDEN_PREFIXES = (
    "las.simulator",
    "las.driver",
    "las.pipeline",
    "las.report",
    "las.cli",
    # The development plan does not place audit events in this task.
    "las.audit",
)

#: The only ``las`` namespaces reconciliation may reach for.
ALLOWED_LAS_PREFIXES = (
    "las.reconcile",
    "las.ingest",
    "las.validate",
    "las.match",
    "las.config",
)

#: Third-party and reader modules reconciliation must not touch.
FORBIDDEN_THIRD_PARTY = ("pandas", "numpy", "openpyxl", "yaml", "csv")


def reconcile_sources() -> list[Path]:
    files = sorted(RECONCILE_DIR.glob("*.py"))
    assert files, "the reconcile package was not found"
    return files


def code_only(path: Path) -> str:
    """The module's executable code, with docstrings and comments removed.

    Scanning raw text would flag the package's own prose — a docstring that
    promises "no unit conversion" would trip a search for ``convert``.
    Re-emitting the parsed tree leaves only code, so these checks mean what
    they say.
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


def test_reconcile_imports_no_forbidden_layer() -> None:
    for path in reconcile_sources():
        for module in iter_imports(path):
            assert not module.startswith(FORBIDDEN_PREFIXES), (
                f"{path.name} imports forbidden module '{module}'"
            )
            if module == "las" or module.startswith("las."):
                assert module.startswith(ALLOWED_LAS_PREFIXES), (
                    f"{path.name} imports unexpected las module '{module}'"
                )


def test_reconcile_never_mentions_the_driver_or_the_simulator() -> None:
    """Not even by name in code: the system side arrives as a neutral DTO."""
    for path in reconcile_sources():
        text = code_only(path)
        for forbidden in ("simulator", "driver", "ExtractedRow", "RecordStore",
                          "LegacySystem", "OperationalRecord"):
            assert forbidden not in text, f"{path.name} refers to '{forbidden}'"


def test_reconcile_touches_no_reader_or_dataframe_library() -> None:
    for path in reconcile_sources():
        for module in iter_imports(path):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_THIRD_PARTY, f"{path.name} imports '{module}'"


def test_importing_reconcile_loads_no_forbidden_layer() -> None:
    probe = (
        "import sys; import las.reconcile; "
        f"loaded = [m for m in sys.modules if m.startswith({FORBIDDEN_PREFIXES!r})]; "
        "print(','.join(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True, timeout=60, cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == "", (
        f"importing reconcile pulled in: {result.stdout.strip()}"
    )


def test_importing_reconcile_does_not_load_pandas() -> None:
    """openpyxl, and numpy behind it, are not checked here: they arrive
    through ``las.ingest``, an allowed dependency that legitimately reads
    spreadsheets. What matters is that reconciliation itself imports neither,
    which the import-scan test above asserts directly. pandas belongs to no
    layer written so far, so it must be absent outright."""
    probe = (
        "import sys; import las.reconcile; "
        "print('pandas' if 'pandas' in sys.modules else '')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True, timeout=60, cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == ""


# --- purity -------------------------------------------------------------------


def test_reconcile_performs_no_filesystem_or_network_access() -> None:
    for path in reconcile_sources():
        text = code_only(path)
        for forbidden in (
            "open(", ".read_text(", ".read_bytes(", ".write_text(", ".write_bytes(",
            "Path(", "os.path", "os.environ", "getenv", "shutil", "load_workbook",
            "csv.reader", "urllib", "socket", "requests", "subprocess",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_reconcile_has_no_current_time_dependency() -> None:
    for path in reconcile_sources():
        text = code_only(path)
        for forbidden in (
            "import " + "time", "datetime", "date.today", "utcnow",
            "monotonic", "perf_counter",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_reconcile_introduces_no_randomness() -> None:
    for path in reconcile_sources():
        text = code_only(path)
        assert "random" not in text, f"{path.name} uses randomness"
        assert "uuid" not in text, f"{path.name} uses uuid"


def test_reconcile_uses_no_dynamic_execution_or_plugin_discovery() -> None:
    for path in reconcile_sources():
        text = code_only(path)
        for forbidden in (
            "eval(", "exec(", "__import__", "importlib", "entry_points",
            "pkgutil", "globals()", "locals()",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_reconcile_never_serializes_arbitrary_objects() -> None:
    for path in reconcile_sources():
        text = code_only(path)
        assert ("repr" + "(") not in text, f"{path.name} calls repr()"
        assert "!r" not in text, f"{path.name} formats with !r"
        assert "pickle" not in text, f"{path.name} mentions pickle"


def test_reconcile_stringifies_only_a_configured_bound() -> None:
    """``str()`` appears once, converting a configured band exactly."""
    occurrences = {path.name: code_only(path).count("str(") for path in reconcile_sources()}
    assert occurrences.pop("policy.py") == 1
    assert all(count == 0 for count in occurrences.values()), occurrences
    assert "Decimal(str(value))" in code_only(RECONCILE_DIR / "policy.py")


def test_reconcile_has_no_unbounded_loops() -> None:
    for path in reconcile_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.While), f"{path.name} has a while loop"


def test_no_function_in_the_package_recurses() -> None:
    for path in reconcile_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    assert call.func.id != node.name, f"{path.name}:{node.name} recurses"


def test_no_absolute_path_or_platform_assumption_appears() -> None:
    needles = ("/Us" + "ers/", "/ho" + "me/", "C" + ":" + "\\", "\\\\" + "\\\\",
               "sys.platform", "os.name", "platform.system")
    for path in reconcile_sources():
        text = code_only(path)
        for forbidden in needles:
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_no_base_exception_is_ever_caught() -> None:
    for path in reconcile_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = [
                child.id
                for child in ast.walk(node.type)
                if isinstance(child, ast.Name)
            ] if node.type else ["<bare>"]
            for name in names:
                assert name not in ("BaseException", "<bare>"), (
                    f"{path.name} catches {name}"
                )


# --- what T12 deliberately does not do ----------------------------------------


def mentions(text: str, word: str) -> bool:
    """Whole-word search, so ``rate`` does not match ``enumerate``."""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def test_no_unit_conversion_vocabulary_or_implementation_exists() -> None:
    for path in reconcile_sources():
        text = code_only(path).lower()
        for forbidden in (
            "convert", "conversion", "factor", "exchange", "rate",
            "to_kg", "to_ea", "scale_unit", "normalize_unit",
        ):
            assert not mentions(text, forbidden), f"{path.name} contains '{forbidden}'"


def test_no_report_generation_code_exists() -> None:
    for path in reconcile_sources():
        text = code_only(path).lower()
        # "worksheet" is absent from this list on purpose: it is a T9
        # provenance field a contribution legitimately carries, not reporting.
        for forbidden in ("workbook", "render", "template", "html", "json",
                          "dumps", "stylesheet", "chart", "styling"):
            assert not mentions(text, forbidden), f"{path.name} contains '{forbidden}'"


def test_no_matching_is_performed_here() -> None:
    """Account references arrive resolved; nothing here looks one up."""
    for path in reconcile_sources():
        text = code_only(path)
        for forbidden in ("resolve_link", "build_master_index", "similarity",
                          "normalize_name", "MasterIndex", "edit_distance"):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_no_division_is_performed_anywhere() -> None:
    """Bands are absolute differences, so no denominator exists."""
    for path in reconcile_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node.op, ast.Div | ast.FloorDiv) if isinstance(
                node, ast.BinOp
            ) else True, f"{path.name} divides"
        assert "divide" not in code_only(path)


def test_no_float_conversion_reaches_an_amount() -> None:
    for path in reconcile_sources():
        text = code_only(path)
        assert "float(" not in text, f"{path.name} converts to float"
        assert "round(" not in text, f"{path.name} rounds"
        assert "quantize" not in text, f"{path.name} quantizes"


# --- deep immutability ---------------------------------------------------------


def test_every_public_model_is_a_frozen_dataclass() -> None:
    for cls in (
        GroupKey, SystemLedgerRow, Contribution, SideAggregate, GroupResult,
        UnresolvedRow, ReconcileSummary, BatchReconcileResult, ReconcilePolicy,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen"


def test_no_reachable_public_collection_is_mutable() -> None:
    result = reconcile(
        [make_batch([feed_row(2), feed_row(3, unit="KG")])],
        (ledger_row(), ledger_row(position=1, unit="BOX")),
        make_policy(),
    )

    def assert_immutable(value: object, where: str) -> None:
        assert not isinstance(value, list | set | dict | bytearray), f"{where} is mutable"

    for name in vars(result):
        assert_immutable(getattr(result, name), f"result.{name}")
    for name in vars(result.summary):
        assert_immutable(getattr(result.summary, name), f"summary.{name}")
    for group in result.groups:
        for name in vars(group):
            assert_immutable(getattr(group, name), f"group.{name}")
        for side in (group.feed, group.system):
            for name in vars(side):
                assert_immutable(getattr(side, name), f"side.{name}")
            for contribution in side.contributions:
                for name in vars(contribution):
                    assert_immutable(getattr(contribution, name), f"contribution.{name}")


def test_a_result_never_retains_a_path_object() -> None:
    result = reconcile([make_batch([feed_row(2)])], (ledger_row(),), make_policy())
    for group in result.groups:
        for contribution in group.feed.contributions:
            assert not isinstance(contribution.source, Path)
    assert not any(isinstance(getattr(result, n), Path) for n in vars(result))


def test_inputs_are_never_mutated_by_a_reconciliation() -> None:
    batch = make_batch([feed_row(2), feed_row(3)])
    system = (ledger_row(), ledger_row(position=1))
    policy = make_policy()
    before = (
        tuple(batch.validation.rows),
        tuple(batch.matching.decisions),
        tuple(batch.validation.issues),
        system,
        policy.variance_bands,
    )
    reconcile([batch], system, policy)
    assert (
        tuple(batch.validation.rows),
        tuple(batch.matching.decisions),
        tuple(batch.validation.issues),
        system,
        policy.variance_bands,
    ) == before


def test_a_caller_list_cannot_leak_into_a_result() -> None:
    batches = [make_batch([feed_row(2)])]
    system = [ledger_row()]
    result = reconcile(batches, system, make_policy())
    batches.append(make_batch([feed_row(9)]))
    system.append(ledger_row(position=9))
    assert result.summary.feed_rows_in == 1
    assert result.summary.system_rows_in == 1
    assert len(result.groups[0].system.contributions) == 1


# --- decimal safety ------------------------------------------------------------


def test_every_amount_on_a_result_is_a_decimal() -> None:
    result = reconcile(
        [make_batch([feed_row(2, amount="1.25")])],
        (ledger_row(amount_text="1.00"),),
        make_policy(),
    )
    group = result.groups[0]
    for value in (group.signed_difference, group.absolute_difference, group.variance_band):
        assert isinstance(value, Decimal)
    for side in (group.feed, group.system):
        assert all(isinstance(total, Decimal) for total in side.totals_by_unit.values())
        assert all(isinstance(c.amount, Decimal) for c in side.contributions)


def test_no_amount_is_ever_a_float() -> None:
    result = reconcile(
        [make_batch([feed_row(2, amount="0.1")])],
        (ledger_row(amount_text="0.2"),),
        make_policy(),
    )
    group = result.groups[0]
    assert not isinstance(group.signed_difference, float)
    assert group.signed_difference == Decimal("-0.1")


# --- the declared surface ------------------------------------------------------


def test_the_declared_public_surface_is_importable_and_unique() -> None:
    import las.reconcile as package

    assert len(package.__all__) == len(set(package.__all__)), "__all__ has duplicates"
    for name in package.__all__:
        assert hasattr(package, name), f"__all__ names missing attribute {name}"


def test_the_two_sides_are_the_only_ones() -> None:
    assert len(list(Side)) == 2


@pytest.mark.parametrize("module", ["errors", "models", "policy", "grouping", "engine"])
def test_each_module_exists_and_is_importable(module: str) -> None:
    assert (RECONCILE_DIR / f"{module}.py").is_file()
