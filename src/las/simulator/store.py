"""In-memory synthetic record store backing the simulated legacy system.

The store is initialized from the committed fixture files
(``accounts.csv`` and ``account_links.csv``) and deterministically derives
a ledger of operational records from them: one record per mapping row and
reporting period, with index-derived amounts, units, and statuses. No
randomness, wall-clock time, or machine state is involved, so two stores
built from the same fixtures are always identical.

Status lifecycle (invented for this project)
--------------------------------------------
Operational records carry a generic workflow status::

    PENDING --> READY or REVIEW
    READY   --> REVIEW or COMPLETE
    REVIEW  --> READY or COMPLETE
    COMPLETE    (terminal; no further transitions)

Any other change — including "changing" to the current status — is
rejected with :class:`InvalidStatusTransitionError`; values outside the
lifecycle are rejected with :class:`InvalidStatusError`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

ACCOUNTS_FILENAME = "accounts.csv"
LINKS_FILENAME = "account_links.csv"

ACCOUNT_FIXTURE_HEADERS = [
    "account_ref",
    "account_name",
    "contact_email",
    "street",
    "city",
    "postal_code",
    "status",
]
LINK_FIXTURE_HEADERS = ["external_ref", "account_ref"]

#: Reporting periods covered by the derived ledger.
LEDGER_PERIODS = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")
LEDGER_UNITS = ("EA", "KG", "BOX")
#: Ledger record IDs occupy a dedicated OPR- range distinct from feed rows.
LEDGER_RECORD_ID_BASE = 88000000


class SimulatorError(Exception):
    """Base class for every error raised by the simulator package."""


class FixtureLoadError(SimulatorError):
    """A fixture file is missing or does not have the expected columns."""


class UnknownRecordError(SimulatorError):
    """A lookup referenced an identifier the store does not contain."""


class InvalidStatusError(SimulatorError):
    """A status value outside the invented lifecycle was supplied."""


class InvalidStatusTransitionError(SimulatorError):
    """A status change violates the documented transition map."""


class RecordStatus(StrEnum):
    """Invented, generic workflow states for operational records."""

    PENDING = "PENDING"
    READY = "READY"
    REVIEW = "REVIEW"
    COMPLETE = "COMPLETE"


#: The only permitted status transitions (see module docstring).
STATUS_TRANSITIONS: dict[RecordStatus, frozenset[RecordStatus]] = {
    RecordStatus.PENDING: frozenset({RecordStatus.READY, RecordStatus.REVIEW}),
    RecordStatus.READY: frozenset({RecordStatus.REVIEW, RecordStatus.COMPLETE}),
    RecordStatus.REVIEW: frozenset({RecordStatus.READY, RecordStatus.COMPLETE}),
    RecordStatus.COMPLETE: frozenset(),
}


@dataclass(frozen=True)
class AccountRecord:
    """One canonical account from master data (``ACC-######``)."""

    account_ref: str
    account_name: str
    contact_email: str
    street: str
    city: str
    postal_code: str
    status: str


@dataclass(frozen=True)
class OperationalRecord:
    """One ledger record held by the simulated legacy system."""

    record_id: str
    external_ref: str
    amount: float
    unit: str
    period: str
    status: RecordStatus


@dataclass(frozen=True)
class StoreSnapshot:
    """Immutable point-in-time view of the store's contents."""

    accounts: tuple[AccountRecord, ...]
    records: tuple[OperationalRecord, ...]


def _read_fixture_csv(path: Path, expected_headers: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise FixtureLoadError(f"fixture file not found: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_headers:
            raise FixtureLoadError(
                f"{path.name} has headers {reader.fieldnames}, expected {expected_headers}"
            )
        return list(reader)


def _derive_ledger(links: list[dict[str, str]]) -> list[OperationalRecord]:
    """Deterministically derive ledger records from the mapping fixture."""
    records: list[OperationalRecord] = []
    index = 0
    for link in links:
        for period in LEDGER_PERIODS:
            records.append(
                OperationalRecord(
                    record_id=f"OPR-{LEDGER_RECORD_ID_BASE + index}",
                    external_ref=link["external_ref"],
                    amount=round(10.0 + ((index * 137) % 4900) / 10.0, 2),
                    unit=LEDGER_UNITS[index % len(LEDGER_UNITS)],
                    period=period,
                    status=list(RecordStatus)[index % len(RecordStatus)],
                )
            )
            index += 1
    return records


class RecordStore:
    """Deterministic in-memory store of accounts, links, and ledger records.

    Normal interaction with the simulated system goes through
    :class:`~las.simulator.system.LegacySystem` screens; the store's own
    read methods exist for test setup and post-run verification.
    """

    def __init__(
        self,
        accounts: list[AccountRecord],
        links: dict[str, str],
        records: list[OperationalRecord],
    ) -> None:
        self._accounts = list(accounts)
        self._accounts_by_ref = {account.account_ref: account for account in self._accounts}
        self._links = dict(links)
        self._records = list(records)
        self._record_index = {record.record_id: i for i, record in enumerate(self._records)}

    @classmethod
    def from_fixtures(cls, fixtures_dir: str | Path) -> RecordStore:
        """Build a store from the committed fixture directory."""
        directory = Path(fixtures_dir)
        account_rows = _read_fixture_csv(
            directory / ACCOUNTS_FILENAME, ACCOUNT_FIXTURE_HEADERS
        )
        link_rows = _read_fixture_csv(directory / LINKS_FILENAME, LINK_FIXTURE_HEADERS)

        accounts = [AccountRecord(**row) for row in account_rows]
        links = {row["external_ref"]: row["account_ref"] for row in link_rows}
        return cls(accounts=accounts, links=links, records=_derive_ledger(link_rows))

    # -- accounts ---------------------------------------------------------

    def list_accounts(self) -> tuple[AccountRecord, ...]:
        return tuple(self._accounts)

    def get_account(self, account_ref: str) -> AccountRecord:
        try:
            return self._accounts_by_ref[account_ref]
        except KeyError:
            raise UnknownRecordError(f"unknown account reference: '{account_ref}'") from None

    def account_for_external_ref(self, external_ref: str) -> AccountRecord:
        try:
            return self.get_account(self._links[external_ref])
        except KeyError:
            raise UnknownRecordError(f"unknown external reference: '{external_ref}'") from None

    # -- operational records ----------------------------------------------

    @property
    def record_count(self) -> int:
        return len(self._records)

    def list_records(self) -> tuple[OperationalRecord, ...]:
        return tuple(self._records)

    def get_record(self, record_id: str) -> OperationalRecord:
        try:
            return self._records[self._record_index[record_id]]
        except KeyError:
            raise UnknownRecordError(f"unknown record: '{record_id}'") from None

    def find_records(
        self,
        external_ref: str | None = None,
        period: str | None = None,
    ) -> tuple[OperationalRecord, ...]:
        """Records matching the given filters, in deterministic store order."""
        return tuple(
            record
            for record in self._records
            if (external_ref is None or record.external_ref == external_ref)
            and (period is None or record.period == period)
        )

    def rows(self, offset: int, limit: int) -> tuple[OperationalRecord, ...]:
        """One logical page of records; offsets beyond the end are empty."""
        if offset < 0 or limit < 1:
            raise ValueError("offset must be >= 0 and limit must be >= 1")
        return tuple(self._records[offset : offset + limit])

    def update_status(self, record_id: str, new_status: RecordStatus | str) -> OperationalRecord:
        """Apply a lifecycle transition; returns the updated record."""
        record = self.get_record(record_id)

        if isinstance(new_status, RecordStatus):
            target = new_status
        else:
            try:
                target = RecordStatus(str(new_status).strip())
            except ValueError:
                allowed = ", ".join(status.value for status in RecordStatus)
                raise InvalidStatusError(
                    f"'{new_status}' is not a valid status (allowed: {allowed})"
                ) from None

        if target not in STATUS_TRANSITIONS[record.status]:
            raise InvalidStatusTransitionError(
                f"status of {record.record_id} cannot change from "
                f"{record.status.value} to {target.value}"
            )

        updated = replace(record, status=target)
        self._records[self._record_index[record_id]] = updated
        return updated

    # -- verification helpers ---------------------------------------------

    def snapshot(self) -> StoreSnapshot:
        """Immutable copy of the current contents (records are frozen)."""
        return StoreSnapshot(accounts=tuple(self._accounts), records=tuple(self._records))
