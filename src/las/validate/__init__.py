"""Deterministic, side-effect-free validation of ingested records.

Depends only on the standard library, ``las.ingest``, and the project's
configuration models. It knows nothing of the simulator, the driver, or any
later stage, and it never reads a file, consults a clock, or uses randomness.

What this layer decides
-----------------------
Whether a record's *business content* is acceptable: required values present,
identifiers well formed, amounts parseable and in range, units supported,
periods well shaped, references present in a supplied reference set, and keys
not duplicated within the batch.

What it deliberately leaves alone
---------------------------------
Structural file problems belong to ingestion. Fuzzy matching, account-link
resolution, and cross-feed reconciliation belong to later stages. Nothing here
corrects, normalizes, filters, reorders, or de-duplicates a record — validation
reports, and every input row survives to the output with its provenance intact.
"""

from las.validate.checks import (
    AMOUNT_PATTERN,
    NON_FINITE_PATTERN,
    PERIOD_PATTERN,
    check_amount,
    check_identifier,
    check_period,
    check_unit,
    compile_pattern,
    is_blank,
)
from las.validate.engine import validate_ingest_result, validate_rows
from las.validate.errors import (
    PolicyError,
    RuleDefinitionError,
    RuleExecutionError,
    ValidationError,
)
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
    MESSAGE_BY_CODE,
    SEVERITY_BY_CODE,
    SEVERITY_RANK,
    BatchValidationResult,
    RowOutcome,
    Severity,
    ValidatedRow,
    ValidationIssue,
    ValidationSummary,
    summarize,
)
from las.validate.policy import (
    ACCOUNT_LINK_REQUIRED_FIELDS,
    ACCOUNT_REQUIRED_FIELDS,
    DEFAULT_ACCOUNT_REF_PATTERN,
    DEFAULT_ALLOWED_UNITS,
    ValidationPolicy,
    master_data_policy,
    policy_from_config,
)
from las.validate.rules import (
    BUILT_IN_BATCH_RULES,
    BUILT_IN_FIELD_RULES,
    BatchRule,
    FieldRule,
    RuleSet,
    default_rule_set,
)

__all__ = [
    "ACCOUNT_LINK_REQUIRED_FIELDS",
    "ACCOUNT_REQUIRED_FIELDS",
    "AMOUNT_PATTERN",
    "BUILT_IN_BATCH_RULES",
    "BUILT_IN_FIELD_RULES",
    "CODE_ACCOUNT_REF_FORMAT_INVALID",
    "CODE_AMOUNT_NOT_DECIMAL",
    "CODE_AMOUNT_NOT_FINITE",
    "CODE_AMOUNT_OUT_OF_RANGE",
    "CODE_DUPLICATE_KEY",
    "CODE_EXTERNAL_REF_FORMAT_INVALID",
    "CODE_EXTERNAL_REF_UNKNOWN",
    "CODE_PERIOD_FORMAT_INVALID",
    "CODE_PERIOD_MONTH_INVALID",
    "CODE_RECORD_ID_FORMAT_INVALID",
    "CODE_REQUIRED_VALUE_MISSING",
    "CODE_UNIT_NOT_SUPPORTED",
    "DEFAULT_ACCOUNT_REF_PATTERN",
    "DEFAULT_ALLOWED_UNITS",
    "MESSAGE_BY_CODE",
    "NON_FINITE_PATTERN",
    "PERIOD_PATTERN",
    "SEVERITY_BY_CODE",
    "SEVERITY_RANK",
    "BatchRule",
    "BatchValidationResult",
    "FieldRule",
    "PolicyError",
    "RowOutcome",
    "RuleDefinitionError",
    "RuleExecutionError",
    "RuleSet",
    "Severity",
    "ValidatedRow",
    "ValidationError",
    "ValidationIssue",
    "ValidationPolicy",
    "ValidationSummary",
    "check_amount",
    "check_identifier",
    "check_period",
    "check_unit",
    "compile_pattern",
    "default_rule_set",
    "is_blank",
    "master_data_policy",
    "policy_from_config",
    "summarize",
    "validate_ingest_result",
    "validate_rows",
]
