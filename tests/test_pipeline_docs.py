"""The pipeline contract, the plan, and the code must agree.

``docs/PIPELINE_CONTRACT.md`` is the authority for T14, and the contract's own
opening paragraph promises that this module holds it to that. These tests read
the prose and check the public implementation matches it, so documentation that
drifts from behaviour fails the build instead of quietly misleading a reader.

How these tests are written
---------------------------
Against public enums, constants and signatures wherever a value exists to
compare — ``STAGE_ORDER`` rather than a hand-written list of stage names — and
against focused document phrases only where the claim is prose. No line numbers,
no reimplementation of the pipeline algorithm, and no bare substring that could
match an unrelated identifier: word-boundary regexes are used for short tokens,
because ``"run"`` appears inside ``"running"`` and would prove nothing.
"""

from __future__ import annotations

import re
from inspect import signature
from pathlib import Path

import pytest

from las.cli import (
    EXIT_BY_CATEGORY,
    EXIT_CONFIGURATION,
    EXIT_EXECUTION,
    EXIT_INTERNAL,
    EXIT_OUTPUT,
    EXIT_SUCCESS,
    EXIT_USAGE,
    build_parser,
)
from las.pipeline import (
    BATCH_ID_LENGTH,
    BATCH_ID_PREFIX,
    EVENT_ARTIFACTS_COMMITTED,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_STAGE_COMPLETED,
    EVENT_STARTED,
    FILENAME_BY_FORMAT,
    MATCHING_OUTCOME_FIELDS,
    STAGE_ORDER,
    ErrorCategory,
    Stage,
    WriteBackMode,
    batch_id,
    run_pipeline,
    stage_order,
)
from las.report import CANONICAL_ENTRY_DATE, WORKBOOK_EPOCH, OutputFormat

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "docs" / "PIPELINE_CONTRACT.md"
CONTRACT = CONTRACT_PATH.read_text(encoding="utf-8")
PLAN = (REPO_ROOT / "docs" / "DEVELOPMENT_PLAN.md").read_text(encoding="utf-8")

#: Whitespace-normalised, so a claim that wrapped across lines still matches.
FLAT = " ".join(CONTRACT.split())

#: The eleven payload keys the contract authorises, and nothing else.
AUDIT_PAYLOAD_KEYS = frozenset(
    {
        "command", "seed", "stage", "count", "artifact_count", "feed_rows",
        "system_rows", "group_count", "unresolved_count", "error_code", "status",
    }
)

AUDIT_EVENTS = (
    EVENT_STARTED,
    EVENT_STAGE_COMPLETED,
    EVENT_ARTIFACTS_COMMITTED,
    EVENT_COMPLETED,
    EVENT_FAILED,
)


def says(*phrases: str) -> bool:
    """Every phrase appears in the whitespace-normalised contract."""
    return all(phrase in FLAT for phrase in phrases)


def word(token: str, text: str = "") -> bool:
    """``token`` appears as a whole word, not inside a longer identifier."""
    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])",
                     text or FLAT) is not None


@pytest.fixture(scope="module")
def t14_clause() -> str:
    start = PLAN.index("**T14 Pipeline + CLI**")
    end = PLAN.index("* **T15 Documentation", start)
    return " ".join(PLAN[start:end].split())


# --- the contract document is committed and authoritative -----------------------


def test_the_contract_document_is_committed_and_substantial() -> None:
    assert CONTRACT_PATH.is_file()
    assert len(CONTRACT) > 4000


def test_the_contract_claims_this_module_holds_it_to_the_implementation() -> None:
    """The claim that used to name a module that did not exist."""
    assert "tests/test_pipeline_docs.py" in FLAT
    assert Path(__file__).name == "test_pipeline_docs.py"


def test_the_plan_points_at_the_contract_document(t14_clause: str) -> None:
    assert "docs/PIPELINE_CONTRACT.md" in t14_clause
    assert "authoritative T14 contract" in t14_clause


# --- 1. the seven stages, and their order ---------------------------------------


def test_the_contract_names_exactly_the_seven_public_stages() -> None:
    documented = {
        token.lower()
        for token in re.findall(r"`([A-Z]{5,9})`", CONTRACT)
        if token.lower() in {s.value for s in Stage}
    }
    assert documented == {stage.value for stage in Stage}


def test_the_contract_spells_the_stage_order_the_engine_runs() -> None:
    arrow = " → ".join(stage.value.upper() for stage in STAGE_ORDER)
    assert arrow in FLAT
    assert stage_order() == STAGE_ORDER
    assert [s.value for s in STAGE_ORDER] == [
        "extract", "ingest", "validate", "match", "reconcile", "report", "verify"
    ]


def test_the_documented_order_is_the_only_order_and_has_no_extra_stage() -> None:
    heading = re.search(r"##\s+(\w+)\s+stages", CONTRACT)
    assert heading is not None and heading.group(1) == "Seven"
    assert len(STAGE_ORDER) == 7 == len(set(STAGE_ORDER))


def test_the_plan_names_the_same_seven_stages_in_the_same_order(t14_clause: str) -> None:
    positions = [t14_clause.index(f"`{s.value.upper()}`") for s in STAGE_ORDER]
    assert positions == sorted(positions)


def test_the_contract_states_every_pre_verify_stage_is_pure() -> None:
    assert says("Every stage before `VERIFY` is pure")
    assert says("No directory is created and no byte is written until the gate has passed")


# --- 2. report-only behaviour ---------------------------------------------------


def test_the_contract_declares_the_pipeline_report_only() -> None:
    assert says("It **never mutates the simulated system**")
    assert says("There is no `--apply`, no `--dry-run`, and no")


def test_the_contract_states_write_back_disabled_with_zero_and_zero() -> None:
    assert says("write-back mode `DISABLED`, `0` attempted updates and `0` applied")
    assert [mode.value for mode in WriteBackMode] == ["disabled"]
    assert WriteBackMode.DISABLED.value == "disabled"


def test_the_only_write_back_mode_in_the_code_is_the_documented_one() -> None:
    assert len(WriteBackMode) == 1
    assert WriteBackMode.DISABLED.name == "DISABLED"


def test_the_plan_agrees_that_t14_is_report_only(t14_clause: str) -> None:
    assert "report-only" in t14_clause
    assert "`DISABLED`" in t14_clause
    assert "zero attempted and zero applied" in t14_clause


# --- 3. the three artifact filenames -------------------------------------------


@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_the_contract_lists_each_artifact_filename(output_format: OutputFormat) -> None:
    assert f"`{FILENAME_BY_FORMAT[output_format]}`" in FLAT


def test_the_contract_documents_no_filename_the_code_does_not_produce() -> None:
    documented = set(re.findall(r"`(reconciliation-[a-z]+\.[a-z]+)`", CONTRACT))
    assert documented == set(FILENAME_BY_FORMAT.values())


def test_the_contract_calls_the_filenames_fixed_direct_child_names() -> None:
    assert says("Fixed constants: independent of time, seed, input filenames, locale")
    assert says("Direct child names only")
    assert says("no caller-configurable filename field")
    assert all("/" not in name and "\\" not in name
               for name in FILENAME_BY_FORMAT.values())


# --- 4. and 5. the commands and their flags ------------------------------------


def documented_commands() -> set[str]:
    block = re.search(r"```text\n(las .+?)```", CONTRACT, re.S)
    assert block is not None
    return {line.split()[1] for line in block.group(1).strip().splitlines()}


def parser_commands() -> set[str]:
    actions = [a for a in build_parser()._actions if hasattr(a, "choices") and a.choices]
    return {name for action in actions for name in action.choices}


def test_the_contract_documents_exactly_the_commands_the_parser_offers() -> None:
    assert documented_commands() == parser_commands() == {
        "run", "demo", "generate-fixtures"
    }


@pytest.mark.parametrize("command", ["run", "demo", "generate-fixtures"])
def test_each_documented_command_is_a_real_subcommand(command: str) -> None:
    assert command in parser_commands()
    assert word(command)


def subcommand_flags(command: str) -> set[str]:
    actions = [a for a in build_parser()._actions if hasattr(a, "choices") and a.choices]
    parser = next(a.choices[command] for a in actions if command in a.choices)
    return {option for action in parser._actions for option in action.option_strings}


def test_run_takes_only_config_and_help() -> None:
    assert subcommand_flags("run") == {"-h", "--help", "--config"}
    assert says("`run` takes `--config` and nothing else")


@pytest.mark.parametrize("command", ["demo", "generate-fixtures"])
def test_the_generating_commands_take_output_dir_and_seed(command: str) -> None:
    assert subcommand_flags(command) == {"-h", "--help", "--output-dir", "--seed"}


@pytest.mark.parametrize("forbidden", ["--apply", "--dry-run", "--overwrite"])
def test_no_command_offers_a_forbidden_flag(forbidden: str) -> None:
    every = set().union(*(subcommand_flags(c) for c in parser_commands()))
    every |= {o for a in build_parser()._actions for o in a.option_strings}
    assert forbidden not in every


@pytest.mark.parametrize("forbidden", ["--apply", "--dry-run", "--overwrite"])
def test_the_contract_states_the_forbidden_flags_do_not_exist(forbidden: str) -> None:
    assert f"`{forbidden}`" in FLAT
    # Named only in a sentence that denies it.
    sentence = next(part for part in FLAT.split(".") if f"`{forbidden}`" in part)
    assert re.search(r"\bno\b|\bnot\b|\bthere is no\b", sentence, re.I)


def test_the_contract_preserves_version_and_deterministic_help() -> None:
    assert says("`--version` is preserved")
    assert says("`--help` is deterministic for the parser and every subcommand")
    assert says("no interactive prompts and no environment-variable defaults")


# --- 6. the exit-code map ------------------------------------------------------


def documented_exit_codes() -> dict[str, str]:
    rows = re.findall(r"^\|\s*`(\d)`\s*\|\s*([^|]+?)\s*\|$", CONTRACT, re.M)
    return {code: meaning for code, meaning in rows}


@pytest.mark.parametrize(
    ("code", "phrase"),
    [
        (EXIT_SUCCESS, "success"),
        (EXIT_USAGE, "argparse usage error"),
        (EXIT_CONFIGURATION, "configuration or configured-input error"),
        (EXIT_OUTPUT, "output conflict or output transaction failure"),
        (EXIT_EXECUTION, "typed pipeline or lower-layer execution failure"),
        (EXIT_INTERNAL, "unexpected internal error"),
    ],
)
def test_each_exit_code_carries_its_documented_meaning(code: int, phrase: str) -> None:
    assert documented_exit_codes()[str(code)] == phrase


def test_the_contract_documents_exactly_the_six_exit_codes() -> None:
    assert set(documented_exit_codes()) == {"0", "2", "3", "4", "5", "6"}
    assert {EXIT_SUCCESS, EXIT_USAGE, EXIT_CONFIGURATION, EXIT_OUTPUT,
            EXIT_EXECUTION, EXIT_INTERNAL} == {0, 2, 3, 4, 5, 6}


def test_every_error_category_maps_to_a_documented_code() -> None:
    assert set(EXIT_BY_CATEGORY) == set(ErrorCategory)
    assert set(map(str, EXIT_BY_CATEGORY.values())) <= set(documented_exit_codes())


def test_the_category_to_code_mapping_is_the_documented_one() -> None:
    assert EXIT_BY_CATEGORY == {
        ErrorCategory.CONFIGURATION: EXIT_CONFIGURATION,
        ErrorCategory.OUTPUT: EXIT_OUTPUT,
        ErrorCategory.EXECUTION: EXIT_EXECUTION,
        ErrorCategory.INTERNAL: EXIT_INTERNAL,
    }


def test_the_contract_describes_stdout_and_stderr_discipline() -> None:
    assert says("Success: the terminal summary on stdout, stderr empty, exit `0`")
    assert says("Failure: stdout empty, one line on stderr")
    assert says("no traceback, no raw exception text, no path and no object")
    assert says("There is no debug or traceback flag in T14")


# --- 7. output-directory conflict ----------------------------------------------


def test_the_contract_refuses_an_existing_output_directory() -> None:
    assert says("The final directory **must not already exist**")
    assert says("the run fails with `output_conflict`")


def test_the_contract_promises_the_existing_directory_is_left_alone() -> None:
    assert says(
        "Its contents are not inspected, merged, overwritten, deleted, renamed "
        "or modified"
    )
    assert says("There is no overwrite mode, no `--overwrite` flag, no backup and no merge")


def test_the_contract_requires_the_parent_to_exist() -> None:
    assert says("The parent must already exist and be a directory")
    assert says("T14 creates exactly the final directory, and only when publication commits")


# --- 8. the atomic directory transaction ---------------------------------------


def test_the_contract_calls_publication_one_atomic_transaction() -> None:
    assert says("The artifact set is one atomic transaction")
    assert says("Files are never published individually")


@pytest.mark.parametrize(
    "step",
    [
        "Complete every pure stage through `VERIFY`",
        "Confirm the final directory does not exist and its parent does",
        "Create one private staging directory as a sibling of the final directory",
        "Write every artifact into staging, in configured format order, binary mode",
        "Flush and close each file",
        "Read each staged file back and verify byte count, SHA-256 and exact equality",
        "Confirm staging holds exactly the expected filenames and nothing else",
        "Re-confirm the final directory still does not exist",
        "Rename the staging directory into place in one operation",
    ],
)
def test_each_transaction_step_is_documented(step: str) -> None:
    assert says(step)


def test_the_contract_describes_the_staging_name_as_internal_only() -> None:
    assert says("The staging name carries randomness so concurrent runs cannot collide")
    assert says(
        "it never appears in a result, a message, a log line or a filename"
    )
    assert says("it is removed on every failure path")


def test_the_contract_states_failure_leaves_nothing_behind() -> None:
    assert says(
        "On any failure before the rename, and on rename failure, the staging "
        "directory is removed and no final directory exists"
    )


def test_the_contract_gives_the_fixture_transaction_the_same_shape() -> None:
    assert says("`generate-fixtures` uses the same shape: stage, verify the four "
                "expected names, rename once")
    assert says("Either all four fixtures appear or the directory does not exist")


# --- 9. and 10. audit events and the payload allowlist -------------------------


@pytest.mark.parametrize("event", AUDIT_EVENTS)
def test_each_authorized_audit_event_is_documented(event: str) -> None:
    assert f"`{event}`" in FLAT


def test_the_contract_documents_exactly_the_five_event_names() -> None:
    section = FLAT[FLAT.index("Event names:"):FLAT.index("Payload keys:")]
    documented = set(re.findall(r"`(pipeline_[a-z_]+)`", section))
    assert documented == set(AUDIT_EVENTS)
    assert len(documented) == 5


@pytest.mark.parametrize("key", sorted(AUDIT_PAYLOAD_KEYS))
def test_each_authorized_payload_key_is_documented(key: str) -> None:
    section = FLAT[FLAT.index("Payload keys:"):FLAT.index("No other key is written")]
    assert f"`{key}`" in section


def test_the_contract_documents_exactly_the_eleven_payload_keys() -> None:
    section = FLAT[FLAT.index("Payload keys:"):FLAT.index("No other key is written")]
    documented = set(re.findall(r"`([a-z_]+)`", section))
    assert documented == AUDIT_PAYLOAD_KEYS
    assert len(documented) == 11
    assert says("No other key is written")


def test_the_contract_states_the_success_and_failure_event_sequences() -> None:
    assert says(
        "Given a trail, a successful run emits `pipeline_started`, one "
        "`pipeline_stage_completed` per stage in stage order, "
        "`pipeline_artifacts_committed`, then `pipeline_completed`"
    )
    assert says("A failure emits `pipeline_failed` once with the last active stage "
                "and a stable `error_code`, and no `pipeline_completed`")


def test_the_contract_states_audit_is_optional_and_off_by_default() -> None:
    """The guarantee above is conditional, and the contract must say so.

    Checked against the signature rather than prose alone: the default really
    is ``None``, so a contract promising unconditional events would be wrong.
    """
    assert says("Audit is optional and disabled by default")
    assert says("no event is recorded and no file is written")
    assert signature(run_pipeline).parameters["trail"].default is None


def test_the_contract_states_the_cli_supplies_no_trail_and_no_logging() -> None:
    assert says("The `las` commands supply no trail")
    assert says("no command configures a console or file handler")


def test_the_contract_keeps_audit_output_outside_the_artifact_set() -> None:
    """Only the guarantees the pipeline and the writer actually own.

    The writer never emits audit output and the transaction never carries it.
    Where a caller-supplied trail physically lives is the caller's business —
    ``run_pipeline`` neither validates nor relocates it — so the contract must
    promise nothing about that location.
    """
    assert says(
        "Audit output is operational metadata, not one of the three report "
        "artifacts: the report writer neither creates nor publishes it, and it "
        "is outside the atomic report-directory publication transaction"
    )
    assert says(
        "A library caller that supplies a trail controls its location and "
        "lifecycle, subject to the audit facility's own path rules"
    )
    assert "never inside the published output directory" not in FLAT


def test_the_contract_forbids_unsafe_audit_content() -> None:
    assert says(
        "No event carries an input path, an output path, a source or account "
        "name, a raw row, a raw exception message, artifact bytes, a temporary "
        "directory name, a username or a hostname"
    )


# --- 11. the fixed terminal summary --------------------------------------------


def summary_block() -> list[str]:
    blocks = re.findall(r"```text\n(.*?)```", CONTRACT, re.S)
    block = next(b for b in blocks if b.startswith("status: success"))
    return block.strip().splitlines()


def test_the_documented_summary_has_the_thirteen_contract_lines() -> None:
    lines = summary_block()
    assert len(lines) == 13
    assert lines[0] == "status: success"
    assert lines[-1] == "write_back: disabled"


def test_the_documented_summary_names_every_matching_outcome_in_order() -> None:
    lines = summary_block()
    documented = [line.split(":")[0].removeprefix("matching_")
                  for line in lines if line.startswith("matching_")]
    assert tuple(documented) == MATCHING_OUTCOME_FIELDS


def test_the_documented_summary_reports_the_only_write_back_mode() -> None:
    assert f"write_back: {WriteBackMode.DISABLED.value}" in summary_block()


def test_the_contract_states_the_summary_says_nothing_unsafe() -> None:
    assert says("LF newlines, one final newline")
    assert says("`generate-fixtures` prints `status: success` then `fixtures: 4`")
    assert says(
        "No path, name, run identifier, timestamp, exception or object "
        "representation is ever printed"
    )
    assert says("Equivalent successful runs produce byte-identical summaries")


# --- 12. deterministic feed-batch identity -------------------------------------


def test_the_contract_states_the_batch_id_prefix_and_length() -> None:
    assert f"`{BATCH_ID_PREFIX}`" in FLAT
    assert says(f"the first {BATCH_ID_LENGTH} lowercase hex characters")


def test_the_documented_algorithm_matches_the_implementation() -> None:
    assert says("The canonical descriptor is compact UTF-8 JSON with sorted keys")
    assert says("Paths are normalized to POSIX so Windows and Unix agree")
    assert says("plus the first 16 lowercase hex characters of the descriptor's SHA-256")


def test_the_documented_descriptor_shape_is_the_one_the_code_hashes() -> None:
    """The documented example is checked for shape, not recomputed here."""
    block = re.search(r"```json\n(\{.+?\})\n```", CONTRACT, re.S)
    assert block is not None
    documented_keys = set(re.findall(r'"([a-z]+)":', block.group(1)))
    assert documented_keys == {"format", "label", "path", "worksheet"}


def test_a_real_identifier_has_the_documented_prefix_and_length() -> None:
    from las.config import FeedConfig

    feed = FeedConfig.model_validate({"name": "feed_alpha", "path": "feed_alpha.xlsx"})
    identifier = batch_id(feed, Path("."))
    assert identifier.startswith(BATCH_ID_PREFIX)
    suffix = identifier.removeprefix(BATCH_ID_PREFIX)
    assert len(suffix) == BATCH_ID_LENGTH
    assert suffix == suffix.lower()
    assert re.fullmatch(r"[0-9a-f]+", suffix)


def test_the_contract_states_reordering_changes_no_identifier() -> None:
    assert says("Reordering feed definitions changes no identifier and no business result")
    assert says("never from list position, object identity or first-seen order")


def test_the_contract_forbids_leaking_the_descriptor() -> None:
    assert says(
        "The descriptor and the source path never appear in an error, a log "
        "line, the terminal output or the run result"
    )


# --- 13. byte determinism in all three formats ---------------------------------


def test_the_contract_promises_byte_identical_artifacts_in_all_three_formats() -> None:
    assert says("**byte-identical artifacts in all three formats**")


def test_the_contract_names_xlsx_as_included_and_says_why() -> None:
    assert says("including XLSX, whose ZIP container is canonicalized for exactly "
                "this reason")
    assert says("docs/REPORTING_CONTRACT.md")


def test_the_contract_lists_what_determinism_does_not_depend_on() -> None:
    for phrase in ("configured feed order", "extraction row order",
                   "mapping iteration", "the clock", "the timezone", "the locale",
                   "the global decimal context", "object identity",
                   "the temporary directory name"):
        assert phrase in FLAT, phrase


def test_the_contract_states_equal_run_results_and_digests() -> None:
    assert says("Equivalent business runs produce equal run results, stage states, "
                "counts, filenames, artifact digests")
    assert says("equal terminal summaries and exit codes")


# --- 14. fixed workbook ZIP metadata and core-property epochs ------------------


def test_the_reporting_contract_documents_the_canonical_zip_metadata() -> None:
    """T14's byte guarantee rests on T13's container, so its terms are checked."""
    reporting = " ".join(
        (REPO_ROOT / "docs" / "REPORTING_CONTRACT.md").read_text(encoding="utf-8").split()
    )
    year, month, day, hour, minute, second = CANONICAL_ENTRY_DATE
    assert f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}" \
        in reporting
    for phrase in ("sorted name order", "one compression method at one level",
                   "`create_system`", "`external_attr`", "no extra field",
                   "no archive or entry comment"):
        assert phrase in reporting, phrase


def test_the_reporting_contract_states_the_core_properties_are_unchanged() -> None:
    reporting = " ".join(
        (REPO_ROOT / "docs" / "REPORTING_CONTRACT.md").read_text(encoding="utf-8").split()
    )
    year, month, day, hour, minute, second = WORKBOOK_EPOCH
    stamp = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    assert stamp in reporting
    assert "created and modified both stay" in reporting


def test_the_canonical_entry_date_is_the_earliest_the_format_allows() -> None:
    assert CANONICAL_ENTRY_DATE == (1980, 1, 1, 0, 0, 0)


# --- 15. lower-layer rules are not reimplemented -------------------------------


def test_the_contract_states_each_stage_uses_the_owning_layer() -> None:
    assert says("The public T10 API")
    assert says("The public T11 API with the configured policy")
    assert says("No exact, fuzzy, ambiguity or eligibility logic is duplicated")
    assert says("No T11 outcome is reinterpreted and no unit is converted")


def test_the_contract_states_the_pipeline_repairs_and_filters_nothing() -> None:
    assert says("Every row is preserved; the pipeline repairs and filters nothing")
    assert says("No role is inferred from a filename")


def test_the_contract_states_verify_rereads_rather_than_recomputes() -> None:
    assert says("It re-reads lower-layer invariants; it never recomputes a "
                "lower-layer outcome")


def test_the_engine_module_states_it_holds_no_business_rule() -> None:
    from las.pipeline import engine

    doc = " ".join((engine.__doc__ or "").split())
    assert "contains no business rule of its own" in doc
    assert "comes from the layer that owns it" in doc


def test_the_contract_keeps_orchestration_mechanics_out_of_the_stages() -> None:
    assert says(
        "Configuration loading, optional audit setup, publication and result "
        "construction are orchestration mechanics, not stages"
    )


# --- the run result holds nothing dangerous ------------------------------------


def test_the_contract_lists_what_a_run_result_may_not_hold() -> None:
    assert says(
        "It holds no path, no config object, no feed or ledger row, no "
        "validation or matching or reconciliation object, no report model, no "
        "artifact bytes, no simulator, no driver, no exception and no audit object"
    )


def test_the_contract_states_run_identifiers_stay_out_of_equality() -> None:
    assert says(
        "They never enter report artifacts, filenames, terminal summaries or "
        "`RunResult` equality"
    )


# --- the contract itself stays self-contained ----------------------------------


def test_the_contract_names_no_absolute_path_or_url() -> None:
    assert "/Users/" not in CONTRACT and "/home/" not in CONTRACT
    assert not re.search(r"(?<![A-Za-z0-9_])[A-Za-z]:\\", CONTRACT)
    assert "\\\\" not in CONTRACT
    assert "http://" not in CONTRACT and "https://" not in CONTRACT


def test_the_contract_carries_no_attribution_trailer() -> None:
    """No trailer of any kind, checked without naming a vendor.

    Spelling out tool or vendor names here would put the very tokens a
    confidentiality scan looks for into the repository, so the shape of a git
    trailer is matched instead of any particular one.
    """
    assert not re.search(r"(?mi)^\s*co-authored-by\s*:", CONTRACT)
    assert not re.search(r"(?mi)^\s*(signed-off-by|generated[- ]with)\b", CONTRACT)


def test_every_email_in_the_contract_uses_the_reserved_domain() -> None:
    for address in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", CONTRACT):
        assert address.endswith("@example.com"), address
