"""The matching pass: pure, deterministic, and total-ordered.

Reads rows and a master index, produces exactly one decision per input row, and
writes to nothing. No I/O, no clock, no randomness, no environment lookups, and
no mutation of a row, a value mapping, an extras mapping, a provenance record,
the master index, or the policy.

Evidence precedence
-------------------
Strongest first, and an earlier step's success is never revisited by a later
one:

1. **exact external-reference link** — the reference resolves, through the
   master link table, to exactly one account;
2. **exact normalized name** — the source name normalizes to a key naming
   exactly one account (several accounts means ``NEEDS_REVIEW``, never a pick);
3. **fuzzy name scoring** — every comparable account is scored and the
   configured bands decide.

Choosing among several source-name columns
------------------------------------------
A policy may declare more than one place a source name can live. The keys are
tried in *policy* order, never in the order the row's extras happen to be
built, so mapping insertion order can never decide anything. A value that is
blank or unsafe is skipped rather than allowed to mask a usable one, and values
that normalize to the same key collapse to a single matching input.

Two genuinely different usable names is a conflict. Nothing in the plan or the
configuration ranks one key above another, so picking a winner would be an
invention; the row becomes ``INELIGIBLE`` with reason
``conflicting_source_names`` instead. Neither name is repeated in the decision.

Name fallback after an unusable reference is authorized by the fixture
contract, not assumed: the generator's ``ambiguous_matches`` defect is defined
as "rows with no usable reference whose source name is nearly equidistant from
two deliberately near-duplicate accounts", which only produces an ambiguous
outcome if name matching runs when the reference cannot be used.

Band decisions
--------------
Over all candidates, never a truncated view:

* a best score below the review threshold → ``UNMATCHED``, **even when two
  candidates tie there**. A tie only means something when the tied score is
  good enough to be a candidate at all; calling two equally poor matches
  "ambiguous" would claim a confidence the evidence does not support;
* otherwise a tie for best → ``NEEDS_REVIEW``, because the plan forbids
  silently matching and nothing may break a tie by ordering;
* otherwise a unique best at or above the acceptance threshold → ``FUZZY``;
* otherwise a unique best inside ``[review, accept)`` → ``NEEDS_REVIEW``.

One name-length bound, verified
-------------------------------
The master index and the policy each carry a ``max_name_length``, and every
entry point here refuses to run unless the two are equal. They have to be: the
index normalized its account names under its own cap, and a source name is
normalized under the policy's, so a mismatch would compare keys produced by two
different rules. Silently taking the smaller value would drop master names the
index had already accepted; silently taking the larger would admit source names
the index would have rejected. Neither is a decision this layer may make, so it
raises :class:`las.match.errors.NameLengthMismatchError` before reading a row —
including for an empty batch, where there is no row to reveal the problem
later. With the bounds equal, one normalizer under one cap governs master
indexing, exact-name lookup, source-name eligibility and fuzzy scoring alike.

Candidate ordering
------------------
Presentation order is score descending, then normalized account name ascending,
then ``account_ref`` ascending. It is computed from account data alone, so it
cannot depend on master row order or on mapping iteration. Any candidate cap is
applied *after* the decision, so trimming the list can never change the verdict.
"""

from __future__ import annotations

from collections.abc import Sequence

from las.ingest import IngestedRow
from las.match.errors import (
    NAME_LENGTH_MISMATCH_MESSAGE,
    MatchExecutionError,
    NameLengthMismatchError,
)
from las.match.index import MasterIndex
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
    REASON_NO_CANDIDATES,
    REASON_NO_MATCHING_INPUT,
    BatchMatchResult,
    Candidate,
    MatchDecision,
    MatchMethod,
    MatchOutcome,
    Score,
    summarize,
)
from las.match.normalize import normalize_name
from las.match.policy import MatchPolicy
from las.match.scoring import similarity_bp

FIELD_EXTERNAL_REF = "external_ref"

#: One fixed operation label for the contract check, so the failure reads
#: identically whichever entry point the caller happened to use.
CONTRACT_OPERATION = "verify_match_contract"


def _require_one_bound(index: MasterIndex, policy: MatchPolicy) -> None:
    """Refuse to match unless the index and the policy share one name cap.

    Called first by every entry point, before any row is read and before any
    input is copied, so a mismatched pair fails without partial work.
    """
    if index.max_name_length != policy.max_name_length:
        raise NameLengthMismatchError(
            NAME_LENGTH_MISMATCH_MESSAGE, operation=CONTRACT_OPERATION
        )


def _resolve_source_name(
    row: IngestedRow, policy: MatchPolicy
) -> tuple[str | None, bool]:
    """Return ``(normalized name, conflicting)`` for this row.

    Iterates the policy's key tuple, so the row's extras insertion order is
    irrelevant. Unusable values are skipped, equivalent values collapse, and
    two distinct usable names report a conflict rather than a winner.
    """
    distinct: list[str] = []
    for key in policy.source_name_keys:
        normalized = normalize_name(
            row.extras.get(key), max_length=policy.max_name_length
        )
        if normalized is None:
            continue
        if normalized not in distinct:
            distinct.append(normalized)
    if not distinct:
        return None, False
    if len(distinct) > 1:
        return None, True
    return distinct[0], False


def _score_against(normalized: str, index: MasterIndex) -> list[tuple[int, str, str]]:
    """(score, normalized account name, account_ref) for every comparable account."""
    try:
        return [
            (
                similarity_bp(normalized, account.normalized_name),
                account.normalized_name,
                account.account_ref,
            )
            for account in index.comparable_accounts()
            if account.normalized_name is not None
        ]
    except Exception as exc:
        # Only the exception's type name travels: its message could quote a name.
        raise MatchExecutionError(
            f"scoring raised {type(exc).__name__} while comparing candidates",
            operation="score_candidates",
        ) from None


def _ranked(scored: Sequence[tuple[int, str, str]]) -> tuple[tuple[int, str, str], ...]:
    """Total order: score descending, then name ascending, then reference."""
    return tuple(sorted(scored, key=lambda entry: (-entry[0], entry[1], entry[2])))


def _present(
    ranked: Sequence[tuple[int, str, str]], policy: MatchPolicy
) -> tuple[Candidate, ...]:
    """Trim to the presentation cap, after the decision has been made."""
    limited = ranked if policy.max_candidates is None else ranked[: policy.max_candidates]
    return tuple(Candidate(account_ref=ref, score=Score(bp)) for bp, _, ref in limited)


def match_row(
    row: IngestedRow, row_index: int, index: MasterIndex, policy: MatchPolicy
) -> MatchDecision:
    """Decide one row. Every path returns a decision; none returns ``None``."""
    _require_one_bound(index, policy)
    reference = row.values.get(FIELD_EXTERNAL_REF)

    def decide(**kwargs) -> MatchDecision:
        return MatchDecision(provenance=row.provenance, row_index=row_index, **kwargs)

    # 1. exact external-reference link.
    if reference:
        linked = index.resolve_link(reference)
        if linked is not None:
            return decide(
                outcome=MatchOutcome.EXACT,
                method=MatchMethod.EXTERNAL_REFERENCE,
                reason=REASON_EXACT_REFERENCE,
                account_ref=linked,
                external_ref=reference,
                score=PERFECT_SCORE,
                candidates=(Candidate(account_ref=linked, score=PERFECT_SCORE),),
            )

    # Source names are resolved only once the link has failed, so a conflict
    # between name columns can never disturb an exact reference match.
    normalized, conflicting = _resolve_source_name(row, policy)

    if conflicting:
        return decide(
            outcome=MatchOutcome.INELIGIBLE,
            method=MatchMethod.NONE,
            reason=REASON_CONFLICTING_SOURCE_NAMES,
            external_ref=reference or None,
        )

    # Nothing usable to match on at all.
    if normalized is None:
        return decide(
            outcome=MatchOutcome.INELIGIBLE,
            method=MatchMethod.NONE,
            reason=REASON_NO_MATCHING_INPUT,
            external_ref=reference or None,
        )

    # 2. exact normalized name.
    named = index.accounts_named(normalized)
    if len(named) == 1:
        return decide(
            outcome=MatchOutcome.EXACT,
            method=MatchMethod.EXACT_NAME,
            reason=REASON_EXACT_NAME,
            account_ref=named[0],
            external_ref=reference or None,
            score=PERFECT_SCORE,
            candidates=(Candidate(account_ref=named[0], score=PERFECT_SCORE),),
        )
    if len(named) > 1:
        candidates = _present(
            _ranked([(PERFECT_SCORE.basis_points, normalized, ref) for ref in named]), policy
        )
        return decide(
            outcome=MatchOutcome.NEEDS_REVIEW,
            method=MatchMethod.EXACT_NAME,
            reason=REASON_AMBIGUOUS_EXACT_NAMES,
            external_ref=reference or None,
            score=PERFECT_SCORE,
            candidates=candidates,
        )

    # 3. fuzzy name scoring, over every comparable account.
    ranked = _ranked(_score_against(normalized, index))
    if not ranked:
        return decide(
            outcome=MatchOutcome.UNMATCHED,
            method=MatchMethod.FUZZY_NAME,
            reason=REASON_NO_CANDIDATES,
            external_ref=reference or None,
        )

    candidates = _present(ranked, policy)
    best_bp = ranked[0][0]
    tied = len(ranked) > 1 and ranked[1][0] == best_bp
    band = policy.band_of(best_bp)

    if band == "below":
        # Checked before the tie: two equally poor candidates are still poor,
        # and calling that "ambiguous" would overstate the evidence.
        return decide(
            outcome=MatchOutcome.UNMATCHED,
            method=MatchMethod.FUZZY_NAME,
            reason=REASON_BELOW_REVIEW,
            external_ref=reference or None,
            score=Score(best_bp),
            candidates=candidates,
        )
    if tied:
        # Above the review threshold a tie is real, and is never broken by
        # ordering.
        return decide(
            outcome=MatchOutcome.NEEDS_REVIEW,
            method=MatchMethod.FUZZY_NAME,
            reason=REASON_AMBIGUOUS_TIE,
            external_ref=reference or None,
            score=Score(best_bp),
            candidates=candidates,
        )
    if band == "accept":
        return decide(
            outcome=MatchOutcome.FUZZY,
            method=MatchMethod.FUZZY_NAME,
            reason=REASON_FUZZY_ACCEPTED,
            account_ref=ranked[0][2],
            external_ref=reference or None,
            score=Score(best_bp),
            candidates=candidates,
        )
    return decide(
        outcome=MatchOutcome.NEEDS_REVIEW,
        method=MatchMethod.FUZZY_NAME,
        reason=REASON_BELOW_ACCEPTANCE,
        external_ref=reference or None,
        score=Score(best_bp),
        candidates=candidates,
    )


def match_rows(
    rows: Sequence[IngestedRow],
    index: MasterIndex,
    policy: MatchPolicy,
    *,
    source: str | None = None,
    worksheet: str | None = None,
) -> BatchMatchResult:
    """Match a sequence of ingested rows, filtering and reordering nothing."""
    # Checked here as well as in ``match_row``, because an empty batch never
    # reaches that function and must still reject a mismatched contract.
    _require_one_bound(index, policy)
    ordered = tuple(rows)
    decisions = tuple(
        match_row(row, position, index, policy) for position, row in enumerate(ordered)
    )
    return BatchMatchResult(
        source=source if source is not None else _batch_source(ordered),
        decisions=decisions,
        summary=summarize(decisions),
        worksheet=worksheet if worksheet is not None else _batch_worksheet(ordered),
    )


def match_validated(
    validation, index: MasterIndex, policy: MatchPolicy
) -> BatchMatchResult:
    """Match a T10 batch, keeping one decision per validated row.

    Validation state is consumed for its rows and provenance; a validation
    error on a field matching does not use — an amount, a unit, a record
    identifier — never makes a row ineligible on its own.
    """
    # Checked before the validation result is read at all, so a mismatched
    # contract fails without touching the caller's batch.
    _require_one_bound(index, policy)
    return match_rows(
        [validated.row for validated in validation.rows],
        index,
        policy,
        source=validation.source,
        worksheet=validation.worksheet,
    )


def _batch_source(rows: Sequence[IngestedRow]) -> str:
    return rows[0].provenance.source if rows else "unknown"


def _batch_worksheet(rows: Sequence[IngestedRow]) -> str | None:
    return rows[0].provenance.worksheet if rows else None
