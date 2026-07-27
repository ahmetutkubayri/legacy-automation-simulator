"""T11 master index: order-independent, duplicate-preserving, strict."""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError

import pytest

from las.match import (
    ConflictingLinkError,
    DanglingLinkError,
    DuplicateAccountError,
    MasterIndexError,
    build_master_index,
)
from match_helpers import (
    DEFAULT_LINKS,
    PLAIN_ACCOUNTS,
    TWIN_A,
    TWIN_B,
    account_row,
    link_row,
    make_index,
)


def test_a_normal_index_exposes_accounts_links_and_names() -> None:
    index = make_index()
    assert index.account_count == 5
    assert index.link_count == 2
    assert index.duplicate_links == 0
    assert index.resolve_link("EXT-10000") == "ACC-100000"
    assert index.resolve_link("EXT-99999") is None
    assert index.accounts_named("demo account 000000") == ("ACC-100000",)
    assert index.accounts_named("nothing like a name") == ()


def test_accounts_are_stored_sorted_by_reference_not_by_file_order() -> None:
    forward = make_index(accounts=(*PLAIN_ACCOUNTS, TWIN_A, TWIN_B))
    reverse = make_index(accounts=tuple(reversed((*PLAIN_ACCOUNTS, TWIN_A, TWIN_B))))
    assert [a.account_ref for a in forward.accounts] == sorted(
        a.account_ref for a in forward.accounts
    )
    assert forward.accounts == reverse.accounts


def test_shuffled_account_order_yields_an_identical_index() -> None:
    accounts = [*PLAIN_ACCOUNTS, TWIN_A, TWIN_B]
    shuffler = random.Random(9)
    baseline = make_index(accounts=tuple(accounts))
    for _ in range(10):
        shuffler.shuffle(accounts)
        assert make_index(accounts=tuple(accounts)) == baseline


def test_shuffled_link_order_yields_an_identical_index() -> None:
    links = [("EXT-10000", "ACC-100000"), ("EXT-10001", "ACC-100001"),
             ("EXT-10002", "ACC-100002")]
    shuffler = random.Random(4)
    baseline = make_index(links=tuple(links))
    for _ in range(10):
        shuffler.shuffle(links)
        assert make_index(links=tuple(links)) == baseline


def test_an_index_with_no_accounts_and_no_links() -> None:
    index = build_master_index([], [])
    assert index.account_count == 0
    assert index.link_count == 0
    assert index.comparable_accounts() == ()
    assert index.resolve_link("EXT-10000") is None


def test_an_index_with_accounts_but_no_links() -> None:
    index = make_index(links=())
    assert index.account_count == 5
    assert index.link_count == 0


def test_a_duplicate_account_reference_is_rejected() -> None:
    with pytest.raises(DuplicateAccountError, match="twice") as excinfo:
        build_master_index(
            [account_row("ACC-100000", "Demo Account 000000"),
             account_row("ACC-100000", "Demo Account 000001", 3)],
            [],
        )
    assert excinfo.value.account_ref == "ACC-100000"
    assert "Demo Account" not in str(excinfo.value)


def test_an_account_row_without_a_reference_is_rejected() -> None:
    with pytest.raises(MasterIndexError, match="no account reference"):
        build_master_index([account_row("", "Demo Account 000000")], [])


def test_a_repeated_identical_link_is_counted_not_rejected() -> None:
    """Saying the same thing twice is harmless, but visible."""
    index = build_master_index(
        [account_row("ACC-100000", "Demo Account 000000")],
        [link_row("EXT-10000", "ACC-100000"), link_row("EXT-10000", "ACC-100000", 3)],
    )
    assert index.link_count == 1
    assert index.duplicate_links == 1


def test_a_conflicting_link_is_rejected() -> None:
    with pytest.raises(ConflictingLinkError, match="two different accounts") as excinfo:
        build_master_index(
            [account_row("ACC-100000", "Demo Account 000000"),
             account_row("ACC-100001", "Demo Account 000001", 3)],
            [link_row("EXT-10000", "ACC-100000"), link_row("EXT-10000", "ACC-100001", 3)],
        )
    assert excinfo.value.external_ref == "EXT-10000"


def test_a_link_to_a_missing_account_is_rejected() -> None:
    with pytest.raises(DanglingLinkError, match="does not contain") as excinfo:
        build_master_index(
            [account_row("ACC-100000", "Demo Account 000000")],
            [link_row("EXT-10000", "ACC-999999")],
        )
    assert excinfo.value.account_ref == "ACC-999999"


def test_a_link_row_missing_a_reference_is_rejected() -> None:
    for values in (("", "ACC-100000"), ("EXT-10000", "")):
        with pytest.raises(MasterIndexError, match="missing one of its references"):
            build_master_index(
                [account_row("ACC-100000", "Demo Account 000000")],
                [link_row(*values)],
            )


def test_duplicate_account_names_are_kept_as_separate_candidates() -> None:
    """A dictionary would silently drop one; a tuple keeps both."""
    index = build_master_index(
        [account_row("ACC-100000", "Shared Name 000001"),
         account_row("ACC-100001", "Shared Name 000001", 3),
         account_row("ACC-100002", "Distinct Name 000002", 4)],
        [],
    )
    assert index.accounts_named("shared name 000001") == ("ACC-100000", "ACC-100001")
    assert index.accounts_named("distinct name 000002") == ("ACC-100002",)


def test_duplicate_name_candidates_are_sorted_regardless_of_input_order() -> None:
    forward = build_master_index(
        [account_row("ACC-100001", "Shared 000001"), account_row("ACC-100000", "Shared 000001", 3)],
        [],
    )
    assert forward.accounts_named("shared 000001") == ("ACC-100000", "ACC-100001")


def test_an_uncomparable_account_name_is_excluded_from_scoring_candidates() -> None:
    index = build_master_index(
        [account_row("ACC-100000", "Demo Account 000000"),
         account_row("ACC-100001", "   ", 3),
         account_row("ACC-100002", "Bad\x00Name", 4)],
        [],
    )
    assert index.account_count == 3  # every account is kept
    assert [a.account_ref for a in index.comparable_accounts()] == ["ACC-100000"]
    assert index.account_by_ref["ACC-100001"].normalized_name is None


def test_the_index_is_deeply_immutable() -> None:
    index = make_index()
    with pytest.raises(FrozenInstanceError):
        index.accounts = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        index.accounts.append("x")  # type: ignore[attr-defined]
    for mapping in (index.account_by_ref, index.account_ref_by_link,
                    index.account_refs_by_name):
        with pytest.raises(TypeError):
            mapping["injected"] = "x"  # type: ignore[index]
        with pytest.raises(AttributeError):
            mapping.clear()  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        index.accounts[0].account_name = "changed"  # type: ignore[misc]


def test_building_the_index_does_not_mutate_its_inputs() -> None:
    accounts = [account_row(ref, name, 2 + i)
                for i, (ref, name) in enumerate((*PLAIN_ACCOUNTS, TWIN_A, TWIN_B))]
    links = [link_row(ext, ref, 2 + i) for i, (ext, ref) in enumerate(DEFAULT_LINKS)]
    before = ([dict(r.values) for r in accounts], [dict(r.values) for r in links])
    build_master_index(accounts, links)
    assert ([dict(r.values) for r in accounts], [dict(r.values) for r in links]) == before


def test_a_master_account_requires_a_reference() -> None:
    from las.match import MasterAccount

    with pytest.raises(MasterIndexError, match="account reference"):
        MasterAccount(account_ref="", account_name="Demo", normalized_name="demo")
