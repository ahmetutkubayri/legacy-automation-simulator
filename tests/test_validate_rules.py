"""T10 rule registry: stable ordering, rejected contradictions, typed failures."""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError

import pytest

from las.validate import (
    BUILT_IN_BATCH_RULES,
    BUILT_IN_FIELD_RULES,
    CODE_AMOUNT_NOT_DECIMAL,
    CODE_DUPLICATE_KEY,
    CODE_REQUIRED_VALUE_MISSING,
    SEVERITY_BY_CODE,
    BatchRule,
    FieldRule,
    RuleDefinitionError,
    RuleExecutionError,
    RuleSet,
    default_rule_set,
    validate_rows,
)
from validate_helpers import Explosive, make_policy, make_row

POLICY = make_policy()


def noop_field_check(value, policy):
    return None


def noop_batch_check(rows, policy):
    return {}


def field_rule(rule_id: str, priority: int = 10, code: str = CODE_AMOUNT_NOT_DECIMAL) -> FieldRule:
    return FieldRule(
        rule_id=rule_id,
        priority=priority,
        codes=(code,),
        check=noop_field_check,
        fields=("amount",),
    )


# --- declaration validation ---------------------------------------------------


@pytest.mark.parametrize("rule_id", ["", "   "])
def test_blank_rule_identifier_is_rejected(rule_id: str) -> None:
    with pytest.raises(RuleDefinitionError, match="non-blank identifier"):
        field_rule(rule_id)


def test_non_integer_priority_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="priority must be an integer"):
        FieldRule(
            rule_id="probe",
            priority="10",  # type: ignore[arg-type]
            codes=(CODE_AMOUNT_NOT_DECIMAL,),
            check=noop_field_check,
            fields=("amount",),
        )


def test_boolean_priority_is_rejected_even_though_bool_subclasses_int() -> None:
    with pytest.raises(RuleDefinitionError, match="priority must be an integer"):
        FieldRule(
            rule_id="probe",
            priority=True,  # type: ignore[arg-type]
            codes=(CODE_AMOUNT_NOT_DECIMAL,),
            check=noop_field_check,
            fields=("amount",),
        )


def test_rule_without_codes_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="at least one code"):
        FieldRule(
            rule_id="probe", priority=1, codes=(), check=noop_field_check, fields=("amount",)
        )


def test_rule_with_an_unknown_code_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="unknown issue code"):
        FieldRule(
            rule_id="probe",
            priority=1,
            codes=("invented_out_of_thin_air",),
            check=noop_field_check,
            fields=("amount",),
        )


def test_field_rule_must_declare_or_derive_its_fields() -> None:
    with pytest.raises(RuleDefinitionError, match="declare fields or derive"):
        FieldRule(
            rule_id="probe",
            priority=1,
            codes=(CODE_AMOUNT_NOT_DECIMAL,),
            check=noop_field_check,
        )


def test_rules_are_frozen_and_coerce_sequences_to_tuples() -> None:
    codes = [CODE_AMOUNT_NOT_DECIMAL]
    fields = ["amount"]
    rule = FieldRule(
        rule_id="probe", priority=1, codes=codes, check=noop_field_check, fields=fields
    )
    assert isinstance(rule.codes, tuple)
    assert isinstance(rule.fields, tuple)
    codes.append(CODE_DUPLICATE_KEY)
    fields.append("unit")
    assert rule.codes == (CODE_AMOUNT_NOT_DECIMAL,)
    assert rule.fields == ("amount",)
    with pytest.raises(FrozenInstanceError):
        rule.priority = 2  # type: ignore[misc]


# --- registry consistency -----------------------------------------------------


def test_empty_rule_set_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="at least one rule"):
        RuleSet()


def test_duplicate_rule_identifier_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="identifier twice"):
        RuleSet(
            field_rules=(
                field_rule("same", code=CODE_AMOUNT_NOT_DECIMAL),
                field_rule("same", code=CODE_REQUIRED_VALUE_MISSING),
            )
        )


def test_duplicate_identifier_across_rule_kinds_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="identifier twice"):
        RuleSet(
            field_rules=(field_rule("shared"),),
            batch_rules=(
                BatchRule(
                    rule_id="shared",
                    priority=1,
                    codes=(CODE_DUPLICATE_KEY,),
                    check=noop_batch_check,
                ),
            ),
        )


def test_two_rules_claiming_one_code_is_rejected() -> None:
    """Every code has exactly one owner, so an issue has one source."""
    with pytest.raises(RuleDefinitionError, match="already owned by rule"):
        RuleSet(
            field_rules=(
                field_rule("first", code=CODE_AMOUNT_NOT_DECIMAL),
                field_rule("second", code=CODE_AMOUNT_NOT_DECIMAL),
            )
        )


def test_rule_set_coerces_sequences_and_is_frozen() -> None:
    rules = [field_rule("probe")]
    rule_set = RuleSet(field_rules=rules)
    assert isinstance(rule_set.field_rules, tuple)
    rules.append(field_rule("other", code=CODE_REQUIRED_VALUE_MISSING))
    assert len(rule_set.field_rules) == 1
    with pytest.raises(FrozenInstanceError):
        rule_set.field_rules = ()  # type: ignore[misc]


def test_priority_lookup_rejects_an_unknown_rule() -> None:
    rule_set = RuleSet(field_rules=(field_rule("probe", priority=5),))
    assert rule_set.priority_of("probe") == 5
    with pytest.raises(RuleDefinitionError, match="no such rule"):
        rule_set.priority_of("absent")


# --- deterministic ordering ---------------------------------------------------


def test_execution_order_follows_priority_then_identifier() -> None:
    rule_set = RuleSet(
        field_rules=(
            field_rule("zebra", priority=10, code=CODE_AMOUNT_NOT_DECIMAL),
            field_rule("alpha", priority=10, code=CODE_REQUIRED_VALUE_MISSING),
            field_rule("middle", priority=5, code="unit_not_supported"),
        )
    )
    assert rule_set.rule_ids == ("middle", "alpha", "zebra")


def test_execution_order_ignores_registration_order() -> None:
    made = [
        field_rule("a", priority=3, code=CODE_AMOUNT_NOT_DECIMAL),
        field_rule("b", priority=1, code=CODE_REQUIRED_VALUE_MISSING),
        field_rule("c", priority=2, code="unit_not_supported"),
    ]
    shuffler = random.Random(11)  # test-local only; nothing under test is random
    baseline = None
    for _ in range(10):
        shuffler.shuffle(made)
        order = RuleSet(field_rules=tuple(made)).rule_ids
        baseline = order if baseline is None else baseline
        assert order == baseline
    assert baseline == ("b", "c", "a")


def test_batch_rules_execute_after_field_rules() -> None:
    ids = default_rule_set().rule_ids
    assert ids[-1] == "duplicate_key"
    assert ids.index("required_value") < ids.index("duplicate_key")


def test_built_in_rule_set_is_self_consistent_and_covers_the_planned_families() -> None:
    rule_set = default_rule_set()
    assert rule_set.rule_ids == (
        "required_value",
        "record_id_format",
        "external_ref_format",
        "account_ref_format",
        "amount_domain",
        "unit_domain",
        "period_domain",
        "external_ref_known",
        "duplicate_key",
    )
    owned = [code for rule in (*BUILT_IN_FIELD_RULES, *BUILT_IN_BATCH_RULES) for code in rule.codes]
    assert sorted(owned) == sorted(SEVERITY_BY_CODE), "every code must have an owning rule"
    assert len(owned) == len(set(owned)), "no code may be owned twice"


def test_required_rule_derives_its_fields_from_the_policy() -> None:
    rule = next(r for r in BUILT_IN_FIELD_RULES if r.rule_id == "required_value")
    policy = make_policy(required_fields=("amount", "record_id"))
    assert rule.applicable_fields(policy) == ("amount", "record_id")  # sorted, stable
    other = make_policy(required_fields=("unit",))
    assert rule.applicable_fields(other) == ("unit",)


def test_static_rules_report_their_declared_fields() -> None:
    rule = next(r for r in BUILT_IN_FIELD_RULES if r.rule_id == "amount_domain")
    assert rule.applicable_fields(POLICY) == ("amount",)


# --- rule failure becomes a typed engine error --------------------------------


def test_a_raising_field_rule_becomes_a_focused_typed_error() -> None:
    def explodes(value, policy):
        raise KeyError("a secret detail that must not travel")

    rule_set = RuleSet(
        field_rules=(
            FieldRule(
                rule_id="explodes",
                priority=1,
                codes=(CODE_AMOUNT_NOT_DECIMAL,),
                check=explodes,
                fields=("amount",),
            ),
        )
    )
    with pytest.raises(RuleExecutionError) as excinfo:
        validate_rows([make_row(source_row=4)], POLICY, rule_set=rule_set)

    message = str(excinfo.value)
    assert "KeyError" in message  # the type travels
    assert "a secret detail" not in message  # the message does not
    assert excinfo.value.rule_id == "explodes"
    assert excinfo.value.row == 4
    assert excinfo.value.field == "amount"


def test_a_raising_batch_rule_becomes_a_focused_typed_error() -> None:
    def explodes(rows, policy):
        raise ValueError("another secret detail")

    rule_set = RuleSet(
        batch_rules=(
            BatchRule(
                rule_id="explodes", priority=1, codes=(CODE_DUPLICATE_KEY,), check=explodes
            ),
        )
    )
    with pytest.raises(RuleExecutionError) as excinfo:
        validate_rows([make_row()], POLICY, rule_set=rule_set)
    assert "ValueError" in str(excinfo.value)
    assert "another secret detail" not in str(excinfo.value)
    assert excinfo.value.rule_id == "explodes"


def test_rule_failure_never_serializes_the_offending_value() -> None:
    """A value whose repr raises must not be touched while reporting."""

    def explodes(value, policy):
        raise RuntimeError(Explosive())  # noqa: TRY004 - deliberate hostile payload

    rule_set = RuleSet(
        field_rules=(
            FieldRule(
                rule_id="explodes",
                priority=1,
                codes=(CODE_AMOUNT_NOT_DECIMAL,),
                check=explodes,
                fields=("amount",),
            ),
        )
    )
    with pytest.raises(RuleExecutionError) as excinfo:
        validate_rows([make_row()], POLICY, rule_set=rule_set)
    assert "RuntimeError" in str(excinfo.value)


def test_a_rule_cannot_mutate_the_row_it_inspects() -> None:
    def tamper(value, policy):
        raise AssertionError("unreachable")  # pragma: no cover

    row = make_row()
    with pytest.raises(TypeError):
        row.values["amount"] = "changed"  # type: ignore[index]
    assert tamper is not None  # the helper exists but is never invoked
