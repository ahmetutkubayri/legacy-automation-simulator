"""Record-store behavior: fixture loading, lookups, statuses, snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest

from las.simulator import (
    FixtureLoadError,
    InvalidStatusError,
    InvalidStatusTransitionError,
    RecordStatus,
    RecordStore,
    UnknownRecordError,
)
from las.simulator.store import LEDGER_PERIODS, STATUS_TRANSITIONS

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"

EXPECTED_ACCOUNTS = 40
EXPECTED_LINKS = 45
EXPECTED_RECORDS = EXPECTED_LINKS * len(LEDGER_PERIODS)


@pytest.fixture()
def store() -> RecordStore:
    return RecordStore.from_fixtures(SAMPLES_DIR)


# --- deterministic loading ---------------------------------------------------


def test_two_loads_produce_identical_contents(store: RecordStore) -> None:
    other = RecordStore.from_fixtures(SAMPLES_DIR)
    assert store.snapshot() == other.snapshot()


def test_row_counts_and_deterministic_ordering(store: RecordStore) -> None:
    accounts = store.list_accounts()
    records = store.list_records()
    assert len(accounts) == EXPECTED_ACCOUNTS
    assert store.record_count == EXPECTED_RECORDS
    assert accounts[0].account_ref == "ACC-100000"
    assert [record.record_id for record in records[:3]] == [
        "OPR-88000000",
        "OPR-88000001",
        "OPR-88000002",
    ]
    # The first link's records cover the ledger periods in order.
    assert [record.period for record in records[: len(LEDGER_PERIODS)]] == list(LEDGER_PERIODS)


def test_missing_fixture_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(FixtureLoadError, match="not found"):
        RecordStore.from_fixtures(tmp_path)


def test_wrong_fixture_headers_raise(tmp_path: Path) -> None:
    (tmp_path / "accounts.csv").write_text("wrong,headers\n1,2\n", encoding="utf-8")
    (tmp_path / "account_links.csv").write_text("external_ref,account_ref\n", encoding="utf-8")
    with pytest.raises(FixtureLoadError, match="headers"):
        RecordStore.from_fixtures(tmp_path)


# --- account lookups ---------------------------------------------------------


def test_account_lookup_by_ref(store: RecordStore) -> None:
    account = store.get_account("ACC-100000")
    assert account.account_name == "Demo Account 000000"


def test_account_lookup_via_external_ref(store: RecordStore) -> None:
    account = store.account_for_external_ref("EXT-10000")
    assert account.account_ref == "ACC-100000"


@pytest.mark.parametrize("lookup", ["ACC-999999", "", "nonsense"])
def test_unknown_account_raises(store: RecordStore, lookup: str) -> None:
    with pytest.raises(UnknownRecordError, match="unknown account"):
        store.get_account(lookup)


def test_unknown_external_ref_raises(store: RecordStore) -> None:
    with pytest.raises(UnknownRecordError, match="unknown external reference"):
        store.account_for_external_ref("EXT-99999")


# --- record queries and pagination -------------------------------------------


def test_find_records_by_external_ref(store: RecordStore) -> None:
    matches = store.find_records(external_ref="EXT-10000")
    assert len(matches) == len(LEDGER_PERIODS)
    assert all(record.external_ref == "EXT-10000" for record in matches)


def test_find_records_by_ref_and_period_is_unique(store: RecordStore) -> None:
    matches = store.find_records(external_ref="EXT-10000", period="2026-01")
    assert len(matches) == 1


def test_find_records_no_match_is_empty(store: RecordStore) -> None:
    assert store.find_records(external_ref="EXT-90000") == ()


def test_rows_pagination_windows(store: RecordStore) -> None:
    assert store.rows(0, 10) == store.list_records()[0:10]
    assert store.rows(EXPECTED_RECORDS - 3, 10) == store.list_records()[-3:]
    assert store.rows(EXPECTED_RECORDS + 5, 10) == ()  # zero rows past the end


def test_rows_rejects_invalid_arguments(store: RecordStore) -> None:
    with pytest.raises(ValueError):
        store.rows(-1, 10)
    with pytest.raises(ValueError):
        store.rows(0, 0)


# --- status lifecycle --------------------------------------------------------


def test_every_documented_transition_is_accepted(store: RecordStore) -> None:
    for source, targets in STATUS_TRANSITIONS.items():
        for target in targets:
            record = next(r for r in store.list_records() if r.status == source)
            updated = store.update_status(record.record_id, target)
            assert updated.status == target
            assert store.get_record(record.record_id).status == target


def test_transitions_outside_the_map_are_rejected(store: RecordStore) -> None:
    pending = next(r for r in store.list_records() if r.status == RecordStatus.PENDING)
    with pytest.raises(InvalidStatusTransitionError, match="PENDING to COMPLETE"):
        store.update_status(pending.record_id, RecordStatus.COMPLETE)

    complete = next(r for r in store.list_records() if r.status == RecordStatus.COMPLETE)
    for target in RecordStatus:
        with pytest.raises(InvalidStatusTransitionError):
            store.update_status(complete.record_id, target)


def test_transition_to_same_status_is_rejected(store: RecordStore) -> None:
    ready = next(r for r in store.list_records() if r.status == RecordStatus.READY)
    with pytest.raises(InvalidStatusTransitionError):
        store.update_status(ready.record_id, RecordStatus.READY)


def test_unknown_status_value_is_rejected(store: RecordStore) -> None:
    record = store.list_records()[0]
    with pytest.raises(InvalidStatusError, match="allowed"):
        store.update_status(record.record_id, "ARCHIVED")


def test_status_update_on_unknown_record_raises(store: RecordStore) -> None:
    with pytest.raises(UnknownRecordError):
        store.update_status("OPR-00000000", RecordStatus.READY)


def test_status_strings_are_accepted_and_stripped(store: RecordStore) -> None:
    pending = next(r for r in store.list_records() if r.status == RecordStatus.PENDING)
    updated = store.update_status(pending.record_id, "  READY ")
    assert updated.status == RecordStatus.READY


# --- snapshots ---------------------------------------------------------------


def test_snapshot_is_unaffected_by_later_updates(store: RecordStore) -> None:
    before = store.snapshot()
    pending = next(r for r in before.records if r.status == RecordStatus.PENDING)
    store.update_status(pending.record_id, RecordStatus.READY)

    unchanged = next(r for r in before.records if r.record_id == pending.record_id)
    assert unchanged.status == RecordStatus.PENDING
    after = store.snapshot()
    assert after != before
    assert len(after.records) == len(before.records)
