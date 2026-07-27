"""Shared, non-test fixtures for the matching suites.

Deliberately not named ``test_*``: several T11 modules import it, and importing
shared data from another test module would couple those modules together.
"""

from __future__ import annotations

from collections.abc import Iterable

from las.ingest import IngestedRow, RowProvenance
from las.match import (
    DEFAULT_MAX_NAME_LENGTH,
    DEFAULT_SOURCE_NAME_KEYS,
    MasterIndex,
    MatchPolicy,
    build_master_index,
)

#: The one name-length bound every helper-built index and policy is made from.
#: Matching rejects a pair whose caps differ, so both helpers read this single
#: name rather than each falling back to a default that happens to equal the
#: other's. Use :func:`make_pair` whenever a test needs a different value.
SHARED_MAX_NAME_LENGTH: int = DEFAULT_MAX_NAME_LENGTH

#: Two accounts whose names differ only in their last character, mirroring the
#: generator's deliberately near-duplicate pair.
TWIN_A = ("ACC-100038", "Synthetic Twin Account 900001")
TWIN_B = ("ACC-100039", "Synthetic Twin Account 900002")
NEAR_TWIN_NAME = "Synthetic Twin Account 90000"

PLAIN_ACCOUNTS = (
    ("ACC-100000", "Demo Account 000000"),
    ("ACC-100001", "Demo Account 000001"),
    ("ACC-100002", "Demo Account 000002"),
)
DEFAULT_LINKS = (
    ("EXT-10000", "ACC-100000"),
    ("EXT-10001", "ACC-100001"),
)


def account_row(account_ref: str, account_name: str, source_row: int = 2) -> IngestedRow:
    return IngestedRow(
        provenance=RowProvenance(source="accounts.csv", source_row=source_row),
        values={"account_ref": account_ref, "account_name": account_name},
    )


def link_row(external_ref: str, account_ref: str, source_row: int = 2) -> IngestedRow:
    return IngestedRow(
        provenance=RowProvenance(source="account_links.csv", source_row=source_row),
        values={"external_ref": external_ref, "account_ref": account_ref},
    )


def make_index(
    accounts: Iterable[tuple[str, str]] = (*PLAIN_ACCOUNTS, TWIN_A, TWIN_B),
    links: Iterable[tuple[str, str]] = DEFAULT_LINKS,
    *,
    max_name_length: int = SHARED_MAX_NAME_LENGTH,
) -> MasterIndex:
    """A master index built from plain (ref, name) and (link, ref) pairs."""
    return build_master_index(
        [account_row(ref, name, 2 + i) for i, (ref, name) in enumerate(accounts)],
        [link_row(ext, ref, 2 + i) for i, (ext, ref) in enumerate(links)],
        max_name_length=max_name_length,
    )


def make_policy(
    *,
    accept_bp: int = 8700,
    review_bp: int = 7000,
    source_name_keys: Iterable[str] = DEFAULT_SOURCE_NAME_KEYS,
    max_candidates: int | None = None,
    max_name_length: int = SHARED_MAX_NAME_LENGTH,
) -> MatchPolicy:
    """A policy mirroring the committed sample configuration's thresholds."""
    return MatchPolicy(
        accept_bp=accept_bp,
        review_bp=review_bp,
        source_name_keys=tuple(source_name_keys),
        max_candidates=max_candidates,
        max_name_length=max_name_length,
    )


def make_pair(
    *,
    max_name_length: int = SHARED_MAX_NAME_LENGTH,
    accounts: Iterable[tuple[str, str]] = (*PLAIN_ACCOUNTS, TWIN_A, TWIN_B),
    links: Iterable[tuple[str, str]] = DEFAULT_LINKS,
    **policy_options,
) -> tuple[MasterIndex, MatchPolicy]:
    """An index and a policy built from one bound, named once.

    The way to vary the cap in a test: passing it here reaches both sides, so
    a test cannot accidentally build the halves of a matching pair under two
    different bounds.
    """
    return (
        make_index(accounts, links, max_name_length=max_name_length),
        make_policy(max_name_length=max_name_length, **policy_options),
    )


def feed_row(
    source_row: int = 2,
    *,
    external_ref: str | None = "EXT-10000",
    source_name: str | None = "Demo Account 000000",
    name_key: str = "reported by",
    source: str = "probe.csv",
    worksheet: str | None = None,
    **values,
) -> IngestedRow:
    """One ingested feed row, with its source name in the retained extras."""
    payload: dict[str, str | None] = {
        "amount": "10.50",
        "external_ref": external_ref,
        "period": "2026-01",
        "record_id": "OPR-00000001",
        "unit": "EA",
    }
    payload.update(values)
    extras: dict[str, str | None] = {}
    if source_name is not None:
        extras[name_key] = source_name
    return IngestedRow(
        provenance=RowProvenance(
            source=source, source_row=source_row, worksheet=worksheet
        ),
        values=payload,
        extras=extras,
    )


class Explosive:
    """A value whose serialization raises, proving nothing stringifies it."""

    def __repr__(self) -> str:  # pragma: no cover - must never run
        raise AssertionError("the value must never be serialized")

    def __str__(self) -> str:  # pragma: no cover - must never run
        raise AssertionError("the value must never be serialized")
