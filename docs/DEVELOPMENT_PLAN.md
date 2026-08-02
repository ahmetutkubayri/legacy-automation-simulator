# Development Plan — Legacy Automation Simulator

An independently developed portfolio project demonstrating enterprise
workflow automation, configurable data validation, entity matching,
record reconciliation, exception reporting, and resilient integration
with a simulated legacy system — using entirely synthetic data.

This document is the public-safe continuation guide: everything needed
to keep developing the project on any machine.

Package: `las` · CLI: `las` · Python: 3.12 · License: MIT

---

## Purpose

Organizations run critical processes on slow, stateful legacy systems
with no APIs, while data arrives from external sources in inconsistent
tabular formats. This project is a reference implementation of a robust
pipeline for that class of problem. Because real legacy systems cannot
ship in a repository, the legacy system is **simulated in-process,
complete with configurable faults**, so the automation's resilience is
reproducible and fully tested.

## Architecture

Ports-and-adapters. The simulator is the "legacy system"; the driver is
the resilient client; pipeline stages (upcoming) consume both through
narrow interfaces.

```
src/las/
├── cli.py                  # entry point (subcommands land in T14)
├── config/    models.py, loader.py        # pydantic models, YAML loading
├── audit/     trail.py                    # append-only JSONL audit trail
├── logging_setup.py                       # console + per-run file logging
│                                          #   (library facility; no CLI command
│                                          #    configures it)
├── simulator/ store.py, screens.py, system.py, faults.py
├── driver/    waits.py, client.py, recovery.py
├── ingest/ validate/ match/ reconcile/ report/   # the business layers
└── pipeline/  models.py, adapters.py, engine.py, writer.py, errors.py
tests/                      # deterministic throughout, no real sleeps
data/samples/               # committed seeded synthetic fixtures + config.yaml
scripts/generate_fixtures.py
docs/DEVELOPMENT_PLAN.md    # this file
.github/workflows/ci.yml    # ruff + pytest on Ubuntu and Windows
```

### Architecture boundaries (enforced by automated tests)

* `las.simulator` imports **nothing** from driver, pipeline, ingest,
  validate, match, reconcile, report, or CLI. It produces faults and
  never retries, recovers, or suppresses them. All randomness lives in
  `simulator/faults.py` behind one seeded injector per instance.
* `las.driver` depends only on the standard library and the public
  `las.simulator` API. All retry loops are bounded `for` loops; no real
  `time.sleep`; no `while True`; no self-recursion; errors carry only
  safe generic context (operation, attempts, screen, timeout).
* Time is injected everywhere (`SimulatedClock` for tests,
  `SystemClock` for demos). Tests advance time instantly.
* Audit payloads are allowlist-sanitized before writing; arbitrary
  objects are never serialized.

## Canonical identifiers (all invented)

| Field | Pattern | Meaning |
|---|---|---|
| `record_id` | `OPR-########` | identifies an operational record |
| `external_ref` | `EXT-#####` | identifier supplied by an external feed |
| `account_ref` | `ACC-######` | canonical account in master data |

`external_ref` values are mapped to accounts via
`data/samples/account_links.csv`. The simulator's derived ledger uses
the dedicated `OPR-88######` range. Record lifecycle (invented):
`PENDING → {READY, REVIEW}`, `READY → {REVIEW, COMPLETE}`,
`REVIEW → {READY, COMPLETE}`, `COMPLETE` terminal.

## Determinism and reproducibility

Committed fixtures are fully derived from seed 42 and must regenerate
byte-identically (SHA-256, lowercase):

```
4394c96f805f342a8313502cebf2868d40b9867e6910b8a389c3a32114c52952  data/samples/accounts.csv
7486a6227e4980a73b45873e8aeb4e08147e577c64e76ac08f80ba891e07865a  data/samples/account_links.csv
1539d435cc2d5164d14deff95ebc7e54f984a534e8004f2803130b049a47c0a4  data/samples/feed_alpha.xlsx
eeebfb192c3902e49e528eb933810e82fca1f7ac1e497c9b415cd3f14060643f  data/samples/feed_beta.csv
```

* `openpyxl` is **pinned exactly** (`==3.1.5`) because its version is
  embedded in the XLSX container and affects byte-level reproducibility
  of `feed_alpha.xlsx`. Do not upgrade it without regenerating fixtures
  and updating the hashes above in the same commit.
* CSV fixtures are pure standard-library output (UTF-8, LF; feed_beta
  uses UTF-8 BOM + semicolons deliberately) and are version-insensitive.
* The XLSX is made deterministic by pinning document properties and
  repacking the zip container (sorted entries, fixed 1980-01-01 entry
  timestamps, fixed platform attributes, fixed core.xml timestamps).
* Nothing in the generator or simulator reads wall-clock time, machine
  name, username, or locale.

## Commands

Run inside a Python 3.11 or 3.12 environment:

```bash
python -m pip install -e ".[dev]"               # install with dev tools
ruff check src tests scripts                    # lint
pytest --cov=las --cov-report=term-missing      # full suite with coverage
python scripts/generate_fixtures.py --seed 42   # regenerate committed fixtures
python scripts/generate_fixtures.py --seed 42 --output-dir temporary-output/check  # spot-check
```

Verification habit after any fixture-related change: regenerate twice
into clean directories, compare SHA-256, and confirm the committed
files still match the hashes above.

## Completed tasks

| Task | Commit | Summary |
|---|---|---|
| T1 | `a06cb81151794051f30fee3ccc2175014ea22479` | Package scaffold, CI (ruff + pytest, Ubuntu/Windows), smoke tests |
| T2 | `cfaa629d4300f585fae03273fc95442d4fc4a3ba` | Typed pydantic config (strict, unknown keys rejected) + YAML loader |
| T3 | `499e5d773c88bf14a055be6d83e1576a64b3fdb7` | JSONL audit trail (allowlisted payload sanitizer) + per-run logging |
| T4 | `57854b3450c2c8d40d14ddcf2a778f4e4d254837` | Deterministic synthetic fixture generator + committed datasets |
| T5 | `3c0ba20518fd1460b7d7d76e0fab0de849dd325e` | Simulator core: store, screens, LegacySystem state machine |
| T6 | `38255a913b90bc9ff1fd10d9a220f8f9eaea1c84` | Deterministic fault injection (none/mild/hostile profiles) |
| T7 | `1f6089fc924cc9290b8edc4b7f19a635774d7efe` | Resilient driver: retry policy, waits, fallback lookup, recovery |
| T8 | `52662b0a2752edd8c25703ba8a67aedc137ccd62` | Paginated extraction with proven completeness; dry-run-safe write-back |
| T9 | `5562b9fb18414b136a9a33b4372666ad5a0d08d9` | CSV/XLSX readers, header normalization, alias resolution |
| T10 | `b4692902a375e2ff75ff40bd1009053f4ceebf80` | Deterministic validation engine with stable issue ordering |
| T11 | `185d8073456a8c6ea1fd488cc472ad992ad3a375` | Deterministic entity matching with explicit review outcomes |
| T12 | `a9ccbd0b43f85837d483839aa253c626786e8dd4` | Exact feed-to-system reconciliation with replay protection |
| T13 | `d3eddb4c74e359ed1e8fd4304b7b8db69ba33d7b` | Deterministic XLSX, JSON, and HTML reporting |
| T14 | `e9d5525fd1bb080c415799c7cdb45e98a28de1a0` | End-to-end pipeline orchestration, atomic publication, CLI |
| T15 | — | README, architecture document, documentation-conformance tests |

A commit cannot record its own identifier, so the T15 row carries none. Git
history is the authority for which commit delivered which task; the hashes above
are a convenience for reading the plan alongside the log, not a second source of
truth.

Narrow maintenance passes sit between the numbered tasks. They are
prerequisites, not roadmap entries, and carry no task number:

| Commit | Summary |
|---|---|
| `38f0a2327f2c98e074cdb62a142a6945cdc4f366` | Add this plan and pin `openpyxl` for fixture reproducibility |
| `8e969d21b7be036b66cc10dab883e85534a9cbb9` | Enforce LF checkout for the synthetic CSV fixtures |
| `9ef603dd29507decafa1919456e03c0b51f3dd55` | Enforce LF checkout for the report golden files |
| `0d4682f00a03433f7819da539725ab5ba1f9d297` | Resolve the installed CLI launcher through the active scheme's scripts directory, so the subprocess suite runs on Windows |
| `fa576a3f6d7657d87bcf339877c48a8e91fd79cb` | Use a `default_factory` for the one mappingproxy dataclass default Python 3.11 rejects; drop the unused `pandas` requirement; widen CI to Python 3.11 and 3.12 and lint `scripts` |

Current state: the full suite is green on Ubuntu and Windows across
Python 3.11 and 3.12, with production coverage high enough that the
only uncovered lines are defensive branches unreachable by
construction. Exact counts are deliberately not written here — they
change with every task, and the suite itself is the authority.

## Current task: T16 — pre-publication sweep and release readiness

T16 is the only remaining task. It adds no feature: it audits what is
already here, repairs what the audit finds, and decides whether the
repository is fit to publish. Its checklist is at the end of this
document — a full-history secret scan, generic pattern scans over every
tracked file, fixture regeneration against the pinned hashes, metadata
review, verification that every documented command still behaves as
written, a fresh-clone run on both operating systems, and only then a
release tag.

T16 is in progress. Five repairs have landed: the sample configuration
path-resolution fix, the audit and logging claim alignment, the
ignored-configuration-surface cleanup, the source-distribution
packaging policy, and the single-version-source cleanup. The
configuration cleanup removed five keys the CLI accepted and then
ignored — `log_level`, and the simulator's `fault_profile`,
`fault_seed`, `busy_delay_min_ms` and `busy_delay_max_ms` — so the
schema now rejects them as unknown keys instead of silently discarding
them. The simulator's fault model and the logging facility are
untouched and still reached directly through `las.simulator` and
`las.logging_setup`.

The packaging repair settles what a release archive carries. Setuptools'
default had swept the test modules into the source distribution while
leaving behind everything they import and read, so the archive advertised
a suite it could not collect. `MANIFEST.in` now prunes it deliberately:
the wheel carries the `las` package and its metadata, the source
distribution adds only the build configuration, `README.md` and
`LICENSE`, and both omit the repository-only surface — tests, helpers,
fixtures, goldens, documentation, scripts, sample data and CI
configuration. The runnable suite lives in the Git repository, not in a
release archive.

The version-source cleanup is complete. `0.1.0` had been maintained by
hand in two places, `pyproject.toml` and `src/las/__init__.py`, which can
drift apart silently. `__version__` in `src/las/__init__.py` is now the
authoritative source: `pyproject.toml` declares `dynamic = ["version"]`
and setuptools derives the package metadata from that attribute, reading
it statically — it parses the module rather than importing it, so the
build needs no installed distribution and importing `las` from a plain
source tree still reports the version without any metadata lookup. The
package version is unchanged at `0.1.0`, and `las --version` still prints
`las 0.1.0`.

The remaining hardening items, an independent final verification and the
release tag all remain, so T16 is not finished and the repository has not
been tagged or released.

## Task clauses

The specification each task was implemented against. T9 through T15 are
complete — see the ledger above for their commits — and are kept here
verbatim because the contract documents and several conformance tests
quote them.

* **T9 Ingest** — CSV/XLSX readers with encoding-fallback chain and
  delimiter inference; header normalization + alias resolution from
  config; unmapped-column reporting.
* **T10 Validation engine** — rule protocol, ERROR/WARN severities,
  built-in rules (required, format, range, referential integrity,
  duplicates); must achieve 100% recall of the generator's injected
  defects, asserted by test. Reconcile sample-config patterns if needed.
* **T11 Matching** — ID normalization, exact matching, fuzzy scoring
  with config thresholds, `needs_review` bucket; nothing below the
  acceptance threshold is silently matched.
* **T12 Reconciliation** — aggregation of both feeds, combined as one
  `FEED` side, against the simulator ledger as one `SYSTEM` side. Feed
  alpha and feed beta are two sources contributing to the same side, not
  opposing sides. The ledger crosses the boundary as a neutral immutable
  `SystemLedgerRow` DTO carrying only the fields reconciliation needs,
  with its `account_ref` already resolved; `las.reconcile` imports
  neither the driver nor the simulator, and T14 owns the adapter that
  builds those rows from public driver `ExtractedRow` values using the
  existing master index for exact external-ref-to-account resolution.
  Grouping is by `account_ref` **and** `period` — never by account
  alone, and never with `unit` in the key, because a unit conflict must
  surface as a verdict rather than hide in two separate groups. Outcome
  precedence is presence, then units, then arithmetic: a missing side
  and a unit mismatch each publish no variance at all, only per-unit
  subtotals. Duplicate rows each contribute; nothing is deduplicated —
  two business-identical rows at different physical rows are two real
  observations. That is distinct from *replay*: one physical feed row
  (source, worksheet, physical row number), one feed batch, or one
  system ledger record (`record_id`, and always `position`) supplied
  twice is refused before aggregation, because it would double a total
  while looking exactly like ordinary duplicate data. Input identity is
  never part of the business group key. Arithmetic is exact with no
  precision ceiling: every amount the T10 parser accepts is aggregatable,
  summed by rescaling to a shared exponent and adding unbounded integer
  coefficients, so no result depends on the process-wide decimal context.
  Variance bands come from config and are **absolute amount differences**
  in the group's shared unit — not percentages, no denominator, no
  division — converted through `Decimal(str(value))`, ascending from
  exactly `0`, with every nonzero upper edge **inclusive**. Every feed
  row is accounted for exactly once as a contribution or an explicitly
  unresolved row; hand-computed fixture assertions.
* **T13 Reporting** — three artifacts rendered **entirely in memory**
  from one report model: a four-sheet XLSX (`Summary`, `Exceptions`,
  `Reconciliation` with static outcome fills, `Review Queue`; write-only
  streaming path above the configured row threshold), a JSON summary,
  and a self-contained HTML summary. Recipients come from config
  (`example.com` only) and are metadata only — no mail is ever sent.
  T13 chooses no filename and writes no file; T14 writes the returned
  bytes to `RunConfig.output_dir`. Decimals render as canonical text in
  every format, so no total is rounded through a spreadsheet float.
  JSON and HTML are byte-identical across renders; XLSX is semantically
  identical, including between the standard and streaming paths.
  Golden-file content tests. Sheet membership, column order, colours,
  the JSON schema and the decimal rules are fixed by
  `docs/REPORTING_CONTRACT.md`, which is the authoritative T13 contract.
* **T14 Pipeline + CLI** — seven-stage orchestrator (`EXTRACT` →
  `INGEST` → `VALIDATE` → `MATCH` → `RECONCILE` → `REPORT` → `VERIFY`)
  wired to configuration; the pipeline keeps optional caller-supplied
  audit support, while the CLI constructs no audit trail and leaves
  logging an unconfigured library facility; `las demo | run |
  generate-fixtures`; end-to-end tests on both OSes; demo <60 s,
  deterministic per seed. Every stage before `VERIFY` is pure — nothing
  is created on disk until the gate passes — and the artifact set is
  published as **one atomic directory transaction** into an output
  directory that must not already exist. T14 is **report-only**: it
  never mutates the simulator, so write-back mode is `DISABLED` with
  zero attempted and zero applied updates. T8's dry-run-by-default
  write-back stays tested but deliberately unwired, because nothing
  states which reconciliation outcome should drive which record status.
  Stage duties, the ledger adapter, feed-batch identity, artifact
  filenames, the transaction algorithm, audit events, exit codes and the
  terminal summary are fixed by `docs/PIPELINE_CONTRACT.md`, which is
  the authoritative T14 contract.
* **T15 Documentation** — full README (problem, guarantees, quickstart,
  deterministic demo transcript, generated artifacts, command
  reference, testing, design decisions, limitations, repository layout)
  and `docs/ARCHITECTURE.md` carrying the layer map as a
  source-controlled Mermaid diagram. Documentation is **text only**: no
  screenshots, no images, no badges, and no committed copy of generated
  output. A conformance suite reads the documents and holds them to the
  parser, the public constants and an executed demo, so a claim that
  drifts from behaviour fails the build.
* **T16 Pre-publication sweep** — full-history secret scan, generic
  pattern scans, fixture regeneration check, metadata review, README
  claim verification, tag v1.0.0, then publish.

## Development workflow

Approval-gated, per task:

1. Implement strictly within the approved task scope.
2. Verify: ruff, full pytest with coverage, confidentiality gate,
   architecture tests, and task-specific checks (hashes, stress runs).
3. Report results and wait for maintainer approval.
4. Stage only maintainer-approved files; show staged names/stat/check
   plus all verification output.
5. Commit with the exact maintainer-approved subject line — no
   attribution trailers of any kind.
6. Git identity: repo-local, name + GitHub noreply address only.

Standing rules: the project is fully self-contained (never read,
reference, or copy anything outside this repository); all data is
synthetic and regenerable from seeds; every email in content uses
`example.com`; no real company, product, or person names; no absolute
paths, network paths, or URLs (badges excepted) anywhere in code,
docs, fixtures, or outputs; descriptive invented naming only.

## Pre-publication checklist (summary; execute fully in T16)

1. Secret scan over the **entire git history** (e.g. gitleaks) — zero.
2. Personal-data scan: every email `example.com`; commit authors are
   the repo-local noreply identity only.
3. Generic pattern scan: no non-badge URLs, UNC paths, absolute user
   paths, or real-domain emails in any tracked file.
4. Fixture regeneration from seed matches committed hashes in the
   pinned environment.
5. Dependency/license review (all MIT/BSD/Apache-compatible; public
   PyPI only).
6. Metadata review: XLSX document properties neutral; `pyproject`
   author fields personal.
7. Fresh-clone verification on a clean venv: install, lint, full test
   suite, demo run — all green on Linux and Windows.
8. README accuracy: every command executed verbatim; every
   quantitative claim re-verified on the publication commit; no
   unverifiable performance claims.
9. Tag `v1.0.0` and publish only after every item above passes.
