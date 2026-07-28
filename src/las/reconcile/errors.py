"""Typed errors for the reconciliation subsystem.

Every message is a fixed template plus safe categorical context: an operation,
a side, a source label, a row number, and invented synthetic identifiers.
Amounts, units, periods, account names, whole rows, absolute paths and raw
exception text never travel — those are exactly the values a reconciliation
error is most tempted to quote when explaining what went wrong.
"""

from __future__ import annotations


class ReconcileError(Exception):
    """Base class for reconciliation failures, carrying safe context only."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        side: str | None = None,
        source: str | None = None,
        row: int | None = None,
        row_index: int | None = None,
        account_ref: str | None = None,
        record_id: str | None = None,
        field: str | None = None,
    ) -> None:
        context: list[str] = []
        if operation is not None:
            context.append(f"operation={operation}")
        if side is not None:
            context.append(f"side={side}")
        if source is not None:
            context.append(f"source={source}")
        if row is not None:
            context.append(f"row={row}")
        if row_index is not None:
            context.append(f"row_index={row_index}")
        if account_ref is not None:
            context.append(f"account_ref={account_ref}")
        if record_id is not None:
            context.append(f"record_id={record_id}")
        if field is not None:
            context.append(f"field={field}")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)
        self.operation = operation
        self.side = side
        self.source = source
        self.row = row
        self.row_index = row_index
        self.account_ref = account_ref
        self.record_id = record_id
        self.field = field


class ReconcilePolicyError(ReconcileError):
    """A reconciliation policy cannot be built from the supplied parameters."""


class VarianceBandError(ReconcilePolicyError):
    """The configured variance bands do not describe a usable ladder."""


class InputContractError(ReconcileError):
    """A supplied input does not satisfy the reconciliation input contract."""


class RowAlignmentError(InputContractError):
    """Validation rows and matching decisions do not describe the same batch.

    Raised rather than zipping the two sequences: a shorter one would silently
    truncate the longer, and rows would vanish without anyone being told.
    """


class DuplicateInputIdentityError(InputContractError):
    """One physical input was supplied more than once.

    Not the same thing as a business duplicate. Two feed rows that report the
    same account, period, unit and amount are two genuine observations when
    they sit at different physical rows, and both contribute. This error is
    about *replay*: one physical row, or one system record, handed to
    reconciliation twice, which would double a total while looking exactly
    like ordinary duplicate data.
    """


class DuplicateFeedBatchError(DuplicateInputIdentityError):
    """The same feed batch was supplied more than once on the feed side."""


class MatchDecisionContractError(InputContractError):
    """A matching decision does not say what its outcome claims.

    Chiefly a decision reported as matched that carries no account reference:
    reconciliation cannot place such a row in a group, and guessing one would
    invent an account the matching stage declined to name.
    """


class LedgerRowError(InputContractError):
    """A system ledger row is missing a field reconciliation cannot do without.

    Raised while the row is constructed, so an unusable system side is refused
    before any grouping begins rather than surfacing as a strange total later.
    """


class ReconcileExecutionError(ReconcileError):
    """A reconciliation step raised while aggregating a group.

    Carries the failing operation and the exception's *type name* only; the
    original message is dropped because it can quote a source value.
    """
