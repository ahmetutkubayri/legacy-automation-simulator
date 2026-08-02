# Pipeline Contract (T14)

The public contract for end-to-end orchestration, artifact publication and the
command line. The development plan's T14 clause names the stages; everything
detailed is defined here, and `tests/test_pipeline_docs.py` asserts the
implementation agrees with this document rather than merely resembling it.

## Report-only

T14 runs the whole flow and publishes reports. It **never mutates the simulated
system**. T8's `update_status` is real, tested and dry-run by default, but
nothing in the repository says which reconciliation outcome should drive which
record status, and a pipeline that guessed would write invented state into the
system it exists to reconcile against.

Every run therefore reports write-back mode `DISABLED`, `0` attempted updates
and `0` applied. There is no `--apply`, no `--dry-run`, and no
outcome-to-status mapping anywhere in this task.

## Seven stages

`EXTRACT → INGEST → VALIDATE → MATCH → RECONCILE → REPORT → VERIFY`

Configuration loading, optional audit setup, publication and result
construction are orchestration mechanics, not stages.

| Stage | What it does |
|---|---|
| `EXTRACT` | Builds one `RecordStore` and one `LegacySystem` from configuration, then reads the whole ledger through the public driver with an unfiltered query. Requires `complete` extraction. Touches no simulator internal and mutates nothing. |
| `INGEST` | Master accounts, account links, master-index construction, and every configured feed. No role is inferred from a filename. |
| `VALIDATE` | The public T10 API. Every row is preserved; the pipeline repairs and filters nothing. |
| `MATCH` | The public T11 API with the configured policy. No exact, fuzzy, ambiguity or eligibility logic is duplicated. |
| `RECONCILE` | The ledger adapter, then T12. No T11 outcome is reinterpreted and no unit is converted. |
| `REPORT` | One T13 `ReportModel` and exactly the configured formats. No file is written. |
| `VERIFY` | The final pure gate — see below. |

Every stage before `VERIFY` is pure. No directory is created and no byte is
written until the gate has passed.

### VERIFY

The gate checks that the run can describe itself truthfully: the adapted ledger
matches the extraction row for row; adapted positions and record identities are
unique; the configured feeds are represented exactly once; the reconciliation
accounting balances and its summary agrees with its detail; the report carries
every group and every unresolved row; the rendered artifacts match the
configured formats in configured order with no format twice; each artifact's
media type and suffix match the T13 contract and its payload is non-empty;
filenames are unique, are plain child names, and every planned path is a direct
child of the configured output directory.

It re-reads lower-layer invariants; it never recomputes a lower-layer outcome.
A `VERIFY` failure produces no output directory and no file.

## Simulator lifecycle

Exactly one store and one system per run, built only from existing
configuration (`visible_rows`, `buffer_rows`) with a `SimulatedClock`. Neither
the simulator nor the driver is returned in the run result, and no run mutates
either. Master data is read from the directory holding the configured feeds,
which is also the fixture directory the store is built from.

Run identifiers and audit timestamps are operational. They never enter report
artifacts, filenames, terminal summaries or `RunResult` equality.

## Config-relative paths

`las run --config FILE` resolves that path once. Every relative path *inside*
the configuration — feeds, output directory — is resolved against the
configuration file's parent directory, never against a later working directory,
a home directory or an inferred repository root. There is no default config
path and no fallback. Absolute input paths are never printed.

`RunConfig` already forbids absolute, drive-anchored, UNC and `..` paths, so
every configured path is relative by construction.

## Feed batch identity

Each configured feed gets a deterministic identifier derived from what the feed
*is*, never from list position, object identity or first-seen order. The
canonical descriptor is compact UTF-8 JSON with sorted keys:

```json
{"format":"xlsx","label":"feed_alpha","path":"feed_alpha.xlsx","worksheet":"__none__"}
```

Paths are normalized to POSIX so Windows and Unix agree. The identifier is
`feed-` plus the first 16 lowercase hex characters of the descriptor's SHA-256.

Two definitions of the same physical feed produce one identifier and are
rejected as duplicate configured input before anything is aggregated. Two
distinct descriptors that hashed alike would raise rather than silently drop a
feed. Reordering feed definitions changes no identifier and no business result.

The descriptor and the source path never appear in an error, a log line, the
terminal output or the run result.

## Ledger adapter

`ExtractedRow → SystemLedgerRow`, consuming only public fields. The external
reference is resolved through `MasterIndex.resolve_link` by exact full-string
lookup: no fuzzy fallback, no name matching, no address field. An unknown
reference, a duplicate record identifier and a duplicate position are each a
typed adapter error, never a dropped row.

The amount crosses as **text** — never through a float — and period and unit
cross verbatim. Position and record identifier are preserved. No `ExtractedRow`,
driver or simulator object is retained.

The result is ordered by position then record identifier, so shuffling the
extraction produces the same tuple.

## Artifact filenames

| Format | Filename |
|---|---|
| XLSX | `reconciliation-report.xlsx` |
| JSON | `reconciliation-summary.json` |
| HTML | `reconciliation-summary.html` |

Fixed constants: independent of time, seed, input filenames, locale, user and
host. Direct child names only — no timestamp, counter, run identifier or random
text, and no caller-configurable filename field.

## Output directory

From `RunConfig.output_dir` (or, for `demo`, from `--output-dir`).

The final directory **must not already exist**. If it does — empty or not — the
run fails with `output_conflict`. Its contents are not inspected, merged,
overwritten, deleted, renamed or modified. There is no overwrite mode, no
`--overwrite` flag, no backup and no merge. The parent must already exist and
be a directory. T14 creates exactly the final directory, and only when
publication commits.

## Directory transaction

The artifact set is one atomic transaction. Files are never published
individually.

1. Complete every pure stage through `VERIFY`.
2. Confirm the final directory does not exist and its parent does.
3. Create one private staging directory as a sibling of the final directory.
4. Write every artifact into staging, in configured format order, binary mode,
   exact T13 bytes, no newline conversion.
5. Flush and close each file.
6. Read each staged file back and verify byte count, SHA-256 and exact equality.
7. Confirm staging holds exactly the expected filenames and nothing else.
8. Re-confirm the final directory still does not exist.
9. Rename the staging directory into place in one operation.

The staging name carries randomness so concurrent runs cannot collide. It is
internal only: it never appears in a result, a message, a log line or a
filename, it never affects final names or bytes, and it is removed on every
failure path.

On any failure before the rename, and on rename failure, the staging directory
is removed and no final directory exists. Because existing directories are
refused, there is no replacement or rollback of prior user data.

`generate-fixtures` uses the same shape: stage, verify the four expected names,
rename once. Either all four fixtures appear or the directory does not exist.

## Audit and logging

Audit is optional and disabled by default. `run_pipeline` takes a `trail`
keyword defaulting to `None`, and when it is `None` no event is recorded and no
file is written. The `las` commands supply no trail, so a CLI run emits no audit
event and creates no audit file. Logging is a library facility too: no command
configures a console or file handler.

The event guarantees below therefore apply to a run a library caller has given a
trail. Audit output is operational metadata, not one of the three report
artifacts: the report writer neither creates nor publishes it, and it is outside
the atomic report-directory publication transaction. Audit events enter no
pipeline result and no artifact byte. A library caller that supplies a trail
controls its location and lifecycle, subject to the audit facility's own path
rules. Audit and logging may exist after a failed run, per existing T3
behaviour.

Event names: `pipeline_started`, `pipeline_stage_completed`,
`pipeline_artifacts_committed`, `pipeline_completed`, `pipeline_failed`.

Payload keys: `command`, `seed`, `stage`, `count`, `artifact_count`,
`feed_rows`, `system_rows`, `group_count`, `unresolved_count`, `error_code`,
`status`. No other key is written.

Given a trail, a successful run emits `pipeline_started`, one
`pipeline_stage_completed` per stage in stage order,
`pipeline_artifacts_committed`, then `pipeline_completed`. A failure emits
`pipeline_failed` once with the last active stage and a stable `error_code`,
and no `pipeline_completed`.

No event carries an input path, an output path, a source or account name, a raw
row, a raw exception message, artifact bytes, a temporary directory name, a
username or a hostname.

## Run result

Deeply immutable and deterministic for equivalent business inputs: overall
status, the seven stage states in order, feed and system input counts,
validation invalid count, matching outcome counts, reconciliation group count
and outcome counts, unresolved count, and one description per written artifact
carrying format, fixed filename, media type, byte count and lowercase SHA-256 —
plus write-back mode `DISABLED` with zero attempted and zero applied.

It holds no path, no config object, no feed or ledger row, no validation or
matching or reconciliation object, no report model, no artifact bytes, no
simulator, no driver, no exception and no audit object.

## Commands

```text
las run --config FILE
las demo --output-dir DIR [--seed INTEGER]
las generate-fixtures --output-dir DIR [--seed INTEGER]
```

`run` takes `--config` and nothing else: no overwrite flag, no apply or dry-run
flag, no output override. Its output directory comes only from configuration.

`demo` generates synthetic inputs into one private temporary directory, builds
an in-memory configuration over them, runs the same engine, writes the same
three filenames, and removes its private directory on success and on failure.
No temporary path is printed or retained.

`generate-fixtures` writes `accounts.csv`, `account_links.csv`,
`feed_alpha.xlsx` and `feed_beta.csv` as one directory transaction.

`--seed` defaults to the existing generator seed authority (`las.fixtures.DEFAULT_SEED`);
no second seed literal is introduced. `--version` is preserved. `--help` is
deterministic for the parser and every subcommand. There are no interactive
prompts and no environment-variable defaults.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success |
| `2` | argparse usage error |
| `3` | configuration or configured-input error |
| `4` | output conflict or output transaction failure |
| `5` | typed pipeline or lower-layer execution failure |
| `6` | unexpected internal error |

Exit `3` covers a missing or malformed config, config validation failure, a
missing or unsupported configured input, and duplicate configured input. Exit
`4` covers an existing output directory, a missing parent, staging creation,
staged write/flush/close/read-back, and the final rename, including the fixture
transaction. Exit `5` covers simulator initialization, extraction, master-index
construction, the ledger adapter, stage contract failures and `VERIFY`.

## stdout and stderr

Success: the terminal summary on stdout, stderr empty, exit `0`.

Failure: stdout empty, one line on stderr — `las: <error_code>` — with one
final LF newline, no traceback, no raw exception text, no path and no object
representation. An unexpected failure prints `las: internal_error` and exits
`6`. There is no debug or traceback flag in T14.

## Terminal summary

```text
status: success
feed_rows: <integer>
system_rows: <integer>
validation_invalid: <integer>
matching_exact: <integer>
matching_fuzzy: <integer>
matching_needs_review: <integer>
matching_unmatched: <integer>
matching_ineligible: <integer>
reconciliation_groups: <integer>
reconciliation_unresolved: <integer>
artifacts: <integer>
write_back: disabled
```

LF newlines, one final newline. `generate-fixtures` prints `status: success`
then `fixtures: 4`. No path, name, run identifier, timestamp, exception or
object representation is ever printed, and machine-readable artifacts never go
to stdout. Equivalent successful runs produce byte-identical summaries.

## Determinism

Equivalent business runs produce equal run results, stage states, counts,
filenames, artifact digests, and **byte-identical artifacts in all three
formats** — including XLSX, whose ZIP container is canonicalized for exactly
this reason (see `docs/REPORTING_CONTRACT.md`) — plus equal terminal summaries
and exit codes. Nothing depends on configured feed order,
extraction row order, mapping iteration, the clock, the timezone, the locale,
the global decimal context, the working directory after config resolution, the
home directory, the path separator, object identity or the temporary directory
name.
