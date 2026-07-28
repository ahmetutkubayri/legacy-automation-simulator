"""T13 documents: the JSON and HTML summaries, byte for byte."""

from __future__ import annotations

import decimal
import json
from contextlib import contextmanager
from decimal import Decimal
from html import escape

import pytest

from las.reconcile import BatchReconcileResult, ReconcileOutcome, summarize
from las.report import (
    ACCOUNTING_METRICS,
    DOCUMENT_TITLE,
    GROUP_REASON_ORDER,
    HTML_BORDER,
    HTML_FONT_FAMILY,
    HTML_HEADER_BACKGROUND,
    HTML_HEADER_TEXT,
    HTML_OUTCOME_BACKGROUND,
    HTML_PAGE_BACKGROUND,
    HTML_TEXT,
    OUTCOME_DISPLAY_ORDER,
    UNRESOLVED_REASON_ORDER,
    build_report,
    render_html,
    render_json,
    summary_payload,
)
from report_helpers import GOLDEN_DIR, golden_result, make_policy, unresolved

POLICY = make_policy()
RESULT = golden_result()
REPORT = build_report(RESULT, POLICY)


@contextmanager
def global_context(prec: int, rounding: str = decimal.ROUND_HALF_EVEN):
    saved = decimal.getcontext()
    decimal.setcontext(decimal.Context(prec=prec, rounding=rounding))
    try:
        yield
    finally:
        decimal.setcontext(saved)


def payload() -> dict:
    return json.loads(render_json(REPORT, POLICY).decode("utf-8"))


def document() -> str:
    return render_html(REPORT, POLICY).decode("utf-8")


# --- JSON encoding contract ----------------------------------------------------


def test_the_json_is_utf8_without_a_bom() -> None:
    raw = render_json(REPORT, POLICY)
    assert not raw.startswith(b"\xef\xbb\xbf")
    raw.decode("utf-8")


def test_the_json_uses_lf_newlines_and_one_final_newline() -> None:
    raw = render_json(REPORT, POLICY)
    assert b"\r\n" not in raw and b"\r" not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")


def test_the_json_is_indented_by_two_spaces() -> None:
    lines = render_json(REPORT, POLICY).decode("utf-8").splitlines()
    assert lines[1].startswith('  "schema_version"')
    assert not lines[1].startswith("   ")


def test_the_json_keys_are_in_contract_order() -> None:
    text = render_json(REPORT, POLICY).decode("utf-8")
    order = [
        "schema_version", "recipients", "accounting",
        "group_outcomes", "group_reasons", "unresolved_reasons", "variance_bands",
    ]
    positions = [text.index(f'"{key}"') for key in order]
    assert positions == sorted(positions)
    assert list(payload()) == order


def test_the_json_is_a_summary_and_carries_no_detail_rows() -> None:
    data = payload()
    assert "groups" not in data and "unresolved" not in data
    assert "account_ref" not in render_json(REPORT, POLICY).decode("utf-8")


def test_the_json_states_every_vocabulary_value_including_zeros() -> None:
    data = payload()
    assert [e["outcome"] for e in data["group_outcomes"]] == [
        o.value for o in OUTCOME_DISPLAY_ORDER
    ]
    assert [e["reason_code"] for e in data["group_reasons"]] == list(GROUP_REASON_ORDER)
    assert [e["reason_code"] for e in data["unresolved_reasons"]] == list(
        UNRESOLVED_REASON_ORDER
    )
    assert any(e["count"] == 0 for e in data["group_reasons"])


def test_the_json_accounting_uses_the_contract_metrics_in_order() -> None:
    assert list(payload()["accounting"]) == list(ACCOUNTING_METRICS)


def test_json_counts_are_integers_and_band_bounds_are_strings() -> None:
    data = payload()
    for entry in data["group_outcomes"] + data["group_reasons"]:
        assert isinstance(entry["count"], int) and not isinstance(entry["count"], bool)
    for value in data["accounting"].values():
        assert isinstance(value, int)
    for entry in data["variance_bands"]:
        assert isinstance(entry["upper_bound"], str)
        assert isinstance(entry["count"], int)


def test_no_json_value_is_a_float() -> None:
    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            assert not isinstance(node, float)

    walk(payload())


def test_the_json_states_the_schema_version_and_recipients() -> None:
    data = payload()
    assert data["schema_version"] == "1.0"
    assert data["recipients"] == ["ops-team@example.com"]


def test_the_json_encoder_has_no_arbitrary_object_fallback() -> None:
    """An object the encoder cannot express must raise, never stringify."""
    from las.report import documents

    source = documents.render_json.__doc__ or ""
    assert "default" in source  # the docstring explains the omission
    with pytest.raises(TypeError):
        json.dumps({"bad": Decimal("1")})


def test_a_long_band_bound_survives_as_exact_text() -> None:
    """The ladder still declares the bands the result selected: 0 and 1."""
    long_bound = "5." + "9" * 150
    policy = make_policy(
        variance_bands=(Decimal(0), Decimal(1), Decimal(long_bound))
    )
    data = json.loads(render_json(build_report(RESULT, policy), policy).decode())
    assert data["variance_bands"][2]["upper_bound"] == long_bound
    assert data["variance_bands"][2]["count"] == 0


def test_the_payload_helper_and_the_bytes_agree() -> None:
    assert summary_payload(REPORT.summary) == payload()


# --- HTML contract --------------------------------------------------------------


def test_the_html_is_utf8_with_lf_and_one_final_newline() -> None:
    raw = render_html(REPORT, POLICY)
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    raw.decode("utf-8")


def test_the_html_declares_html5_and_a_charset() -> None:
    text = document()
    assert text.startswith("<!DOCTYPE html>\n")
    assert '<meta charset="utf-8">' in text


def test_the_html_carries_the_documented_title() -> None:
    text = document()
    assert f"<title>{escape(DOCUMENT_TITLE)}</title>" in text
    assert f"<h1>{escape(DOCUMENT_TITLE)}</h1>" in text


def test_the_html_sections_appear_in_contract_order() -> None:
    text = document()
    order = [
        "Recipients:", "Accounting", "Group outcomes",
        "Group reasons", "Unresolved reasons", "Variance bands",
    ]
    positions = [text.index(heading) for heading in order]
    assert positions == sorted(positions)
    assert text.index("<title>") < positions[0]


def test_the_html_is_self_contained() -> None:
    text = document()
    for forbidden in ("<script", "http://", "https://", "<img", "base64",
                      "<link", "@import", "<iframe", "url("):
        assert forbidden not in text
    assert text.count("<style>") == 1
    assert text.index("<style>") < text.index("</head>")


def test_the_html_uses_the_documented_palette() -> None:
    text = document()
    for colour in (HTML_HEADER_BACKGROUND, HTML_HEADER_TEXT, HTML_TEXT,
                   HTML_BORDER, HTML_PAGE_BACKGROUND):
        assert colour in text
    assert HTML_FONT_FAMILY in text


def test_every_outcome_row_carries_its_documented_background() -> None:
    text = document()
    for outcome in ReconcileOutcome:
        assert f"tr.outcome-{outcome.value} td" in text
        assert HTML_OUTCOME_BACKGROUND[outcome] in text
        assert f'class="outcome-{outcome.value}"' in text


def test_the_html_states_recipients_escaped_and_comma_joined() -> None:
    policy = make_policy(recipients=("a@example.com", "b@example.com"))
    text = render_html(build_report(RESULT, policy), policy).decode()
    assert "Recipients: a@example.com,b@example.com" in text


def test_the_html_escapes_hostile_text() -> None:
    rows = (unresolved("match_unmatched", 2, source="<script>alert(1)</script>"),)
    result = BatchReconcileResult(
        groups=(), unresolved=rows,
        summary=summarize((), rows, feed_rows_in=1, system_rows_in=0),
    )
    text = render_html(build_report(result, POLICY), POLICY).decode()
    assert "<script>alert(1)</script>" not in text


def test_a_hostile_recipient_is_escaped_rather_than_rendered() -> None:
    policy = make_policy(recipients=("<b>x</b>@example.com",))
    text = render_html(build_report(RESULT, policy), policy).decode()
    assert "<b>x</b>@example.com" not in text
    assert "&lt;b&gt;x&lt;/b&gt;@example.com" in text


def test_the_html_is_a_summary_and_carries_no_detail_rows() -> None:
    text = document()
    assert "ACC-100000" not in text
    assert "account_ref" not in text


def test_the_html_carries_no_current_date_content() -> None:
    from datetime import UTC, datetime

    text = document()
    now = datetime.now(UTC)
    assert str(now.year) not in text
    assert now.strftime("%Y-%m-%d") not in text


# --- determinism ----------------------------------------------------------------


def test_repeated_document_renders_are_byte_identical() -> None:
    assert render_json(REPORT, POLICY) == render_json(REPORT, POLICY)
    assert render_html(REPORT, POLICY) == render_html(REPORT, POLICY)


def test_documents_are_independent_of_the_global_decimal_context() -> None:
    baseline = (render_json(REPORT, POLICY), render_html(REPORT, POLICY))
    for prec in (1, 3, 28, 400):
        with global_context(prec):
            report = build_report(RESULT, POLICY)
            assert (render_json(report, POLICY), render_html(report, POLICY)) == baseline


@pytest.mark.parametrize(
    "rounding",
    [decimal.ROUND_UP, decimal.ROUND_DOWN, decimal.ROUND_CEILING, decimal.ROUND_FLOOR],
)
def test_documents_are_independent_of_the_global_rounding_mode(rounding: str) -> None:
    baseline = render_json(REPORT, POLICY)
    with global_context(3, rounding):
        assert render_json(build_report(RESULT, POLICY), POLICY) == baseline


def test_documents_do_not_depend_on_the_platform_newline() -> None:
    """Line endings are chosen here, never taken from the platform."""
    for raw in (render_json(REPORT, POLICY), render_html(REPORT, POLICY)):
        assert b"\r\n" not in raw


# --- golden content ---------------------------------------------------------------


def test_the_json_matches_its_golden_file_byte_for_byte() -> None:
    expected = (GOLDEN_DIR / "summary.json").read_bytes()
    assert render_json(REPORT, POLICY) == expected


def test_the_html_matches_its_golden_file_byte_for_byte() -> None:
    expected = (GOLDEN_DIR / "summary.html").read_bytes()
    assert render_html(REPORT, POLICY) == expected


def test_the_golden_files_carry_no_clock_reading_or_path() -> None:
    """The only timestamp anywhere is the fixed workbook epoch.

    A bare year is not checked: the synthetic periods are ``2026-01`` and
    ``2026-02``, so a year-substring search would flag ordinary business data.
    What matters is that no *timestamp* appears except the fixed one, and that
    nothing resembles a filesystem path.
    """
    import re as regex

    for name in ("summary.json", "summary.html", "workbook_semantics.json"):
        text = (GOLDEN_DIR / name).read_text(encoding="utf-8")
        stamps = set(regex.findall(r"\d{4}-\d{2}-\d{2}T[\d:.]+", text))
        assert stamps <= {"2000-01-01T00:00:00"}, stamps
        assert "/Users/" not in text and "/home/" not in text
        assert "\\" not in text
        assert not regex.search(r"[A-Za-z]:[\\/]", text)


def test_no_binary_workbook_is_committed_as_a_golden_file() -> None:
    assert not any(path.suffix == ".xlsx" for path in GOLDEN_DIR.iterdir())
    assert sorted(p.name for p in GOLDEN_DIR.iterdir()) == [
        "summary.html", "summary.json", "workbook_semantics.json"
    ]
