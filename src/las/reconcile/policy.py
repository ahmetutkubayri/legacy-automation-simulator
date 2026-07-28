"""The immutable parameter set one reconciliation pass runs against.

Variance bands
--------------
``ReportConfig.variance_bands`` are **absolute amount-difference upper bounds**,
expressed in the shared validated unit of the group being reconciled. They are
not percentages: there is no denominator anywhere in this package and no
division is performed, so a group whose totals are zero raises no special case.

Each configured value is converted once through ``Decimal(str(value))``, so the
``1.0`` an author wrote becomes exactly ``1``, not the nearest binary double.

The ladder must start at exactly zero and ascend strictly. Reading it against
the committed ``[0.0, 1.0, 5.0]``:

===============================  ====================  ===================
absolute difference              outcome               selected band
===============================  ====================  ===================
``0``                            ``BALANCED``          ``0``
``0 < d <= 1``                   ``WITHIN_TOLERANCE``  ``1``
``1 < d <= 5``                   ``WITHIN_TOLERANCE``  ``5``
``d > 5``                        ``MISMATCH``          none
===============================  ====================  ===================

Every nonzero upper edge is **inclusive**: a difference of exactly ``1`` is
inside the ``1`` band, and only a difference strictly greater than the largest
configured bound is a mismatch. The zero band is inclusive in the same sense,
but since differences are absolute it can only ever hold an exact tie.

Grouping key
------------
Fixed, not configurable: account reference then period. It is stated here as
:data:`GROUP_KEY_FIELDS` so that one declared order governs both the key and
the order groups are reported in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from las.config import ReportConfig
from las.reconcile.errors import ReconcilePolicyError, VarianceBandError

#: The reconciliation group key, in its declared order. Unit is deliberately
#: not a component; see :class:`las.reconcile.models.GroupKey`.
GROUP_KEY_FIELDS: tuple[str, ...] = ("account_ref", "period")

#: The lowest band edge the ladder must start at, so that an exact tie always
#: lands in a band of its own rather than sharing one with small differences.
REQUIRED_FIRST_BAND = Decimal(0)


def to_decimal_bound(value: float | int | str, name: str) -> Decimal:
    """Convert one configured bound to an exact decimal.

    Routed through ``str`` on purpose: ``Decimal(0.1)`` would carry the binary
    float's full artefact, whereas ``Decimal(str(0.1))`` is the decimal the
    configuration author actually wrote.
    """
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise VarianceBandError(f"{name} is not a usable decimal bound") from None
    if not converted.is_finite():
        raise VarianceBandError(f"{name} must be a finite decimal bound")
    return converted


@dataclass(frozen=True)
class ReconcilePolicy:
    """Immutable, deeply frozen reconciliation parameters."""

    variance_bands: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "variance_bands", tuple(self.variance_bands))

        if not self.variance_bands:
            raise VarianceBandError("at least one variance band must be declared")
        for band in self.variance_bands:
            if not isinstance(band, Decimal):
                raise VarianceBandError("variance bands must be exact decimals")
            if not band.is_finite():
                raise VarianceBandError("variance bands must be finite")
            if band < 0:
                raise VarianceBandError("variance bands must not be negative")
        if self.variance_bands[0] != REQUIRED_FIRST_BAND:
            raise VarianceBandError("the first variance band must be exactly zero")
        if any(
            later <= earlier
            for earlier, later in zip(
                self.variance_bands, self.variance_bands[1:], strict=False
            )
        ):
            raise VarianceBandError("variance bands must ascend strictly")

    @property
    def largest_band(self) -> Decimal:
        return self.variance_bands[-1]

    def band_for(self, absolute_difference: Decimal) -> Decimal | None:
        """The narrowest band that contains this difference, or ``None``.

        Upper edges are inclusive, so a difference equal to a bound falls
        inside that bound's band. ``None`` means the difference is larger than
        every configured bound, which is the only way to be a mismatch.
        """
        if absolute_difference < 0:
            raise ReconcilePolicyError(
                "a variance band is selected from an absolute difference",
                operation="band_for",
            )
        for band in self.variance_bands:
            if absolute_difference <= band:
                return band
        return None


def policy_from_config(
    config: ReportConfig, *, variance_bands: Iterable[float] | None = None
) -> ReconcilePolicy:
    """Build a reconciliation policy from the configured variance bands.

    The bands live on the report configuration because reporting also colours
    by them; this is the same ladder, read once and converted exactly.
    """
    source: Sequence[float] = (
        list(config.variance_bands) if variance_bands is None else list(variance_bands)
    )
    return ReconcilePolicy(
        variance_bands=tuple(
            to_decimal_bound(value, f"variance_bands[{position}]")
            for position, value in enumerate(source)
        )
    )
