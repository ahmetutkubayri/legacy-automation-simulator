"""T13 policy: the fixed layout, the style map, and what config may supply."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

from las.config import ReportConfig, load_config
from las.reconcile import ReconcileOutcome
from las.report import (
    COLUMN_NAMES_BY_SHEET,
    EXCEPTION_OUTCOMES,
    GROUP_COLUMN_NAMES,
    GROUP_REASON_ORDER,
    HEADER_FILL_ARGB,
    HEADER_FONT_ARGB,
    HTML_OUTCOME_BACKGROUND,
    OUTCOME_DISPLAY_ORDER,
    OUTCOME_FILL_ARGB,
    REVIEW_QUEUE_COLUMN_NAMES,
    SHEET_ORDER,
    SUMMARY_COLUMN_NAMES,
    UNRESOLVED_REASON_ORDER,
    OutputFormat,
    ReportPolicy,
    ReportPolicyError,
    SheetDefinitionError,
    SheetId,
    UnsupportedFormatError,
    check_sheet_names,
    policy_from_config,
)
from report_helpers import SAMPLE_BANDS, make_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"


# --- construction from configuration -------------------------------------------


def test_the_committed_configuration_builds_the_sample_policy() -> None:
    policy = policy_from_config(load_config(SAMPLES_DIR / "config.yaml").report)
    assert policy.formats == (OutputFormat.XLSX, OutputFormat.JSON, OutputFormat.HTML)
    assert policy.recipients == ("ops-team@example.com",)
    assert policy.variance_bands == SAMPLE_BANDS
    assert policy.streaming_row_threshold == 50_000


def test_the_configured_format_order_is_preserved() -> None:
    policy = policy_from_config(ReportConfig(formats=["html", "xlsx"]))
    assert policy.formats == (OutputFormat.HTML, OutputFormat.XLSX)


def test_configured_bands_convert_exactly_rather_than_through_binary() -> None:
    policy = policy_from_config(ReportConfig(variance_bands=[0.0, 0.1, 0.3]))
    assert policy.variance_bands == (Decimal("0"), Decimal("0.1"), Decimal("0.3"))


# --- format validation ---------------------------------------------------------


def test_a_format_outside_the_authorized_set_is_refused() -> None:
    with pytest.raises(UnsupportedFormatError, match="outside the authorized set"):
        ReportPolicy(
            formats=("pdf",), recipients=(), variance_bands=(Decimal(0),),
            streaming_row_threshold=10,
        )


def test_an_empty_format_list_is_refused() -> None:
    with pytest.raises(UnsupportedFormatError, match="at least one"):
        make_policy(formats=())


def test_a_repeated_format_is_refused() -> None:
    with pytest.raises(UnsupportedFormatError, match="declared twice"):
        make_policy(formats=(OutputFormat.JSON, OutputFormat.JSON))


# --- other configured values ---------------------------------------------------


@pytest.mark.parametrize("recipient", ["", "   ", None])
def test_a_blank_recipient_is_refused(recipient: object) -> None:
    with pytest.raises(ReportPolicyError, match="non-blank label"):
        make_policy(recipients=(recipient,))  # type: ignore[arg-type]


def test_no_recipients_at_all_is_allowed() -> None:
    assert make_policy(recipients=()).recipients == ()


@pytest.mark.parametrize(
    "bands",
    [(), (Decimal(-1),), (Decimal(0), Decimal(0)), (Decimal(5), Decimal(1)),
     (Decimal("NaN"),), (Decimal("Infinity"),)],
)
def test_an_unusable_variance_ladder_is_refused(bands: tuple) -> None:
    with pytest.raises(ReportPolicyError):
        make_policy(variance_bands=bands)


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "10"])
def test_an_unusable_streaming_threshold_is_refused(value: object) -> None:
    with pytest.raises(ReportPolicyError, match="streaming_row_threshold"):
        make_policy(streaming_row_threshold=value)  # type: ignore[arg-type]


def test_the_streaming_boundary_is_inclusive() -> None:
    policy = make_policy(streaming_row_threshold=10)
    assert not policy.streams(9)
    assert policy.streams(10)
    assert policy.streams(11)


# --- the fixed layout ----------------------------------------------------------


def test_the_sheet_order_is_the_documented_one() -> None:
    assert make_policy().sheet_order == SHEET_ORDER
    assert [s.value for s in SHEET_ORDER] == [
        "Summary", "Exceptions", "Reconciliation", "Review Queue"
    ]


def test_the_summary_columns_are_the_documented_three() -> None:
    assert SUMMARY_COLUMN_NAMES == ("section", "metric", "value")


def test_the_group_columns_are_the_documented_thirteen() -> None:
    assert GROUP_COLUMN_NAMES == (
        "account_ref", "period", "outcome", "reason_code",
        "feed_units", "system_units", "feed_total", "system_total",
        "signed_difference", "absolute_difference", "variance_band_upper",
        "feed_contribution_count", "system_contribution_count",
    )
    assert len(GROUP_COLUMN_NAMES) == 13


def test_the_review_queue_columns_are_the_documented_eleven() -> None:
    assert REVIEW_QUEUE_COLUMN_NAMES == (
        "source", "worksheet", "source_row", "row_index", "unresolved_reason",
        "match_outcome", "match_method", "account_ref", "external_ref",
        "period", "unit",
    )
    assert len(REVIEW_QUEUE_COLUMN_NAMES) == 11


def test_exceptions_and_reconciliation_share_one_column_set() -> None:
    policy = make_policy()
    assert policy.columns_for(SheetId.EXCEPTIONS) == policy.columns_for(
        SheetId.RECONCILIATION
    )


def test_columns_are_returned_as_immutable_tuples() -> None:
    columns = make_policy().columns_for(SheetId.SUMMARY)
    assert isinstance(columns, tuple)
    assert [c.name for c in columns] == list(SUMMARY_COLUMN_NAMES)
    with pytest.raises(AttributeError):
        columns.append(columns[0])  # type: ignore[attr-defined]


def test_the_column_map_is_read_only() -> None:
    assert isinstance(COLUMN_NAMES_BY_SHEET, MappingProxyType)
    with pytest.raises(TypeError):
        COLUMN_NAMES_BY_SHEET[SheetId.SUMMARY] = ()  # type: ignore[index]


# --- vocabulary order ----------------------------------------------------------


def test_the_outcome_display_order_is_the_documented_one() -> None:
    assert [o.value for o in OUTCOME_DISPLAY_ORDER] == [
        "balanced", "within_tolerance", "mismatch",
        "unit_mismatch", "missing_in_feed", "missing_in_system",
    ]
    assert set(OUTCOME_DISPLAY_ORDER) == set(ReconcileOutcome)


def test_the_display_order_differs_from_the_enum_declaration_order() -> None:
    """Deliberate: T12 declares decision precedence, a report reads settled first."""
    assert list(OUTCOME_DISPLAY_ORDER) != list(ReconcileOutcome)


def test_the_exception_outcomes_are_the_four_unsettled_ones() -> None:
    assert set(EXCEPTION_OUTCOMES) == {
        ReconcileOutcome.MISMATCH,
        ReconcileOutcome.UNIT_MISMATCH,
        ReconcileOutcome.MISSING_IN_FEED,
        ReconcileOutcome.MISSING_IN_SYSTEM,
    }
    policy = make_policy()
    assert not policy.is_exception(ReconcileOutcome.BALANCED)
    assert not policy.is_exception(ReconcileOutcome.WITHIN_TOLERANCE)
    assert all(policy.is_exception(outcome) for outcome in EXCEPTION_OUTCOMES)


def test_the_reason_orders_come_from_t12_and_do_not_overlap() -> None:
    from las.reconcile import MATCH_REASONS, MESSAGE_BY_REASON, VALIDATION_REASONS

    assert set(GROUP_REASON_ORDER) | set(UNRESOLVED_REASON_ORDER) == set(
        MESSAGE_BY_REASON
    )
    assert not set(GROUP_REASON_ORDER) & set(UNRESOLVED_REASON_ORDER)
    assert tuple(MATCH_REASONS) + tuple(VALIDATION_REASONS) == UNRESOLVED_REASON_ORDER
    assert len(GROUP_REASON_ORDER) == 7
    assert len(UNRESOLVED_REASON_ORDER) == 6


# --- styles --------------------------------------------------------------------


def test_the_header_style_is_the_documented_one() -> None:
    assert make_policy().header_style() == ("FF1F4E78", "FFFFFFFF", True)
    assert HEADER_FILL_ARGB == "FF1F4E78"
    assert HEADER_FONT_ARGB == "FFFFFFFF"


def test_every_outcome_maps_to_its_documented_fill() -> None:
    expected = {
        ReconcileOutcome.BALANCED: "FFE2F0D9",
        ReconcileOutcome.WITHIN_TOLERANCE: "FFFFF2CC",
        ReconcileOutcome.MISMATCH: "FFF4CCCC",
        ReconcileOutcome.UNIT_MISMATCH: "FFFCE5CD",
        ReconcileOutcome.MISSING_IN_FEED: "FFDDEBF7",
        ReconcileOutcome.MISSING_IN_SYSTEM: "FFE4DFEC",
    }
    assert dict(OUTCOME_FILL_ARGB) == expected
    policy = make_policy()
    for outcome, argb in expected.items():
        assert policy.fill_for(outcome) == argb


def test_no_outcome_shares_a_fill_with_another() -> None:
    assert len(set(OUTCOME_FILL_ARGB.values())) == len(OUTCOME_FILL_ARGB)


def test_the_html_palette_is_the_workbook_palette_without_alpha() -> None:
    for outcome, argb in OUTCOME_FILL_ARGB.items():
        assert HTML_OUTCOME_BACKGROUND[outcome] == f"#{argb[2:]}"
    assert make_policy().html_background_for(ReconcileOutcome.BALANCED) == "#E2F0D9"


def test_the_style_maps_are_read_only() -> None:
    for mapping in (OUTCOME_FILL_ARGB, HTML_OUTCOME_BACKGROUND):
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping[ReconcileOutcome.BALANCED] = "FF000000"  # type: ignore[index]


def test_every_workbook_colour_is_eight_uppercase_hex_digits() -> None:
    import re

    pattern = re.compile(r"[0-9A-F]{8}")
    for argb in (HEADER_FILL_ARGB, HEADER_FONT_ARGB, *OUTCOME_FILL_ARGB.values()):
        assert pattern.fullmatch(argb), argb


def test_an_invalid_colour_is_rejected_by_the_validator() -> None:
    from las.report.policy import StyleDefinitionError, _check_argb, _check_hex

    for bad in ("F1F4E78", "GG1F4E78", "#1F4E78", "ff1f4e78", "", None):
        with pytest.raises(StyleDefinitionError, match="eight uppercase"):
            _check_argb(bad, "probe")  # type: ignore[arg-type]
    for bad in ("1F4E78", "#1F4E7", "#gggggg", None):
        with pytest.raises(StyleDefinitionError, match="six-digit"):
            _check_hex(bad, "probe")  # type: ignore[arg-type]


# --- sheet names ---------------------------------------------------------------


def test_the_contract_sheet_names_are_valid() -> None:
    check_sheet_names(SHEET_ORDER)


def test_an_unusable_sheet_name_is_refused() -> None:
    from enum import StrEnum

    class Bad(StrEnum):
        BLANK = "   "
        LONG = "x" * 32
        BRACKET = "a[b]"
        SLASH = "a/b"

    for member in Bad:
        with pytest.raises(SheetDefinitionError):
            check_sheet_names((member,))


def test_a_repeated_sheet_name_is_refused() -> None:
    with pytest.raises(SheetDefinitionError, match="declared twice"):
        check_sheet_names((SheetId.SUMMARY, SheetId.SUMMARY))


# --- immutability and equality -------------------------------------------------


def test_the_policy_copies_its_caller_sequences() -> None:
    formats = [OutputFormat.JSON]
    recipients = ["a@example.com"]
    bands = [Decimal(0), Decimal(1)]
    policy = ReportPolicy(
        formats=formats, recipients=recipients,
        variance_bands=bands, streaming_row_threshold=5,
    )
    formats.append(OutputFormat.HTML)
    recipients.append("b@example.com")
    bands.append(Decimal(9))
    assert policy.formats == (OutputFormat.JSON,)
    assert policy.recipients == ("a@example.com",)
    assert policy.variance_bands == (Decimal(0), Decimal(1))


def test_the_policy_cannot_be_mutated() -> None:
    policy = make_policy()
    with pytest.raises(FrozenInstanceError):
        policy.formats = ()  # type: ignore[misc]
    for collection in (policy.formats, policy.recipients, policy.variance_bands):
        with pytest.raises(AttributeError):
            collection.append(None)  # type: ignore[attr-defined]


def test_equal_policies_compare_equal() -> None:
    assert make_policy() == make_policy()
    assert make_policy() != make_policy(streaming_row_threshold=1)


def test_the_policy_declares_no_locale_or_time_field() -> None:
    import dataclasses

    names = {f.name for f in dataclasses.fields(ReportPolicy)}
    assert names == {
        "formats", "recipients", "variance_bands", "streaming_row_threshold"
    }
    for forbidden in ("locale", "timezone", "tz", "now", "today", "date", "encoding"):
        assert not any(forbidden in name for name in names)


def test_an_unvalidated_config_with_a_bad_format_is_refused() -> None:
    """``model_construct`` bypasses pydantic, so the policy checks too."""
    config = ReportConfig.model_construct(
        formats=["pdf"], recipients=[], variance_bands=[0.0], streaming_row_threshold=10
    )
    with pytest.raises(UnsupportedFormatError, match="outside the authorized set"):
        policy_from_config(config)


def test_the_sample_ladder_renders_as_the_documented_bounds() -> None:
    from las.report import render_decimal

    policy = policy_from_config(load_config(SAMPLES_DIR / "config.yaml").report)
    assert [render_decimal(band) for band in policy.variance_bands] == ["0", "1", "5"]


def test_a_bound_with_real_precision_keeps_it() -> None:
    from las.report import render_decimal

    policy = policy_from_config(ReportConfig(variance_bands=[0.0, 0.25, 1.5]))
    assert [render_decimal(band) for band in policy.variance_bands] == ["0", "0.25", "1.5"]
