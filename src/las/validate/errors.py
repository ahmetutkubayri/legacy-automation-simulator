"""Typed errors for the validation engine.

Only two things can go wrong in a pure validation pass: the rule set is
self-contradictory as *defined*, or a rule misbehaves while running. Both
are programming errors rather than data problems, so neither is reported as
a validation issue.

Messages carry the rule identifier, the row number, and — when a rule
raises — the offending exception's *type name only*. The exception's own
message never travels, because it can quote arbitrary source values.
"""

from __future__ import annotations


class ValidationError(Exception):
    """Base class for validation-engine failures, with safe context only."""

    def __init__(
        self,
        message: str,
        *,
        rule_id: str | None = None,
        source: str | None = None,
        row: int | None = None,
        field: str | None = None,
    ) -> None:
        context: list[str] = []
        if rule_id is not None:
            context.append(f"rule={rule_id}")
        if source is not None:
            context.append(f"source={source}")
        if row is not None:
            context.append(f"row={row}")
        if field is not None:
            context.append(f"field={field}")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)
        self.rule_id = rule_id
        self.source = source
        self.row = row
        self.field = field


class RuleDefinitionError(ValidationError):
    """A rule or registry is contradictory as declared.

    Raised when the rule set is built, not when data is validated: a registry
    that cannot produce deterministic results is a defect in the code.
    """


class RuleExecutionError(ValidationError):
    """A rule raised while checking a value.

    Carries the failing rule and location plus the exception's type name. The
    original message is deliberately dropped.
    """


class PolicyError(ValidationError):
    """A validation policy cannot be built from the supplied parameters."""
