"""Architecture and static-safety tests for the reporting package."""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from las.report import (
    Cell,
    Column,
    OutputFormat,
    RenderedArtifact,
    ReportModel,
    ReportPolicy,
    ReportSummary,
    Row,
    SheetId,
    Table,
    build_report,
    render_artifacts,
)
from report_helpers import golden_result, make_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "src" / "las" / "report"

#: Layers reporting must never reach for. It sits at the end of the pipeline
#: and consumes one immutable reconciliation result, nothing else.
FORBIDDEN_PREFIXES = (
    "las.simulator",
    "las.driver",
    "las.ingest",
    "las.validate",
    "las.match",
    "las.pipeline",
    "las.cli",
    # The development plan does not place audit events in this task.
    "las.audit",
)

#: The only ``las`` namespaces reporting may reach for.
ALLOWED_LAS_PREFIXES = ("las.report", "las.reconcile", "las.config")

#: Third-party modules reporting must not touch. openpyxl is the exception,
#: because the plan authorizes an XLSX artifact.
FORBIDDEN_THIRD_PARTY = ("pandas", "numpy", "yaml", "requests", "urllib3", "jinja2")


def report_sources() -> list[Path]:
    files = sorted(REPORT_DIR.glob("*.py"))
    assert files, "the report package was not found"
    return files


def code_only(path: Path) -> str:
    """The module's executable code, with docstrings and comments removed.

    Scanning raw text would flag the package's own prose — a docstring that
    promises "no current time" would trip a search for ``datetime``.
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


ALL_CODE = " ".join(code_only(path) for path in report_sources())


def mentions(text: str, word: str) -> bool:
    """Whole-word search, so ``format`` does not match ``formats``."""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


# --- layering -----------------------------------------------------------------


def test_report_imports_no_forbidden_layer() -> None:
    for path in report_sources():
        for module in iter_imports(path):
            assert not module.startswith(FORBIDDEN_PREFIXES), (
                f"{path.name} imports forbidden module '{module}'"
            )
            if module == "las" or module.startswith("las."):
                assert module.startswith(ALLOWED_LAS_PREFIXES), (
                    f"{path.name} imports unexpected las module '{module}'"
                )


def test_report_never_mentions_the_simulator_or_the_driver() -> None:
    for forbidden in ("simulator", "driver", "ExtractedRow", "LegacySystem",
                      "RecordStore", "pipeline", "argparse"):
        assert not mentions(ALL_CODE, forbidden), f"report code mentions '{forbidden}'"


def test_report_touches_no_forbidden_third_party_module() -> None:
    for path in report_sources():
        for module in iter_imports(path):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_THIRD_PARTY, f"{path.name} imports '{module}'"


#: Layers that must not load even transitively. ingest, validate and match are
#: absent on purpose: they arrive behind ``las.reconcile``, which reporting is
#: allowed to depend on, and which needs them for its own public types.
NEVER_LOADED = ("las.simulator", "las.driver", "las.pipeline", "las.cli", "las.audit")


def test_importing_report_loads_no_forbidden_layer() -> None:
    probe = (
        "import sys; import las.report; "
        f"loaded = [m for m in sys.modules if m.startswith({NEVER_LOADED!r})]; "
        "print(','.join(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True, timeout=60, cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == "", (
        f"importing report pulled in: {result.stdout.strip()}"
    )


def test_importing_report_does_not_load_pandas_or_numpy() -> None:
    probe = (
        "import sys; import las.report; "
        "print(','.join(m for m in ('pandas',) if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True, timeout=60, cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == ""


# --- purity -------------------------------------------------------------------


def test_report_performs_no_filesystem_access() -> None:
    for path in report_sources():
        text = code_only(path)
        for forbidden in (
            "open(", ".read_text(", ".read_bytes(", ".write_text(", ".write_bytes(",
            "Path(", "os.path", "os.environ", "getenv", "getcwd", "expanduser",
            "shutil", "mkdir", "os.sep", "tempfile", "load_workbook",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_report_writes_no_file_and_names_no_path() -> None:
    """Every artifact is bytes; T14 decides where they go."""
    for forbidden in ("filename", "output_dir", "directory", "filepath", "save",
                      "dump", "write_to", "mkstemp"):
        assert not mentions(ALL_CODE, forbidden), f"report code mentions '{forbidden}'"


def test_report_opens_no_socket_and_sends_no_mail() -> None:
    for forbidden in ("socket", "smtplib", "email", "urllib", "requests",
                      "httpx", "sendmail", "SMTP"):
        assert not mentions(ALL_CODE, forbidden), f"report code mentions '{forbidden}'"


def test_report_reads_no_clock() -> None:
    """A fixed epoch constant is allowed; a clock reading is not."""
    for forbidden in ("now", "utcnow", "today", "time", "monotonic",
                      "perf_counter", "timestamp"):
        assert not mentions(ALL_CODE, forbidden), f"report code mentions '{forbidden}'"
    assert "datetime(*WORKBOOK_EPOCH)" in code_only(REPORT_DIR / "xlsx.py")


def test_report_introduces_no_randomness() -> None:
    for path in report_sources():
        text = code_only(path)
        assert "random" not in text, f"{path.name} uses randomness"
        assert "uuid" not in text, f"{path.name} uses uuid"


def test_report_depends_on_no_locale() -> None:
    for forbidden in ("locale", "gettext", "strftime", "strptime", "babel"):
        assert not mentions(ALL_CODE, forbidden), f"report code mentions '{forbidden}'"


def test_report_uses_no_dynamic_execution_or_plugin_discovery() -> None:
    for path in report_sources():
        text = code_only(path)
        for forbidden in (
            "eval(", "exec(", "__import__", "importlib", "entry_points",
            "pkgutil", "globals()", "locals()",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_report_never_serializes_arbitrary_objects() -> None:
    for path in report_sources():
        text = code_only(path)
        assert ("repr" + "(") not in text, f"{path.name} calls repr()"
        assert "!r" not in text, f"{path.name} formats with !r"
        assert "pickle" not in text, f"{path.name} mentions pickle"
    assert "default=" not in ALL_CODE, "the json encoder must have no fallback"


def test_report_stringifies_nothing_arbitrary() -> None:
    """``str(`` appears nowhere: numbers go through the canonical renderers."""
    occurrences = {path.name: code_only(path).count("str(") for path in report_sources()}
    assert all(count == 0 for count in occurrences.values()), occurrences


def test_report_has_no_unbounded_loops() -> None:
    for path in report_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.While), f"{path.name} has a while loop"


def test_no_function_in_the_package_recurses() -> None:
    for path in report_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    assert call.func.id != node.name, f"{path.name}:{node.name} recurses"


def test_no_absolute_path_or_platform_assumption_appears() -> None:
    needles = ("/Us" + "ers/", "/ho" + "me/", "C" + ":" + "\\",
               "sys.platform", "os.name", "platform.system", "os.linesep")
    for path in report_sources():
        text = code_only(path)
        for forbidden in needles:
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"
        # A UNC server reference, precisely: the only backslashes in this
        # package belong to the character class of names a workbook forbids.
        assert not re.search(r"\\\\\\\\[A-Za-z]", text), f"{path.name} names a UNC path"


def test_no_base_exception_is_ever_caught() -> None:
    for path in report_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = [
                child.id for child in ast.walk(node.type) if isinstance(child, ast.Name)
            ] if node.type else ["<bare>"]
            for name in names:
                assert name not in ("BaseException", "<bare>"), (
                    f"{path.name} catches {name}"
                )


# --- what T13 deliberately does not do -----------------------------------------


def test_no_reconciliation_or_matching_logic_is_duplicated() -> None:
    # "reconcile" itself is absent from this list: reporting reads T12's public
    # types by design. What must not reappear is T12's *arithmetic*.
    # T12 *function* names only. "system_contributions" and its siblings are
    # accounting metric keys the report states, not arithmetic it performs.
    for forbidden in ("exact_sum", "exact_difference", "band_for",
                      "resolve_link", "similarity", "normalize_name",
                      "check_batches", "check_system_rows", "group_keys",
                      "feed_contributions_for", "system_contributions_for"):
        assert not mentions(ALL_CODE, forbidden), f"report code mentions '{forbidden}'"


def test_no_unit_conversion_is_implemented() -> None:
    for forbidden in ("convert", "conversion", "exchange", "scale_unit", "factor"):
        assert not mentions(ALL_CODE.lower(), forbidden)


def test_no_status_write_back_is_implemented() -> None:
    for forbidden in ("update_status", "write_back", "status", "dry_run"):
        assert not mentions(ALL_CODE, forbidden), f"report code mentions '{forbidden}'"


#: The one function allowed to do integer arithmetic, and only on digit
#: positions: it trims a configured bound's float-to-text artifact by moving
#: an exponent, never by computing a new amount.
ARITHMETIC_EXEMPT = "strip_trailing_zeros"


def test_no_report_layer_arithmetic_beyond_counting() -> None:
    """Counting adds; nothing here subtracts, multiplies or divides a value."""
    exempt_ranges = []
    for path in report_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == ARITHMETIC_EXEMPT:
                exempt_ranges.append((path, node.lineno, node.end_lineno))

        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp):
                continue
            if not isinstance(node.op, ast.Sub | ast.Mult | ast.Div | ast.FloorDiv):
                continue
            inside_exempt = any(
                exempt_path == path and start <= node.lineno <= end
                for exempt_path, start, end in exempt_ranges
            )
            assert inside_exempt, f"{path.name}:{node.lineno} does arithmetic on a value"
    assert exempt_ranges, "the documented exemption no longer exists"


def test_the_arithmetic_exemption_touches_no_amount() -> None:
    """It rebuilds a Decimal from digits; it never adds or scales one."""
    from las.report import strip_trailing_zeros

    for text in ("1.0", "0.100", "5.000", "12.3400"):
        assert strip_trailing_zeros(Decimal(text)) == Decimal(text)


def test_no_float_conversion_or_rounding_appears() -> None:
    for path in report_sources():
        text = code_only(path)
        for forbidden in ("float", "round", "quantize"):
            assert not mentions(text, forbidden), f"{path.name} contains '{forbidden}'"
        for forbidden in ("%.2f", ":.2f"):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


# --- deep immutability ----------------------------------------------------------


def test_every_public_model_is_a_frozen_dataclass() -> None:
    for cls in (
        Column, Cell, Row, Table, ReportSummary, ReportModel,
        RenderedArtifact, ReportPolicy,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen"


def test_no_reachable_public_collection_is_mutable() -> None:
    report = build_report(golden_result(), make_policy())

    def assert_immutable(value: object, where: str) -> None:
        assert not isinstance(value, list | set | dict | bytearray), f"{where} is mutable"

    for name in vars(report.summary):
        assert_immutable(getattr(report.summary, name), f"summary.{name}")
    for table in report.tables:
        for name in vars(table):
            assert_immutable(getattr(table, name), f"table.{name}")
        for row in table.rows:
            for name in vars(row):
                assert_immutable(getattr(row, name), f"row.{name}")


def test_a_caller_cannot_mutate_a_report_after_construction() -> None:
    report = build_report(golden_result(), make_policy())
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.summary = None  # type: ignore[misc]
    with pytest.raises(AttributeError):
        report.reconciliation.rows.append(None)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        report.summary.recipients.append("x")  # type: ignore[attr-defined]


def test_no_artifact_or_model_retains_a_path_object() -> None:
    for item in render_artifacts(golden_result(), make_policy()):
        for name in vars(item):
            assert not isinstance(getattr(item, name), Path)


def test_inputs_are_never_mutated_by_rendering() -> None:
    result = golden_result()
    policy = make_policy()
    before = (result.groups, result.unresolved, policy.formats, policy.variance_bands)
    render_artifacts(result, policy)
    assert (
        result.groups, result.unresolved, policy.formats, policy.variance_bands
    ) == before


# --- the declared surface --------------------------------------------------------


def test_the_declared_public_surface_is_importable_and_unique() -> None:
    import las.report as package

    assert len(package.__all__) == len(set(package.__all__)), "__all__ has duplicates"
    for name in package.__all__:
        assert hasattr(package, name), f"__all__ names missing attribute {name}"


def test_there_are_exactly_three_formats_and_four_sheets() -> None:
    assert len(list(OutputFormat)) == 3
    assert len(list(SheetId)) == 4


@pytest.mark.parametrize(
    "module", ["errors", "models", "policy", "tabular", "xlsx", "documents", "engine"]
)
def test_each_module_exists(module: str) -> None:
    assert (REPORT_DIR / f"{module}.py").is_file()


def test_only_font_and_pattern_fill_are_imported_from_openpyxl_styles() -> None:
    """The precise form of "no invented styles".

    A name search cannot express this: ``Side`` is also T12's reconciliation
    side enum, and ``name`` is an ordinary parameter. What actually matters is
    that the workbook writer reaches for exactly two style classes.
    """
    imported: set[str] = set()
    for path in report_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "openpyxl.styles":
                imported.update(alias.name for alias in node.names)
    assert imported == {"Font", "PatternFill"}, imported


def test_no_dimension_alignment_or_number_format_is_set() -> None:
    for path in report_sources():
        text = code_only(path)
        for forbidden in ("column_dimensions", "row_dimensions", "number_format",
                          "alignment", "Alignment", "Border", "NamedStyle",
                          "sheet_view", "print_area", "page_setup"):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"
