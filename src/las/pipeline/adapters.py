"""The two adapters that let the composition root wire layers together.

Feed batch identity
-------------------
Each configured feed gets an identifier derived from what the feed *is*, not
from where it sits in a list or which object happens to hold it: a canonical
descriptor — POSIX-normalized config-relative path, format, worksheet, label —
encoded as compact UTF-8 JSON with fixed key order, hashed with SHA-256. Two
configurations describing the same physical feed therefore collide by design,
and that collision is the duplicate-input error. Reordering feed definitions
changes nothing.

The descriptor itself never leaves this module. It contains a path, and the
identifier exists precisely so that nothing downstream needs one.

Ledger adaptation
-----------------
``ExtractedRow`` values from the driver become neutral ``SystemLedgerRow``
values for reconciliation. The external reference is resolved through the
master index by exact full-string lookup — no fuzzy fallback, no name matching,
no address field — and an unresolvable reference is an error rather than a
dropped row. The amount crosses as text, so no binary float ever touches a
system total, and period and unit cross verbatim.

Nothing driver-shaped survives the crossing: the result holds only the neutral
DTO, ordered by position then record identifier so that shuffling the
extraction changes nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath

from las.config import FeedConfig
from las.driver import ExtractedRow
from las.match import MasterIndex
from las.pipeline.errors import DuplicateFeedError, LedgerAdapterError
from las.reconcile import SystemLedgerRow

#: Public identifier prefix and the number of hex characters kept from the
#: digest. Sixteen characters is 64 bits — far more than enough to separate a
#: handful of configured feeds, and short enough to read in a log line.
BATCH_ID_PREFIX = "feed-"
BATCH_ID_LENGTH = 16

#: What a descriptor records when a feed declares no worksheet. A fixed marker
#: rather than an omitted key, so the JSON shape never varies.
NULL_MARKER = "__none__"


def _descriptor(feed: FeedConfig, config_dir: Path) -> str:
    """The canonical description of one physical feed, as compact JSON.

    Read through ``PureWindowsPath`` and emitted as POSIX, so a configuration
    written with either separator describes the same feed. A plain ``Path`` is
    wrong here: on POSIX a backslash is an ordinary filename character, so
    ``sub\\feed.xlsx`` would stay one long name and the two spellings would
    hash differently. T2's own path validator reads paths the same way.
    """
    relative = PureWindowsPath(feed.path)
    payload = {
        "format": relative.suffix.lower().lstrip("."),
        "label": feed.name,
        "path": relative.as_posix(),
        "worksheet": NULL_MARKER,
    }
    del config_dir  # the descriptor is config-relative; the root never enters it
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def batch_id(feed: FeedConfig, config_dir: Path) -> str:
    """The deterministic public identifier for one configured feed."""
    digest = hashlib.sha256(_descriptor(feed, config_dir).encode("utf-8")).hexdigest()
    return f"{BATCH_ID_PREFIX}{digest[:BATCH_ID_LENGTH]}"


def physical_key(feed: FeedConfig) -> tuple[str, str, str]:
    """What makes a feed the *same physical input*: path, format, worksheet.

    Deliberately without the label. The configuration already forces feed
    names to be unique, so two definitions of one file always differ by name —
    if the label counted, "the same physical feed twice" could never be
    detected at all, which is the thing this check exists to catch.
    """
    relative = PureWindowsPath(feed.path)
    return (relative.as_posix(), relative.suffix.lower().lstrip("."), NULL_MARKER)


def batch_ids(feeds: Sequence[FeedConfig], config_dir: Path) -> tuple[str, ...]:
    """One identifier per configured feed, refusing duplicates and collisions.

    Two definitions of one physical file are a duplicate input and are refused
    before anything is aggregated, whatever they are named. Separately, two
    genuinely different descriptors that hashed alike would be a collision — it
    will not happen, and if it did, first-wins would silently drop a feed, so
    it raises instead.
    """
    seen_physical: set[tuple[str, str, str]] = set()
    seen_ids: dict[str, str] = {}
    identifiers: list[str] = []

    for feed in feeds:
        key = physical_key(feed)
        if key in seen_physical:
            raise DuplicateFeedError(
                "two feed definitions describe the same physical input"
            )
        seen_physical.add(key)

        descriptor = _descriptor(feed, config_dir)
        identifier = batch_id(feed, config_dir)
        previous = seen_ids.get(identifier)
        if previous is not None and previous != descriptor:
            raise DuplicateFeedError(
                "two distinct feed definitions share one identifier"
            )
        seen_ids[identifier] = descriptor
        identifiers.append(identifier)
    return tuple(identifiers)


def adapt_ledger(
    rows: Sequence[ExtractedRow], index: MasterIndex
) -> tuple[SystemLedgerRow, ...]:
    """Turn extracted rows into neutral ledger rows, in one stable order.

    Ordered by position then record identifier, so the result is the same
    tuple however the extraction happened to arrive.
    """
    adapted: list[SystemLedgerRow] = []
    seen_records: set[str] = set()
    seen_positions: set[int] = set()

    for row in rows:
        account_ref = index.resolve_link(row.external_ref)
        if account_ref is None:
            raise LedgerAdapterError(
                "an extracted row references an account the master data does not map"
            )
        if row.record_id in seen_records:
            raise LedgerAdapterError(
                "two extracted rows share one record identifier"
            )
        if row.position in seen_positions:
            raise LedgerAdapterError("two extracted rows claim one position")
        seen_records.add(row.record_id)
        seen_positions.add(row.position)

        try:
            adapted.append(
                SystemLedgerRow(
                    account_ref=account_ref,
                    period=row.period,
                    unit=row.unit,
                    amount_text=row.amount,
                    position=row.position,
                    record_id=row.record_id,
                    external_ref=row.external_ref,
                )
            )
        except Exception as exc:
            # Only the exception's type name would be safe here, and even that
            # says nothing a caller can act on: the row is unusable.
            del exc
            raise LedgerAdapterError(
                "an extracted row cannot become a system ledger row"
            ) from None

    return tuple(sorted(adapted, key=lambda item: (item.position, item.record_id or "")))
