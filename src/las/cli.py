"""Command-line entry point for the Legacy Automation Simulator.

Three commands, all thin: ``run`` executes the pipeline against a configuration
file, ``demo`` generates synthetic inputs and runs the same pipeline over them,
and ``generate-fixtures`` writes the synthetic datasets on their own. Every one
of them delegates — no business rule lives here, and the only things this module
decides are how a result is printed and which exit code it earns.

Output discipline
-----------------
Success writes a fixed summary to stdout and nothing to stderr. Handled
execution failures write one line to stderr — ``las: <error_code>`` — and
nothing to stdout: no traceback, no exception text, no path, no object
representation. Argument parsing uses argparse diagnostics; uncaught
``KeyboardInterrupt`` follows Python and platform behaviour. The full contract,
including the exit-code map, is in ``docs/PIPELINE_CONTRACT.md``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from las import __version__
from las.config import ConfigError, RunConfig, load_config
from las.fixtures import DEFAULT_SEED
from las.pipeline import (
    ErrorCategory,
    PipelineError,
    PipelineRequest,
    RunResult,
    publish_fixtures,
    run_pipeline,
)
from las.pipeline.engine import MATCHING_OUTCOME_FIELDS

#: Exit codes, fixed. ``2`` belongs to argparse and is never produced here.
EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_CONFIGURATION = 3
EXIT_OUTPUT = 4
EXIT_EXECUTION = 5
EXIT_INTERNAL = 6

EXIT_BY_CATEGORY = {
    ErrorCategory.CONFIGURATION: EXIT_CONFIGURATION,
    ErrorCategory.OUTPUT: EXIT_OUTPUT,
    ErrorCategory.EXECUTION: EXIT_EXECUTION,
    ErrorCategory.INTERNAL: EXIT_INTERNAL,
}

#: Printed when something outside every approved hierarchy reaches the boundary.
INTERNAL_ERROR_CODE = "internal_error"


class ConfigurationFailure(PipelineError):
    """The configuration could not be loaded. Raised only at this boundary."""

    error_code = "configuration_error"
    category = ErrorCategory.CONFIGURATION

    def __init__(self) -> None:
        super().__init__("the run configuration could not be loaded")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="las",
        description=(
            "Legacy Automation Simulator: resilient workflow automation "
            "against a simulated legacy system, on synthetic data."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command")

    run = commands.add_parser(
        "run", help="run the full pipeline against a configuration file"
    )
    run.add_argument(
        "--config", required=True, type=Path,
        help="path to the run configuration file",
    )

    demo = commands.add_parser(
        "demo", help="generate synthetic inputs and run the pipeline over them"
    )
    demo.add_argument(
        "--output-dir", required=True, type=Path,
        help="directory to publish the report into; must not already exist",
    )
    demo.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help="seed for the synthetic inputs",
    )

    fixtures = commands.add_parser(
        "generate-fixtures", help="write the synthetic sample datasets"
    )
    fixtures.add_argument(
        "--output-dir", required=True, type=Path,
        help="directory to write the fixtures into; must not already exist",
    )
    fixtures.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help="seed for the synthetic datasets",
    )
    return parser


def format_summary(result: RunResult) -> str:
    """The fixed terminal summary: counts only, one per line, LF-terminated."""
    matching = result.summary.matching_counts
    lines = [
        "status: success",
        f"feed_rows: {result.summary.feed_rows:d}",
        f"system_rows: {result.summary.system_rows:d}",
        f"validation_invalid: {result.summary.validation_invalid:d}",
    ]
    lines.extend(
        f"matching_{name}: {matching.get(name, 0):d}"
        for name in MATCHING_OUTCOME_FIELDS
    )
    lines.extend(
        (
            f"reconciliation_groups: {result.summary.reconciliation_groups:d}",
            f"reconciliation_unresolved: {result.summary.reconciliation_unresolved:d}",
            f"artifacts: {result.artifact_count:d}",
            f"write_back: {result.write_back_mode.value}",
        )
    )
    return "\n".join(lines) + "\n"


def demo_config(seed: int) -> RunConfig:
    """An in-memory configuration over freshly generated inputs.

    Feed paths are bare filenames beside the generated fixtures, so the
    config-relative resolution rule applies unchanged. ``output_dir`` here is a
    placeholder the request overrides: demo publishes where the caller said.
    """
    return RunConfig.model_validate(
        {
            "seed": seed,
            "output_dir": "report",
            "feeds": [
                {
                    "name": "feed_alpha",
                    "path": "feed_alpha.xlsx",
                    "header_aliases": {
                        "Record Id": "record_id",
                        "External Reference": "external_ref",
                        "Quantity": "amount",
                        "Quantity Unit": "unit",
                        "Reporting Period": "period",
                    },
                },
                {
                    "name": "feed_beta",
                    "path": "feed_beta.csv",
                    "header_aliases": {
                        "REC_ID": "record_id",
                        "EXT_REF": "external_ref",
                        "AMT": "amount",
                        "UOM": "unit",
                        "PRD": "period",
                    },
                },
            ],
        }
    )


def command_run(config_path: Path, out: TextIO) -> int:
    try:
        config = load_config(config_path)
    except (ConfigError, OSError):
        raise ConfigurationFailure from None
    request = PipelineRequest(config=config, config_dir=config_path.resolve().parent)
    out.write(format_summary(run_pipeline(request)))
    return EXIT_SUCCESS


def command_demo(output_dir: Path, seed: int, out: TextIO) -> int:
    """Generate inputs privately, run the pipeline, and clean up either way."""
    try:
        config = demo_config(seed)
    except ValueError:
        raise ConfigurationFailure from None
    inputs = Path(tempfile.mkdtemp(prefix=".las-demo-"))
    try:
        samples = inputs / "samples"
        publish_fixtures(samples, seed)
        request = PipelineRequest(
            config=config, config_dir=samples, output_override=output_dir
        )
        out.write(format_summary(run_pipeline(request)))
        return EXIT_SUCCESS
    finally:
        # The private input directory never outlives the command, and its name
        # never appears anywhere a caller can see.
        shutil.rmtree(inputs, ignore_errors=True)


def command_generate_fixtures(output_dir: Path, seed: int, out: TextIO) -> int:
    names = publish_fixtures(output_dir, seed)
    out.write(f"status: success\nfixtures: {len(names):d}\n")
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out, err = sys.stdout, sys.stderr

    if args.command is None:
        parser.print_help(out)
        return EXIT_SUCCESS

    try:
        if args.command == "run":
            return command_run(args.config, out)
        if args.command == "demo":
            return command_demo(args.output_dir, args.seed, out)
        return command_generate_fixtures(args.output_dir, args.seed, out)
    except PipelineError as error:
        err.write(f"las: {error.error_code}\n")
        return EXIT_BY_CATEGORY[error.category]
    except Exception:
        # Nothing is known to be safe about an unexpected failure, so nothing
        # is said about it beyond a stable code.
        err.write(f"las: {INTERNAL_ERROR_CODE}\n")
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
