"""Command-line entry point for the Legacy Automation Simulator.

Subcommands (``demo``, ``run``, ``generate-fixtures``) are added by later
stages of the roadmap; for now the CLI only reports its version so the
packaging, entry point, and CI wiring can be verified end to end.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from las import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="las",
        description=(
            "Legacy Automation Simulator: resilient workflow automation "
            "against a simulated legacy system, on synthetic data."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
