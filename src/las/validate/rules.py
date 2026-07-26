"""The rule registry and the built-in rules.

A small explicit registry, not a framework: two frozen rule types, one frozen
rule set, and a fixed list of built-ins. No plugin discovery, no dynamic
imports, no ``eval``, no reflection.

Two rule shapes
---------------
* :class:`FieldRule` looks at one canonical field's text in isolation. It is
  the shape almost every rule wants.
* :class:`BatchRule` looks across the whole batch at once. Only duplicate-key
  detection needs that, because "duplicate" is not a property a single row has.

Determinism
-----------
Execution order is ``(priority, rule_id)`` — never registration order — so
shuffling how a rule set is assembled cannot change a result. The registry
rejects a duplicate ``rule_id``, and it rejects two rules claiming the same
issue code, so every code has exactly one owner and no issue can be emitted
twice for one underlying defect.

Redundancy
----------
Domain rules declare ``applies_to_blank=False`` and are skipped for absent
values, so a missing amount reports only that it is missing rather than also
reporting that it is not a decimal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from las.ingest import IngestedRow
from las.validate.checks import (
    check_amount,
    check_identifier,
    check_period,
    check_unit,
    is_blank,
)
from las.validate.errors import RuleDefinitionError
from las.validate.models import (
    CODE_ACCOUNT_REF_FORMAT_INVALID,
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_AMOUNT_NOT_FINITE,
    CODE_AMOUNT_OUT_OF_RANGE,
    CODE_DUPLICATE_KEY,
    CODE_EXTERNAL_REF_FORMAT_INVALID,
    CODE_EXTERNAL_REF_UNKNOWN,
    CODE_PERIOD_FORMAT_INVALID,
    CODE_PERIOD_MONTH_INVALID,
    CODE_RECORD_ID_FORMAT_INVALID,
    CODE_REQUIRED_VALUE_MISSING,
    CODE_UNIT_NOT_SUPPORTED,
    SEVERITY_BY_CODE,
)
from las.validate.policy import (
    FIELD_ACCOUNT_REF,
    FIELD_AMOUNT,
    FIELD_EXTERNAL_REF,
    FIELD_PERIOD,
    FIELD_RECORD_ID,
    FIELD_UNIT,
    ValidationPolicy,
)

FieldCheck = Callable[[str | None, ValidationPolicy], str | None]
BatchCheck = Callable[[Sequence[IngestedRow], ValidationPolicy], Mapping[int, str]]


def _validate_declaration(rule_id: str, priority: int, codes: tuple[str, ...]) -> None:
    if not rule_id or not rule_id.strip():
        raise RuleDefinitionError("a rule must declare a non-blank identifier")
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise RuleDefinitionError("a rule priority must be an integer", rule_id=rule_id)
    if not codes:
        raise RuleDefinitionError("a rule must declare at least one code", rule_id=rule_id)
    unknown = sorted(set(codes) - set(SEVERITY_BY_CODE))
    if unknown:
        raise RuleDefinitionError(
            f"unknown issue code(s): {', '.join(unknown)}", rule_id=rule_id
        )


@dataclass(frozen=True)
class FieldRule:
    """Checks one canonical field's text, independently of every other row."""

    rule_id: str
    priority: int
    codes: tuple[str, ...]
    check: FieldCheck
    fields: tuple[str, ...] = ()
    fields_from_policy: Callable[[ValidationPolicy], tuple[str, ...]] | None = None
    applies_to_blank: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "codes", tuple(self.codes))
        object.__setattr__(self, "fields", tuple(self.fields))
        _validate_declaration(self.rule_id, self.priority, self.codes)
        if not self.fields and self.fields_from_policy is None:
            raise RuleDefinitionError(
                "a field rule must declare fields or derive them from the policy",
                rule_id=self.rule_id,
            )

    def applicable_fields(self, policy: ValidationPolicy) -> tuple[str, ...]:
        if self.fields_from_policy is not None:
            return tuple(self.fields_from_policy(policy))
        return self.fields


@dataclass(frozen=True)
class BatchRule:
    """Checks a property no single row has, such as duplication."""

    rule_id: str
    priority: int
    codes: tuple[str, ...]
    check: BatchCheck
    field: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "codes", tuple(self.codes))
        _validate_declaration(self.rule_id, self.priority, self.codes)


@dataclass(frozen=True)
class RuleSet:
    """An immutable, self-consistent collection of rules."""

    field_rules: tuple[FieldRule, ...] = ()
    batch_rules: tuple[BatchRule, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "field_rules", tuple(self.field_rules))
        object.__setattr__(self, "batch_rules", tuple(self.batch_rules))
        if not self.field_rules and not self.batch_rules:
            raise RuleDefinitionError("a rule set must declare at least one rule")

        seen_ids: dict[str, bool] = {}
        owner_of_code: dict[str, str] = {}
        for rule in (*self.field_rules, *self.batch_rules):
            if rule.rule_id in seen_ids:
                raise RuleDefinitionError(
                    "the rule set declares this identifier twice", rule_id=rule.rule_id
                )
            seen_ids[rule.rule_id] = True
            for code in rule.codes:
                existing = owner_of_code.get(code)
                if existing is not None:
                    raise RuleDefinitionError(
                        f"issue code '{code}' is already owned by rule '{existing}'",
                        rule_id=rule.rule_id,
                    )
                owner_of_code[code] = rule.rule_id

    @property
    def ordered_field_rules(self) -> tuple[FieldRule, ...]:
        return tuple(sorted(self.field_rules, key=lambda r: (r.priority, r.rule_id)))

    @property
    def ordered_batch_rules(self) -> tuple[BatchRule, ...]:
        return tuple(sorted(self.batch_rules, key=lambda r: (r.priority, r.rule_id)))

    @property
    def rule_ids(self) -> tuple[str, ...]:
        """Identifiers in execution order, independent of how they were added."""
        return tuple(
            rule.rule_id
            for rule in (*self.ordered_field_rules, *self.ordered_batch_rules)
        )

    def priority_of(self, rule_id: str) -> int:
        for rule in (*self.field_rules, *self.batch_rules):
            if rule.rule_id == rule_id:
                return rule.priority
        raise RuleDefinitionError("no such rule in this rule set", rule_id=rule_id)


# --- built-in checks ----------------------------------------------------------


def _required(value: str | None, policy: ValidationPolicy) -> str | None:
    return CODE_REQUIRED_VALUE_MISSING if is_blank(value) else None


def _record_id(value: str | None, policy: ValidationPolicy) -> str | None:
    return check_identifier(
        value or "", policy.record_id_regex, CODE_RECORD_ID_FORMAT_INVALID
    )


def _external_ref_format(value: str | None, policy: ValidationPolicy) -> str | None:
    return check_identifier(
        value or "", policy.external_ref_regex, CODE_EXTERNAL_REF_FORMAT_INVALID
    )


def _account_ref(value: str | None, policy: ValidationPolicy) -> str | None:
    return check_identifier(
        value or "", policy.account_ref_regex, CODE_ACCOUNT_REF_FORMAT_INVALID
    )


def _amount(value: str | None, policy: ValidationPolicy) -> str | None:
    return check_amount(
        value or "", minimum=policy.amount_min, maximum=policy.amount_max
    )


def _unit(value: str | None, policy: ValidationPolicy) -> str | None:
    if not policy.check_units:
        return None
    return check_unit(value or "", policy.unit_set)


def _period(value: str | None, policy: ValidationPolicy) -> str | None:
    if not policy.check_period:
        return None
    return check_period(value or "", policy.period_regex)


def _external_ref_known(value: str | None, policy: ValidationPolicy) -> str | None:
    """Exact membership in a caller-supplied reference set.

    This is referential integrity, which the development plan places in this
    task. It is deliberately *not* matching: no normalization, no scoring, no
    account resolution — only whether this exact reference appears in the set
    the caller handed in. With no set supplied the rule stays silent.
    """
    if policy.known_external_refs is None:
        return None
    if not policy.external_ref_regex.fullmatch(value or ""):
        # Malformed references are already reported by the format rule.
        return None
    return None if value in policy.known_external_refs else CODE_EXTERNAL_REF_UNKNOWN


#: The domain check that governs each field when it is used as a key part.
#: Reusing the field rules' own checks keeps one definition of "valid" rather
#: than a second, looser one hidden inside duplicate detection.
_KEY_PART_CHECKS: Mapping[str, FieldCheck] = {
    FIELD_RECORD_ID: _record_id,
    FIELD_EXTERNAL_REF: _external_ref_format,
    FIELD_ACCOUNT_REF: _account_ref,
    FIELD_AMOUNT: _amount,
    FIELD_UNIT: _unit,
    FIELD_PERIOD: _period,
}


def _key_is_usable(row: IngestedRow, policy: ValidationPolicy) -> bool:
    """Whether every part of this row's duplicate key is present and valid."""
    for name in policy.duplicate_key_fields:
        text = row.values.get(name)
        if is_blank(text):
            return False
        check = _KEY_PART_CHECKS.get(name)
        if check is not None and check(text, policy) is not None:
            return False
    return True


def _duplicate_keys(
    rows: Sequence[IngestedRow], policy: ValidationPolicy
) -> Mapping[int, str]:
    """Flag every row whose duplicate key appears more than once in the batch.

    Both copies are flagged and both are kept: choosing which one survives is
    not validation's decision.

    A row is skipped when any part of its key is absent, or fails that field's
    own domain check. Two rows sharing an unusable key are not meaningfully
    "the same record", so reporting them as duplicates would pile a misleading
    warning on top of the real defect another rule already reported.
    """
    keys: dict[tuple[str, ...], list[int]] = {}
    for index, row in enumerate(rows):
        if not _key_is_usable(row, policy):
            continue
        parts = tuple((row.values.get(name) or "") for name in policy.duplicate_key_fields)
        keys.setdefault(parts, []).append(index)
    flagged: dict[int, str] = {}
    for indexes in keys.values():
        if len(indexes) > 1:
            for index in indexes:
                flagged[index] = CODE_DUPLICATE_KEY
    return flagged


# --- the built-in rule set ----------------------------------------------------

REQUIRED_VALUE_RULE = FieldRule(
    rule_id="required_value",
    priority=10,
    codes=(CODE_REQUIRED_VALUE_MISSING,),
    check=_required,
    fields_from_policy=lambda policy: tuple(sorted(policy.required_fields)),
    applies_to_blank=True,
)

RECORD_ID_FORMAT_RULE = FieldRule(
    rule_id="record_id_format",
    priority=20,
    codes=(CODE_RECORD_ID_FORMAT_INVALID,),
    check=_record_id,
    fields=(FIELD_RECORD_ID,),
)

EXTERNAL_REF_FORMAT_RULE = FieldRule(
    rule_id="external_ref_format",
    priority=21,
    codes=(CODE_EXTERNAL_REF_FORMAT_INVALID,),
    check=_external_ref_format,
    fields=(FIELD_EXTERNAL_REF,),
)

ACCOUNT_REF_FORMAT_RULE = FieldRule(
    rule_id="account_ref_format",
    priority=22,
    codes=(CODE_ACCOUNT_REF_FORMAT_INVALID,),
    check=_account_ref,
    fields=(FIELD_ACCOUNT_REF,),
)

AMOUNT_DOMAIN_RULE = FieldRule(
    rule_id="amount_domain",
    priority=30,
    codes=(CODE_AMOUNT_NOT_DECIMAL, CODE_AMOUNT_NOT_FINITE, CODE_AMOUNT_OUT_OF_RANGE),
    check=_amount,
    fields=(FIELD_AMOUNT,),
)

UNIT_DOMAIN_RULE = FieldRule(
    rule_id="unit_domain",
    priority=40,
    codes=(CODE_UNIT_NOT_SUPPORTED,),
    check=_unit,
    fields=(FIELD_UNIT,),
)

PERIOD_DOMAIN_RULE = FieldRule(
    rule_id="period_domain",
    priority=50,
    codes=(CODE_PERIOD_FORMAT_INVALID, CODE_PERIOD_MONTH_INVALID),
    check=_period,
    fields=(FIELD_PERIOD,),
)

EXTERNAL_REF_KNOWN_RULE = FieldRule(
    rule_id="external_ref_known",
    priority=60,
    codes=(CODE_EXTERNAL_REF_UNKNOWN,),
    check=_external_ref_known,
    fields=(FIELD_EXTERNAL_REF,),
)

DUPLICATE_KEY_RULE = BatchRule(
    rule_id="duplicate_key",
    priority=70,
    codes=(CODE_DUPLICATE_KEY,),
    check=_duplicate_keys,
)

#: The rules the development plan names for this task: required, format,
#: range, referential integrity, and duplicates.
BUILT_IN_FIELD_RULES: tuple[FieldRule, ...] = (
    REQUIRED_VALUE_RULE,
    RECORD_ID_FORMAT_RULE,
    EXTERNAL_REF_FORMAT_RULE,
    ACCOUNT_REF_FORMAT_RULE,
    AMOUNT_DOMAIN_RULE,
    UNIT_DOMAIN_RULE,
    PERIOD_DOMAIN_RULE,
    EXTERNAL_REF_KNOWN_RULE,
)

BUILT_IN_BATCH_RULES: tuple[BatchRule, ...] = (DUPLICATE_KEY_RULE,)


def default_rule_set() -> RuleSet:
    """The built-in rule set. Cheap to build and immutable once built."""
    return RuleSet(
        field_rules=BUILT_IN_FIELD_RULES, batch_rules=BUILT_IN_BATCH_RULES
    )
