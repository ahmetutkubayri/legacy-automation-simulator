"""Repository-level policy: committed fixtures must check out byte-identically.

The committed synthetic fixtures are byte-reproducible from their seed, and
``docs/DEVELOPMENT_PLAN.md`` pins one SHA-256 per file for *every* platform.
That only holds if Git hands the working tree the same bytes it stored.

``.gitattributes`` opens with ``* text=auto``, which normalizes text to LF in
the repository but converts it to the platform's native ending on checkout —
CRLF on Windows. The CSV fixtures are text, so their bytes drifted there while
``feed_alpha.xlsx`` survived untouched, because Git detects it as binary and
never converts it. These tests pin the narrower ``eol=lf`` rule that fixes it,
so the same regression cannot return silently.

Everything here only *reads*: it queries Git and compares bytes, and never
rewrites a fixture. It is cross-platform — no shell, no platform branches, and
NUL-separated Git output wherever parsing is involved.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_PREFIX = "data/samples"
XLSX_FIXTURE = f"{SAMPLES_PREFIX}/feed_alpha.xlsx"

#: The CSV fixtures whose hashes the plan documents, as a floor. The tests
#: enforce the policy over whatever CSVs are actually committed, so a future
#: fixture is covered automatically rather than needing to be remembered.
EXPECTED_CSV_FIXTURES = frozenset(
    {
        f"{SAMPLES_PREFIX}/accounts.csv",
        f"{SAMPLES_PREFIX}/account_links.csv",
        f"{SAMPLES_PREFIX}/feed_beta.csv",
    }
)

#: The T13 golden files, compared byte for byte against renderer output whose
#: contract fixes it at LF. They need the same narrow rule the CSVs got.
REPORT_GOLDEN_PREFIX = "tests/golden/report"
EXPECTED_REPORT_GOLDENS = frozenset(
    {
        f"{REPORT_GOLDEN_PREFIX}/summary.json",
        f"{REPORT_GOLDEN_PREFIX}/summary.html",
        f"{REPORT_GOLDEN_PREFIX}/workbook_semantics.json",
    }
)
REPORT_GOLDEN_RULES = (
    f"{REPORT_GOLDEN_PREFIX}/*.json text eol=lf",
    f"{REPORT_GOLDEN_PREFIX}/*.html text eol=lf",
)


def git(*arguments: str) -> bytes:
    """Run a Git command in the repository and return raw stdout."""
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return completed.stdout


@pytest.fixture(scope="module", autouse=True)
def require_git_repository() -> None:
    """Skip cleanly when there is no Git available, e.g. a source tarball."""
    try:
        git("rev-parse", "--git-dir")
    except (OSError, subprocess.SubprocessError):
        pytest.skip("these policy tests need a Git checkout")


@pytest.fixture(scope="module")
def committed_csv_fixtures() -> tuple[str, ...]:
    listing = git("ls-files", "-z", "--", f"{SAMPLES_PREFIX}/*.csv").decode("utf-8")
    paths = tuple(sorted(entry for entry in listing.split("\0") if entry))
    assert paths, "no committed CSV fixtures were found"
    return paths


@pytest.fixture(scope="module")
def committed_report_goldens() -> tuple[str, ...]:
    listing = git("ls-files", "-z", "--", f"{REPORT_GOLDEN_PREFIX}/*").decode("utf-8")
    paths = tuple(sorted(entry for entry in listing.split("\0") if entry))
    assert paths, "no committed report golden files were found"
    return paths


def gitattributes_rules() -> list[str]:
    """The declared rules, in file order, without comments or blank lines."""
    return [
        line.strip()
        for line in (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def attributes(path: str, *names: str) -> dict[str, str]:
    """Effective Git attributes for one path, parsed from NUL-separated output."""
    fields = git("check-attr", "-z", *names, "--", path).decode("utf-8").split("\0")
    parsed: dict[str, str] = {}
    # Output is a flat run of (path, attribute, value) triples.
    for index in range(0, len(fields) - 2, 3):
        parsed[fields[index + 1]] = fields[index + 2]
    return parsed


def eol_state(path: str) -> tuple[str, str]:
    """The ``i/`` (index) and ``w/`` (working tree) line-ending markers."""
    line = git("ls-files", "--eol", "--", path).decode("utf-8")
    match = re.match(r"i/(\S+)\s+w/(\S+)\s+attr/", line)
    assert match is not None, f"could not parse ls-files --eol output for {path}"
    return match.group(1), match.group(2)


def committed_blob(path: str) -> bytes:
    """The bytes Git actually stored, independent of any checkout."""
    return git("cat-file", "blob", f"HEAD:{path}")


# --- the committed set ---------------------------------------------------------


def test_the_expected_csv_fixtures_are_committed(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    assert set(committed_csv_fixtures) >= EXPECTED_CSV_FIXTURES


# --- Git attributes -----------------------------------------------------------


def test_every_committed_csv_fixture_is_pinned_to_lf(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    """The narrow rule must beat the repository-wide ``* text=auto``."""
    for path in committed_csv_fixtures:
        applied = attributes(path, "text", "eol")
        assert applied["eol"] == "lf", f"{path} does not pin eol=lf"
        assert applied["text"] == "set", f"{path} has text={applied['text']}, not set"


def test_csv_fixtures_check_out_with_lf_on_this_platform(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    """Index and working tree agree, whichever platform is running."""
    for path in committed_csv_fixtures:
        index_eol, worktree_eol = eol_state(path)
        assert index_eol == "lf", f"{path} is stored as {index_eol}"
        assert worktree_eol == "lf", f"{path} checked out as {worktree_eol}"


def test_the_narrow_rule_is_declared_after_the_broad_one() -> None:
    """Git resolves attributes with the last matching pattern, so order matters."""
    lines = gitattributes_rules()
    assert "* text=auto" in lines
    narrow = f"{SAMPLES_PREFIX}/*.csv text eol=lf"
    assert narrow in lines, f"missing rule: {narrow}"
    assert lines.index(narrow) > lines.index("* text=auto")
    # Nothing after it may re-broaden the fixtures back to native endings.
    for line in lines[lines.index(narrow) + 1 :]:
        assert not line.startswith("*"), f"a later rule may override the fix: {line}"


# --- committed bytes ----------------------------------------------------------


def test_committed_csv_blobs_use_lf_line_endings(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    for path in committed_csv_fixtures:
        blob = committed_blob(path)
        assert blob, f"{path} is empty"
        assert blob.count(b"\n") > 1, f"{path} has no line breaks to check"


def test_committed_csv_blobs_contain_no_crlf(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    for path in committed_csv_fixtures:
        assert b"\r\n" not in committed_blob(path), f"{path} contains CRLF"


def test_committed_csv_blobs_contain_no_bare_carriage_returns(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    for path in committed_csv_fixtures:
        assert b"\r" not in committed_blob(path), f"{path} contains a carriage return"


def test_working_tree_is_byte_identical_to_the_committed_blob(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    """The property that failed on Windows, asserted directly.

    A CRLF checkout makes the working tree differ from the stored blob, which
    is precisely what broke the documented hashes.
    """
    for path in (*committed_csv_fixtures, XLSX_FIXTURE):
        blob = committed_blob(path)
        on_disk = (REPO_ROOT / path).read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == hashlib.sha256(blob).hexdigest(), (
            f"{path} on disk differs from the committed blob"
        )


# --- the XLSX fixture stays binary --------------------------------------------


def test_xlsx_fixture_is_treated_as_binary_and_never_converted() -> None:
    index_eol, worktree_eol = eol_state(XLSX_FIXTURE)
    assert index_eol == "-text", f"the workbook is stored as {index_eol}"
    assert worktree_eol == "-text", f"the workbook checked out as {worktree_eol}"


def test_xlsx_fixture_needs_no_eol_rule_of_its_own() -> None:
    """Binary detection already exempts it; an eol rule would be misleading."""
    applied = attributes(XLSX_FIXTURE, "text", "eol")
    assert applied["eol"] == "unspecified"
    assert applied["text"] == "auto"
    contents = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    for line in contents.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert not stripped.startswith(f"{SAMPLES_PREFIX}/*.xlsx"), (
            "the workbook must not be given an eol rule"
        )


# --- the T13 report goldens -----------------------------------------------------
#
# The same defect, in a second place. The golden files are compared byte for
# byte against renderer output, and the reporting contract fixes that output at
# LF everywhere; a CRLF checkout on Windows made the comparison fail while the
# report content was identical. These tests pin the narrow rules that fix it.


def test_the_expected_report_goldens_are_committed(
    committed_report_goldens: tuple[str, ...],
) -> None:
    assert set(committed_report_goldens) == EXPECTED_REPORT_GOLDENS


def test_gitattributes_declares_both_report_golden_rules() -> None:
    lines = gitattributes_rules()
    for rule in REPORT_GOLDEN_RULES:
        assert rule in lines, f"missing rule: {rule}"


def test_the_report_golden_rules_come_after_the_broad_rule() -> None:
    """Git resolves attributes with the last matching pattern, so order matters."""
    lines = gitattributes_rules()
    broad = lines.index("* text=auto")
    for rule in REPORT_GOLDEN_RULES:
        assert lines.index(rule) > broad, f"{rule} would be overridden by * text=auto"


def test_nothing_after_the_report_rules_re_broadens_them() -> None:
    lines = gitattributes_rules()
    last = max(lines.index(rule) for rule in REPORT_GOLDEN_RULES)
    for line in lines[last + 1 :]:
        assert not line.startswith("*"), f"a later rule may override the fix: {line}"


def test_every_report_golden_resolves_to_text_set_and_eol_lf(
    committed_report_goldens: tuple[str, ...],
) -> None:
    for path in committed_report_goldens:
        applied = attributes(path, "text", "eol")
        assert applied["text"] == "set", f"{path} has text={applied['text']}, not set"
        assert applied["eol"] == "lf", f"{path} has eol={applied['eol']}, not lf"


def test_no_rule_applies_crlf_to_the_report_golden_directory(
    committed_report_goldens: tuple[str, ...],
) -> None:
    for path in committed_report_goldens:
        assert attributes(path, "eol")["eol"] != "crlf"
    for line in gitattributes_rules():
        if line.startswith(REPORT_GOLDEN_PREFIX) or line.startswith("*"):
            assert "eol=crlf" not in line, f"a rule forces CRLF: {line}"


def test_report_goldens_are_stored_and_checked_out_as_lf(
    committed_report_goldens: tuple[str, ...],
) -> None:
    """Index and working tree agree, whichever platform is running."""
    for path in committed_report_goldens:
        index_eol, worktree_eol = eol_state(path)
        assert index_eol == "lf", f"{path} is stored as {index_eol}"
        assert worktree_eol == "lf", f"{path} checked out as {worktree_eol}"


def test_report_golden_blobs_contain_lf_and_no_carriage_return(
    committed_report_goldens: tuple[str, ...],
) -> None:
    """What Git stored, read straight from the index, on any host."""
    for path in committed_report_goldens:
        blob = committed_blob(path)
        assert blob, f"{path} is empty"
        assert b"\n" in blob, f"{path} has no line breaks to check"
        assert b"\r\n" not in blob, f"{path} contains CRLF"
        assert b"\r" not in blob, f"{path} contains a carriage return"


def test_report_goldens_on_disk_match_their_committed_blobs(
    committed_report_goldens: tuple[str, ...],
) -> None:
    for path in committed_report_goldens:
        blob = committed_blob(path)
        on_disk = (REPO_ROOT / path).read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == hashlib.sha256(blob).hexdigest(), (
            f"{path} on disk differs from the committed blob"
        )


def test_the_csv_fixture_rule_is_unchanged_by_the_report_rules() -> None:
    """The earlier fix must survive: both narrow rules coexist."""
    lines = gitattributes_rules()
    csv_rule = f"{SAMPLES_PREFIX}/*.csv text eol=lf"
    assert csv_rule in lines
    assert lines.index(csv_rule) > lines.index("* text=auto")
    assert all(lines.index(rule) > lines.index("* text=auto") for rule in REPORT_GOLDEN_RULES)


# --- the tests themselves change nothing --------------------------------------


def test_these_checks_do_not_rewrite_any_fixture(
    committed_csv_fixtures: tuple[str, ...],
) -> None:
    paths = (*committed_csv_fixtures, XLSX_FIXTURE)
    before = {p: hashlib.sha256((REPO_ROOT / p).read_bytes()).hexdigest() for p in paths}

    for path in paths:
        attributes(path, "text", "eol")
        eol_state(path)
        committed_blob(path)

    after = {p: hashlib.sha256((REPO_ROOT / p).read_bytes()).hexdigest() for p in paths}
    assert after == before
