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

The tracked ignore policy is held to the same standard, because it is part of
what a reader clones: it must cover every directory the documented commands
write into, and it must not carry rules for one contributor's local editor
files.

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


def is_ignored(path: str) -> bool:
    """Whether Git's ignore rules cover ``path``.

    Asked of Git rather than parsed out of the file, so precedence, negation
    and directory rules are decided by the same engine a contributor's clone
    uses. The path is never created: ``check-ignore`` answers for a pathname
    whether or not anything exists there.
    """
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode in (0, 1), completed.stderr.decode("utf-8", "replace")
    return completed.returncode == 0


def ignore_rules() -> list[str]:
    """The declared ignore patterns, without comments or blank lines."""
    return [
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


#: An address-shaped token. Deliberately loose: repository policy cares about
#: anything a reader could mistake for a real contact, not about RFC syntax.
_EMAIL_TOKEN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: The reserved namespace tracked content may use.
EXAMPLE_NAMESPACE = "example.com"

#: Domains the scan must refuse. Written split, so this module's own committed
#: blob carries no address-shaped token outside the reserved namespace and
#: cannot fail the very scan it defines.
REFUSED_DOMAINS = (
    "example" + ".net",
    "example" + ".org",
    "host" + ".invalid",
    "other-domain" + ".com",
    "example.com" + ".elsewhere.test",
    "not" + "example.com",
)


def in_example_namespace(domain: str) -> bool:
    """Whether ``domain`` is the reserved domain itself or a subdomain of it."""
    lowered = domain.lower().rstrip(".")
    return lowered == EXAMPLE_NAMESPACE or lowered.endswith(f".{EXAMPLE_NAMESPACE}")


def tracked_text_blobs() -> list[tuple[str, str]]:
    """Every tracked path with its current tracked content, decoded as text.

    Git decides the inventory — ``ls-files``, NUL-separated — so an ignored or
    untracked scratch file can neither satisfy nor break the policy, and
    nothing outside the repository is read. The bytes come from the tracked
    tree as it stands rather than from ``HEAD``, because this is a gate run
    *before* a commit: reading the committed blob would make the pass that
    repairs a violation the one pass that cannot prove the repair.

    Binary content is skipped deterministically — an embedded NUL or a decode
    failure — so the XLSX fixture is never interpreted as text.
    """
    listing = git("ls-files", "-z").decode("utf-8")
    blobs: list[tuple[str, str]] = []
    for path in (entry for entry in listing.split("\0") if entry):
        raw = (REPO_ROOT / path).read_bytes()
        if b"\0" in raw:
            continue
        try:
            blobs.append((path, raw.decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return blobs


#: Wildcards wide enough to hide documentation a reader came for.
BROAD_MARKDOWN_PATTERNS = frozenset({"*.md", "**/*.md", "*.local.md", "**/*.local.md"})

_GLOB_METACHARACTERS = "*?["


def names_one_local_markdown_file(rule: str) -> bool:
    """Whether ``rule`` is an active rule for a single root-level Markdown file.

    That shape — one literal filename, no directory, no glob — is a personal
    preference and belongs in a local exclude rather than the tracked policy. A
    path-specific rule for a genuinely generated document is a different thing
    and stays allowed, which is why the directory and glob tests come first.
    """
    if rule.startswith("!"):
        return False  # A negation re-includes; it hides nothing.
    if "/" in rule.rstrip("/"):
        return False  # Path-specific, so not an individual local working file.
    if any(character in rule for character in _GLOB_METACHARACTERS):
        return False  # A pattern, judged by BROAD_MARKDOWN_PATTERNS instead.
    return rule.lower().endswith(".md")


# --- the tracked email namespace ------------------------------------------------


def test_every_email_in_tracked_text_uses_the_example_com_namespace() -> None:
    """No tracked address may point outside the reserved documentation domain.

    Two rules meet here, and the gap between them is deliberate. The *runtime*
    contract in ``las.config`` accepts a report recipient on the exact reserved
    domain and refuses everything else, subdomains included. This *repository*
    rule is one step wider: tracked content may also use a reserved subdomain,
    because a negative test has to name an address the runtime rule rejects,
    and it must do that without borrowing a third-party domain to make the
    point. Anything outside the reserved namespace is refused either way.

    This covers the tracked tree only. A full-history scan is a separate
    release gate and this test does not stand in for it.
    """
    offenders: list[str] = []
    seen = 0
    for path, text in tracked_text_blobs():
        for token in _EMAIL_TOKEN.findall(text):
            seen += 1
            if not in_example_namespace(token.rpartition("@")[2]):
                offenders.append(f"{path}: {token}")
    assert seen, "the scan matched no address at all, so it proves nothing"
    assert not offenders, "addresses outside the reserved namespace: " + ", ".join(offenders)

    # The predicate itself, so a future loosening cannot slip through silently.
    for allowed in ("ops-team@example.com", "someone@sub.example.com"):
        assert in_example_namespace(allowed.rpartition("@")[2]), allowed
    for refused in REFUSED_DOMAINS:
        assert not in_example_namespace(refused), refused


# --- the tracked ignore policy --------------------------------------------------


def test_the_ignore_policy_covers_generated_output_and_carries_no_personal_rule() -> None:
    """The shipped ignore rules, judged by what they do rather than how they read.

    Three claims, one fact each. The documented spot-check directory really is
    ignored, so following this repository's own instructions cannot leave an
    untracked directory behind. The long-standing output, cache and environment
    guarantees still hold. And the tracked file carries no rule for one
    contributor's local working file: a rule naming a single Markdown file is a
    personal preference, it belongs in a local exclude, and a wildcard broad
    enough to swallow Markdown would hide real documentation from a reader.
    """
    # The path is asked about, never created; `output/` and `logs/` are the
    # neighbours it was documented alongside.
    assert is_ignored("temporary-output/check")
    assert is_ignored("temporary-output/anything/deeper.csv")

    for path in ("output/report.xlsx", "logs/run.jsonl", "__pycache__/module.pyc",
                 "src/las/module.pyc", ".pytest_cache/v/cache", ".ruff_cache/content",
                 ".coverage", "htmlcov/index.html", "venv/bin/python",
                 ".venv/bin/python", "build/lib/las", "dist/package.whl",
                 "name.egg-info/PKG-INFO"):
        assert is_ignored(path), f"{path} is no longer ignored"

    rules = ignore_rules()
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "Local-only working files" not in text, "an ad-hoc local-file section returned"
    for rule in rules:
        assert not names_one_local_markdown_file(rule), (
            f"{rule} ignores one Markdown file; that belongs in a local exclude"
        )
        assert rule.lstrip("!") not in BROAD_MARKDOWN_PATTERNS, (
            f"{rule} would hide documentation a reader came for"
        )

    # The predicate refuses only that one shape. A path-specific rule for a
    # generated document remains available to a future task.
    assert names_one_local_markdown_file("SCRATCHPAD.md")
    for allowed in ("docs/generated/summary.md", "build/notes.md", "!KEEP.md",
                    "reports/*.md"):
        assert not names_one_local_markdown_file(allowed), f"{allowed} must stay legal"

    # Nothing the reader needs is hidden by the rules above.
    for path in ("README.md", "docs/DEVELOPMENT_PLAN.md", "docs/ARCHITECTURE.md",
                 "pyproject.toml", "src/las/__init__.py"):
        assert not is_ignored(path), f"{path} is ignored but must be readable"


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
