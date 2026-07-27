"""Typed errors for the matching subsystem.

Every message is a fixed template plus safe categorical context: an operation,
a source label, a row number, and invented synthetic identifiers. Account
names, source names, full rows, absolute paths and raw exception text never
travel, because those are exactly the values a matching error is most tempted
to quote.
"""

from __future__ import annotations

#: The only message a bound mismatch ever produces. Fixed rather than
#: formatted, so the failure reads identically from every entry point and can
#: carry nothing at all — not even the two integers. A caller who wants them
#: reads ``MasterIndex.max_name_length`` and ``MatchPolicy.max_name_length``,
#: both of which are public and immutable.
NAME_LENGTH_MISMATCH_MESSAGE = (
    "the master index and the matching policy were built with different "
    "name-length caps"
)


class MatchError(Exception):
    """Base class for matching failures, carrying safe context only."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        source: str | None = None,
        row: int | None = None,
        account_ref: str | None = None,
        external_ref: str | None = None,
    ) -> None:
        context: list[str] = []
        if operation is not None:
            context.append(f"operation={operation}")
        if source is not None:
            context.append(f"source={source}")
        if row is not None:
            context.append(f"row={row}")
        if account_ref is not None:
            context.append(f"account_ref={account_ref}")
        if external_ref is not None:
            context.append(f"external_ref={external_ref}")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(message + suffix)
        self.operation = operation
        self.source = source
        self.row = row
        self.account_ref = account_ref
        self.external_ref = external_ref


class MatchPolicyError(MatchError):
    """A matching policy cannot be built from the supplied parameters."""


class ScoreRangeError(MatchError):
    """A score falls outside the fixed basis-point range."""


class MasterIndexError(MatchError):
    """The master data cannot be turned into a usable index."""


class DuplicateAccountError(MasterIndexError):
    """Two master rows claim the same account reference."""


class ConflictingLinkError(MasterIndexError):
    """One external reference is linked to two different accounts."""


class DanglingLinkError(MasterIndexError):
    """A link points at an account the master data does not contain."""


class OverlongNameError(MasterIndexError):
    """A master account name exceeds the configured safety cap.

    Loud rather than silent: an account name longer than the cap is a
    data-quality problem an operator should see, whereas a blank name is
    merely an account that cannot take part in name matching.
    """


class NameLengthMismatchError(MatchError):
    """The master index and the matching policy disagree on the name cap.

    Master names were indexed under one bound and source names would be judged
    under another, so the two sides of every comparison would not mean the same
    thing. Choosing either value would silently change the verdict, so matching
    refuses instead — before a single row is read.
    """


class MatchExecutionError(MatchError):
    """A scoring or matching step raised while comparing candidates.

    Carries the failing operation and the exception's *type name* only; the
    original message is dropped because it can quote a source name.
    """
