"""The README, the architecture document and the plan must agree with the code.

``docs/PIPELINE_CONTRACT.md`` and ``docs/REPORTING_CONTRACT.md`` already have
conformance suites. This one covers the presentation layer — the documents a
reviewer reads first — and holds them to the same standard: every command,
flag, exit code, filename and sheet name is checked against the parser or a
public constant rather than against another piece of prose.

The demo transcript is not merely compared. It is produced: the suite runs the
installed command into a temporary directory, twice, and asserts the documented
output and byte-identical artifacts. A transcript that drifted from behaviour
would fail here rather than mislead a reader.

Numbers that age badly — test totals, coverage percentages, timings — are
forbidden outright, because a figure in prose is a promise nobody will keep.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

import las
from las.cli import (
    EXIT_CONFIGURATION,
    EXIT_EXECUTION,
    EXIT_INTERNAL,
    EXIT_OUTPUT,
    EXIT_SUCCESS,
    EXIT_USAGE,
    build_parser,
)
from las.pipeline import FILENAME_BY_FORMAT, MATCHING_OUTCOME_FIELDS, STAGE_ORDER
from las.report import SHEET_ORDER
from pipeline_helpers import installed_launcher

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
ARCHITECTURE_PATH = REPO_ROOT / "docs" / "ARCHITECTURE.md"
PLAN_PATH = REPO_ROOT / "docs" / "DEVELOPMENT_PLAN.md"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

README = README_PATH.read_text(encoding="utf-8")
ARCHITECTURE = ARCHITECTURE_PATH.read_text(encoding="utf-8")
PLAN = PLAN_PATH.read_text(encoding="utf-8")
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")

#: Whitespace-normalised, so a sentence that wrapped still matches.
README_FLAT = " ".join(README.split())
ARCHITECTURE_FLAT = " ".join(ARCHITECTURE.split())

#: The README's required second-level headings, in order.
README_HEADINGS = (
    "The problem this simulates",
    "What it guarantees",
    "Quick start",
    "The deterministic demo",
    "Generated artifacts",
    "Architecture at a glance",
    "Command reference",
    "Testing and reliability",
    "Design decisions",
    "Limitations and non-goals",
    "Repository layout",
    "Development",
)

#: Permitted beyond the required set. A licence pointer is conventional and the
#: repository really does carry the file it names.
README_OPTIONAL_HEADINGS = ("License",)

ARCHITECTURE_HEADINGS = (
    "Scope and authoritative contracts",
    "Layer map",
    "The dependency rule",
    "How boundaries are enforced",
    "The simulated legacy system",
    "The resilient driver",
    "Data flow through one run",
    "Determinism as a design constraint",
    "Failure design",
    "Trade-offs and rejected alternatives",
)

#: The exact demo transcript, rebuilt from public constants so a renamed
#: matching outcome cannot leave the README quietly wrong.
TRANSCRIPT_LINES = (
    "status: success",
    "feed_rows: 211",
    "system_rows: 270",
    "validation_invalid: 12",
    *(
        f"matching_{name}: {count}"
        for name, count in zip(
            MATCHING_OUTCOME_FIELDS, (204, 0, 2, 5, 0), strict=True
        )
    ),
    "reconciliation_groups: 240",
    "reconciliation_unresolved: 14",
    "artifacts: 3",
    "write_back: disabled",
)
TRANSCRIPT = "\n".join(TRANSCRIPT_LINES) + "\n"

#: The prose this suite polices. Scanned for every forbidden literal.
PROSE = {
    "README.md": README,
    "docs/ARCHITECTURE.md": ARCHITECTURE,
    "docs/DEVELOPMENT_PLAN.md": PLAN,
}

#: The prose plus this module. A scanner must contain the needles it searches
#: for — ``.png`` and ``PYTHONPATH`` are its working vocabulary — so it is held
#: only to the rules that no test file has any reason to break. The repository's
#: other boundary suites read their subject rather than themselves for the same
#: reason.
EVERY_CHANGED_FILE = {
    **PROSE,
    "tests/test_readme_docs.py": Path(__file__).read_text(encoding="utf-8"),
}

#: Backwards-compatible alias for the checks that only look at prose.
DOCUMENTS = PROSE


def headings(text: str, level: int = 2) -> list[str]:
    return re.findall(rf"^{'#' * level} (.+)$", text, re.M)


#: Needles assembled from fragments, so this module does not itself contain the
#: literals a pre-publication scan looks for. The repository's boundary suites
#: use the same idiom.
SCHEMES = ("http" + "://", "http" + "s://")
IMAGE_MARKER = "!" + "["
SCREENSHOT = "scr" + "eenshot"


def links(text: str) -> list[str]:
    """Every markdown link target that is not an anchor or an external URL."""
    return [
        target
        for _, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text)
        if not target.startswith("#")
    ]


def fenced_blocks(text: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", text, re.S)


# --- README structure -----------------------------------------------------------


def test_the_readme_has_one_title_naming_the_project() -> None:
    titles = headings(README, level=1)
    assert titles == ["Legacy Automation Simulator"]


def test_the_readme_headings_are_the_required_ones_in_order() -> None:
    present = headings(README)
    required = [h for h in present if h in README_HEADINGS]
    assert required == list(README_HEADINGS)
    unexpected = [
        h for h in present if h not in README_HEADINGS + README_OPTIONAL_HEADINGS
    ]
    assert unexpected == [], f"unexpected README headings: {unexpected}"


def test_the_readme_states_the_data_is_synthetic() -> None:
    assert "synthetic" in README_FLAT
    sentence = next(s for s in README_FLAT.split(".") if "synthetic" in s)
    assert "Every record" in sentence or "every record" in sentence.lower()


def test_the_readme_states_no_external_service_or_private_data_is_needed() -> None:
    assert "No external service or private dataset is required" in README_FLAT
    for phrase in ("no network call", "no credential"):
        assert phrase in README_FLAT.lower(), phrase


@pytest.mark.parametrize(
    "target",
    [
        "docs/ARCHITECTURE.md",
        "docs/PIPELINE_CONTRACT.md",
        "docs/REPORTING_CONTRACT.md",
        "docs/DEVELOPMENT_PLAN.md",
        ".github/workflows/ci.yml",
        "LICENSE",
    ],
)
def test_the_readme_links_to(target: str) -> None:
    assert target in links(README), f"README does not link to {target}"


@pytest.mark.parametrize(
    "path",
    [
        "src/las/",
        "simulator/",
        "ingest/",
        "validate/",
        "match/",
        "reconcile/",
        "report/",
        "pipeline/",
        "data/samples/",
        "docs/",
        "tests/",
        ".github/workflows/ci.yml",
    ],
)
def test_the_repository_layout_names(path: str) -> None:
    layout = README.split("## Repository layout", 1)[1].split("## ", 1)[0]
    assert path in layout, f"the layout section omits {path}"


def test_every_linked_document_exists() -> None:
    for document, text in (("README.md", README), ("docs/ARCHITECTURE.md", ARCHITECTURE)):
        base = (REPO_ROOT / document).parent
        for target in links(text):
            assert (base / target).exists(), f"{document} links to missing {target}"


def test_no_documentation_link_is_external_or_absolute() -> None:
    for document, text in DOCUMENTS.items():
        for target in links(text):
            assert not target.startswith((*SCHEMES, "/")), (
                f"{document} links to {target}"
            )


# --- CLI agreement --------------------------------------------------------------


def parser_commands() -> set[str]:
    actions = [a for a in build_parser()._actions if getattr(a, "choices", None)]
    return {name for action in actions for name in action.choices}


def subcommand_flags(command: str) -> set[str]:
    actions = [a for a in build_parser()._actions if getattr(a, "choices", None)]
    parser = next(a.choices[command] for a in actions if command in a.choices)
    return {option for action in parser._actions for option in action.option_strings}


def documented_commands() -> dict[str, str]:
    block = next(b for b in fenced_blocks(README, "text") if b.startswith("las "))
    return {line.split()[1]: line for line in block.strip().splitlines()}


def test_the_readme_documents_exactly_the_parser_subcommands() -> None:
    assert set(documented_commands()) == parser_commands()


@pytest.mark.parametrize("command", sorted(parser_commands()))
def test_each_documented_command_names_its_real_flags(command: str) -> None:
    documented = set(re.findall(r"--[a-z][a-z-]*", documented_commands()[command]))
    actual = {
        flag
        for flag in subcommand_flags(command)
        if flag.startswith("--") and flag != "--help"
    }
    assert documented == actual, f"{command}: documented {documented}, real {actual}"


def test_the_readme_advertises_no_unsupported_flag() -> None:
    every = {o for a in build_parser()._actions for o in a.option_strings}
    for command in parser_commands():
        every |= subcommand_flags(command)
    for flag in set(re.findall(r"--[a-z][a-z-]*", README)):
        # Placeholders inside a development command are not CLI flags.
        if flag in {"--cov", "--cov-report"}:
            continue
        assert flag in every, f"the README advertises unsupported flag {flag}"


@pytest.mark.parametrize(
    ("code", "meaning"),
    [
        (EXIT_SUCCESS, "success"),
        (EXIT_USAGE, "argument parsing"),
        (EXIT_CONFIGURATION, "configuration or configured input"),
        (EXIT_OUTPUT, "output conflict or publication transaction"),
        (EXIT_EXECUTION, "typed pipeline or lower-layer failure"),
        (EXIT_INTERNAL, "unexpected internal failure"),
    ],
)
def test_the_readme_exit_code_table_agrees_with_the_cli(code: int, meaning: str) -> None:
    assert re.search(rf"\|\s*`{code}`\s*\|\s*{re.escape(meaning)}\s*\|", README)


def test_the_readme_documents_exactly_six_exit_codes() -> None:
    section = README.split("Exit codes:", 1)[1].split("## ", 1)[0]
    documented = {int(c) for c in re.findall(r"\|\s*`(\d)`\s*\|", section)}
    assert documented == {
        EXIT_SUCCESS, EXIT_USAGE, EXIT_CONFIGURATION,
        EXIT_OUTPUT, EXIT_EXECUTION, EXIT_INTERNAL,
    }


def test_the_readme_states_the_failure_output_discipline() -> None:
    assert "stdout is empty" in README_FLAT
    assert "`las: <error_code>`" in README_FLAT
    assert "No traceback" in README_FLAT


@pytest.mark.parametrize("filename", sorted(FILENAME_BY_FORMAT.values()))
def test_the_readme_names_each_artifact(filename: str) -> None:
    assert f"`{filename}`" in README


def test_the_readme_names_no_artifact_the_code_does_not_produce() -> None:
    documented = set(re.findall(r"`(reconciliation-[a-z]+\.[a-z]+)`", README))
    assert documented == set(FILENAME_BY_FORMAT.values())


def test_the_readme_names_the_workbook_sheets() -> None:
    section = README.split("## Generated artifacts", 1)[1].split("## ", 1)[0]
    for sheet in SHEET_ORDER:
        assert sheet.value in section, f"the artifacts section omits {sheet.value}"


def test_the_entry_point_is_unchanged() -> None:
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["scripts"] == {"las": "las.cli:main"}


# --- the demo, executed ---------------------------------------------------------


def test_the_readme_contains_the_exact_transcript() -> None:
    blocks = fenced_blocks(README, "text")
    assert TRANSCRIPT in blocks, "the README transcript does not match the constants"


def test_the_documented_transcript_has_thirteen_lines() -> None:
    assert len(TRANSCRIPT_LINES) == 13
    assert TRANSCRIPT_LINES[0] == "status: success"
    assert TRANSCRIPT_LINES[-1] == "write_back: disabled"


def test_the_transcript_matching_keys_are_the_public_ones_in_order() -> None:
    documented = [
        line.split(":")[0].removeprefix("matching_")
        for line in TRANSCRIPT_LINES
        if line.startswith("matching_")
    ]
    assert tuple(documented) == MATCHING_OUTCOME_FIELDS


def run_demo(output: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(installed_launcher()), "demo", "--output-dir", str(output)],
        capture_output=True, text=True, timeout=300,
    )


def test_the_documented_demo_command_produces_the_documented_output(
    tmp_path: Path,
) -> None:
    """The transcript is executed, not merely quoted."""
    result = run_demo(tmp_path / "demo-output")

    assert result.returncode == EXIT_SUCCESS
    assert result.stderr == ""
    assert result.stdout == TRANSCRIPT
    produced = sorted(p.name for p in (tmp_path / "demo-output").iterdir())
    assert produced == sorted(FILENAME_BY_FORMAT.values())
    assert len(produced) == 3


def test_two_demo_runs_agree_byte_for_byte(tmp_path: Path) -> None:
    first = run_demo(tmp_path / "one")
    second = run_demo(tmp_path / "two")

    assert first.stdout == second.stdout == TRANSCRIPT
    for name in FILENAME_BY_FORMAT.values():
        one = (tmp_path / "one" / name).read_bytes()
        two = (tmp_path / "two" / name).read_bytes()
        assert one == two, name
        assert hashlib.sha256(one).hexdigest() == hashlib.sha256(two).hexdigest()


def test_the_readme_promises_the_determinism_the_demo_shows() -> None:
    section = README.split("## The deterministic demo", 1)[1].split("## ", 1)[0]
    flat = " ".join(section.split())
    for phrase in ("byte-identical artifacts", "identical SHA-256 values", "fresh"):
        assert phrase in flat, phrase


def test_the_readme_explains_that_output_directories_must_not_exist() -> None:
    assert "must not already exist" in README_FLAT


# --- architecture agreement -----------------------------------------------------


def test_the_architecture_document_headings_are_the_required_ones_in_order() -> None:
    assert headings(ARCHITECTURE, level=1) == ["Architecture"]
    assert headings(ARCHITECTURE) == list(ARCHITECTURE_HEADINGS)


def test_the_architecture_document_holds_exactly_one_diagram() -> None:
    assert len(fenced_blocks(ARCHITECTURE, "mermaid")) == 1


def diagram() -> str:
    return fenced_blocks(ARCHITECTURE, "mermaid")[0]


def test_the_diagram_is_a_flowchart() -> None:
    assert diagram().strip().startswith("flowchart")


@pytest.mark.parametrize(
    "node",
    ["CLI", "Config", "Pipeline", "Audit", "RecordStore", "LegacySystem",
     "LegacyDriver", "Ingest", "Validate", "Match", "Reconcile", "Report",
     "Writer", "Xlsx", "Json", "Html"],
)
def test_the_diagram_declares_node(node: str) -> None:
    assert re.search(rf"^\s*{node}\[", diagram(), re.M), f"no node {node}"


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("CLI", "Pipeline"), ("Config", "Pipeline"),
        ("RecordStore", "LegacySystem"), ("LegacySystem", "LegacyDriver"),
        ("LegacyDriver", "Pipeline"),
        ("Pipeline", "Ingest"), ("Pipeline", "Validate"), ("Pipeline", "Match"),
        ("Pipeline", "Reconcile"), ("Pipeline", "Report"), ("Pipeline", "Writer"),
        ("Writer", "Xlsx"), ("Writer", "Json"), ("Writer", "Html"),
    ],
)
def test_the_diagram_connects(source: str, target: str) -> None:
    assert re.search(rf"^\s*{source}\s*-->(\|[^|]*\|)?\s*{target}\s*$", diagram(), re.M), (
        f"no solid edge {source} -> {target}"
    )


def test_the_audit_edge_is_dashed_and_labelled_operational() -> None:
    assert re.search(r"^\s*Pipeline\s*-\.\s*\"operational events\"\s*\.->\s*Audit\s*$",
                     diagram(), re.M)


def test_the_simulator_path_is_labelled_read_only() -> None:
    assert re.search(r'LegacyDriver\s*-->\|"read-only extraction"\|\s*Pipeline',
                     diagram())


def test_no_edge_points_from_the_writer_or_a_report_back_at_the_simulator() -> None:
    for source in ("Writer", "Report", "Xlsx", "Json", "Html", "Pipeline"):
        for target in ("RecordStore", "LegacySystem", "LegacyDriver"):
            assert not re.search(
                rf"^\s*{source}\s*-[.>-]*[^\n]*\b{target}\s*$", diagram(), re.M
            ), f"{source} -> {target} would imply mutation"


def test_no_lower_layer_points_back_at_the_pipeline_except_the_driver() -> None:
    for layer in ("Ingest", "Validate", "Match", "Reconcile", "Report", "Writer"):
        assert not re.search(rf"^\s*{layer}\s*-->[^\n]*\bPipeline\s*$", diagram(), re.M)


@pytest.mark.parametrize("stage", [s.value for s in STAGE_ORDER])
def test_the_architecture_document_names_stage(stage: str) -> None:
    section = ARCHITECTURE.split("## Data flow through one run", 1)[1].split("## ", 1)[0]
    assert f"**{stage}**" in section


def test_the_documented_stage_order_matches_the_engine() -> None:
    section = ARCHITECTURE.split("## Data flow through one run", 1)[1].split("## ", 1)[0]
    found = re.findall(r"^\d+\. \*\*([a-z]+)\*\*", section, re.M)
    assert found == [s.value for s in STAGE_ORDER]


def test_the_architecture_document_defers_to_the_contracts() -> None:
    for target in ("PIPELINE_CONTRACT.md", "REPORTING_CONTRACT.md", "DEVELOPMENT_PLAN.md"):
        assert target in links(ARCHITECTURE), f"architecture does not link {target}"
    assert "does not restate behaviour that another document owns" in ARCHITECTURE_FLAT


def test_the_architecture_document_states_audit_is_not_a_stage() -> None:
    assert "audit is not one of the seven stages" in ARCHITECTURE_FLAT


def test_the_architecture_document_states_orchestration_is_read_only() -> None:
    assert "Orchestration is read-only" in ARCHITECTURE_FLAT


# --- development-plan agreement -------------------------------------------------


@pytest.mark.parametrize("task", [f"T{n}" for n in range(1, 16)])
def test_the_completed_ledger_records(task: str) -> None:
    ledger = PLAN.split("## Completed tasks", 1)[1].split("## ", 1)[0]
    assert re.search(rf"^\| {task} \|", ledger, re.M), f"{task} missing from the ledger"


def test_the_plan_retains_the_final_release_audit_record() -> None:
    assert re.search(r"^## T16 — public-release sweep", PLAN, re.M)
    assert not re.search(r"^## (Current|Next) task: T8\b", PLAN, re.M)


def test_the_plan_carries_no_stale_status_claim() -> None:
    for stale in ("280 tests", "Nothing pushed to a remote", "Remaining tasks after T8",
                  "`SystemClock` for demos"):
        assert stale not in PLAN, f"stale claim remains: {stale}"


def test_the_plan_records_t16_evidence_and_preserves_publication_gates() -> None:
    """Completed technical work and publication requirements stay distinct."""
    flat = " ".join(PLAN.split())
    assert "Nothing in T16 has started" not in flat

    # The landed repairs, named.
    assert "path-resolution" in flat
    assert "audit and logging claim alignment" in flat
    assert "ignored-configuration-surface cleanup" in flat
    assert "ignored-configuration-surface pass has not started" not in flat
    assert "source-distribution packaging policy" in flat
    assert "single-version-source cleanup" in flat
    assert "clock and determinism wording alignment" in flat
    assert "release-plan alignment" in flat
    assert "public-state and ignore-policy alignment" in flat
    assert "email-namespace policy repair" in flat
    assert "workflow least-privilege hardening" in flat
    assert "Initial T16 work landed ten repairs" in flat

    # The removed keys are recorded, and the lower-level APIs are not claimed gone.
    for key in ("log_level", "fault_profile", "fault_seed",
                "busy_delay_min_ms", "busy_delay_max_ms"):
        assert key in flat, f"the plan does not record the removed key {key}"
    assert "fault model and the logging facility are untouched" in flat

    # The packaging repair is described by what it decided, not merely named.
    assert "the wheel carries the `las` package and its metadata" in flat
    assert "The runnable suite lives in the Git repository, not in a release archive" in flat
    assert "packaging repair has not started" not in flat

    # The version-source cleanup has landed, and the plan names the one source.
    assert "The version-source cleanup is complete" in flat
    assert "The version-source cleanup has not started" not in flat
    assert "`__version__` in `src/las/__init__.py` is now the authoritative source" in flat
    assert 'declares `dynamic = ["version"]`' in flat
    assert "setuptools derives the package metadata from that attribute" in flat
    assert "The package version is unchanged at `0.1.0`" in flat

    # The clock and determinism wording alignment has landed, and the plan
    # records what was false as well as what is now true.
    assert "The clock and determinism wording alignment is complete" in flat
    assert "The clock and determinism wording alignment has not started" not in flat
    assert "`SystemClock` for demos" not in flat
    assert "Every pipeline path builds `LegacySystem` with a `SimulatedClock`" in flat
    assert "neither sleeps in real time" in flat
    assert "nothing in the CLI or the pipeline selects it" in flat
    assert "the CLI constructs no such trail" in flat
    assert "no executable statement changed" in flat

    # The release plan now names a tag the metadata can support. Checked
    # against the real classifier and the real package version, not against
    # the plan's own prose, so a version bump would fail here.
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert las.__version__ == "0.1.0"
    assert "Development Status :: 3 - Alpha" in manifest["project"]["classifiers"]
    assert "The release-plan alignment is complete" in flat
    assert "The release-plan alignment has not started" not in flat
    assert "Both instructions now read `v0.1.0`" in flat
    assert "the first Git tag must match the package version" in flat
    assert f"The approved first tag is therefore `v{las.__version__}`" in flat
    assert "without creating a tag, release or changelog" in flat
    assert "Tag creation requires the recorded T16 checks" in flat

    # The corrected instruction reaches both places that carried the old one,
    # and no section still asks for the tag the metadata cannot support.
    clause = PLAN.split("* **T16 Public-release sweep**", 1)[1].split("\n\n", 1)[0]
    checklist = PLAN.split("## First-release checklist", 1)[1]
    assert "tag v0.1.0" in " ".join(clause.split())
    assert "Tag `v0.1.0`, matching the package version" in " ".join(checklist.split())
    assert "v1.0.0" not in PLAN, "a v1.0.0 release instruction survives"

    # Public visibility and release publication are distinct. Live publication
    # state belongs to Git refs and GitHub Releases, not a stale prose snapshot.
    t16_flat = " ".join(PLAN.split("## T16 —", 1)[1].split("\n## ", 1)[0].split())
    assert re.search(r"^## T16 — public-release sweep", PLAN, re.M)
    assert "The repository is already publicly visible" in t16_flat
    assert "a visibility state and not a release" in t16_flat
    assert "Git tags and GitHub Releases record publication status" in t16_flat
    assert "does not hard-code their live absence or presence" in t16_flat
    assert "no release tag exists locally or on the remote" not in t16_flat
    assert "the first GitHub Release remains pending" not in t16_flat
    assert "only the first has happened" not in t16_flat
    assert "no GitHub Release exists" not in flat, "an unverified release claim survives"
    assert "fit to publish" not in t16_flat, "the old publish-decision framing survives"
    assert "Pre-publication" not in PLAN, "a stale pre-publication label survives"
    assert "then publish" not in " ".join(clause.split()), "ambiguous publish wording survives"
    assert "The public-state and ignore-policy alignment is complete" in flat
    assert "The public-state and ignore-policy alignment has not started" not in flat
    assert "`temporary-output/`" in flat and "is now ignored alongside" in flat
    assert "no history was rewritten" in flat

    # The email-namespace repair, checked against the production constants it
    # promises it did not touch rather than against the plan's own prose.
    from las.config.models import _RECIPIENT_PATTERN, ALLOWED_EMAIL_DOMAIN

    assert ALLOWED_EMAIL_DOMAIN == "example.com"
    assert _RECIPIENT_PATTERN.fullmatch("someone@example.com")
    assert not _RECIPIENT_PATTERN.fullmatch("someone@sub.example.com")
    assert "The email-namespace policy repair is complete" in flat
    assert "The email-namespace policy repair has not started" not in flat
    assert "other-domain" not in PLAN, "the plan still names the retired domain"
    assert "reserved `example.com` namespace" in flat
    assert "a reserved subdomain such as `sub.example.com` appears" in flat
    assert "no real or third-party domain permitted anywhere" in flat
    assert "configuration accepts exact `@example.com` only" in flat
    assert "scans every tracked text file so the lapse cannot return" in flat
    assert "Production validation was not touched" in flat
    assert "does not stand in for item 1's full-history scan" in flat

    # The workflow hardening, read out of the workflow itself rather than
    # trusted from the plan's own description of it.
    import yaml

    ci = yaml.load(WORKFLOW, Loader=yaml.BaseLoader)
    assert ci["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in ci["jobs"].values())
    assert "The workflow least-privilege hardening is complete" in flat
    assert "The workflow least-privilege hardening has not started" not in flat
    assert "grants exactly `contents: read` at the workflow level" in flat
    assert "no job carries a permission override" in flat
    assert "every scope that is not named stays unavailable to the token" in flat
    assert "`fetch-depth: 0` remains deliberately" in flat
    assert "the commands and the step order are all byte-identical" in flat
    assert "fails if the least-privilege declaration or the full-history checkout drifts" in flat
    assert "says nothing about what third-party action code may do inside the runner" in flat

    # The audit record names completed work without relaxing the final gates.
    assert "The README honesty work is complete" in t16_flat
    assert "The configuration-bound test coverage is complete" in t16_flat
    assert "entire reachable Git history" in t16_flat
    assert "No private denylist was used" in t16_flat
    assert "all six combinations" in t16_flat
    assert "no production configuration constraint changed" in t16_flat

    # The checklist ends in a release, not an ambiguous "publish".
    checklist_flat = " ".join(checklist.split())
    assert "create the first GitHub Release only after every item above passes" in checklist_flat
    assert "the maintainer has explicitly approved it" in checklist_flat

    publication = t16_flat.split("The final publication gates are", 1)[1]
    assert "a fresh independent final verification" in publication
    assert "four-way CI on the release candidate" in publication
    assert "explicit maintainer approval" in publication
    assert f"then the `v{las.__version__}` tag and the first GitHub Release" in publication
    assert "Completing a local check does not create either publication object" in publication
    assert "the repository has not been tagged or released" not in flat
    for overstated in ("T16 is complete", "T16 is finished", "ready to publish",
                       "release readiness has been achieved"):
        assert overstated not in flat, f"the plan overstates progress: {overstated}"

    # The status section carries no hash of its own and no placeholder: the
    # ledger above is the only place a commit hash belongs, and this pass is
    # not committed yet. Bounded to the T16 section so the ledger is untouched.
    status = PLAN.split("## T16 —", 1)[1].split("\n## ", 1)[0]
    assert not re.search(r"\b[0-9a-f]{40}\b", status), "the status section pins a commit"
    for placeholder in ("TBD", "TODO", "FIXME", "XXX", "<commit", "PLACEHOLDER"):
        assert placeholder not in status, f"the status section carries {placeholder}"


@pytest.mark.parametrize(
    ("digest", "name"),
    [
        ("4394c96f805f342a8313502cebf2868d40b9867e6910b8a389c3a32114c52952",
         "accounts.csv"),
        ("7486a6227e4980a73b45873e8aeb4e08147e577c64e76ac08f80ba891e07865a",
         "account_links.csv"),
        ("1539d435cc2d5164d14deff95ebc7e54f984a534e8004f2803130b049a47c0a4",
         "feed_alpha.xlsx"),
        ("eeebfb192c3902e49e528eb933810e82fca1f7ac1e497c9b415cd3f14060643f",
         "feed_beta.csv"),
    ],
)
def test_the_pinned_fixture_digest_is_intact(digest: str, name: str) -> None:
    assert f"{digest}  data/samples/{name}" in PLAN


def test_the_pinned_fixture_digests_keep_their_order() -> None:
    found = re.findall(r"^[0-9a-f]{64}  (data/samples/\S+)$", PLAN, re.M)
    assert found == [
        "data/samples/accounts.csv", "data/samples/account_links.csv",
        "data/samples/feed_alpha.xlsx", "data/samples/feed_beta.csv",
    ]


@pytest.mark.parametrize(
    "marker",
    ["* **T12 Reconciliation", "* **T13 Reporting",
     "* **T14 Pipeline + CLI", "* **T15 Documentation"],
)
def test_the_clause_marker_other_suites_slice_on_is_present(marker: str) -> None:
    assert marker in PLAN


def test_the_clause_markers_keep_their_relative_order() -> None:
    positions = [
        PLAN.index(m)
        for m in ("* **T12 Reconciliation", "* **T13 Reporting",
                  "* **T14 Pipeline + CLI", "* **T15 Documentation")
    ]
    assert positions == sorted(positions)


def test_the_plan_no_longer_requires_screenshots() -> None:
    """The word may appear in a prohibition; it must not appear as a task."""
    flat = " ".join(PLAN.split()).lower()
    for occurrence in re.finditer(SCREENSHOT, flat):
        preceding = flat[max(0, occurrence.start() - 12):occurrence.start()]
        assert preceding.rstrip().endswith("no"), (
            f"the plan still asks for {SCREENSHOT}s: ...{preceding}"
        )


def test_every_commit_hash_in_the_ledger_is_forty_hex_characters() -> None:
    ledger = PLAN.split("## Completed tasks", 1)[1].split("## ", 1)[0]
    for digest in re.findall(r"`([0-9a-f]{7,})`", ledger):
        assert len(digest) == 40, f"{digest} is not a full commit hash"


def ledger_rows() -> dict[str, str]:
    """Task identifier to the contents of its commit cell."""
    ledger = PLAN.split("## Completed tasks", 1)[1].split("## ", 1)[0]
    return {
        task: cell.strip()
        for task, cell in re.findall(r"^\| (T\d+) \|([^|]*)\|", ledger, re.M)
    }


def test_the_ledger_marks_t15_complete() -> None:
    assert "T15" in ledger_rows()


def test_the_t15_row_carries_no_self_referential_placeholder() -> None:
    """A commit cannot contain its own hash: editing the file changes it."""
    cell = ledger_rows()["T15"]
    assert cell == "—", f"the T15 commit cell should be an em dash, not {cell!r}"
    assert not re.fullmatch(r"`?[0-9a-f]{7,40}`?", cell)


@pytest.mark.parametrize(
    "placeholder",
    ["(this change)", "this commit", "TBD", "to be determined",
     "pending commit", "forthcoming", "see below once"],
)
def test_the_plan_contains_no_ledger_placeholder(placeholder: str) -> None:
    assert placeholder.lower() not in PLAN.lower(), placeholder


def test_the_plan_promises_no_post_commit_hash_update() -> None:
    flat = " ".join(PLAN.split()).lower()
    for promise in ("will be replaced with", "will be updated with the",
                    "update this row after", "fill in the hash",
                    "replace with the commit"):
        assert promise not in flat, promise


def test_the_plan_says_git_history_is_authoritative() -> None:
    flat = " ".join(PLAN.split())
    assert "A commit cannot record its own identifier" in flat
    assert "Git history is the authority" in flat


def test_every_plan_hash_is_already_in_history() -> None:
    """No recorded hash may be predicted: each must be reachable from HEAD.

    Referring to an earlier commit is accurate and useful. Referring to the
    commit that will carry this document is impossible, which is why the T15
    row records no hash at all.
    """
    for digest in sorted(set(re.findall(r"`([0-9a-f]{40})`", PLAN))):
        reachable = subprocess.run(
            ["git", "merge-base", "--is-ancestor", digest, "HEAD"],
            capture_output=True, cwd=REPO_ROOT,
        )
        assert reachable.returncode == 0, f"{digest} is not in this history"


def test_every_retained_plan_hash_resolves_to_a_real_commit() -> None:
    """No fabricated identifier survives anywhere in the document."""
    digests = sorted(set(re.findall(r"`([0-9a-f]{40})`", PLAN)))
    assert digests, "the plan records no commit hashes at all"
    for digest in digests:
        resolved = subprocess.run(
            ["git", "cat-file", "-t", digest],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        assert resolved.returncode == 0 and resolved.stdout.strip() == "commit", (
            f"{digest} is not a commit in this repository"
        )


# --- platform-neutral commands --------------------------------------------------


@pytest.mark.parametrize("document", sorted(PROSE))
def test_no_shell_activation_command_appears_in(document: str) -> None:
    """Activation is shell- and platform-specific; the commands must not be."""
    text = PROSE[document]
    assert ("bin" + "/activate") not in text
    assert ("Scripts" + "\\activate") not in text
    # Matched as a command, not as the English word: the plan legitimately
    # calls Git history "a second source of truth".
    assert not re.search(r"(?m)^\s*source\s+\S*activate", text)
    assert not re.search(r"(?m)^\s*\.\s+\S*activate", text)


@pytest.mark.parametrize("document", sorted(PROSE))
def test_no_platform_specific_executable_path_appears_in(document: str) -> None:
    text = PROSE[document]
    for needle in ("venv" + "/bin/", ".venv" + "/bin/", "Scripts" + "\\", "/usr/bin/"):
        assert needle not in text, needle


def test_the_plan_and_readme_document_the_same_development_commands() -> None:
    plan_block = next(
        b for b in fenced_blocks(PLAN, "bash") if "ruff check" in b
    )
    readme_block = next(
        b for b in fenced_blocks(README, "console") if "ruff check" in b
    )
    for command in (
        'python -m pip install -e ".[dev]"',
        "ruff check src tests scripts",
        "pytest --cov=las --cov-report=term-missing",
    ):
        assert command in plan_block, f"the plan omits: {command}"
        assert command in readme_block, f"the README omits: {command}"


def test_the_plan_names_the_supported_interpreters_for_those_commands() -> None:
    assert "Python 3.11 or 3.12 environment" in " ".join(PLAN.split())


# --- CI and compatibility agreement ---------------------------------------------


def test_the_readme_states_both_supported_python_versions() -> None:
    assert "Python 3.11 and 3.12" in README_FLAT


def test_the_readme_states_both_operating_systems() -> None:
    assert "Ubuntu and Windows" in README_FLAT


def test_the_workflow_matrix_is_the_documented_one() -> None:
    versions = re.search(r"python-version:\s*\[([^\]]+)\]", WORKFLOW).group(1)
    assert sorted(v.strip().strip('"') for v in versions.split(",")) == ["3.11", "3.12"]
    systems = re.search(r"os:\s*\[([^\]]+)\]", WORKFLOW).group(1)
    assert sorted(s.strip() for s in systems.split(",")) == [
        "ubuntu-latest", "windows-latest"
    ]


def test_the_workflow_lints_every_documented_target() -> None:
    assert "ruff check src tests scripts" in WORKFLOW
    development = README.split("## Development", 1)[1]
    assert "ruff check src tests scripts" in development


def test_the_readme_links_the_workflow_rather_than_embedding_a_badge() -> None:
    assert ".github/workflows/ci.yml" in links(README)
    assert IMAGE_MARKER not in README, "the README embeds an image"
    assert "badge" not in README.lower()


# --- no brittle numeric claims --------------------------------------------------


@pytest.mark.parametrize("document", ["README.md", "docs/ARCHITECTURE.md"])
def test_no_exact_test_total_is_claimed(document: str) -> None:
    text = DOCUMENTS[document]
    assert not re.search(r"\b\d{3,}\s+(tests?|passed)\b", text)
    assert not re.search(r"\b(tests?|suite)\s+(count|total)\s*[:=]\s*\d+", text, re.I)


@pytest.mark.parametrize("document", ["README.md", "docs/ARCHITECTURE.md"])
def test_no_coverage_percentage_is_claimed(document: str) -> None:
    assert not re.search(r"\b\d{1,3}\s*%\s*(coverage|covered)", DOCUMENTS[document], re.I)
    assert not re.search(r"coverage\D{0,20}\b\d{1,3}\s*%", DOCUMENTS[document], re.I)


@pytest.mark.parametrize("document", ["README.md", "docs/ARCHITECTURE.md"])
def test_no_timing_or_throughput_promise_is_made(document: str) -> None:
    text = DOCUMENTS[document]
    assert not re.search(r"\b\d+(\.\d+)?\s*(ms|milliseconds?|seconds?|s)\b(?!\w)", text)
    assert not re.search(r"\b(rows|records)\s*(/|per )\s*(second|sec|s)\b", text, re.I)


def test_the_readme_declines_to_make_performance_claims() -> None:
    assert "No performance or scale claims are made" in README_FLAT


# --- self-containment and confidentiality ---------------------------------------


@pytest.mark.parametrize("document", sorted(PROSE))
def test_no_absolute_or_machine_specific_path_appears_in(document: str) -> None:
    text = PROSE[document]
    # Needles are assembled so this module does not itself contain the literals
    # a pre-publication path scan looks for.
    assert ("/Us" + "ers/") not in text
    assert ("/ho" + "me/") not in text
    boundary = "(?<" + "!" + "[A-Za-z0-9_])"
    assert not re.search(boundary + "[A-Z]" + ":" + r"\\", text)
    assert ("\\\\" + "\\\\") not in text
    assert "PYTHONPATH" not in text
    if document in {"README.md", "docs/ARCHITECTURE.md"}:
        # The plan legitimately documents creating a virtual environment;
        # the presentation documents must stay interpreter-neutral.
        assert ("venv" + "/bin/") not in text


@pytest.mark.parametrize("document", sorted(EVERY_CHANGED_FILE))
def test_no_external_url_appears_in(document: str) -> None:
    for scheme in SCHEMES:
        assert scheme not in EVERY_CHANGED_FILE[document], scheme


@pytest.mark.parametrize("document", sorted(EVERY_CHANGED_FILE))
def test_every_email_uses_the_reserved_domain_in(document: str) -> None:
    """The reserved namespace, which is the exact domain or a subdomain of it.

    Wider than the runtime recipient contract on purpose: documentation and the
    negative tests it describes need to name an address the runtime refuses,
    and a reserved subdomain does that without borrowing a real domain.
    """
    for address in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                              EVERY_CHANGED_FILE[document]):
        domain = address.rpartition("@")[2].lower()
        assert domain == "example.com" or domain.endswith(".example.com"), address


@pytest.mark.parametrize("document", sorted(EVERY_CHANGED_FILE))
def test_no_attribution_trailer_appears_in(document: str) -> None:
    """Matched by trailer shape, so no vendor name enters the repository."""
    text = EVERY_CHANGED_FILE[document]
    assert not re.search(r"(?mi)^\s*co-authored-by\s*:", text)
    assert not re.search(r"(?mi)^\s*(signed-off-by|generated[- ]with)\b", text)


@pytest.mark.parametrize("document", sorted(EVERY_CHANGED_FILE))
def test_no_username_or_hostname_appears_in(document: str) -> None:
    text = EVERY_CHANGED_FILE[document].lower()
    for needle in ("local" + "host", "." + "internal", "hosted" + "toolcache"):
        assert needle not in text, needle


@pytest.mark.parametrize("document", sorted(PROSE))
def test_no_image_or_screenshot_is_referenced_in(document: str) -> None:
    assert IMAGE_MARKER not in PROSE[document]
    for extension in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        assert extension not in PROSE[document].lower(), extension


def test_no_generated_report_is_committed() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    ).stdout.split()
    for name in FILENAME_BY_FORMAT.values():
        assert not any(entry.endswith(name) for entry in tracked), (
            f"a generated {name} is committed"
        )


def test_no_demo_output_directory_is_committed() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    ).stdout.split()
    for entry in tracked:
        assert not entry.startswith(("demo-output/", "synthetic-inputs/", "output/"))


def test_the_documents_avoid_first_person_marketing_voice() -> None:
    for document in ("README.md", "docs/ARCHITECTURE.md"):
        flat = " ".join(DOCUMENTS[document].split())
        for phrase in ("we built", "we have built", "our production", "our team"):
            assert phrase not in flat.lower(), f"{document} contains '{phrase}'"
