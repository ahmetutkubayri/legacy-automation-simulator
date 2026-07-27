"""T11 cross-format consistency: one logical feed, matched from CSV and XLSX."""

from __future__ import annotations

from decimal import Decimal  # noqa: E402  (kept beside its single use below)
from pathlib import Path

import pytest
from openpyxl import Workbook

from las.ingest import FieldSpec, IngestSchema, read_source
from las.match import BatchMatchResult, match_validated
from las.validate import ValidationPolicy, validate_ingest_result
from match_helpers import NEAR_TWIN_NAME, make_pair

HEADERS = ["record_id", "external_ref", "amount", "unit", "period", "Reported By"]

SCHEMA = IngestSchema(
    fields=(
        FieldSpec("amount"),
        FieldSpec("external_ref", required=False),
        FieldSpec("period"),
        FieldSpec("record_id"),
        FieldSpec("unit", required=False),
    )
)
#: Built together from one bound: matching rejects a pair that disagrees.
INDEX, MATCH_POLICY = make_pair()
VALIDATION_POLICY = ValidationPolicy(
    required_fields=frozenset({"record_id", "amount", "period"}),
    record_id_pattern=r"^OPR-\d{8}$",
    external_ref_pattern=r"^EXT-\d{5}$",
    amount_min=Decimal("0.0"),
    amount_max=Decimal("1000000.0"),
    duplicate_key_fields=("record_id",),
)

ROWS = [
    ["OPR-00000001", "EXT-10000", "10.50", "EA", "2026-01", "Demo Account 000000"],
    ["OPR-00000002", "", "20.25", "KG", "2026-02", NEAR_TWIN_NAME],
    ["OPR-00000003", "EXT-90000", "30.00", "EA", "2026-03", "Unlisted Source 1"],
    ["OPR-00000004", "", "40.00", "EA", "2026-04", "Demo Account 000002"],
    ["OPR-00000005", "", "50.00", "EA", "2026-05", ""],
]


def write_pair(tmp_path: Path) -> tuple[Path, Path]:
    csv_path = tmp_path / "probe.csv"
    lines = [",".join(HEADERS)]
    for row in ROWS:
        lines.append(",".join(row))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(HEADERS)
    for row in ROWS:
        sheet.append([cell if cell != "" else None for cell in row])
    xlsx_path = tmp_path / "probe.xlsx"
    workbook.save(xlsx_path)
    workbook.close()
    return csv_path, xlsx_path


def match_both(tmp_path: Path) -> tuple[BatchMatchResult, BatchMatchResult]:
    csv_path, xlsx_path = write_pair(tmp_path)
    results = []
    for path in (csv_path, xlsx_path):
        ingested = read_source(path, SCHEMA)
        validated = validate_ingest_result(ingested, VALIDATION_POLICY)
        results.append(match_validated(validated, INDEX, MATCH_POLICY))
    return results[0], results[1]


def shape(result: BatchMatchResult) -> list[tuple]:
    """Everything except format-specific metadata."""
    return [
        (
            d.provenance.source_row,
            d.outcome.value,
            d.method.value,
            d.reason,
            d.account_ref,
            d.external_ref,
            None if d.score is None else d.score.basis_points,
            tuple((c.account_ref, c.score.basis_points) for c in d.candidates),
        )
        for d in result.decisions
    ]


def test_identical_outcomes_methods_and_accounts(tmp_path: Path) -> None:
    from_csv, from_xlsx = match_both(tmp_path)
    assert [d.outcome for d in from_csv.decisions] == [d.outcome for d in from_xlsx.decisions]
    assert [d.method for d in from_csv.decisions] == [d.method for d in from_xlsx.decisions]
    assert [d.account_ref for d in from_csv.decisions] == [
        d.account_ref for d in from_xlsx.decisions
    ]


def test_identical_scores_and_candidate_ordering(tmp_path: Path) -> None:
    from_csv, from_xlsx = match_both(tmp_path)
    assert shape(from_csv) == shape(from_xlsx)


def test_equivalent_summaries(tmp_path: Path) -> None:
    from_csv, from_xlsx = match_both(tmp_path)
    assert from_csv.summary == from_xlsx.summary


def test_every_outcome_kind_is_exercised(tmp_path: Path) -> None:
    """The pair covers exact link, ambiguous tie, unmatched, name, ineligible."""
    from_csv, _ = match_both(tmp_path)
    outcomes = [d.outcome.value for d in from_csv.decisions]
    assert outcomes == ["exact", "needs_review", "unmatched", "exact", "ineligible"]
    assert [d.method.value for d in from_csv.decisions] == [
        "external_reference",
        "fuzzy_name",
        "fuzzy_name",
        "exact_name",
        "none",
    ]


def test_provenance_differs_only_in_the_worksheet(tmp_path: Path) -> None:
    from_csv, from_xlsx = match_both(tmp_path)
    assert from_csv.worksheet is None
    assert from_xlsx.worksheet == "records"
    assert from_csv.source == "probe.csv"
    assert from_xlsx.source == "probe.xlsx"
    for csv_decision, xlsx_decision in zip(
        from_csv.decisions, from_xlsx.decisions, strict=True
    ):
        assert csv_decision.provenance.source_row == xlsx_decision.provenance.source_row
        assert csv_decision.provenance.worksheet is None
        assert xlsx_decision.provenance.worksheet == "records"


def test_an_empty_csv_field_and_an_absent_cell_behave_alike(tmp_path: Path) -> None:
    from_csv, from_xlsx = match_both(tmp_path)
    # Row 6 carries no reference and no name in either format.
    assert from_csv.decisions[4].outcome.value == "ineligible"
    assert from_xlsx.decisions[4].outcome.value == "ineligible"


@pytest.mark.parametrize("run", range(3))
def test_repeated_cross_format_runs_are_equal(tmp_path: Path, run: int) -> None:
    from_csv, from_xlsx = match_both(tmp_path)
    assert shape(from_csv) == shape(from_xlsx)
