"""The immutable master index built from the synthetic account fixtures.

Built from two T9 ingestion results — ``accounts.csv`` and
``account_links.csv`` — and never from file order: accounts are stored sorted
by ``account_ref``, and every lookup is a mapping, so shuffling either input
produces an identical index.

Only three master fields are used: ``account_ref``, ``account_name``, and the
link's ``external_ref``. The address, contact and status columns are read by
ingestion but are not part of matching, because nothing in the development plan
authorizes them for this task.

Duplicate names are kept
------------------------
Names map to a *tuple* of account references, not to one account. Two accounts
sharing a normalized name therefore both survive as candidates and the row
becomes ambiguous, rather than one silently overwriting the other in a dict.

Rejected master data
--------------------
A duplicate ``account_ref``, a link pointing at an account that does not exist,
one ``external_ref`` linked to two different accounts, or an account name past
the configured length cap each raise a typed error. A link repeated verbatim is
not an error — it says the same thing twice — but it is counted so the caller
can see it.

An account whose name is merely blank or carries a control character is *not*
rejected: it is kept, and simply takes no part in name matching. The length cap
is different because it is a safety bound, so exceeding it is a data-quality
problem an operator should be told about rather than a name that happens to be
uncomparable.

One bound, retained
-------------------
``max_name_length`` is the cap the names in this index were judged by, and the
index keeps it. It has to: matching compares a source name normalized under the
*policy's* cap against master keys normalized under this one, so the two must
be the same value or the comparison is between two different things. Retaining
it is what lets every matching entry point verify that before it starts, rather
than trusting two defaults to coincide.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from las.ingest import IngestedRow, IngestResult
from las.match.errors import (
    ConflictingLinkError,
    DanglingLinkError,
    DuplicateAccountError,
    MasterIndexError,
    OverlongNameError,
)
from las.match.normalize import (
    DEFAULT_MAX_NAME_LENGTH,
    name_length_fault,
    normalize_name,
)

FIELD_ACCOUNT_REF = "account_ref"
FIELD_ACCOUNT_NAME = "account_name"
FIELD_EXTERNAL_REF = "external_ref"


@dataclass(frozen=True)
class MasterAccount:
    """One synthetic account, with its comparison key precomputed."""

    account_ref: str
    account_name: str
    normalized_name: str | None

    def __post_init__(self) -> None:
        if not self.account_ref:
            raise MasterIndexError("a master account must carry an account reference")


@dataclass(frozen=True)
class MasterIndex:
    """Immutable, order-independent view of the master data."""

    accounts: tuple[MasterAccount, ...]
    account_by_ref: Mapping[str, MasterAccount]
    account_ref_by_link: Mapping[str, str]
    account_refs_by_name: Mapping[str, tuple[str, ...]]
    duplicate_links: int = 0
    max_name_length: int = DEFAULT_MAX_NAME_LENGTH

    def __post_init__(self) -> None:
        fault = name_length_fault(self.max_name_length)
        if fault is not None:
            raise MasterIndexError(fault)
        object.__setattr__(self, "accounts", tuple(self.accounts))
        object.__setattr__(self, "account_by_ref", MappingProxyType(dict(self.account_by_ref)))
        object.__setattr__(
            self, "account_ref_by_link", MappingProxyType(dict(self.account_ref_by_link))
        )
        object.__setattr__(
            self,
            "account_refs_by_name",
            MappingProxyType(
                {name: tuple(refs) for name, refs in self.account_refs_by_name.items()}
            ),
        )

    @property
    def account_count(self) -> int:
        return len(self.accounts)

    @property
    def link_count(self) -> int:
        return len(self.account_ref_by_link)

    def resolve_link(self, external_ref: str) -> str | None:
        """The account a reference links to, or ``None`` when unlinked."""
        return self.account_ref_by_link.get(external_ref)

    def accounts_named(self, normalized: str) -> tuple[str, ...]:
        """Every account whose normalized name equals this key."""
        return self.account_refs_by_name.get(normalized, ())

    def comparable_accounts(self) -> tuple[MasterAccount, ...]:
        """Accounts whose names can take part in scoring, in stored order."""
        return tuple(a for a in self.accounts if a.normalized_name is not None)


def _account_rows(result: IngestResult | Sequence[IngestedRow]) -> tuple[IngestedRow, ...]:
    return tuple(result.rows if isinstance(result, IngestResult) else result)


def build_master_index(
    accounts: IngestResult | Sequence[IngestedRow],
    links: IngestResult | Sequence[IngestedRow],
    *,
    max_name_length: int = DEFAULT_MAX_NAME_LENGTH,
) -> MasterIndex:
    """Build the index, rejecting master data that cannot be matched against."""
    operation = "build_master_index"

    # Checked before a single row is read, so an unusable cap cannot be
    # discovered halfway through building.
    fault = name_length_fault(max_name_length)
    if fault is not None:
        raise MasterIndexError(fault, operation=operation)

    by_ref: dict[str, MasterAccount] = {}
    for row in _account_rows(accounts):
        ref = row.values.get(FIELD_ACCOUNT_REF)
        if not ref:
            raise MasterIndexError(
                "a master account row carries no account reference",
                operation=operation,
                row=row.provenance.source_row,
                source=row.provenance.source,
            )
        if ref in by_ref:
            raise DuplicateAccountError(
                "the master data declares this account reference twice",
                operation=operation,
                row=row.provenance.source_row,
                source=row.provenance.source,
                account_ref=ref,
            )
        name = row.values.get(FIELD_ACCOUNT_NAME) or ""
        if len(name) > max_name_length:
            raise OverlongNameError(
                "the account name exceeds the configured name-length cap",
                operation=operation,
                row=row.provenance.source_row,
                source=row.provenance.source,
                account_ref=ref,
            )
        by_ref[ref] = MasterAccount(
            account_ref=ref,
            account_name=name,
            normalized_name=normalize_name(name, max_length=max_name_length),
        )

    link_map: dict[str, str] = {}
    duplicates = 0
    for row in _account_rows(links):
        external = row.values.get(FIELD_EXTERNAL_REF)
        target = row.values.get(FIELD_ACCOUNT_REF)
        if not external or not target:
            raise MasterIndexError(
                "a master link row is missing one of its references",
                operation=operation,
                row=row.provenance.source_row,
                source=row.provenance.source,
            )
        if target not in by_ref:
            raise DanglingLinkError(
                "the link points at an account the master data does not contain",
                operation=operation,
                row=row.provenance.source_row,
                source=row.provenance.source,
                account_ref=target,
                external_ref=external,
            )
        existing = link_map.get(external)
        if existing is None:
            link_map[external] = target
        elif existing == target:
            duplicates += 1  # the same statement twice, which is harmless
        else:
            raise ConflictingLinkError(
                "the external reference is linked to two different accounts",
                operation=operation,
                row=row.provenance.source_row,
                source=row.provenance.source,
                external_ref=external,
            )

    ordered = tuple(by_ref[ref] for ref in sorted(by_ref))
    by_name: dict[str, list[str]] = {}
    for account in ordered:
        if account.normalized_name is not None:
            by_name.setdefault(account.normalized_name, []).append(account.account_ref)

    return MasterIndex(
        accounts=ordered,
        account_by_ref=by_ref,
        account_ref_by_link=link_map,
        account_refs_by_name={name: tuple(sorted(refs)) for name, refs in by_name.items()},
        duplicate_links=duplicates,
        max_name_length=max_name_length,
    )
