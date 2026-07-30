"""T14 adapters: feed-batch identity and the ledger adapter."""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path

import pytest

from las.config import FeedConfig
from las.driver import ExtractedRow, LegacyDriver, extract_query_results
from las.ingest import ACCOUNT_LINK_SCHEMA, ACCOUNT_SCHEMA, read_source
from las.match import build_master_index
from las.pipeline import (
    BATCH_ID_LENGTH,
    BATCH_ID_PREFIX,
    DuplicateFeedError,
    LedgerAdapterError,
    adapt_ledger,
    batch_id,
    batch_ids,
)
from las.simulator import LegacySystem, RecordStore, SimulatedClock
from pipeline_helpers import SAMPLES_DIR

CONFIG_DIR = Path("/anywhere")


@pytest.fixture(scope="module")
def index():
    return build_master_index(
        read_source(SAMPLES_DIR / "accounts.csv", ACCOUNT_SCHEMA),
        read_source(SAMPLES_DIR / "account_links.csv", ACCOUNT_LINK_SCHEMA),
    )


@pytest.fixture(scope="module")
def extracted() -> tuple[ExtractedRow, ...]:
    store = RecordStore.from_fixtures(SAMPLES_DIR)
    system = LegacySystem(store, visible_rows=12, buffer_rows=3, clock=SimulatedClock())
    return extract_query_results(LegacyDriver(system)).rows


def feed(name: str = "feed_alpha", path: str = "feed_alpha.xlsx") -> FeedConfig:
    return FeedConfig(name=name, path=path)


# --- feed-batch identity ----------------------------------------------------------


def test_a_batch_id_has_the_documented_shape() -> None:
    identifier = batch_id(feed(), CONFIG_DIR)
    assert identifier.startswith(BATCH_ID_PREFIX)
    body = identifier[len(BATCH_ID_PREFIX):]
    assert len(body) == BATCH_ID_LENGTH == 16
    assert body == body.lower()
    assert all(character in "0123456789abcdef" for character in body)


def test_the_same_feed_always_gets_the_same_id() -> None:
    assert batch_id(feed(), CONFIG_DIR) == batch_id(feed(), CONFIG_DIR)


def test_different_feeds_get_different_ids() -> None:
    assert batch_id(feed(), CONFIG_DIR) != batch_id(
        feed("feed_beta", "feed_beta.csv"), CONFIG_DIR
    )


def test_the_id_ignores_the_config_directory() -> None:
    """Identity describes the feed, not where the project happens to live."""
    assert batch_id(feed(), Path("/one")) == batch_id(feed(), Path("/another"))


def test_the_id_ignores_the_path_separator() -> None:
    windows = FeedConfig(name="feed_alpha", path="sub\\feed_alpha.xlsx")
    posix = FeedConfig(name="feed_alpha", path="sub/feed_alpha.xlsx")
    assert batch_id(windows, CONFIG_DIR) == batch_id(posix, CONFIG_DIR)


def test_the_id_ignores_definition_order() -> None:
    one, two = feed(), feed("feed_beta", "feed_beta.csv")
    assert batch_ids((one, two), CONFIG_DIR) == tuple(
        reversed(batch_ids((two, one), CONFIG_DIR))
    )


def test_two_definitions_of_one_physical_feed_are_refused() -> None:
    with pytest.raises(DuplicateFeedError, match="same physical input"):
        batch_ids((feed(), feed()), CONFIG_DIR)


def test_ids_are_not_derived_from_object_identity() -> None:
    one, two = feed(), feed()
    assert one is not two
    assert batch_id(one, CONFIG_DIR) == batch_id(two, CONFIG_DIR)


def test_the_descriptor_never_escapes(capsys: pytest.CaptureFixture) -> None:
    identifier = batch_id(FeedConfig(name="feed_alpha", path="a/b/feed_alpha.xlsx"), CONFIG_DIR)
    assert "a/b" not in identifier and "feed_alpha.xlsx" not in identifier
    try:
        batch_ids((feed(), feed()), CONFIG_DIR)
    except DuplicateFeedError as error:
        assert "feed_alpha.xlsx" not in str(error)
        assert "/" not in str(error)


# --- the ledger adapter ------------------------------------------------------------


def test_all_committed_ledger_rows_adapt(extracted, index) -> None:
    rows = adapt_ledger(extracted, index)
    assert len(rows) == len(extracted) == 270
    assert all(row.account_ref.startswith("ACC-") for row in rows)


def test_the_adapter_resolves_through_the_exact_link(extracted, index) -> None:
    rows = adapt_ledger(extracted, index)
    by_record = {row.record_id: row for row in rows}
    for source in extracted:
        assert by_record[source.record_id].account_ref == index.resolve_link(
            source.external_ref
        )


def test_the_adapter_preserves_amount_text_verbatim(extracted, index) -> None:
    rows = {row.record_id: row for row in adapt_ledger(extracted, index)}
    for source in extracted:
        assert rows[source.record_id].amount_text == source.amount
        assert isinstance(rows[source.record_id].amount_text, str)


def test_the_adapter_preserves_period_unit_position_and_record(extracted, index) -> None:
    rows = {row.record_id: row for row in adapt_ledger(extracted, index)}
    for source in extracted:
        adapted = rows[source.record_id]
        assert adapted.period == source.period
        assert adapted.unit == source.unit
        assert adapted.position == source.position
        assert adapted.external_ref == source.external_ref


def test_the_adapted_order_is_position_then_record(extracted, index) -> None:
    rows = adapt_ledger(extracted, index)
    keys = [(row.position, row.record_id) for row in rows]
    assert keys == sorted(keys)


def test_shuffling_the_extraction_changes_nothing(extracted, index) -> None:
    baseline = adapt_ledger(extracted, index)
    shuffler = random.Random(14)
    rows = list(extracted)
    for _ in range(5):
        shuffler.shuffle(rows)
        assert adapt_ledger(rows, index) == baseline


def test_an_unknown_reference_is_refused(extracted, index) -> None:
    stray = dataclasses.replace(extracted[0], external_ref="EXT-99999")
    with pytest.raises(LedgerAdapterError, match="does not map"):
        adapt_ledger([stray], index)


def test_a_duplicate_record_identity_is_refused(extracted, index) -> None:
    twin = dataclasses.replace(extracted[0], position=999)
    with pytest.raises(LedgerAdapterError, match="record identifier"):
        adapt_ledger([extracted[0], twin], index)


def test_a_duplicate_position_is_refused(extracted, index) -> None:
    twin = dataclasses.replace(extracted[0], record_id="OPR-88999999")
    with pytest.raises(LedgerAdapterError, match="position"):
        adapt_ledger([extracted[0], twin], index)


def test_the_adapter_retains_no_driver_object(extracted, index) -> None:
    from las.reconcile import SystemLedgerRow

    for row in adapt_ledger(extracted, index):
        assert isinstance(row, SystemLedgerRow)
        assert type(row).__module__ == "las.reconcile.models"
        for value in vars(row).values():
            assert not isinstance(value, ExtractedRow)


def test_the_adapter_uses_no_name_or_address(index) -> None:
    """Only the link table decides; account names never enter the lookup."""
    import ast
    import inspect

    from las.pipeline import adapters

    source = inspect.getsource(adapters.adapt_ledger)
    tree = ast.parse(source.lstrip())
    text = ast.unparse(tree)
    for forbidden in ("account_name", "normalize_name", "similarity", "street",
                      "city", "postal", "contact_email", "accounts_named"):
        assert forbidden not in text


def test_the_adapter_mutates_nothing(extracted, index) -> None:
    before = [dataclasses.astuple(row) for row in extracted]
    adapt_ledger(extracted, index)
    assert [dataclasses.astuple(row) for row in extracted] == before


def test_an_adapter_error_names_no_row_value(extracted, index) -> None:
    stray = dataclasses.replace(extracted[0], external_ref="EXT-99999")
    with pytest.raises(LedgerAdapterError) as excinfo:
        adapt_ledger([stray], index)
    message = str(excinfo.value)
    assert "EXT-99999" not in message
    assert stray.amount not in message
    assert stray.record_id not in message
