"""Immutable result types for matching.

Outcomes and methods are named after the development plan's vocabulary: it
calls for "exact matching, fuzzy scoring with config thresholds, ``needs_review``
bucket", so ``NEEDS_REVIEW`` is the bucket's name here rather than a synonym.

What a decision may carry
-------------------------
Synthetic identifiers (``account_ref``, ``external_ref``) and a bounded integer
score are part of the public contract, so they travel. Account names, source
names, whole source rows, addresses, paths, timestamps and object identities do
not. Reason text comes from a fixed template table keyed by a stable code, so a
decision can explain itself without quoting the data behind it.

Deep immutability
-----------------
Frozen dataclasses are not enough on their own: a frozen field can still hold a
caller's live list. Sequences are coerced to tuples and mappings are copied and
then wrapped read-only, so neither the caller's container nor the returned
object can change afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from types import MappingProxyType

from las.ingest import RowProvenance
from las.match.errors import ScoreRangeError
from las.match.scoring import SCORE_MAX_BP, SCORE_MIN_BP


class MatchOutcome(StrEnum):
    """What matching concluded about one row."""

    #: A single account identified by unambiguous exact evidence.
    EXACT = "exact"
    #: A single account chosen by score, at or above the acceptance threshold.
    FUZZY = "fuzzy"
    #: Evidence exists but is not conclusive; a person should decide.
    NEEDS_REVIEW = "needs_review"
    #: Matching ran and found nothing good enough.
    UNMATCHED = "unmatched"
    #: Matching could not legitimately run on this row.
    INELIGIBLE = "ineligible"


class MatchMethod(StrEnum):
    """Which evidence produced the outcome."""

    EXTERNAL_REFERENCE = "external_reference"
    EXACT_NAME = "exact_name"
    FUZZY_NAME = "fuzzy_name"
    NONE = "none"


#: Stable reason codes. Part of the public contract, so never reworded.
REASON_EXACT_REFERENCE = "exact_reference_matched"
REASON_EXACT_NAME = "exact_name_matched"
REASON_FUZZY_ACCEPTED = "fuzzy_threshold_met"
REASON_BELOW_ACCEPTANCE = "below_acceptance_threshold"
REASON_BELOW_REVIEW = "below_review_threshold"
REASON_AMBIGUOUS_TIE = "ambiguous_tie"
REASON_AMBIGUOUS_EXACT_NAMES = "ambiguous_exact_names"
REASON_REFERENCE_UNKNOWN = "reference_not_in_master"
REASON_NO_MATCHING_INPUT = "no_usable_matching_input"
REASON_CONFLICTING_SOURCE_NAMES = "conflicting_source_names"
REASON_NO_CANDIDATES = "no_candidates_available"

#: Fixed message templates. None interpolates a name or a source value.
REASON_MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        REASON_EXACT_REFERENCE: "external reference resolved to one account",
        REASON_EXACT_NAME: "normalized name matched exactly one account",
        REASON_FUZZY_ACCEPTED: "best candidate met the acceptance threshold",
        REASON_BELOW_ACCEPTANCE: "best candidate is below the acceptance threshold",
        REASON_BELOW_REVIEW: "best candidate is below the review threshold",
        REASON_AMBIGUOUS_TIE: "two or more candidates share the best score",
        REASON_AMBIGUOUS_EXACT_NAMES: "the normalized name matches several accounts",
        REASON_REFERENCE_UNKNOWN: "external reference is absent from the master index",
        REASON_NO_MATCHING_INPUT: "no usable reference or name to match on",
        REASON_CONFLICTING_SOURCE_NAMES: (
            "the row carries two different usable source names"
        ),
        REASON_NO_CANDIDATES: "the master index offers no comparable account",
    }
)


@dataclass(frozen=True, order=True)
class Score:
    """A bounded integer similarity, in basis points from 0 to 10,000.

    Integer throughout, so equality and ordering are exact and no float
    tolerance is ever involved.
    """

    basis_points: int

    def __post_init__(self) -> None:
        if isinstance(self.basis_points, bool) or not isinstance(self.basis_points, int):
            raise ScoreRangeError("a score must be an integer basis-point value")
        if not SCORE_MIN_BP <= self.basis_points <= SCORE_MAX_BP:
            raise ScoreRangeError(
                f"a score must lie between {SCORE_MIN_BP} and {SCORE_MAX_BP} basis points"
            )

    @property
    def is_perfect(self) -> bool:
        return self.basis_points == SCORE_MAX_BP


#: The score an exact match carries, so exact and fuzzy compare on one scale.
PERFECT_SCORE = Score(SCORE_MAX_BP)


@dataclass(frozen=True)
class Candidate:
    """One account considered for a row, with its score.

    Carries the synthetic account reference and the score only. The account's
    name is used to rank candidates but is not exposed, so no name can leak
    through a result object.
    """

    account_ref: str
    score: Score

    def __post_init__(self) -> None:
        if not self.account_ref:
            raise ScoreRangeError("a candidate must name an account reference")


@dataclass(frozen=True)
class MatchDecision:
    """Exactly one decision per input row."""

    provenance: RowProvenance
    row_index: int
    outcome: MatchOutcome
    method: MatchMethod
    reason: str
    account_ref: str | None = None
    external_ref: str | None = None
    score: Score | None = None
    candidates: tuple[Candidate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if self.reason not in REASON_MESSAGES:
            raise ScoreRangeError(f"unknown match reason code '{self.reason}'")
        if self.row_index < 0:
            raise ScoreRangeError("row_index is zero-based and non-negative")

    @property
    def message(self) -> str:
        """The fixed template for this decision's reason."""
        return REASON_MESSAGES[self.reason]

    @property
    def is_matched(self) -> bool:
        return self.outcome in (MatchOutcome.EXACT, MatchOutcome.FUZZY)


@dataclass(frozen=True)
class MatchSummary:
    """Deterministic counts over one batch. Every mapping is read-only."""

    total_rows: int
    exact: int
    fuzzy: int
    needs_review: int
    unmatched: int
    ineligible: int
    by_method: Mapping[str, int] = dc_field(default_factory=lambda: MappingProxyType({}))
    by_reason: Mapping[str, int] = dc_field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name in ("by_method", "by_reason"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @property
    def matched(self) -> int:
        return self.exact + self.fuzzy


@dataclass(frozen=True)
class BatchMatchResult:
    """One decision per input row, in input order, plus deterministic counts."""

    source: str
    decisions: tuple[MatchDecision, ...]
    summary: MatchSummary
    worksheet: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decisions", tuple(self.decisions))

    @property
    def row_count(self) -> int:
        return len(self.decisions)

    def with_outcome(self, outcome: MatchOutcome) -> tuple[MatchDecision, ...]:
        return tuple(d for d in self.decisions if d.outcome is outcome)

    def with_reason(self, reason: str) -> tuple[MatchDecision, ...]:
        return tuple(d for d in self.decisions if d.reason == reason)


def summarize(decisions: Sequence[MatchDecision]) -> MatchSummary:
    """Counts whose mappings are ordered deterministically, not by insertion."""
    by_outcome = {
        outcome: sum(1 for d in decisions if d.outcome is outcome) for outcome in MatchOutcome
    }
    by_method = {
        method.value: sum(1 for d in decisions if d.method is method) for method in MatchMethod
    }
    present = {d.reason for d in decisions}
    by_reason = {
        reason: sum(1 for d in decisions if d.reason == reason) for reason in sorted(present)
    }
    return MatchSummary(
        total_rows=len(decisions),
        exact=by_outcome[MatchOutcome.EXACT],
        fuzzy=by_outcome[MatchOutcome.FUZZY],
        needs_review=by_outcome[MatchOutcome.NEEDS_REVIEW],
        unmatched=by_outcome[MatchOutcome.UNMATCHED],
        ineligible=by_outcome[MatchOutcome.INELIGIBLE],
        by_method=by_method,
        by_reason=by_reason,
    )
