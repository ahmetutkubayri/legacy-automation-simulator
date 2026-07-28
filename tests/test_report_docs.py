"""The reporting contract, the plan, the config docs, and the code must agree.

``docs/REPORTING_CONTRACT.md`` is the authority for T13. These tests read that
prose and check the implementation matches it, so documentation that drifts
from behaviour fails the build rather than quietly misleading a reader.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from las.config import ReportConfig
from las.reconcile import ReconcileOutcome
from las.report import (
    ACCOUNTING_METRICS,
    DOCUMENT_TITLE,
    GROUP_COLUMN_NAMES,
    HEADER_FILL_ARGB,
    HEADER_FONT_ARGB,
    HTML_OUTCOME_BACKGROUND,
    MEDIA_TYPE_BY_FORMAT,
    OUTCOME_DISPLAY_ORDER,
    OUTCOME_FILL_ARGB,
    REVIEW_QUEUE_COLUMN_NAMES,
    SCHEMA_VERSION,
    SHEET_ORDER,
    SUFFIX_BY_FORMAT,
    SUMMARY_COLUMN_NAMES,
    SUMMARY_SECTIONS,
    WORKBOOK_CREATOR,
    OutputFormat,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (REPO_ROOT / "docs" / "REPORTING_CONTRACT.md").read_text(encoding="utf-8")
PLAN = (REPO_ROOT / "docs" / "DEVELOPMENT_PLAN.md").read_text(encoding="utf-8")
FLAT = " ".join(CONTRACT.split())


@pytest.fixture(scope="module")
def t13_clause() -> str:
    start = PLAN.index("**T13 Reporting**")
    end = PLAN.index("* **T14 Pipeline", start)
    return " ".join(PLAN[start:end].split())


# --- the contract document exists and is authoritative --------------------------


def test_the_contract_document_is_committed_and_substantial() -> None:
    assert (REPO_ROOT / "docs" / "REPORTING_CONTRACT.md").is_file()
    assert len(CONTRACT) > 4000


def test_the_plan_points_at_the_contract_document(t13_clause: str) -> None:
    assert "docs/REPORTING_CONTRACT.md" in t13_clause
    assert "authoritative T13 contract" in t13_clause


def test_the_plan_states_the_three_formats(t13_clause: str) -> None:
    for phrase in ("XLSX", "JSON summary", "HTML summary"):
        assert phrase in t13_clause


def test_the_plan_states_that_t13_writes_nothing(t13_clause: str) -> None:
    assert "entirely in memory" in t13_clause
    assert "chooses no filename and writes no file" in t13_clause
    assert "T14 writes the returned" in t13_clause


def test_the_plan_states_that_recipients_are_metadata_only(t13_clause: str) -> None:
    assert "metadata only" in t13_clause
    assert "no mail is ever sent" in t13_clause


def test_the_plan_states_the_determinism_guarantee(t13_clause: str) -> None:
    assert "byte-identical across renders" in t13_clause
    assert "semantically identical" in t13_clause


# --- sheets and columns ---------------------------------------------------------


def test_the_contract_names_the_four_sheets_in_order() -> None:
    positions = [CONTRACT.index(f"`{sheet.value}`") for sheet in SHEET_ORDER]
    assert positions == sorted(positions)
    for sheet in SHEET_ORDER:
        assert f"`{sheet.value}`" in CONTRACT


def test_the_contract_lists_every_summary_column() -> None:
    for name in SUMMARY_COLUMN_NAMES:
        assert f"`{name}`" in CONTRACT


def test_the_contract_lists_every_group_column_in_order() -> None:
    positions = [FLAT.index(f"`{name}`") for name in GROUP_COLUMN_NAMES]
    assert positions == sorted(positions), "columns are documented out of order"


def test_the_contract_lists_every_review_queue_column() -> None:
    for name in REVIEW_QUEUE_COLUMN_NAMES:
        assert f"`{name}`" in CONTRACT


def test_the_contract_lists_every_accounting_metric_in_order() -> None:
    positions = [FLAT.index(f"`{name}`") for name in ACCOUNTING_METRICS]
    assert positions == sorted(positions)


def test_the_contract_names_every_summary_section() -> None:
    for section in SUMMARY_SECTIONS:
        assert f"`{section}`" in CONTRACT


def test_the_contract_states_the_documented_outcome_display_order() -> None:
    documented = FLAT[FLAT.index("Outcome display order is") :][:200]
    for outcome in OUTCOME_DISPLAY_ORDER:
        assert f"`{outcome.value}`" in documented
    positions = [documented.index(f"`{o.value}`") for o in OUTCOME_DISPLAY_ORDER]
    assert positions == sorted(positions)


def test_the_contract_explains_why_the_order_differs_from_the_enum() -> None:
    assert "not** `ReconcileOutcome`'s declaration order" in FLAT
    assert list(OUTCOME_DISPLAY_ORDER) != list(ReconcileOutcome)


# --- membership rules -----------------------------------------------------------


def test_the_contract_states_the_exceptions_membership_rule() -> None:
    for outcome in ("MISMATCH", "UNIT_MISMATCH", "MISSING_IN_FEED", "MISSING_IN_SYSTEM"):
        assert f"`{outcome}`" in CONTRACT
    assert "Never `BALANCED` or" in FLAT
    assert "a filter, not a re-sort" in FLAT


def test_the_contract_states_the_review_queue_membership_rule() -> None:
    assert "The sheet name is not a filter" in FLAT
    assert "all six unresolved categories" in FLAT
    for reason in ("match_needs_review", "match_unmatched", "match_ineligible",
                   "invalid_amount", "invalid_unit", "invalid_period"):
        assert f"`{reason}`" in CONTRACT


def test_the_contract_states_that_blanks_are_never_zeros() -> None:
    assert "never manufactures a zero" in FLAT


# --- styles ----------------------------------------------------------------------


def test_the_contract_states_the_header_style() -> None:
    assert f"`{HEADER_FILL_ARGB}`" in CONTRACT
    assert f"`{HEADER_FONT_ARGB}`" in CONTRACT


def test_the_contract_states_every_outcome_fill() -> None:
    for outcome, argb in OUTCOME_FILL_ARGB.items():
        assert f"| `{outcome.name}` | `{argb}` |" in CONTRACT


def test_the_contract_states_every_html_background() -> None:
    for outcome, colour in HTML_OUTCOME_BACKGROUND.items():
        assert f"`{outcome.value}`" in CONTRACT
        assert f"`{colour}`" in CONTRACT


def test_the_contract_states_that_only_reconciliation_is_filled() -> None:
    assert "on `Reconciliation` only" in CONTRACT
    assert "receive only the header style" in FLAT


# --- decimals, formulas, schema --------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12", "12"), ("12.3400", "12.3400"), ("-2.5", "-2.5"),
     ("-0", "0"), ("0E-100", "0")],
)
def test_the_documented_decimal_table_matches_the_renderer(
    value: str, expected: str
) -> None:
    from las.report import render_decimal

    assert f"| `Decimal(\"{value}\")` | `{expected}` |" in CONTRACT
    assert render_decimal(Decimal(value)) == expected


def test_the_contract_states_the_text_cell_decision() -> None:
    assert "stored as **text**, not numeric cells" in CONTRACT
    assert "JSON decimal values are **strings**" in CONTRACT


def test_the_contract_states_the_formula_injection_strategy() -> None:
    assert "never prefixed, trimmed or rewritten" in FLAT
    assert "zero formula cells" in FLAT
    for character in ("`=`", "`+`", "`-`", "`@`"):
        assert character in CONTRACT


def test_the_contract_documents_the_json_schema_keys() -> None:
    for key in ("schema_version", "recipients", "accounting", "group_outcomes",
                "group_reasons", "unresolved_reasons", "variance_bands"):
        assert f'"{key}"' in CONTRACT


def test_the_contract_states_the_json_encoding_rules() -> None:
    for phrase in ("UTF-8, no BOM", "LF newlines", "two-space indentation",
                   "ensure_ascii=False", "one final newline"):
        assert phrase in FLAT


def test_the_documented_schema_version_matches_the_code() -> None:
    assert f'"schema_version": "{SCHEMA_VERSION}"' in CONTRACT


# --- documents, metadata, streaming ----------------------------------------------


def test_the_contract_states_the_html_title_and_palette() -> None:
    assert DOCUMENT_TITLE in CONTRACT
    for colour in ("#1F4E78", "#FFFFFF", "#222222", "#B7B7B7"):
        assert f"`{colour}`" in CONTRACT
    assert "`Arial, sans-serif`" in CONTRACT


def test_the_contract_states_the_html_is_self_contained() -> None:
    for phrase in ("No JavaScript", "no external CSS", "no images",
                   "no external links", "one `<style>` element"):
        assert phrase in FLAT


def test_the_contract_states_the_fixed_workbook_metadata() -> None:
    assert f"`{WORKBOOK_CREATOR}`" in CONTRACT
    assert "`2000-01-01 00:00:00`" in CONTRACT
    assert "Never the current time, a username, a host name" in FLAT


def test_the_contract_states_the_streaming_rule() -> None:
    assert "greater than or equal to the threshold" in FLAT
    assert "Summary never triggers streaming" in FLAT
    assert "indistinguishable after reload" in FLAT


def test_the_contract_distinguishes_semantic_from_byte_determinism() -> None:
    assert "byte-identical** across repeated renders" in FLAT
    assert "semantically identical**" in FLAT
    assert "byte-identical XLSX is not claimed" in FLAT


def test_the_contract_states_the_media_types_and_suffixes() -> None:
    for output_format in OutputFormat:
        assert f"`{MEDIA_TYPE_BY_FORMAT[output_format]}`" in CONTRACT
        assert f"`{SUFFIX_BY_FORMAT[output_format]}`" in CONTRACT


def test_the_contract_states_the_output_ownership_rule() -> None:
    assert "renders entirely in memory" in FLAT
    assert "never a path or a filename" in FLAT
    assert "T14 chooses filenames" in FLAT


def test_the_contract_lists_the_input_verification_rules() -> None:
    for phrase in ("group keys are unique", "publish no difference and no band",
                   "differs by exactly zero", "selects no band",
                   "never recomputes T12's arithmetic"):
        assert phrase in FLAT


# --- the configuration documentation ---------------------------------------------


def config_doc() -> str:
    """The ReportConfig docstring with its line wrapping flattened."""
    return " ".join((ReportConfig.__doc__ or "").split())


def test_the_config_docstring_defers_the_layout_to_the_contract() -> None:
    doc = config_doc()
    assert "docs/REPORTING_CONTRACT.md" in doc
    assert "not to config" in doc


def test_the_config_docstring_describes_all_four_fields() -> None:
    doc = config_doc()
    for field in ("formats", "recipients", "streaming_row_threshold", "variance_bands"):
        assert f"``{field}``" in doc


def test_the_config_docstring_states_that_no_mail_is_sent() -> None:
    assert "no mail is ever sent" in config_doc()


def test_the_configured_field_names_and_defaults_are_unchanged() -> None:
    """The contract was written down; nothing about the config moved."""
    assert set(ReportConfig.model_fields) == {
        "formats", "recipients", "variance_bands", "streaming_row_threshold"
    }
    config = ReportConfig()
    assert config.formats == ["xlsx", "json", "html"]
    assert config.recipients == ["ops-team@example.com"]
    assert config.variance_bands == [0.0, 1.0, 5.0]
    assert config.streaming_row_threshold == 50_000


def test_the_plan_still_documents_the_four_fixture_hashes() -> None:
    hashes = re.findall(r"^([0-9a-f]{64})\s+data/samples/(\S+)$", PLAN, re.MULTILINE)
    assert len(hashes) == 4
    assert {name for _, name in hashes} == {
        "accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv"
    }


def test_the_contract_document_names_no_path_username_or_real_domain() -> None:
    assert "/Users/" not in CONTRACT and "/home/" not in CONTRACT
    for address in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", CONTRACT):
        assert address.endswith("example.com"), address
    assert "http://" not in CONTRACT and "https://" not in CONTRACT


def test_the_contract_states_the_bound_normalisation_rule() -> None:
    assert "configured variance bound** is normalised" in FLAT
    assert "An *amount* is never normalised" in FLAT
    assert "`upper_bound:0`, `upper_bound:1`, `upper_bound:5`" in FLAT
