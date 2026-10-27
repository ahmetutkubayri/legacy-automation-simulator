"""The immutable parameter set one matching pass runs against.

Where the thresholds come from
------------------------------
:class:`las.config.MatchConfig` defines fractional thresholds. Both are
converted once through ``Decimal(str(value))``, scaled by ``10000`` and
rounded half up to whole basis points. The effective integer cutoffs define
accept, review and below-review bands; no score below the effective accept
cutoff is silently matched. Extra decimal places can round up or down, and
a tiny positive fraction such as ``0.00004`` becomes zero basis points.

So the repository specifies **two bands, not a margin**. Quantization is part
of the configured-threshold conversion, not a later adjustment to a decision.

What the repository does *not* define
-------------------------------------
There is no ambiguity margin and no maximum candidate count anywhere in the
plan, the configuration models or the sample configuration. Neither is invented
here:

* Ties are resolved by the plan's own rule — "nothing below the acceptance
  threshold is silently matched" — combined with the refusal to break a tie
  arbitrarily. A tie for best place, at any score, goes to ``NEEDS_REVIEW``.
  No margin value is fabricated to describe "nearly tied".
* ``max_candidates`` defaults to ``None``, meaning no cap. When a caller sets
  one it trims the *presented* candidate tuple only, after the decision has
  already been computed over every candidate.

Name-length safety cap
----------------------
``max_name_length`` bounds the work one comparison can cost. The repository
states no name-length limit anywhere — the longest name the contract produces
is twenty-nine characters — so this is documented as a safety cap rather than
a matching rule, and it lives here so that public behaviour is controlled by
an explicit, immutable policy value rather than by a constant hidden inside
normalization. The same bound governs source names and master account names —
and that is verified rather than assumed: :class:`las.match.index.MasterIndex`
retains the cap it was built with, and every matching entry point refuses a
pair whose two caps differ.

Source-name location
--------------------
The feeds carry their source name in a column that is not a canonical field, so
T9 retains it in ``IngestedRow.extras`` under its normalized header:
``reported by`` in feed_alpha and ``src name`` in feed_beta. Those two keys are
the fixture-contract default, tried in order, and are overridable per call.

When a row carries a usable name under *more than one* of those keys, the keys
are not ranked against each other. Nothing in the plan or the configuration
defines a precedence between them, so inventing one would silently pick a
winner. Values that normalize to the same key collapse to a single matching
input; two genuinely different names make the row ineligible instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from las.config import MatchConfig
from las.match.errors import MatchPolicyError
from las.match.normalize import DEFAULT_MAX_NAME_LENGTH, name_length_fault
from las.match.scoring import SCORE_MAX_BP, SCORE_MIN_BP

#: Normalized extras keys holding a source name, from the fixture contract.
DEFAULT_SOURCE_NAME_KEYS: tuple[str, ...] = ("reported by", "src name")


def to_basis_points(fraction: float, name: str) -> int:
    """Convert a configured 0–1 fraction to whole basis points, rounding half up.

    Routed through ``Decimal(str(...))`` so the configured ``0.87`` becomes
    ``8700`` rather than whatever the nearest binary double rounds to.
    Values beyond four decimal places are quantized, not retained exactly.
    """
    try:
        scaled = (Decimal(str(fraction)) * SCORE_MAX_BP).to_integral_value(ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise MatchPolicyError(f"{name} is not a usable threshold") from None
    value = int(scaled)
    if not SCORE_MIN_BP <= value <= SCORE_MAX_BP:
        raise MatchPolicyError(f"{name} must fall between 0 and 1 inclusive")
    return value


@dataclass(frozen=True)
class MatchPolicy:
    """Immutable, deeply frozen matching parameters."""

    accept_bp: int
    review_bp: int
    source_name_keys: tuple[str, ...] = DEFAULT_SOURCE_NAME_KEYS
    max_candidates: int | None = None
    max_name_length: int = DEFAULT_MAX_NAME_LENGTH

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_name_keys", tuple(self.source_name_keys))

        for name in ("accept_bp", "review_bp"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise MatchPolicyError(f"{name} must be an integer basis-point value")
            if not SCORE_MIN_BP <= value <= SCORE_MAX_BP:
                raise MatchPolicyError(
                    f"{name} must lie between {SCORE_MIN_BP} and {SCORE_MAX_BP}"
                )
        if self.review_bp > self.accept_bp:
            raise MatchPolicyError("review_bp must not exceed accept_bp")

        if not self.source_name_keys:
            raise MatchPolicyError("at least one source-name key must be declared")
        if any(not key or not key.strip() for key in self.source_name_keys):
            raise MatchPolicyError("a source-name key must not be blank")
        repeated = sorted(
            {key for key in self.source_name_keys if self.source_name_keys.count(key) > 1}
        )
        if repeated:
            raise MatchPolicyError(f"source-name keys repeat: {', '.join(repeated)}")

        # One shared rule, so the policy and the master index cannot come to
        # disagree about what a usable cap is.
        fault = name_length_fault(self.max_name_length)
        if fault is not None:
            raise MatchPolicyError(fault)

        if self.max_candidates is not None:
            if isinstance(self.max_candidates, bool) or not isinstance(
                self.max_candidates, int
            ):
                raise MatchPolicyError("max_candidates must be an integer or None")
            if self.max_candidates < 1:
                raise MatchPolicyError("max_candidates must be at least one")

    def band_of(self, basis_points: int) -> str:
        """Which configured band a score falls into: accept, review, or below."""
        if basis_points >= self.accept_bp:
            return "accept"
        if basis_points >= self.review_bp:
            return "review"
        return "below"


def policy_from_config(
    config: MatchConfig,
    *,
    source_name_keys: Iterable[str] = DEFAULT_SOURCE_NAME_KEYS,
    max_candidates: int | None = None,
    max_name_length: int = DEFAULT_MAX_NAME_LENGTH,
) -> MatchPolicy:
    """Build a policy from the configured thresholds."""
    return MatchPolicy(
        accept_bp=to_basis_points(config.fuzzy_threshold, "fuzzy_threshold"),
        review_bp=to_basis_points(config.review_threshold, "review_threshold"),
        source_name_keys=tuple(source_name_keys),
        max_candidates=max_candidates,
        max_name_length=max_name_length,
    )
