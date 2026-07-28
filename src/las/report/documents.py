"""The two summary documents: JSON and HTML.

Both are *summaries*. Neither carries reconciliation rows or unresolved rows —
those live in the workbook, which is the artifact built to be read row by row.
Duplicating hundreds of rows into a JSON payload and an HTML page would make
three things to keep in step instead of one.

Both are byte-deterministic. The encodings, newlines, indentation, key order
and separators are all fixed here rather than left to a library default, so two
runs of the same report produce identical bytes on any platform.

Named ``documents`` rather than ``json`` and ``html`` because a module called
``json.py`` inside this package would shadow the standard library module it
needs.
"""

from __future__ import annotations

import json
from html import escape

from las.reconcile import ReconcileOutcome
from las.report.errors import RenderError
from las.report.models import ReportModel, ReportSummary
from las.report.policy import (
    DOCUMENT_TITLE,
    HTML_BORDER,
    HTML_FONT_FAMILY,
    HTML_HEADER_BACKGROUND,
    HTML_HEADER_TEXT,
    HTML_PAGE_BACKGROUND,
    HTML_TEXT,
    ReportPolicy,
)

#: One line ending, everywhere, on every platform.
NEWLINE = "\n"

#: Section headings for the HTML summary, in their fixed order.
HTML_SECTIONS: tuple[tuple[str, str], ...] = (
    ("accounting", "Accounting"),
    ("group_outcomes", "Group outcomes"),
    ("group_reasons", "Group reasons"),
    ("unresolved_reasons", "Unresolved reasons"),
    ("variance_bands", "Variance bands"),
)


def summary_payload(summary: ReportSummary) -> dict:
    """The JSON document as ordinary Python objects, in contract key order.

    Counts are integers; every decimal is canonical text. JSON has no exact
    decimal type, so a band bound written as a JSON number would be a float the
    moment anything parsed it — which is the one thing this project spends most
    of its effort avoiding.
    """
    return {
        "schema_version": summary.schema_version,
        "recipients": list(summary.recipients),
        "accounting": dict(summary.accounting),
        "group_outcomes": [
            {"outcome": key, "count": count} for key, count in summary.group_outcomes
        ],
        "group_reasons": [
            {"reason_code": key, "count": count} for key, count in summary.group_reasons
        ],
        "unresolved_reasons": [
            {"reason_code": key, "count": count}
            for key, count in summary.unresolved_reasons
        ],
        "variance_bands": [
            {"upper_bound": bound, "count": count}
            for bound, count in summary.variance_bands
        ],
    }


def render_json(report: ReportModel, policy: ReportPolicy) -> bytes:
    """UTF-8, no BOM, two-space indent, LF newlines, one final newline.

    ``default`` is deliberately not supplied: an object the encoder does not
    already understand is a bug in the report model, and silently stringifying
    it would put an unreviewed representation into a published artifact.
    """
    del policy  # the payload is fully determined by the model
    try:
        text = json.dumps(
            summary_payload(report.summary),
            indent=2,
            ensure_ascii=False,
            separators=(",", ": "),
            sort_keys=False,
        )
    except (TypeError, ValueError) as exc:
        raise RenderError(
            f"json rendering raised {type(exc).__name__}",
            operation="render_json",
            output_format="json",
        ) from None
    return (text.replace("\r\n", NEWLINE) + NEWLINE).encode("utf-8")


def _style_sheet() -> str:
    outcome_rules = NEWLINE.join(
        f"    tr.outcome-{outcome.value} td {{ background: "
        f"{background}; }}"
        for outcome, background in (
            (outcome, _html_background(outcome)) for outcome in ReconcileOutcome
        )
    )
    return NEWLINE.join(
        (
            "    body { font-family: " + HTML_FONT_FAMILY + "; color: "
            + HTML_TEXT + "; background: " + HTML_PAGE_BACKGROUND + "; }",
            "    table { border-collapse: collapse; margin-bottom: 1.5rem; }",
            "    th, td { border: 1px solid " + HTML_BORDER
            + "; padding: 0.3rem 0.6rem; text-align: left; }",
            "    th { background: " + HTML_HEADER_BACKGROUND + "; color: "
            + HTML_HEADER_TEXT + "; }",
            outcome_rules,
        )
    )


def _html_background(outcome: ReconcileOutcome) -> str:
    from las.report.policy import HTML_OUTCOME_BACKGROUND

    return HTML_OUTCOME_BACKGROUND[outcome]


def _table(caption: str, headers: tuple[str, str], rows: list[tuple[str, str, str]]) -> str:
    """One HTML table. Every cell is escaped; no value is ever markup."""
    lines = [
        f"  <h2>{escape(caption)}</h2>",
        "  <table>",
        "    <thead>",
        f"      <tr><th>{escape(headers[0])}</th><th>{escape(headers[1])}</th></tr>",
        "    </thead>",
        "    <tbody>",
    ]
    for key, value, css_class in rows:
        attribute = f' class="{escape(css_class)}"' if css_class else ""
        lines.append(
            f"      <tr{attribute}><td>{escape(key)}</td><td>{escape(value)}</td></tr>"
        )
    lines.extend(("    </tbody>", "  </table>"))
    return NEWLINE.join(lines)


def _section_rows(summary: ReportSummary, name: str) -> list[tuple[str, str, str]]:
    if name == "accounting":
        return [(key, f"{value:d}", "") for key, value in summary.accounting.items()]
    if name == "group_outcomes":
        return [
            (key, f"{count:d}", f"outcome-{key}") for key, count in summary.group_outcomes
        ]
    if name == "group_reasons":
        return [(key, f"{count:d}", "") for key, count in summary.group_reasons]
    if name == "unresolved_reasons":
        return [(key, f"{count:d}", "") for key, count in summary.unresolved_reasons]
    return [
        (f"upper_bound:{bound}", f"{count:d}", "")
        for bound, count in summary.variance_bands
    ]


def render_html(report: ReportModel, policy: ReportPolicy) -> bytes:
    """A self-contained HTML5 summary: one style element, nothing external.

    No script, no image, no external stylesheet, no embedded resource, no
    link off the page and no current-date content — so the document renders
    identically offline, today and in a year.
    """
    del policy  # the document is fully determined by the model
    summary = report.summary
    try:
        parts = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            f"  <title>{escape(DOCUMENT_TITLE)}</title>",
            "  <style>",
            _style_sheet(),
            "  </style>",
            "</head>",
            "<body>",
            f"  <h1>{escape(DOCUMENT_TITLE)}</h1>",
            f"  <p>Recipients: {escape(summary.recipients_text)}</p>",
        ]
        for name, caption in HTML_SECTIONS:
            parts.append(_table(caption, ("metric", "value"), _section_rows(summary, name)))
        parts.extend(("</body>", "</html>"))
    except (TypeError, ValueError, KeyError) as exc:
        raise RenderError(
            f"html rendering raised {type(exc).__name__}",
            operation="render_html",
            output_format="html",
        ) from None
    return (NEWLINE.join(parts) + NEWLINE).encode("utf-8")
