"""The immutable parameter set one validation pass runs against.

Where each parameter comes from
-------------------------------
Most of it comes from :class:`las.config.ValidationConfig`, which already
carries the required fields, the two identifier patterns, the amount bounds,
and the duplicate-key fields.

Two things the configuration does not define are taken from the committed
fixture contract instead, and are overridable per call rather than invented in
config (adding keys to the configuration models would change earlier tasks):

* **allowed units** — the generator's ``UNITS`` are ``EA``, ``KG`` and ``BOX``,
  matching the simulator's ledger units. Stored sorted, so the set has one
  deterministic order everywhere it is reported.
* **period pattern** — every generated period is ``YYYY-MM``, so that is the
  shape validated, with the month range checked separately. The six specific
  months the generator happens to emit are deliberately *not* enforced: that
  would over-fit the fixture rather than describe the contract.

The account-reference pattern is likewise fixture-derived (``ACC-``) and only
ever applied to schemas that actually declare an ``account_ref`` field.

Field order
-----------
``field_order`` is the schema-owned sequence that ranks fields when issues are
sorted. It lives here, on the policy, precisely so that ranking can never be
inferred from a row's mapping insertion order — two rows holding the same
values under keys inserted in opposite orders must produce identical results.
The default is every field this package knows about, in sorted order. A field
a row carries but the order does not name is ranked after every named field,
by its own name, so unexpected fields are still ordered deterministically.

Reference set
-------------
``known_external_refs`` is supplied by the caller, never read from disk, which
keeps validation pure. When it is absent the referential-integrity rule is
simply inapplicable and emits nothing — it never guesses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

from las.config import CANONICAL_FIELDS, ValidationConfig
from las.validate.checks import PERIOD_PATTERN, compile_pattern
from las.validate.errors import PolicyError

#: Units the synthetic contract defines, sorted for deterministic reporting.
DEFAULT_ALLOWED_UNITS: tuple[str, ...] = ("BOX", "EA", "KG")

#: Master-data account references, from the committed ``accounts.csv``.
DEFAULT_ACCOUNT_REF_PATTERN = r"ACC-[0-9]{6}"

#: Canonical field names this package knows how to check.
FIELD_RECORD_ID = "record_id"
FIELD_EXTERNAL_REF = "external_ref"
FIELD_ACCOUNT_REF = "account_ref"
FIELD_AMOUNT = "amount"
FIELD_UNIT = "unit"
FIELD_PERIOD = "period"

#: Every field this package can rank, in one sorted, deterministic order.
DEFAULT_FIELD_ORDER: tuple[str, ...] = tuple(
    sorted({*CANONICAL_FIELDS, FIELD_ACCOUNT_REF})
)


def _decimal_bound(value: float, name: str) -> Decimal:
    """Convert a configured float bound to an exact decimal.

    Routed through ``str`` on purpose: ``Decimal(0.1)`` would carry the binary
    float's full artefact, whereas ``Decimal(str(0.1))`` is the decimal the
    configuration author actually wrote.
    """
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise PolicyError(f"{name} is not a usable decimal bound") from None


@dataclass(frozen=True)
class ValidationPolicy:
    """Immutable, deeply frozen validation parameters."""

    required_fields: frozenset[str]
    record_id_pattern: str
    external_ref_pattern: str
    amount_min: Decimal
    amount_max: Decimal
    duplicate_key_fields: tuple[str, ...]
    allowed_units: tuple[str, ...] = DEFAULT_ALLOWED_UNITS
    period_pattern: str = PERIOD_PATTERN
    account_ref_pattern: str = DEFAULT_ACCOUNT_REF_PATTERN
    known_external_refs: frozenset[str] | None = None
    field_order: tuple[str, ...] = DEFAULT_FIELD_ORDER
    check_period: bool = True
    check_units: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_fields", frozenset(self.required_fields))
        object.__setattr__(self, "duplicate_key_fields", tuple(self.duplicate_key_fields))
        object.__setattr__(self, "allowed_units", tuple(sorted(set(self.allowed_units))))
        object.__setattr__(self, "field_order", tuple(self.field_order))
        if self.known_external_refs is not None:
            object.__setattr__(
                self, "known_external_refs", frozenset(self.known_external_refs)
            )

        if not self.required_fields:
            raise PolicyError("at least one required field must be declared")
        if not self.duplicate_key_fields:
            raise PolicyError("at least one duplicate-key field must be declared")
        if not self.allowed_units:
            raise PolicyError("at least one allowed unit must be declared")
        if not self.field_order:
            raise PolicyError("the field order must name at least one field")
        if any(not name or not name.strip() for name in self.field_order):
            raise PolicyError("the field order must not contain a blank name")
        repeated = sorted(
            {name for name in self.field_order if self.field_order.count(name) > 1}
        )
        if repeated:
            raise PolicyError(
                f"the field order repeats: {', '.join(repeated)}"
            )
        if self.amount_min > self.amount_max:
            raise PolicyError("amount_min must not exceed amount_max")
        for name in ("record_id_pattern", "external_ref_pattern",
                     "account_ref_pattern", "period_pattern"):
            try:
                compile_pattern(getattr(self, name))
            except Exception:
                raise PolicyError(f"{name} is not a valid regular expression") from None

        # Compiled once, with ASCII semantics; see las.validate.checks.
        object.__setattr__(self, "_record_id", compile_pattern(self.record_id_pattern))
        object.__setattr__(
            self, "_external_ref", compile_pattern(self.external_ref_pattern)
        )
        object.__setattr__(
            self, "_account_ref", compile_pattern(self.account_ref_pattern)
        )
        object.__setattr__(self, "_period", compile_pattern(self.period_pattern))
        object.__setattr__(self, "_units", frozenset(self.allowed_units))
        object.__setattr__(
            self,
            "_field_rank",
            MappingProxyType(
                {name: position for position, name in enumerate(self.field_order)}
            ),
        )


    # -- compiled accessors ------------------------------------------------

    @property
    def record_id_regex(self):
        return self._record_id  # type: ignore[attr-defined]

    @property
    def external_ref_regex(self):
        return self._external_ref  # type: ignore[attr-defined]

    @property
    def account_ref_regex(self):
        return self._account_ref  # type: ignore[attr-defined]

    @property
    def period_regex(self):
        return self._period  # type: ignore[attr-defined]

    @property
    def unit_set(self) -> frozenset[str]:
        return self._units  # type: ignore[attr-defined]

    @property
    def allowed_units_text(self) -> str:
        """The allowed units as one deterministic, safe categorical token."""
        return ", ".join(self.allowed_units)

    def requires(self, field_name: str) -> bool:
        return field_name in self.required_fields

    def field_rank(self, field_name: str | None) -> tuple[int, int, str]:
        """The sort rank of one field, owned by the policy rather than a row.

        Named fields rank by their declared position; a field the order does
        not name ranks after all of them, by its own name; and a row-scope
        issue (no field at all) ranks after every field-scope issue.
        """
        if field_name is None:
            return (2, 0, "")
        position = self._field_rank.get(field_name)  # type: ignore[attr-defined]
        if position is None:
            return (1, 0, field_name)
        return (0, position, "")



def policy_from_config(
    config: ValidationConfig,
    *,
    allowed_units: Iterable[str] = DEFAULT_ALLOWED_UNITS,
    period_pattern: str = PERIOD_PATTERN,
    account_ref_pattern: str = DEFAULT_ACCOUNT_REF_PATTERN,
    known_external_refs: Iterable[str] | None = None,
    field_order: Iterable[str] = DEFAULT_FIELD_ORDER,
    check_period: bool = True,
    check_units: bool = True,
) -> ValidationPolicy:
    """Build a policy from configuration plus fixture-derived defaults."""
    unknown = sorted(set(config.required_fields) - set(CANONICAL_FIELDS))
    if unknown:
        raise PolicyError(f"required field(s) are not canonical: {', '.join(unknown)}")
    return ValidationPolicy(
        required_fields=frozenset(config.required_fields),
        record_id_pattern=config.record_id_pattern,
        external_ref_pattern=config.external_ref_pattern,
        amount_min=_decimal_bound(config.amount_min, "amount_min"),
        amount_max=_decimal_bound(config.amount_max, "amount_max"),
        duplicate_key_fields=tuple(config.duplicate_key_fields),
        allowed_units=tuple(allowed_units),
        period_pattern=period_pattern,
        account_ref_pattern=account_ref_pattern,
        known_external_refs=(
            None if known_external_refs is None else frozenset(known_external_refs)
        ),
        field_order=tuple(field_order),
        check_period=check_period,
        check_units=check_units,
    )


#: Master-data fields the account schemas require, from the fixture contract.
ACCOUNT_REQUIRED_FIELDS: frozenset[str] = frozenset({FIELD_ACCOUNT_REF})
ACCOUNT_LINK_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {FIELD_EXTERNAL_REF, FIELD_ACCOUNT_REF}
)


def master_data_policy(
    *,
    required_fields: Iterable[str] = ACCOUNT_LINK_REQUIRED_FIELDS,
    external_ref_pattern: str = r"EXT-[0-9]{5}",
    account_ref_pattern: str = DEFAULT_ACCOUNT_REF_PATTERN,
    known_external_refs: Iterable[str] | None = None,
) -> ValidationPolicy:
    """A policy for the master-data schemas, which carry no amount or period.

    Amount, unit and period rules are inert here because those fields are not
    part of the master-data schemas, so the engine never reaches them.
    """
    return ValidationPolicy(
        required_fields=frozenset(required_fields),
        record_id_pattern=r"OPR-[0-9]{8}",
        external_ref_pattern=external_ref_pattern,
        amount_min=Decimal("0"),
        amount_max=Decimal("1000000"),
        duplicate_key_fields=(FIELD_EXTERNAL_REF,),
        account_ref_pattern=account_ref_pattern,
        known_external_refs=(
            None if known_external_refs is None else frozenset(known_external_refs)
        ),
        check_period=False,
        check_units=False,
    )
