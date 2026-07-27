"""Deterministic entity matching over ingested and validated records.

Depends only on the standard library, ``las.ingest``, ``las.validate``, and the
project's configuration models. It knows nothing of the simulator, the driver,
or any later stage, and it never reads a file, consults a clock, or uses
randomness.

What this layer decides
-----------------------
Which synthetic account, if any, each source row refers to — by resolving an
exact external-reference link, then an exact normalized name, then a bounded
integer name score judged against the configured thresholds.

What it deliberately leaves alone
---------------------------------
Cross-feed reconciliation, amount aggregation, unit-mismatch resolution and
variance banding all belong to a later stage. Nothing here corrects an
identifier, repairs a name, filters a row, or writes anything: every input row
produces exactly one decision, in input order, with its provenance intact.
"""

from las.match.engine import match_row, match_rows, match_validated
from las.match.errors import (
    NAME_LENGTH_MISMATCH_MESSAGE,
    ConflictingLinkError,
    DanglingLinkError,
    DuplicateAccountError,
    MasterIndexError,
    MatchError,
    MatchExecutionError,
    MatchPolicyError,
    NameLengthMismatchError,
    OverlongNameError,
    ScoreRangeError,
)
from las.match.index import MasterAccount, MasterIndex, build_master_index
from las.match.models import (
    PERFECT_SCORE,
    REASON_AMBIGUOUS_EXACT_NAMES,
    REASON_AMBIGUOUS_TIE,
    REASON_BELOW_ACCEPTANCE,
    REASON_BELOW_REVIEW,
    REASON_CONFLICTING_SOURCE_NAMES,
    REASON_EXACT_NAME,
    REASON_EXACT_REFERENCE,
    REASON_FUZZY_ACCEPTED,
    REASON_MESSAGES,
    REASON_NO_CANDIDATES,
    REASON_NO_MATCHING_INPUT,
    REASON_REFERENCE_UNKNOWN,
    BatchMatchResult,
    Candidate,
    MatchDecision,
    MatchMethod,
    MatchOutcome,
    MatchSummary,
    Score,
    summarize,
)
from las.match.normalize import (
    DEFAULT_MAX_NAME_LENGTH,
    MAX_CONFIGURABLE_NAME_LENGTH,
    is_comparable,
    normalize_name,
)
from las.match.policy import (
    DEFAULT_SOURCE_NAME_KEYS,
    MatchPolicy,
    policy_from_config,
    to_basis_points,
)
from las.match.scoring import SCORE_MAX_BP, SCORE_MIN_BP, edit_distance, similarity_bp

__all__ = [
    "DEFAULT_SOURCE_NAME_KEYS",
    "DEFAULT_MAX_NAME_LENGTH",
    "MAX_CONFIGURABLE_NAME_LENGTH",
    "NAME_LENGTH_MISMATCH_MESSAGE",
    "PERFECT_SCORE",
    "REASON_AMBIGUOUS_EXACT_NAMES",
    "REASON_AMBIGUOUS_TIE",
    "REASON_BELOW_ACCEPTANCE",
    "REASON_BELOW_REVIEW",
    "REASON_CONFLICTING_SOURCE_NAMES",
    "REASON_EXACT_NAME",
    "REASON_EXACT_REFERENCE",
    "REASON_FUZZY_ACCEPTED",
    "REASON_MESSAGES",
    "REASON_NO_CANDIDATES",
    "REASON_NO_MATCHING_INPUT",
    "REASON_REFERENCE_UNKNOWN",
    "SCORE_MAX_BP",
    "SCORE_MIN_BP",
    "BatchMatchResult",
    "Candidate",
    "ConflictingLinkError",
    "DanglingLinkError",
    "DuplicateAccountError",
    "MasterAccount",
    "MasterIndex",
    "MasterIndexError",
    "MatchDecision",
    "MatchError",
    "MatchExecutionError",
    "MatchMethod",
    "MatchOutcome",
    "MatchPolicy",
    "MatchPolicyError",
    "MatchSummary",
    "NameLengthMismatchError",
    "OverlongNameError",
    "Score",
    "ScoreRangeError",
    "build_master_index",
    "edit_distance",
    "is_comparable",
    "match_row",
    "match_rows",
    "match_validated",
    "normalize_name",
    "policy_from_config",
    "similarity_bp",
    "summarize",
    "to_basis_points",
]
