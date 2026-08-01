"""Shared, non-test fixtures for the T14 suites.

Deliberately not named ``test_*``: several pipeline modules import it, and
importing shared data from another test module would couple those together.

The builders here assemble a *config-relative* project in a temporary
directory: the four committed fixtures copied beside the committed
configuration, whose feed paths already resolve from the directory holding it.
That is exactly the layout the resolution rule describes, and it keeps every
test free of the repository's own directory shape.

Only ``output_dir`` is overridden, so each test can publish where it likes. No
feed path is rewritten on the way through: a helper that quietly repaired the
committed configuration would hide a configuration no ``las run`` can load.
"""

from __future__ import annotations

import os
import shutil
import sysconfig
from pathlib import Path

import yaml

from las.config import RunConfig, load_config
from las.pipeline import PipelineRequest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
FIXTURE_NAMES = ("accounts.csv", "account_links.csv", "feed_alpha.xlsx", "feed_beta.csv")

#: The console script declared by ``[project.scripts]``, without any suffix.
LAUNCHER_STEM = "las"

#: ``os.name`` on Windows. Named once so the platform test reads as a fact
#: rather than a magic string.
WINDOWS_OS_NAME = "nt"


def launcher_path(scripts_dir: Path | str, os_name: str = os.name) -> Path:
    """Where the installed ``las`` console script lives, for one layout.

    Pure: it takes the scripts directory and the operating-system name and
    touches neither the filesystem nor the running interpreter, so both
    platform layouts can be checked from either host.

    The two variables are exactly the two things that differ. Installers put
    console scripts in the *scripts* directory of the active scheme, which is
    ``bin`` beside the interpreter on POSIX but ``Scripts`` one level below it
    on Windows, and Windows needs the ``.exe`` launcher shim because
    ``CreateProcess`` will not run an extensionless file.
    """
    suffix = ".exe" if os_name == WINDOWS_OS_NAME else ""
    return Path(scripts_dir) / f"{LAUNCHER_STEM}{suffix}"


def installed_launcher() -> Path:
    """The ``las`` launcher installed in the *active* environment.

    ``sysconfig.get_path("scripts")`` is the authority: it already knows the
    scheme in use, so it resolves a venv, a system install and Windows alike.
    Deriving the directory from ``sys.executable`` instead happens to work on
    POSIX — where the interpreter sits in the same ``bin`` directory — and
    silently breaks on Windows, where it is one directory too high.

    Raises rather than falling back: a missing launcher means the package is
    not installed, and quietly running something else would turn these tests
    into a check of the wrong program.
    """
    path = launcher_path(sysconfig.get_path("scripts"))
    if not path.is_file():
        raise FileNotFoundError(
            f"the {LAUNCHER_STEM!r} console script is not installed in the active "
            f"environment's scripts directory ({path.name} was expected); "
            "install the package with `pip install -e .[dev]` before running the "
            "subprocess CLI tests"
        )
    return path


def make_project(root: Path, *, output_dir: str = "report", feeds=None) -> Path:
    """Copy the committed fixtures beside a config-relative configuration."""
    root.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_NAMES:
        shutil.copy2(SAMPLES_DIR / name, root / name)

    raw = yaml.safe_load((SAMPLES_DIR / "config.yaml").read_text(encoding="utf-8"))
    raw["output_dir"] = output_dir
    if feeds is not None:
        raw["feeds"] = feeds
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
    return config_path


def make_request(root: Path, **kwargs) -> PipelineRequest:
    config_path = make_project(root, **kwargs)
    return PipelineRequest(config=load_config(config_path), config_dir=root)


def load_project_config(root: Path, **kwargs) -> RunConfig:
    return load_config(make_project(root, **kwargs))


def staging_left(root: Path) -> list[str]:
    """Any staging or demo directory the transaction should have removed."""
    return sorted(
        entry.name
        for entry in root.rglob("*")
        if entry.is_dir() and entry.name.startswith((".las-staging-", ".las-demo-"))
    )
