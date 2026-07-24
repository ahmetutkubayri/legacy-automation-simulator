"""Deterministic synthetic fixture generator for the Legacy Automation Simulator.

Generates four sample datasets under ``data/samples/`` (or a chosen
directory), fully derived from an explicit seed. Every value is invented
and *visibly* synthetic: account IDs ``ACC-######``, external references
``EXT-#####``, operational record IDs ``OPR-########``, template-based
names and addresses ("Demo Account 000007", "100007 Example Avenue",
"Synthetic City 07"), and email addresses only on ``example.com``. No
name generator is used, so values cannot accidentally coincide with a
real person, company, or address.

Datasets
--------
* ``accounts.csv`` — synthetic account master data (UTF-8, LF, comma).
* ``account_links.csv`` — external reference -> account mapping (UTF-8, LF).
* ``feed_alpha.xlsx`` — operational records, Title Case headers.
* ``feed_beta.csv`` — the same record concept with different column names,
  column order, a semicolon delimiter, and a UTF-8 BOM (deliberate
  ingest obstacles).

Injected defects (all counts deterministic, documented, and disjoint)
---------------------------------------------------------------------
* ``duplicate_records`` (4): feed_alpha rows written twice, byte-identical.
* ``invalid_record_id_format`` (3): feed_beta record IDs that break the
  ``OPR-########`` pattern.
* ``orphan_external_refs`` (5): feed_beta rows referencing ``EXT-9####``
  values that exist in no mapping row.
* ``missing_required_values`` (4): rows with an empty amount (2 per feed).
* ``invalid_amounts`` (3): feed_alpha amounts that are negative or
  unparseable.
* ``unit_mismatches`` (3): the same record ID present in both feeds with
  identical amount but conflicting units.
* ``ambiguous_matches`` (2): feed_alpha rows with no usable reference
  whose source name is nearly equidistant from two deliberately
  near-duplicate accounts ("... Ltd" vs "... Ltd.").

Determinism strategy
--------------------
No wall-clock time, machine name, username, locale, or filesystem
metadata influences output. Names and addresses are index-derived
templates; only row-level choices (units, periods, amounts, link
selection) come from a seeded ``random.Random``. CSV files are written
UTF-8 with LF line endings. The XLSX is made byte-identical across runs by (a) pinning the
workbook's creator/created/modified properties to fixed values and
(b) repacking the zip container with sorted entries, a fixed 1980-01-01
entry timestamp, and fixed platform attributes.

Usage
-----
    python scripts/generate_fixtures.py [--seed 42] [--output-dir data/samples]

Prints a machine-readable JSON summary (seed, per-file row counts and
paths, defect counts by type).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook

DEFAULT_SEED = 42
DEFAULT_OUTPUT_DIR = Path("data") / "samples"

ACCOUNTS_FILENAME = "accounts.csv"
LINKS_FILENAME = "account_links.csv"
FEED_ALPHA_FILENAME = "feed_alpha.xlsx"
FEED_BETA_FILENAME = "feed_beta.csv"

ACCOUNT_COUNT = 40
LINK_COUNT = 45
CLEAN_ALPHA_COUNT = 100
CLEAN_BETA_COUNT = 80

DUPLICATE_RECORD_COUNT = 4
INVALID_RECORD_ID_COUNT = 3
ORPHAN_REFERENCE_COUNT = 5
MISSING_VALUE_COUNT = 4  # 2 in each feed
INVALID_AMOUNT_COUNT = 3
UNIT_MISMATCH_COUNT = 3
AMBIGUOUS_MATCH_COUNT = 2

FEED_ALPHA_ROW_COUNT = (
    CLEAN_ALPHA_COUNT
    + UNIT_MISMATCH_COUNT
    + DUPLICATE_RECORD_COUNT * 2
    + MISSING_VALUE_COUNT // 2
    + INVALID_AMOUNT_COUNT
    + AMBIGUOUS_MATCH_COUNT
)
FEED_BETA_ROW_COUNT = (
    CLEAN_BETA_COUNT
    + UNIT_MISMATCH_COUNT
    + INVALID_RECORD_ID_COUNT
    + ORPHAN_REFERENCE_COUNT
    + MISSING_VALUE_COUNT // 2
)

ACCOUNT_HEADERS = [
    "account_ref",
    "account_name",
    "contact_email",
    "street",
    "city",
    "postal_code",
    "status",
]
LINK_HEADERS = ["external_ref", "account_ref"]
FEED_ALPHA_HEADERS = [
    "Record Id",
    "External Reference",
    "Quantity",
    "Quantity Unit",
    "Reporting Period",
    "Reported By",
]
FEED_BETA_HEADERS = ["PRD", "AMT", "EXT_REF", "REC_ID", "UOM", "SRC_NAME"]

PERIODS = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")
UNITS = ("EA", "KG", "BOX")
STATUSES = ("ACTIVE", "DORMANT", "CLOSED")

INVALID_AMOUNT_VALUES = ("-42.50", "not-a-number", "12.34.56")
INVALID_RECORD_ID_VALUES = ("OPR12345678", "opr-00000001", "REC-1")

#: Deliberately near-duplicate accounts used for the ambiguity fixtures.
#: Visibly synthetic, differing only in their final digit.
AMBIGUOUS_ACCOUNT_NAMES = ("Synthetic Twin Account 900001", "Synthetic Twin Account 900002")
AMBIGUOUS_SOURCE_NAME = "Synthetic Twin Account 90000"

#: Fixed, neutral XLSX document properties (no user or machine identity).
XLSX_CREATOR = "synthetic-fixture-generator"
XLSX_TIMESTAMP = datetime(2026, 1, 1, 0, 0, 0)
_ZIP_ENTRY_DATE = (1980, 1, 1, 0, 0, 0)


def _build_accounts(rng: random.Random) -> list[dict[str, str]]:
    """Template-derived, visibly synthetic account master data."""
    accounts: list[dict[str, str]] = []
    for index in range(ACCOUNT_COUNT - AMBIGUOUS_MATCH_COUNT):
        accounts.append(
            {
                "account_ref": f"ACC-{100000 + index}",
                "account_name": f"Demo Account {index:06d}",
                "contact_email": f"contact-{index:06d}@example.com",
                "street": f"{100000 + index} Example Avenue",
                "city": f"Synthetic City {index % 10:02d}",
                "postal_code": f"{(10000 + index * 137) % 100000:05d}",
                "status": rng.choice(STATUSES),
            }
        )
    # The near-duplicate pair backing the ambiguous-match fixtures.
    for offset, name in enumerate(AMBIGUOUS_ACCOUNT_NAMES):
        index = ACCOUNT_COUNT - AMBIGUOUS_MATCH_COUNT + offset
        accounts.append(
            {
                "account_ref": f"ACC-{100000 + index}",
                "account_name": name,
                "contact_email": f"contact-{index:06d}@example.com",
                "street": f"{900001 + offset} Example Avenue",
                "city": "Synthetic City 99",
                "postal_code": f"9921{offset}",
                "status": "ACTIVE",
            }
        )
    return accounts


def _build_links(accounts: Sequence[dict[str, str]], rng: random.Random) -> list[dict[str, str]]:
    links = [
        {"external_ref": f"EXT-{10000 + index}", "account_ref": account["account_ref"]}
        for index, account in enumerate(accounts)
    ]
    for extra in range(LINK_COUNT - len(accounts)):
        target = rng.choice(accounts[: ACCOUNT_COUNT - AMBIGUOUS_MATCH_COUNT])
        links.append(
            {
                "external_ref": f"EXT-{10000 + len(accounts) + extra}",
                "account_ref": target["account_ref"],
            }
        )
    return links


def _clean_row(
    record_id: str,
    links: Sequence[dict[str, str]],
    names_by_account: dict[str, str],
    rng: random.Random,
) -> dict[str, str | float]:
    link = rng.choice(links)
    return {
        "record_id": record_id,
        "external_ref": link["external_ref"],
        "amount": round(rng.uniform(1.0, 500.0), 2),
        "unit": rng.choice(UNITS),
        "period": rng.choice(PERIODS),
        "source_name": names_by_account[link["account_ref"]],
    }


def _build_feeds(
    accounts: Sequence[dict[str, str]],
    links: Sequence[dict[str, str]],
    rng: random.Random,
) -> tuple[list[dict[str, str | float]], list[dict[str, str | float]]]:
    names_by_account = {a["account_ref"]: a["account_name"] for a in accounts}
    alpha: list[dict[str, str | float]] = []
    beta: list[dict[str, str | float]] = []

    for index in range(CLEAN_ALPHA_COUNT):
        alpha.append(_clean_row(f"OPR-{10000000 + index}", links, names_by_account, rng))
    for index in range(CLEAN_BETA_COUNT):
        beta.append(_clean_row(f"OPR-{20000000 + index}", links, names_by_account, rng))

    # Unit mismatches: same record and amount in both feeds, conflicting unit.
    for index in range(UNIT_MISMATCH_COUNT):
        row = _clean_row(f"OPR-{30000000 + index}", links, names_by_account, rng)
        alpha.append({**row, "unit": "EA"})
        beta.append({**row, "unit": "KG"})

    # Duplicates: the same feed_alpha row appears twice, byte-identical.
    for index in range(DUPLICATE_RECORD_COUNT):
        row = _clean_row(f"OPR-{40000000 + index}", links, names_by_account, rng)
        alpha.append(row)
        alpha.append(dict(row))

    # Missing required values: amount left empty (2 per feed).
    for index in range(MISSING_VALUE_COUNT // 2):
        row = _clean_row(f"OPR-{50000000 + index}", links, names_by_account, rng)
        alpha.append({**row, "amount": ""})
    for index in range(MISSING_VALUE_COUNT // 2):
        row = _clean_row(f"OPR-{51000000 + index}", links, names_by_account, rng)
        beta.append({**row, "amount": ""})

    # Invalid amounts: negative or unparseable strings (feed_alpha).
    for index, bad_amount in enumerate(INVALID_AMOUNT_VALUES):
        row = _clean_row(f"OPR-{60000000 + index}", links, names_by_account, rng)
        alpha.append({**row, "amount": bad_amount})

    # Invalid record-ID formats (feed_beta).
    for index, bad_id in enumerate(INVALID_RECORD_ID_VALUES):
        row = _clean_row(f"OPR-{70000000 + index}", links, names_by_account, rng)
        beta.append({**row, "record_id": bad_id})

    # Orphan references: EXT-9#### values that no mapping row contains.
    for index in range(ORPHAN_REFERENCE_COUNT):
        row = _clean_row(f"OPR-{80000000 + index}", links, names_by_account, rng)
        beta.append(
            {
                **row,
                "external_ref": f"EXT-{90000 + index}",
                "source_name": f"Unlisted Source {index + 1}",
            }
        )

    # Ambiguous matches: no usable reference, source name close to both twins.
    for index in range(AMBIGUOUS_MATCH_COUNT):
        row = _clean_row(f"OPR-{90000000 + index}", links, names_by_account, rng)
        alpha.append(
            {
                **row,
                "external_ref": "",
                "source_name": AMBIGUOUS_SOURCE_NAME,
            }
        )

    return alpha, beta


def _write_csv(path: Path, headers: Sequence[str], rows: list[list[str]], **fmt: str) -> None:
    encoding = fmt.get("encoding", "utf-8")
    delimiter = fmt.get("delimiter", ",")
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def _write_xlsx_deterministic(path: Path, headers: Sequence[str], rows: list[list]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(list(headers))
    for row in rows:
        sheet.append(row)

    properties = workbook.properties
    properties.creator = XLSX_CREATOR
    properties.lastModifiedBy = XLSX_CREATOR
    properties.created = XLSX_TIMESTAMP
    properties.modified = XLSX_TIMESTAMP

    buffer = io.BytesIO()
    workbook.save(buffer)
    path.write_bytes(_repack_zip_deterministic(buffer.getvalue()))


def _repack_zip_deterministic(payload: bytes) -> bytes:
    """Rewrite a zip container with fixed timestamps and platform metadata.

    Two sources of nondeterminism are removed here: ``zipfile`` stamps
    every entry with the current local time and producing platform, and
    openpyxl overwrites the document's ``dcterms:modified`` property with
    the save time regardless of what the workbook properties were set to.
    """
    fixed_stamp = XLSX_TIMESTAMP.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    source = ZipFile(io.BytesIO(payload))
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            data = source.read(name)
            if name == "docProps/core.xml":
                data = re.sub(
                    rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:)",
                    rb"\g<1>" + fixed_stamp + rb"\g<2>",
                    data,
                )
            info = ZipInfo(name, date_time=_ZIP_ENTRY_DATE)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            target.writestr(info, data)
    return buffer.getvalue()


def _alpha_cells(row: dict[str, str | float]) -> list:
    return [
        row["record_id"],
        row["external_ref"],
        row["amount"],
        row["unit"],
        row["period"],
        row["source_name"],
    ]


def _beta_cells(row: dict[str, str | float]) -> list[str]:
    amount = row["amount"]
    amount_text = f"{amount:.2f}" if isinstance(amount, float) else str(amount)
    return [
        str(row["period"]),
        amount_text,
        str(row["external_ref"]),
        str(row["record_id"]),
        str(row["unit"]),
        str(row["source_name"]),
    ]


def generate_fixtures(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Generate all four datasets and return a machine-readable summary."""
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)

    accounts = _build_accounts(rng)
    links = _build_links(accounts, rng)
    alpha, beta = _build_feeds(accounts, links, rng)

    accounts_path = target_dir / ACCOUNTS_FILENAME
    links_path = target_dir / LINKS_FILENAME
    alpha_path = target_dir / FEED_ALPHA_FILENAME
    beta_path = target_dir / FEED_BETA_FILENAME

    _write_csv(
        accounts_path,
        ACCOUNT_HEADERS,
        [[account[column] for column in ACCOUNT_HEADERS] for account in accounts],
    )
    _write_csv(
        links_path,
        LINK_HEADERS,
        [[link[column] for column in LINK_HEADERS] for link in links],
    )
    _write_xlsx_deterministic(alpha_path, FEED_ALPHA_HEADERS, [_alpha_cells(r) for r in alpha])
    _write_csv(
        beta_path,
        FEED_BETA_HEADERS,
        [_beta_cells(row) for row in beta],
        encoding="utf-8-sig",
        delimiter=";",
    )

    return {
        "seed": seed,
        "files": {
            ACCOUNTS_FILENAME: {"path": str(accounts_path), "rows": len(accounts)},
            LINKS_FILENAME: {"path": str(links_path), "rows": len(links)},
            FEED_ALPHA_FILENAME: {"path": str(alpha_path), "rows": len(alpha)},
            FEED_BETA_FILENAME: {"path": str(beta_path), "rows": len(beta)},
        },
        "defects": {
            "duplicate_records": DUPLICATE_RECORD_COUNT,
            "invalid_record_id_format": INVALID_RECORD_ID_COUNT,
            "orphan_external_refs": ORPHAN_REFERENCE_COUNT,
            "missing_required_values": MISSING_VALUE_COUNT,
            "invalid_amounts": INVALID_AMOUNT_COUNT,
            "unit_mismatches": UNIT_MISMATCH_COUNT,
            "ambiguous_matches": AMBIGUOUS_MATCH_COUNT,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic sample datasets."
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    summary = generate_fixtures(output_dir=args.output_dir, seed=args.seed)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
