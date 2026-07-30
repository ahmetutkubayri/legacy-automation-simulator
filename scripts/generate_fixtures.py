"""Thin command-line wrapper around :mod:`las.fixtures`.

The generator itself lives in the package so that this script, ``las
generate-fixtures`` and ``las demo`` all share one implementation. Every public
name is re-exported here, so existing callers that load this file directly keep
working unchanged.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from las.fixtures import *  # noqa: F403 - deliberate re-export of the public API
from las.fixtures import DEFAULT_OUTPUT_DIR, DEFAULT_SEED, generate_fixtures


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
