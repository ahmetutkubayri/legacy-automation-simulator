# Reporting Contract (T13)

The public contract for reporting over reconciliation results. The development
plan's T13 clause states the formats; everything detailed — sheet membership,
column order, colours, schema and decimal rendering — is defined here, and
`tests/test_report_docs.py` asserts that the implementation agrees with this
document rather than merely resembling it.

Nothing in this contract is configurable. `ReportConfig` supplies four values
(formats, recipients, variance bands, streaming threshold); the layout is fixed
so that two runs of this project produce the same report shape.

## Output ownership

T13 renders entirely in memory. It does not choose an output directory, derive
a filename, write a file, inspect the working directory or a home directory,
send mail, or open a socket. T14 chooses filenames and writes bytes to
`RunConfig.output_dir`.

Each artifact carries only its format, a fixed media type, a fixed suffix, and
immutable bytes — never a path or a filename.

| Format | Media type | Suffix |
|---|---|---|
| XLSX | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | `.xlsx` |
| JSON | `application/json` | `.json` |
| HTML | `text/html` | `.html` |

Artifacts are returned in `ReportConfig.formats` order; the sample
configuration's order is XLSX, JSON, HTML.

`recipients` is report metadata only. It appears on the Summary sheet, in the
JSON summary and in the HTML summary. No mail is ever sent, no message is
constructed, and no recipient is inferred. The existing `example.com`
restriction in `ReportConfig` is unchanged.

## Workbook

Exactly four sheets, in this order, with case-sensitive names:

1. `Summary`
2. `Exceptions`
3. `Reconciliation`
4. `Review Queue`

No additional visible or hidden sheet. No charts, images, formulas, macros,
external links, pivot tables, merged cells, icon sets, data bars,
conditional-formatting formulas, autofilters, freeze panes, custom widths, or
hidden rows and columns. The fills below are static, chosen after T12 has
already fixed the outcome; nothing recalculates inside a spreadsheet.

### Summary

Columns: `section`, `metric`, `value`.

Sections appear in this order, and no row is omitted for being zero:

| Section | Rows |
|---|---|
| `metadata` | `schema_version` → `1.0`; `recipients` → comma-joined in configured order |
| `accounting` | the eight metrics below, in order |
| `group_outcome` | every outcome, in display order |
| `group_reason` | every group reason, in T12 declaration order |
| `unresolved_reason` | every unresolved reason, in T12 declaration order |
| `variance_band` | one row per configured bound, ascending |

Accounting metrics, in order: `feed_rows_input`, `system_rows_input`,
`feed_contributions`, `system_contributions`, `unresolved_rows`, `group_count`,
`exception_group_count`, `review_queue_count`.

Outcome display order is `balanced`, `within_tolerance`, `mismatch`,
`unit_mismatch`, `missing_in_feed`, `missing_in_system`. This is deliberately
**not** `ReconcileOutcome`'s declaration order, which runs decision-precedence
first: a reader wants the settled outcomes before the unsettled ones.

Variance-band metrics read `upper_bound:<canonical-decimal>` — for the sample
ladder, `upper_bound:0`, `upper_bound:1`, `upper_bound:5` — and the value is the
number of groups assigned to that band.

### Reconciliation

Every group exactly once, in T12's own order: account reference, then period.
Never re-sorted by outcome or variance.

Columns, in order: `account_ref`, `period`, `outcome`, `reason_code`,
`feed_units`, `system_units`, `feed_total`, `system_total`,
`signed_difference`, `absolute_difference`, `variance_band_upper`,
`feed_contribution_count`, `system_contribution_count`.

`feed_units` and `system_units` are the side's exact validated unit tokens,
sorted, joined with `|`, and blank when the side is absent. A total appears
only where T12 published one. For missing-side and unit-mismatch groups the
variance columns are blank cells: the report never manufactures a zero and
never computes a total or a variance of its own.

### Exceptions

Exactly the groups whose outcome is `MISMATCH`, `UNIT_MISMATCH`,
`MISSING_IN_FEED` or `MISSING_IN_SYSTEM`. Never `BALANCED` or
`WITHIN_TOLERANCE`, and never an unresolved row. Same columns as
Reconciliation, in the same order, and the same T12 row order — a filter, not a
re-sort. Each group appears once.

### Review Queue

Every unresolved feed row exactly once, in T12's order. The sheet name is not a
filter: it holds all six unresolved categories — `match_needs_review`,
`match_unmatched`, `match_ineligible`, `invalid_amount`, `invalid_unit`,
`invalid_period`.

Columns, in order: `source`, `worksheet`, `source_row`, `row_index`,
`unresolved_reason`, `match_outcome`, `match_method`, `account_ref`,
`external_ref`, `period`, `unit`.

`match_method`, `external_ref`, `period` and `unit` are blank today, because
T12's unresolved record does not carry them. They are declared so the sheet's
shape is fixed by this contract rather than by what one release exposes. The
sheet never carries source names, account names, raw amount text, extras, full
validation issues, complete source rows, paths, or exception text.

## Styles

### Header, on all four sheets

Fill `FF1F4E78`, font colour `FFFFFFFF`, bold. Workbook default font family and
size. No borders, italics, font changes, number formats, alignment rules, row
heights or column widths.

### Outcome fills, on `Reconciliation` only

| Outcome | ARGB |
|---|---|
| `BALANCED` | `FFE2F0D9` |
| `WITHIN_TOLERANCE` | `FFFFF2CC` |
| `MISMATCH` | `FFF4CCCC` |
| `UNIT_MISMATCH` | `FFFCE5CD` |
| `MISSING_IN_FEED` | `FFDDEBF7` |
| `MISSING_IN_SYSTEM` | `FFE4DFEC` |

Applied across the complete data row. Precedence is trivial: the header row
takes the header style, and each data row takes exactly one outcome fill.
Because every group carries exactly one T12 outcome, overlap is impossible.
Summary, Exceptions and Review Queue receive only the header style. A blank
cell inside a filled row stays blank — the fill never creates a zero or a
placeholder.

## Decimal rendering

One canonical renderer, used by all three formats: plain base-10 fixed point,
no exponent notation, no thousands separator, no locale, no float, no rounding,
no quantization, and no dependence on the global decimal context. Trailing
fractional zeros the value carries are preserved. Any zero — `0`, `-0`,
`0E-100` — renders as exactly `0`.

| Value | Rendered |
|---|---|
| `Decimal("12")` | `12` |
| `Decimal("12.3400")` | `12.3400` |
| `Decimal("-2.5")` | `-2.5` |
| `Decimal("-0")` | `0` |
| `Decimal("0E-100")` | `0` |

A **configured variance bound** is normalised before rendering: a band written
as `1.0` in YAML arrives as the float `1.0` and becomes `Decimal("1.0")` only
because that is how a float prints, so its fractional trailing zeros are
dropped and the sample ladder reads `upper_bound:0`, `upper_bound:1`,
`upper_bound:5`. An *amount* is never normalised — `12.3400` came from source
text that really did carry those zeros.

XLSX decimal cells are stored as **text**, not numeric cells: Excel's numeric
type is a binary double and cannot hold the 101-, 150- or 300-significant-digit
values reconciliation can produce. JSON decimal values are **strings** for the
same reason.

## Formula-injection safety

Every report-controlled string is written to the workbook as an explicit text
cell, including any beginning `=`, `+`, `-` or `@`, and including negative
decimal strings. The original characters are never prefixed, trimmed or
rewritten — canonical identifiers stay canonical — and the cell's data type is
set to text so openpyxl cannot classify it as a formula. Loading a rendered
workbook with `data_only=False` finds zero formula cells.

HTML escapes all text. JSON uses the standard encoder with no `default`
fallback, so an object the encoder does not understand raises instead of being
silently stringified.

## JSON summary

A summary only: no reconciliation rows, no unresolved rows. UTF-8, no BOM, LF
newlines, two-space indentation, `ensure_ascii=False`, one final newline. Keys
in this order: `schema_version`, `recipients`, `accounting`, `group_outcomes`,
`group_reasons`, `unresolved_reasons`, `variance_bands`.

```json
{
  "schema_version": "1.0",
  "recipients": ["ops-team@example.com"],
  "accounting": {
    "feed_rows_input": 0,
    "system_rows_input": 0,
    "feed_contributions": 0,
    "system_contributions": 0,
    "unresolved_rows": 0,
    "group_count": 0,
    "exception_group_count": 0,
    "review_queue_count": 0
  },
  "group_outcomes": [{"outcome": "balanced", "count": 0}],
  "group_reasons": [{"reason_code": "example_reason", "count": 0}],
  "unresolved_reasons": [{"reason_code": "example_reason", "count": 0}],
  "variance_bands": [{"upper_bound": "0", "count": 0}]
}
```

The single entries above illustrate shape. At runtime each array carries every
public vocabulary value or configured band, in the order defined above,
including zero counts. All counts are JSON integers; all band bounds are
canonical decimal strings.

## HTML summary

Also a summary only. Sections in this order: document title, recipients,
accounting table, group-outcome table, group-reason table, unresolved-reason
table, variance-band table.

Title: `Legacy Automation Simulator — Reconciliation Summary`.

UTF-8, LF newlines, one final newline, HTML5 doctype, `<meta charset="utf-8">`.
No JavaScript, no external CSS, no images, no external links, no embedded
base64 resources. All CSS lives in one `<style>` element in `<head>`.

Palette: header background `#1F4E78`, header text `#FFFFFF`, normal text
`#222222`, table border `#B7B7B7`, page background `#FFFFFF`. Font family
`Arial, sans-serif`.

Group-outcome rows carry the workbook fills without the alpha byte: `balanced`
`#E2F0D9`, `within_tolerance` `#FFF2CC`, `mismatch` `#F4CCCC`, `unit_mismatch`
`#FCE5CD`, `missing_in_feed` `#DDEBF7`, `missing_in_system` `#E4DFEC`.

No dynamic CSS, animation, script, external font or current-time content.
Recipients are displayed escaped, comma-separated, in configured order.

## Streaming

`ReportConfig.streaming_row_threshold` affects only workbook construction. The
workbook is built in write-only mode when any **detail** sheet — Exceptions,
Reconciliation or Review Queue — has a data-row count greater than or equal to
the threshold. Summary never triggers streaming, because its size is bounded by
the vocabulary rather than the data.

The two paths must be indistinguishable after reload: same sheet names and
order, dimensions, values, cell data types, header styles, outcome fills, zero
formulas, and fixed workbook properties.

## Workbook metadata

Fixed, and derived from nothing about the machine: creator and lastModifiedBy
`Legacy Automation Simulator`, title `Reconciliation Report`, subject
`Deterministic reconciliation output`, description
`Synthetic portfolio reconciliation report`, created and modified
`2000-01-01 00:00:00`. Never the current time, a username, a host name, an
absolute path, or a source filename.

## Determinism

JSON and HTML are **byte-identical** across repeated renders. XLSX is
**semantically identical** across repeated renders and between the standard and
streaming paths — raw ZIP bytes are not part of this contract and byte-identical
XLSX is not claimed.

No output depends on mapping insertion order, the current date or time, the
timezone, the locale, the global decimal context, the operating system's newline
convention, the working directory, filesystem ordering, or Python object
identity.

## Input verification

The whole T12 contract is checked before any table exists, and nothing is
repaired. A malformed result raises a fixed-message report-contract error.

Verified: summary group and unresolved counts match their sequences; outcome,
reason, unresolved and contribution counts all agree with the detail; the
accounting balances; group keys are unique; unresolved physical identities are
unique; every group has one outcome and one reason; `UNIT_MISMATCH` and
missing-side groups publish no difference and no band; `BALANCED` differs by
exactly zero; `WITHIN_TOLERANCE` differs by a nonzero banded amount;
`MISMATCH` selects no band; compared groups share exactly one unit and have a
total on each side; and every selected band is one the policy declares.

The validator re-reads T12's invariants — it never recomputes T12's arithmetic.
A second implementation of the same sum would eventually disagree with the
first, and there would be no way to know which was right.
