"""Deterministic cross-feed reconciliation over ingested, validated and matched records.

Depends only on the standard library, ``las.ingest``, ``las.validate``,
``las.match``, and the project's configuration models. It knows nothing of the
simulator, the driver, reporting, the pipeline or the CLI, and it never reads a
file, consults a clock, or uses randomness.

What this layer decides
-----------------------
Whether, for each account and period, what the feeds reported adds up to what
the system ledger holds — aggregating both feeds as one ``FEED`` side against
one ``SYSTEM`` side, comparing only quantities that share a unit, and banding
the exact decimal difference against the configured variance ladder.

The system side arrives as :class:`SystemLedgerRow`, a neutral immutable
data-transfer object carrying only the fields reconciliation needs, with its
account reference already resolved. T14 will build those rows from the public
driver ``ExtractedRow`` values and the existing master index; this package
never holds a driver or simulator object and performs no matching of its own.

What it deliberately leaves alone
---------------------------------
Entity matching, unit conversion, report generation, pipeline orchestration and
CLI behaviour all belong elsewhere. Nothing here revisits a matching verdict,
approves a row routed to review, repairs a value, converts between units, or
removes a duplicate: every input row is accounted for exactly once, and every
group carries exactly one outcome and one stable reason.
"""

from las.reconcile.engine import reconcile
from las.reconcile.errors import (
    DuplicateFeedBatchError,
    DuplicateInputIdentityError,
    InputContractError,
    LedgerRowError,
    MatchDecisionContractError,
    ReconcileError,
    ReconcileExecutionError,
    ReconcilePolicyError,
    RowAlignmentError,
    VarianceBandError,
)
from las.reconcile.grouping import (
    ACCEPTED_OUTCOMES,
    check_alignment,
    check_batches,
    check_system_rows,
    exact_difference,
    exact_sum,
    feed_contributions,
    system_contributions,
    totals_by_unit,
)
from las.reconcile.models import (
    MATCH_REASONS,
    MESSAGE_BY_REASON,
    OUTCOME_RANK,
    REASON_INVALID_AMOUNT,
    REASON_INVALID_PERIOD,
    REASON_INVALID_UNIT,
    REASON_MATCH_INELIGIBLE,
    REASON_MATCH_NEEDS_REVIEW,
    REASON_MATCH_UNMATCHED,
    REASON_MISSING_IN_FEED,
    REASON_MISSING_IN_SYSTEM,
    REASON_MULTIPLE_UNITS_ON_ONE_SIDE,
    REASON_OUTSIDE_VARIANCE_BANDS,
    REASON_TOTALS_EQUAL,
    REASON_UNIT_DIFFERS_ACROSS_SIDES,
    REASON_WITHIN_VARIANCE_BAND,
    SIDE_RANK,
    VALIDATION_REASONS,
    BatchReconcileResult,
    Contribution,
    FeedBatch,
    GroupKey,
    GroupResult,
    ReconcileOutcome,
    ReconcileSummary,
    Side,
    SideAggregate,
    SystemLedgerRow,
    UnresolvedRow,
    summarize,
)
from las.reconcile.policy import (
    GROUP_KEY_FIELDS,
    REQUIRED_FIRST_BAND,
    ReconcilePolicy,
    policy_from_config,
    to_decimal_bound,
)

__all__ = [
    "ACCEPTED_OUTCOMES",
    "GROUP_KEY_FIELDS",
    "MATCH_REASONS",
    "MESSAGE_BY_REASON",
    "OUTCOME_RANK",
    "REASON_INVALID_AMOUNT",
    "REASON_INVALID_PERIOD",
    "REASON_INVALID_UNIT",
    "REASON_MATCH_INELIGIBLE",
    "REASON_MATCH_NEEDS_REVIEW",
    "REASON_MATCH_UNMATCHED",
    "REASON_MISSING_IN_FEED",
    "REASON_MISSING_IN_SYSTEM",
    "REASON_MULTIPLE_UNITS_ON_ONE_SIDE",
    "REASON_OUTSIDE_VARIANCE_BANDS",
    "REASON_TOTALS_EQUAL",
    "REASON_UNIT_DIFFERS_ACROSS_SIDES",
    "REASON_WITHIN_VARIANCE_BAND",
    "REQUIRED_FIRST_BAND",
    "SIDE_RANK",
    "VALIDATION_REASONS",
    "BatchReconcileResult",
    "Contribution",
    "DuplicateFeedBatchError",
    "DuplicateInputIdentityError",
    "FeedBatch",
    "GroupKey",
    "GroupResult",
    "InputContractError",
    "LedgerRowError",
    "MatchDecisionContractError",
    "ReconcileError",
    "ReconcileExecutionError",
    "ReconcileOutcome",
    "ReconcilePolicy",
    "ReconcilePolicyError",
    "ReconcileSummary",
    "RowAlignmentError",
    "Side",
    "SideAggregate",
    "SystemLedgerRow",
    "UnresolvedRow",
    "VarianceBandError",
    "check_alignment",
    "check_batches",
    "check_system_rows",
    "exact_difference",
    "exact_sum",
    "feed_contributions",
    "policy_from_config",
    "reconcile",
    "summarize",
    "system_contributions",
    "to_decimal_bound",
    "totals_by_unit",
]
