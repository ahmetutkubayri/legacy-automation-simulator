# Legacy Automation Simulator

A reconciliation pipeline that reads records out of a simulated legacy system,
validates and matches them against independent data feeds, reconciles the two
sides, and publishes a report — deterministically, and without touching
anything outside the working directory it is pointed at.

**Every record, identifier, amount, account and scenario in this repository is
synthetic.** The datasets are generated from a fixed seed by code in this
repository, the "legacy system" is an in-process simulation, and no part of the
project describes or contains data from any real organisation.

**No external service or private dataset is required.** There is no network
call, no credential, no database and no API key anywhere in the codebase.
Install the package and the demo runs.

## The problem this simulates

A recurring shape of back-office work, reduced to its essentials:

- One **system of record** holds account-like entries. It is old, slow, and
  reachable only through a screen-driven interface that paginates results,
  stalls occasionally, and shows transient error dialogs.
- Several **independent feeds** arrive separately, carrying operational records
  with monetary amounts, units and reporting periods. Each feed uses its own
  column names.
- The data disagrees. Records are malformed, duplicated, ambiguous, or refer to
  accounts that cannot be resolved. Two feeds may describe the same period from
  different angles.
- Somebody has to **validate** every row, **match** it to an account,
  **reconcile** the feeds against the system, and produce a report that a human
  can act on — including an explicit queue of what could not be decided
  automatically.
- When any of that fails, it must fail **safely**: no half-written report, no
  silently dropped row, and the same inputs must always produce the same answer.

That last requirement is the interesting one, and most of this repository
exists to satisfy it.

## What it guarantees

- **Deterministic inputs.** The synthetic datasets regenerate byte-identically
  from their seed. Four SHA-256 values are pinned in
  [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) and checked by tests.
- **Deterministic business results.** Matching, reconciliation and reporting
  depend on explicit sort keys, never on dictionary or filesystem ordering.
  Reordering the configured feeds, or the rows coming out of the simulator,
  changes nothing.
- **Byte-identical artifacts.** Two runs over equivalent inputs produce the
  same XLSX, JSON and HTML bytes, and therefore the same SHA-256 values.
- **A canonical workbook container.** The XLSX ZIP is rewritten with fixed
  entry timestamps and platform metadata, so the file does not change merely
  because the clock did.
- **Atomic publication.** The report set is staged in a private sibling
  directory, verified, and renamed into place in one operation. Either all
  three artifacts appear together, or the output directory does not exist.
- **Typed, stable failures.** Handled execution failures carry a stable error
  code and a message safe to print. Their terminal output exposes no path,
  row value or raw exception.
- **Bounded retries, no uncontrolled sleeps.** The driver retries a fixed
  number of times against an injected clock. Tests advance time instantly;
  nothing sleeps.
- **Report-only orchestration.** The pipeline never mutates the simulated
  system. Write-back is reported as disabled, with zero updates attempted and
  zero applied.
- **Optional, safe audit events.** A library caller may hand the pipeline an
  audit trail. The `las` commands do not supply one, so a CLI run records no
  event and writes no audit or log file, and no command configures console or
  file logging. When a trail *is* supplied, payloads are restricted to an
  allowlist of scalar keys — no paths, names, row contents or exception text —
  and the events stay outside the three report artifacts.
- **Cross-platform CI.** Ubuntu and Windows, Python 3.11 and 3.12, on every
  push: [.github/workflows/ci.yml](.github/workflows/ci.yml).

Exact stage duties, exit codes, audit keys and transaction steps are fixed by
[docs/PIPELINE_CONTRACT.md](docs/PIPELINE_CONTRACT.md); sheet layouts, column
order, colours and the JSON schema by
[docs/REPORTING_CONTRACT.md](docs/REPORTING_CONTRACT.md).

## Quick start

```console
python -m pip install -e .
las demo --output-dir demo-output
```

That is the whole setup. `demo` generates synthetic inputs into a private
temporary directory, runs the full pipeline over them, writes the report, and
removes the temporary directory afterwards.

To write the synthetic datasets somewhere you can inspect them, or to run
against a configuration file of your own:

```console
las generate-fixtures --output-dir synthetic-inputs
las run --config path/to/config.yml
```

**Output directories must not already exist.** Every command that writes
refuses to run if its destination is present — empty or not — rather than
merging into or overwriting someone else's results. Pick a new directory name,
or remove the old one deliberately.

## The deterministic demo

```console
las demo --output-dir demo-output
```

succeeds with exactly this on stdout, and nothing on stderr:

```text
status: success
feed_rows: 211
system_rows: 270
validation_invalid: 12
matching_exact: 204
matching_fuzzy: 0
matching_needs_review: 2
matching_unmatched: 5
matching_ineligible: 0
reconciliation_groups: 240
reconciliation_unresolved: 14
artifacts: 3
write_back: disabled
```

Run it again into a second, fresh output directory and you get the same
terminal summary, the same business result, byte-identical artifacts, and
identical SHA-256 values for all three files. The documentation test suite
performs exactly that comparison, so this claim is executed rather than
asserted.

## Generated artifacts

Three files, with fixed names — no timestamp, counter or run identifier:

| File | Purpose |
|---|---|
| `reconciliation-report.xlsx` | Structured workbook for detailed review |
| `reconciliation-summary.json` | Machine-readable summary for downstream tools |
| `reconciliation-summary.html` | Self-contained, portable human-readable summary |

The workbook carries four sheets: **Summary**, **Exceptions**,
**Reconciliation**, and **Review Queue**. Every cell is written as text,
including the numbers, so no total is rounded through a spreadsheet float and
no value beginning with `=` can be interpreted as a formula.

Full schemas, column order and fill colours are in
[docs/REPORTING_CONTRACT.md](docs/REPORTING_CONTRACT.md). No generated report is
committed to this repository.

## Architecture at a glance

- The **CLI** is the only entry point, and it is deliberately thin — it decides
  how a result is printed and which exit code it earns, nothing more.
- The **pipeline** is the composition root: the one package allowed to import
  every layer, because composing them is all it does. It holds no business rule
  of its own.
- The **simulator** exposes a legacy-style system, and a **driver** operates it
  through screen-shaped interactions with bounded retries. Orchestration reads
  from it and never writes to it.
- **Lower layers do not depend on orchestration.** Ingest, validation, matching,
  reconciliation and reporting import nothing from the pipeline or the CLI, and
  architecture tests enforce that in both directions.
- **Publication is one atomic transaction**, not three file writes.
- **Audit is operational and optional**, not a pipeline stage. It exists only
  when a library caller supplies a trail, may then survive a failed run, and
  never enters a result. The CLI supplies none and configures no logging.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layer map and the
dependency rule, [docs/PIPELINE_CONTRACT.md](docs/PIPELINE_CONTRACT.md) for
stage behaviour, and [docs/REPORTING_CONTRACT.md](docs/REPORTING_CONTRACT.md)
for report structure.

## Command reference

```text
las run --config FILE
las demo --output-dir DIR [--seed INTEGER]
las generate-fixtures --output-dir DIR [--seed INTEGER]
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | success |
| `2` | argument parsing |
| `3` | configuration or configured input |
| `4` | output conflict or publication transaction |
| `5` | typed pipeline or lower-layer failure |
| `6` | unexpected internal failure |

For execution failures handled by the CLI, stdout is empty and stderr carries
exactly one stable line of the form `las: <error_code>`. No traceback, raw
exception text, path or object representation is printed for those failures.
Argument parsing uses argparse's standard diagnostics. Ctrl-C raises
`KeyboardInterrupt`, which the CLI does not catch; Python and the platform
determine its diagnostics and exit status. The stable error-line guarantee
does not cover interrupts.

## Testing and reliability

The suite is deterministic end to end — no network, no real sleeps, no reliance
on wall-clock time — and covers:

- **Unit tests** for every layer's public behaviour.
- **Boundary and architecture tests** proving the dependency direction holds,
  that layers import nothing forbidden, and that the simulator contains no
  retry vocabulary.
- **Documentation-to-code conformance**, holding this README, the architecture
  document and both contracts to the parser, the public constants and an
  executed demo.
- **Fixture hash tests** pinning the four synthetic datasets byte for byte.
- **Golden semantic tests** for the JSON, HTML and workbook content.
- **Byte-determinism tests** proving repeated renders are identical, including
  under a changed clock, timezone, locale and global decimal context.
- **Fault injection** for staged-write failures, read-back corruption and
  rename failures.
- **CLI subprocess tests** driving the installed command as an operator would.
- **Atomic-publication failure tests** proving no partial output survives.
- **Ubuntu and Windows CI on Python 3.11 and 3.12**, defined in
  [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Design decisions

**Synthetic fixtures instead of external data.** The project needs data with
known defects — duplicates, orphan references, unit mismatches — so tests can
assert exact recall. Generating it from a seed also makes the whole repository
self-contained and safe to publish. The cost is that the data is not real, so
it cannot demonstrate handling of genuinely unforeseen input.

**Explicit ordering instead of incidental collection order.** Every sort is
written down, even where a dictionary happened to iterate correctly. This makes
results reproducible across versions and platforms, at the price of more code
and more sort keys to maintain.

**Immutable result objects.** Frozen dataclasses, tuples, and read-only
mappings throughout, so a result cannot be edited after the fact by a caller who
holds it. Construction is slightly more expensive and defensive copying is
explicit — an acceptable trade for results that cannot drift.

**Typed failures instead of leaking lower-level exceptions.** Each layer
converts what it catches into its own error type with a stable code. Debugging
loses the original traceback at the boundary, which is why an enabled audit
trail records the stage; the gain is safe terminal output for handled execution
failures, without paths or row values.

**Report-only orchestration.** The driver's status write-back is implemented and
tested, but deliberately not wired in. Nothing in this project states which
reconciliation outcome should drive which record status, and a pipeline that
guessed would write invented state into the system it exists to check.

**Atomic whole-directory publication.** Writing three files individually has a
failure mode with no good answer: the second write fails and a reader finds one
real report beside one missing one. Staging and renaming the directory removes
that state entirely. The cost is that output cannot be updated incrementally.

**A text architecture diagram instead of an image.** The layer map is Mermaid
source in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). It diffs, reviews and
updates like code, and cannot silently go stale the way an exported picture
does. It is less expressive than a hand-drawn diagram would be.

## Limitations and non-goals

- This is a **synthetic simulator**, not a production integration.
- It **does not connect to any network service**, database or external API.
- It contains **no real customer, company or operational data**.
- It **does not write reconciliation outcomes back** into the simulated system.
- It is **not a general-purpose accounting or ERP platform**; the data model is
  only as rich as the scenario requires.
- There is **no graphical interface**. The deliverables are a CLI and files.
- **No performance or scale claims are made.** The demo dataset is small by
  design, and nothing here has been benchmarked or tuned for throughput.
- The contracts prioritise **determinism and reviewability over feature
  breadth**. Where the two conflict, this project chooses the reproducible
  answer.

## Repository layout

```text
src/las/
├── simulator/              # the simulated legacy system and its fault model
├── driver/                 # screen-driven access with bounded retries
├── ingest/                 # CSV and XLSX readers, header alias resolution
├── validate/               # rule engine over ingested rows
├── match/                  # account resolution, exact and fuzzy
├── reconcile/              # feed-versus-system aggregation and outcomes
├── report/                 # XLSX, JSON and HTML rendering, in memory
└── pipeline/               # composition root, verification gate, atomic writer
data/samples/               # committed synthetic fixtures and a sample config
docs/                       # architecture and the authoritative contracts
tests/                      # the full suite
.github/workflows/ci.yml    # Ubuntu and Windows, Python 3.11 and 3.12
```

## Development

```console
python -m pip install -e ".[dev]"
ruff check src tests scripts
pytest --cov=las --cov-report=term-missing
```

The four fixture hashes and the deterministic report guarantees are protected by
tests, so a change that alters generated bytes fails the suite rather than
quietly shifting the baseline. Regenerating the fixtures is expected to
reproduce the committed files exactly.

## License

MIT — see [LICENSE](LICENSE).
