"""T13 tables: sheet membership, column values, blanks, and row order."""

from __future__ import annotations

import random

from las.reconcile import (
    REASON_INVALID_AMOUNT,
    REASON_MATCH_NEEDS_REVIEW,
    BatchReconcileResult,
    ReconcileOutcome,
    Side,
    summarize,
)
from las.report import (
    ACCOUNTING_METRICS,
    GROUP_COLUMN_NAMES,
    GROUP_REASON_ORDER,
    OUTCOME_DISPLAY_ORDER,
    REVIEW_QUEUE_COLUMN_NAMES,
    UNIT_SEPARATOR,
    UNRESOLVED_REASON_ORDER,
    SheetId,
    band_counts,
    build_report,
    build_summary,
    exceptions_table,
    reconciliation_table,
    review_queue_table,
    summary_table,
)
from report_helpers import aggregate, golden_result, group, make_policy

POLICY = make_policy()
RESULT = golden_result()


def column(table, row_index: int, name: str) -> str | None:
    return table.rows[row_index].texts[table.headers.index(name)]


# --- membership ----------------------------------------------------------------


def test_reconciliation_holds_every_group_exactly_once() -> None:
    table = reconciliation_table(RESULT, POLICY)
    assert table.row_count == len(RESULT.groups)
    keys = [(column(table, i, "account_ref"), column(table, i, "period"))
            for i in range(table.row_count)]
    assert len(keys) == len(set(keys))


def test_exceptions_holds_only_the_four_unsettled_outcomes() -> None:
    table = exceptions_table(RESULT, POLICY)
    outcomes = {column(table, i, "outcome") for i in range(table.row_count)}
    assert outcomes == {
        "mismatch", "unit_mismatch", "missing_in_feed", "missing_in_system"
    }
    assert "balanced" not in outcomes
    assert "within_tolerance" not in outcomes


def test_exceptions_is_a_filter_not_a_re_sort() -> None:
    """An account reads the same way down either sheet."""
    everything = reconciliation_table(RESULT, POLICY)
    exceptions = exceptions_table(RESULT, POLICY)
    full_keys = [
        (column(everything, i, "account_ref"), column(everything, i, "period"))
        for i in range(everything.row_count)
    ]
    exception_keys = [
        (column(exceptions, i, "account_ref"), column(exceptions, i, "period"))
        for i in range(exceptions.row_count)
    ]
    assert exception_keys == [key for key in full_keys if key in set(exception_keys)]


def test_the_review_queue_holds_every_unresolved_row_whatever_stopped_it() -> None:
    table = review_queue_table(RESULT, POLICY)
    assert table.row_count == len(RESULT.unresolved)
    reasons = {column(table, i, "unresolved_reason") for i in range(table.row_count)}
    assert reasons == set(UNRESOLVED_REASON_ORDER)


def test_the_review_queue_is_not_limited_to_needs_review() -> None:
    table = review_queue_table(RESULT, POLICY)
    reasons = [column(table, i, "unresolved_reason") for i in range(table.row_count)]
    assert REASON_MATCH_NEEDS_REVIEW in reasons
    assert REASON_INVALID_AMOUNT in reasons


# --- columns -------------------------------------------------------------------


def test_the_group_sheets_use_the_contract_headers_in_order() -> None:
    for table in (reconciliation_table(RESULT, POLICY), exceptions_table(RESULT, POLICY)):
        assert table.headers == GROUP_COLUMN_NAMES


def test_the_review_queue_uses_the_contract_headers_in_order() -> None:
    assert review_queue_table(RESULT, POLICY).headers == REVIEW_QUEUE_COLUMN_NAMES


def test_a_compared_group_carries_its_exact_values() -> None:
    table = reconciliation_table(RESULT, POLICY)
    assert column(table, 1, "outcome") == "within_tolerance"
    assert column(table, 1, "feed_total") == "10.50"
    assert column(table, 1, "system_total") == "10.00"
    assert column(table, 1, "signed_difference") == "0.50"
    assert column(table, 1, "absolute_difference") == "0.50"
    assert column(table, 1, "variance_band_upper") == "1"
    assert column(table, 1, "feed_contribution_count") == "1"
    assert column(table, 1, "system_contribution_count") == "1"


def test_a_unit_mismatch_group_publishes_no_variance() -> None:
    table = reconciliation_table(RESULT, POLICY)
    row = next(
        i for i in range(table.row_count) if column(table, i, "outcome") == "unit_mismatch"
    )
    for name in ("signed_difference", "absolute_difference", "variance_band_upper"):
        assert column(table, row, name) is None
    # The per-unit evidence still stands.
    assert column(table, row, "feed_units") == "EA"
    assert column(table, row, "system_units") == "KG"


def test_a_missing_side_publishes_no_variance_and_no_units() -> None:
    table = reconciliation_table(RESULT, POLICY)
    row = next(
        i for i in range(table.row_count)
        if column(table, i, "outcome") == "missing_in_feed"
    )
    assert column(table, row, "feed_units") is None
    assert column(table, row, "feed_total") is None
    assert column(table, row, "feed_contribution_count") == "0"
    for name in ("signed_difference", "absolute_difference", "variance_band_upper"):
        assert column(table, row, name) is None


def test_a_blank_is_never_rendered_as_zero() -> None:
    table = reconciliation_table(RESULT, POLICY)
    for index in range(table.row_count):
        if column(table, index, "outcome") in ("unit_mismatch", "missing_in_feed"):
            assert column(table, index, "signed_difference") != "0"
            assert column(table, index, "signed_difference") is None


def test_multiple_units_on_one_side_are_sorted_and_joined() -> None:
    mixed = group(
        "ACC-100000", "2026-01", ReconcileOutcome.UNIT_MISMATCH,
        "multiple_units_on_one_side",
        aggregate(Side.FEED, ("1.00", "KG"), ("2.00", "EA"), ("3.00", "BOX")),
        aggregate(Side.SYSTEM, ("4.00", "EA")),
    )
    result = BatchReconcileResult(
        groups=(mixed,), unresolved=(),
        summary=summarize((mixed,), (), feed_rows_in=3, system_rows_in=1),
    )
    table = reconciliation_table(result, POLICY)
    assert column(table, 0, "feed_units") == UNIT_SEPARATOR.join(("BOX", "EA", "KG"))
    assert column(table, 0, "feed_total") is None  # never summed across units


def test_the_review_queue_leaves_unavailable_columns_blank() -> None:
    table = review_queue_table(RESULT, POLICY)
    for name in ("match_method", "external_ref", "period", "unit"):
        assert all(column(table, i, name) is None for i in range(table.row_count))


def test_the_review_queue_keeps_only_safe_provenance() -> None:
    table = review_queue_table(RESULT, POLICY)
    assert column(table, 0, "source") == "alpha.csv"
    assert column(table, 0, "worksheet") is None
    assert column(table, 0, "source_row") == "2"
    assert column(table, 0, "row_index") == "0"
    assert column(table, 0, "match_outcome") == "needs_review"


# --- fills ---------------------------------------------------------------------


def test_every_reconciliation_row_carries_exactly_its_outcome_fill() -> None:
    table = reconciliation_table(RESULT, POLICY)
    for index, row in enumerate(table.rows):
        outcome = ReconcileOutcome(column(table, index, "outcome"))
        assert row.fill == POLICY.fill_for(outcome)


def test_no_other_sheet_carries_an_outcome_fill() -> None:
    summary = build_summary(RESULT, POLICY)
    for table in (
        summary_table(summary, POLICY),
        review_queue_table(RESULT, POLICY),
    ):
        assert all(row.fill is None for row in table.rows)


def test_exceptions_rows_carry_the_same_model_fill_as_reconciliation() -> None:
    """The model records each row's outcome fill; only the workbook renderer
    decides which sheets paint it, which ``test_report_xlsx`` asserts."""
    everything = {
        (column(t := reconciliation_table(RESULT, POLICY), i, "account_ref"),
         column(t, i, "period")): t.rows[i].fill
        for i in range(reconciliation_table(RESULT, POLICY).row_count)
    }
    exceptions = exceptions_table(RESULT, POLICY)
    for index, row in enumerate(exceptions.rows):
        key = (column(exceptions, index, "account_ref"),
               column(exceptions, index, "period"))
        assert row.fill == everything[key]


# --- the summary ---------------------------------------------------------------


def test_the_summary_states_every_vocabulary_value_including_zeros() -> None:
    summary = build_summary(RESULT, POLICY)
    assert [key for key, _ in summary.group_outcomes] == [
        o.value for o in OUTCOME_DISPLAY_ORDER
    ]
    assert [key for key, _ in summary.group_reasons] == list(GROUP_REASON_ORDER)
    assert [key for key, _ in summary.unresolved_reasons] == list(
        UNRESOLVED_REASON_ORDER
    )
    assert any(count == 0 for _, count in summary.group_reasons)


def test_the_summary_accounting_uses_the_contract_metrics_in_order() -> None:
    summary = build_summary(RESULT, POLICY)
    assert list(summary.accounting) == list(ACCOUNTING_METRICS)


def test_the_summary_counts_agree_with_the_result() -> None:
    summary = build_summary(RESULT, POLICY)
    assert summary.accounting["group_count"] == len(RESULT.groups)
    assert summary.accounting["review_queue_count"] == len(RESULT.unresolved)
    assert summary.accounting["exception_group_count"] == exceptions_table(
        RESULT, POLICY
    ).row_count
    assert summary.accounting["unresolved_rows"] == len(RESULT.unresolved)


def test_band_counts_cover_every_configured_bound_ascending() -> None:
    counts = band_counts(RESULT, POLICY)
    assert [bound for bound, _ in counts] == ["0", "1", "5"]
    assert dict(counts)["0"] == 1  # the one balanced group
    assert dict(counts)["1"] == 1  # the one within-tolerance group
    assert dict(counts)["5"] == 0  # stated even though it is empty


def test_the_summary_table_follows_the_documented_section_order() -> None:
    table = summary_table(build_summary(RESULT, POLICY), POLICY)
    assert table.headers == ("section", "metric", "value")
    sections = []
    for row in table.rows:
        section = row.texts[0]
        if not sections or sections[-1] != section:
            sections.append(section)
    assert sections == [
        "metadata", "accounting", "group_outcome",
        "group_reason", "unresolved_reason", "variance_band",
    ]


def test_the_metadata_section_states_the_schema_and_recipients() -> None:
    table = summary_table(build_summary(RESULT, POLICY), POLICY)
    assert table.rows[0].texts == ("metadata", "schema_version", "1.0")
    assert table.rows[1].texts == ("metadata", "recipients", "ops-team@example.com")


def test_the_variance_band_metrics_use_the_documented_prefix() -> None:
    table = summary_table(build_summary(RESULT, POLICY), POLICY)
    bands = [row.texts[1] for row in table.rows if row.texts[0] == "variance_band"]
    assert bands == ["upper_bound:0", "upper_bound:1", "upper_bound:5"]


# --- determinism ---------------------------------------------------------------


def test_building_the_same_report_twice_gives_an_equal_model() -> None:
    assert build_report(RESULT, POLICY) == build_report(RESULT, POLICY)


def test_row_order_follows_t12_and_not_the_outcome() -> None:
    table = reconciliation_table(RESULT, POLICY)
    keys = [(column(table, i, "account_ref"), column(table, i, "period"))
            for i in range(table.row_count)]
    assert keys == sorted(keys)


def test_shuffling_equivalent_groups_does_not_change_the_rendered_keys() -> None:
    """T12 hands over a sorted sequence; a report of the same groups reads alike."""
    groups = list(RESULT.groups)
    shuffler = random.Random(3)
    baseline = [
        (column(reconciliation_table(RESULT, POLICY), i, "account_ref"),
         column(reconciliation_table(RESULT, POLICY), i, "period"))
        for i in range(len(groups))
    ]
    for _ in range(5):
        shuffler.shuffle(groups)
        ordered = tuple(sorted(groups, key=lambda g: g.key.sort_key))
        result = BatchReconcileResult(
            groups=ordered, unresolved=RESULT.unresolved,
            summary=summarize(
                ordered, RESULT.unresolved,
                feed_rows_in=RESULT.summary.feed_rows_in,
                system_rows_in=RESULT.summary.system_rows_in,
            ),
        )
        table = reconciliation_table(result, POLICY)
        assert [
            (column(table, i, "account_ref"), column(table, i, "period"))
            for i in range(table.row_count)
        ] == baseline


def test_an_empty_result_produces_empty_detail_and_a_full_summary() -> None:
    empty = BatchReconcileResult(
        groups=(), unresolved=(),
        summary=summarize((), (), feed_rows_in=0, system_rows_in=0),
    )
    report = build_report(empty, POLICY)
    assert report.reconciliation.row_count == 0
    assert report.exceptions.row_count == 0
    assert report.review_queue.row_count == 0
    # Every vocabulary row is still stated, all at zero.
    assert report.summary_table.row_count == 2 + len(ACCOUNTING_METRICS) + 6 + 7 + 6 + 3
    assert all(count == 0 for _, count in report.summary.group_outcomes)


def test_the_model_exposes_its_four_tables_in_sheet_order() -> None:
    report = build_report(RESULT, POLICY)
    assert [table.sheet for table in report.tables] == list(POLICY.sheet_order)
    assert report.table_for(SheetId.REVIEW_QUEUE) is report.review_queue
