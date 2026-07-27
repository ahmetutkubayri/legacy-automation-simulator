"""Architecture and static-safety tests for the matching package."""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from las.match import MatchExecutionError, match_row, match_rows
from match_helpers import NEAR_TWIN_NAME, Explosive, feed_row, make_index, make_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
MATCH_DIR = REPO_ROOT / "src" / "las" / "match"

#: Layers that depend on matching, and which matching must never import.
FORBIDDEN_PREFIXES = (
    "las.simulator",
    "las.driver",
    "las.pipeline",
    "las.reconcile",
    "las.report",
    "las.cli",
    # The development plan does not place audit events in this task.
    "las.audit",
)

#: The only ``las`` namespaces matching may reach for.
ALLOWED_LAS_PREFIXES = ("las.match", "las.ingest", "las.validate", "las.config")

#: Third-party and reader modules matching must not touch.
FORBIDDEN_THIRD_PARTY = ("pandas", "numpy", "openpyxl", "yaml", "pydantic", "csv", "difflib")


def match_sources() -> list[Path]:
    files = sorted(MATCH_DIR.glob("*.py"))
    assert files, "the match package was not found"
    return files


def code_only(path: Path) -> str:
    """The module's executable code, with docstrings and comments removed.

    Scanning raw text would flag the package's own prose — a docstring saying
    "no randomness" would trip a search for ``random``. Re-emitting the parsed
    tree leaves only code, so these checks mean what they say.
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


def test_match_imports_no_forbidden_layer() -> None:
    for path in match_sources():
        for module in iter_imports(path):
            assert not module.startswith(FORBIDDEN_PREFIXES), (
                f"{path.name} imports forbidden module '{module}'"
            )
            if module == "las" or module.startswith("las."):
                assert module.startswith(ALLOWED_LAS_PREFIXES), (
                    f"{path.name} imports unexpected las module '{module}'"
                )


def test_match_touches_no_reader_or_dataframe_library() -> None:
    for path in match_sources():
        for module in iter_imports(path):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_THIRD_PARTY, f"{path.name} imports '{module}'"


def test_importing_match_loads_no_forbidden_layer() -> None:
    probe = (
        "import sys; import las.match; "
        f"loaded = [m for m in sys.modules if m.startswith({FORBIDDEN_PREFIXES!r})]; "
        "print(','.join(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True, timeout=60, cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == "", f"importing match pulled in: {result.stdout.strip()}"


def test_importing_match_does_not_load_pandas() -> None:
    probe = (
        "import sys; import las.match; "
        "print('pandas' if 'pandas' in sys.modules else '')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True, timeout=60, cwd=REPO_ROOT,
    )
    assert result.stdout.strip() == ""


def test_scoring_is_written_here_rather_than_borrowed() -> None:
    """difflib is deliberately unused; the algorithm is defined in-package."""
    for path in match_sources():
        assert "difflib" not in code_only(path)
        assert "SequenceMatcher" not in code_only(path)


# --- purity -------------------------------------------------------------------


def test_match_performs_no_filesystem_or_network_access() -> None:
    for path in match_sources():
        text = code_only(path)
        for forbidden in (
            "open(", ".read_text(", ".read_bytes(", ".write_text(", ".write_bytes(",
            "Path(", "os.path", "os.environ", "getenv", "shutil", "load_workbook",
            "csv.reader", "urllib", "socket", "requests", "subprocess",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_match_has_no_current_time_dependency() -> None:
    for path in match_sources():
        text = code_only(path)
        for forbidden in (
            "import " + "time", "datetime.now", "date.today", "utcnow",
            "monotonic", "perf_counter",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_match_introduces_no_randomness() -> None:
    for path in match_sources():
        text = code_only(path)
        assert "random" not in text, f"{path.name} uses randomness"
        assert "uuid" not in text, f"{path.name} uses uuid"


def test_match_uses_no_dynamic_execution_or_plugin_discovery() -> None:
    for path in match_sources():
        text = code_only(path)
        for forbidden in (
            "eval(", "exec(", "__import__", "importlib", "entry_points",
            "pkgutil", "globals()", "locals()",
        ):
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


def test_match_never_serializes_arbitrary_objects() -> None:
    for path in match_sources():
        text = code_only(path)
        assert ("repr" + "(") not in text, f"{path.name} calls repr()"
        assert "!r" not in text, f"{path.name} formats with !r"
        assert "pickle" not in text, f"{path.name} mentions pickle"


def test_match_stringifies_only_a_configured_threshold() -> None:
    """``str()`` appears once, converting a configured float bound exactly."""
    occurrences = {path.name: code_only(path).count("str(") for path in match_sources()}
    assert occurrences.pop("policy.py") == 1
    assert all(count == 0 for count in occurrences.values()), occurrences
    assert "Decimal(str(fraction))" in code_only(MATCH_DIR / "policy.py")


def test_match_has_no_unbounded_loops() -> None:
    for path in match_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.While), f"{path.name} has a while loop"


def test_no_function_in_the_package_recurses() -> None:
    for path in match_sources():
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
    for path in match_sources():
        text = code_only(path)
        for forbidden in needles:
            assert forbidden not in text, f"{path.name} contains '{forbidden}'"


# --- deep immutability ---------------------------------------------------------


def test_every_public_model_is_a_frozen_dataclass() -> None:
    from las.match import (
        BatchMatchResult,
        Candidate,
        MasterAccount,
        MasterIndex,
        MatchDecision,
        MatchPolicy,
        MatchSummary,
        Score,
    )

    for cls in (
        Score, Candidate, MatchDecision, MatchSummary, BatchMatchResult,
        MatchPolicy, MasterAccount, MasterIndex,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen"


def test_no_reachable_public_collection_is_mutable() -> None:
    index = make_index()
    result = match_rows(
        [feed_row(2), feed_row(3, external_ref=None, source_name=NEAR_TWIN_NAME)],
        index,
        make_policy(),
    )

    def assert_immutable(value: object, where: str) -> None:
        assert not isinstance(value, list | set | dict | bytearray), f"{where} is mutable"

    for name in vars(result):
        assert_immutable(getattr(result, name), f"result.{name}")
    for name in vars(result.summary):
        assert_immutable(getattr(result.summary, name), f"summary.{name}")
    for decision in result.decisions:
        for name in vars(decision):
            assert_immutable(getattr(decision, name), f"decision.{name}")
        for candidate in decision.candidates:
            for name in vars(candidate):
                assert_immutable(getattr(candidate, name), f"candidate.{name}")
    for name in vars(index):
        assert_immutable(getattr(index, name), f"index.{name}")


def test_a_result_never_retains_a_path_object() -> None:
    result = match_rows([feed_row(2)], make_index(), make_policy())
    for name in vars(result):
        assert not isinstance(getattr(result, name), Path)
    assert not isinstance(result.decisions[0].provenance.source, Path)


# --- safe messages -------------------------------------------------------------


def test_a_broken_scoring_function_becomes_a_typed_error_without_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explodes(left: str, right: str) -> int:
        raise ZeroDivisionError("a secret detail that must not travel")

    monkeypatch.setattr("las.match.engine.similarity_bp", explodes)
    with pytest.raises(MatchExecutionError) as excinfo:
        match_row(
            feed_row(external_ref=None, source_name="Nothing Like Any Account"),
            0,
            make_index(),
            make_policy(),
        )
    message = str(excinfo.value)
    assert "ZeroDivisionError" in message
    assert "a secret detail" not in message


def test_a_scoring_failure_never_serializes_the_offending_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explodes(left: str, right: str) -> int:
        raise RuntimeError(Explosive())  # noqa: TRY004 - deliberate hostile payload

    monkeypatch.setattr("las.match.engine.similarity_bp", explodes)
    with pytest.raises(MatchExecutionError, match="RuntimeError"):
        match_row(
            feed_row(external_ref=None, source_name="Nothing Like Any Account"),
            0,
            make_index(),
            make_policy(),
        )


def test_no_decision_or_error_ever_echoes_a_source_or_account_name() -> None:
    """Names are used for scoring and then stay behind."""
    sentinel = "Zzsentinel Account Name"
    index = make_index(accounts=(("ACC-100000", sentinel),), links=())
    result = match_rows(
        [feed_row(2, external_ref=None, source_name="Zzsentinel Account Nam")],
        index,
        make_policy(),
    )
    rendered = repr_free(result)
    assert sentinel.lower() not in rendered.lower()


def repr_free(result) -> str:
    """Render only the safe scalar fields of a result, without repr()."""
    parts: list[str] = [result.source, result.worksheet or ""]
    for decision in result.decisions:
        parts += [
            decision.outcome.value,
            decision.method.value,
            decision.reason,
            decision.message,
            decision.account_ref or "",
            decision.external_ref or "",
        ]
        parts += [candidate.account_ref for candidate in decision.candidates]
    return " ".join(parts)


def test_the_declared_public_surface_is_importable_and_unique() -> None:
    import las.match as package

    assert len(package.__all__) == len(set(package.__all__)), "__all__ has duplicates"
    for name in package.__all__:
        assert hasattr(package, name), f"__all__ names missing attribute {name}"
