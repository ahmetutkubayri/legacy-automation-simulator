"""Release-archive policy: what the wheel and the sdist are allowed to carry.

Setuptools' implicit default swept ``tests/test*.py`` into the source
distribution and left behind everything those modules import or read —
``conftest.py``, the shared helpers, the goldens, the sample data, the contract
documents, the fixture generator. The archive advertised a test suite it could
not collect. ``MANIFEST.in`` makes the choice explicit instead; this module is
what stops the default from quietly coming back.

The contract, in one place:

* the **wheel** carries the ``las`` package and its wheel metadata, nothing
  else;
* the **sdist** adds only what a consumer needs to build, install, license and
  read about the package — build configuration, ``README.md``, ``LICENSE`` and
  generated metadata;
* **neither** publishes a repository-only surface: tests, test helpers,
  fixtures, goldens, documentation, scripts, committed sample data, CI
  configuration, Git metadata or local build output.

The runnable suite lives in the Git repository, so nothing here runs the
repository's tests out of an extracted archive — that is precisely the thing
the policy declines to ship.

Everything is measured against archives this module builds itself, because Git
tracking does not decide archive membership: the untracked, build-generated
``.egg-info`` ships, while tracked helpers did not. Only a built archive is
evidence.

The build runs once per session, in a disposable copy under pytest's temporary
directory, so no build output ever lands in the repository. It uses the running
interpreter and ``--no-isolation`` — no shell, no network, no download.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

import las

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The working-tree surfaces that can affect archive membership. Everything a
#: rule might accidentally sweep in is copied, so a regression has something to
#: be caught doing.
COPIED_FILES = (
    "pyproject.toml",
    "MANIFEST.in",
    "README.md",
    "LICENSE",
    ".gitignore",
    ".gitattributes",
)
COPIED_TREES = ("src", "tests", "docs", "scripts", "data", ".github")

#: Never copied: Git metadata, environments, caches, coverage and any earlier
#: build output. Their absence from the copy keeps the archives a product of
#: the declared configuration rather than of one machine's leftovers.
IGNORED = shutil.ignore_patterns(
    ".git", ".venv", "venv", "__pycache__", "*.py[cod]", "*.egg-info",
    ".pytest_cache", ".ruff_cache", ".coverage*", "htmlcov",
    "build", "dist", ".eggs", "output", "logs",
)

#: Repository directories that must not appear in either archive.
REPOSITORY_ONLY_TREES = ("tests", "docs", "scripts", "data", ".github")

#: Substrings that betray a repository-only or machine-local member.
FORBIDDEN_FRAGMENTS = (
    ".git", ".venv", "venv/", "__pycache__", ".pyc", ".egg/",
    ".pytest_cache", ".ruff_cache", ".coverage", "htmlcov",
    "golden", "conftest", "_helpers", "workflows", "samples",
)


@dataclass(frozen=True)
class Archives:
    """Both built archives, with member names normalised to POSIX form."""

    sdist_path: Path
    wheel_path: Path
    sdist_members: tuple[str, ...]
    wheel_members: tuple[str, ...]
    build_root: Path

    @property
    def sdist_relative(self) -> tuple[str, ...]:
        """Sdist members without the ``<name>-<version>/`` top-level prefix."""
        return tuple(name.split("/", 1)[1] for name in self.sdist_members if "/" in name)


def normalise(name: str) -> str:
    """One member name, in POSIX form, whatever wrote the archive."""
    return name.replace("\\", "/")


def read_manifest() -> dict:
    """``pyproject.toml``, parsed."""
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def project_metadata() -> tuple[str, str]:
    """The distribution's filename stem, and the one authoritative version.

    The name comes from the manifest; the version comes from the package
    attribute the manifest now derives it from. Read rather than restated:
    this module must not become a second place either literal lives.
    """
    return read_manifest()["project"]["name"].replace("-", "_"), las.__version__


def metadata_version(document: str) -> str:
    """The ``Version`` field of a core-metadata document."""
    for line in document.splitlines():
        if not line.strip():
            break  # The header block ends at the first blank line.
        if line.startswith("Version:"):
            return line.partition(":")[2].strip()
    raise AssertionError("the metadata document declares no Version")


@pytest.fixture(scope="session")
def archives(tmp_path_factory: pytest.TempPathFactory) -> Archives:
    """Build both archives once, from a disposable copy of the working tree."""
    base = tmp_path_factory.mktemp("packaging")
    project = base / "project"
    outdir = base / "archives"
    project.mkdir()

    for name in COPIED_FILES:
        source = REPO_ROOT / name
        assert source.is_file(), f"{name} is missing from the working tree"
        shutil.copy2(source, project / name)
    for name in COPIED_TREES:
        source = REPO_ROOT / name
        assert source.is_dir(), f"{name}/ is missing from the working tree"
        shutil.copytree(source, project / name, ignore=IGNORED)

    completed = subprocess.run(
        [
            sys.executable, "-m", "build", "--no-isolation",
            "--sdist", "--wheel", "--outdir", str(outdir), str(project),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, (
        "python -m build failed\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )

    sdists = sorted(outdir.glob("*.tar.gz"))
    wheels = sorted(outdir.glob("*.whl"))
    assert len(sdists) == 1, f"expected exactly one sdist, got {[p.name for p in sdists]}"
    assert len(wheels) == 1, f"expected exactly one wheel, got {[p.name for p in wheels]}"

    with tarfile.open(sdists[0]) as archive:
        sdist_members = tuple(
            normalise(member.name) for member in archive.getmembers() if member.isfile()
        )
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_members = tuple(
            normalise(info.filename) for info in archive.infolist() if not info.is_dir()
        )

    return Archives(sdists[0], wheels[0], sdist_members, wheel_members, base)


def package_modules() -> set[str]:
    """Every Python module currently under ``src/las``, as POSIX paths."""
    root = REPO_ROOT / "src"
    return {
        path.relative_to(root).as_posix()
        for path in (root / "las").rglob("*.py")
        if "__pycache__" not in path.parts
    }


# --- the sdist: what it must not carry ------------------------------------------


def test_the_sdist_omits_every_repository_only_surface(archives: Archives) -> None:
    """The defect, asserted directly: no test tree, and no support tree either.

    Both halves matter. Shipping neither is coherent; shipping the tests
    without their fixtures is the incoherence this rule removes.
    """
    for member in archives.sdist_relative:
        for tree in REPOSITORY_ONLY_TREES:
            assert not member.startswith(f"{tree}/"), f"the sdist ships {member}"
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in member, f"the sdist ships {member}"

    assert not [m for m in archives.sdist_relative if m.endswith("_test.py")]
    assert not [m for m in archives.sdist_relative if Path(m).name.startswith("test_")]


# --- the sdist: what it must keep -----------------------------------------------


def test_the_sdist_keeps_what_a_consumer_needs_to_build_and_install(
    archives: Archives,
) -> None:
    """Pruning the suite must not prune the release surface with it."""
    members = set(archives.sdist_relative)
    for required in ("MANIFEST.in", "pyproject.toml", "README.md", "LICENSE", "PKG-INFO"):
        assert required in members, f"the sdist lost {required}"

    # The generated metadata setuptools needs to install from source.
    assert [m for m in members if m.endswith(".egg-info/PKG-INFO")]

    # And the package itself, in full.
    assert {m[len("src/"):] for m in members if m.startswith("src/las/")} == package_modules()


# --- the wheel: package and metadata, nothing else --------------------------------


def test_the_wheel_ships_only_the_package_and_its_metadata(archives: Archives) -> None:
    name, version = project_metadata()
    dist_info = f"{name}-{version}.dist-info/"

    for member in archives.wheel_members:
        assert member.startswith("las/") or member.startswith(dist_info), (
            f"the wheel ships {member}"
        )
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in member, f"the wheel ships {member}"

    assert {m for m in archives.wheel_members if m.startswith("las/")} == package_modules()


# --- both archives: structurally sound -------------------------------------------


def test_neither_archive_has_an_absolute_or_escaping_member(archives: Archives) -> None:
    """No member may be absolute or climb out of the extraction directory.

    Also pins the filenames to the metadata that produced them, so a rename or
    a drifted version cannot pass unnoticed.
    """
    name, version = project_metadata()
    assert archives.sdist_path.name == f"{name}-{version}.tar.gz"
    assert archives.wheel_path.name.startswith(f"{name}-{version}-")
    assert archives.wheel_path.suffix == ".whl"

    for member in (*archives.sdist_members, *archives.wheel_members):
        assert not member.startswith("/"), f"absolute member: {member}"
        assert ":" not in member.split("/")[0], f"drive-qualified member: {member}"
        assert ".." not in Path(member).parts, f"traversing member: {member}"

    # Every sdist member sits under the single expected root directory.
    roots = {member.split("/", 1)[0] for member in archives.sdist_members}
    assert roots == {f"{name}-{version}"}


def test_neither_archive_leaks_a_local_build_path(archives: Archives) -> None:
    """Nothing may carry the machine that built it.

    Checked against real absolute paths — the disposable build directory and
    the repository itself — rather than by guessing at usernames, which would
    match ordinary prose.
    """
    leaks = (str(archives.build_root), str(REPO_ROOT), REPO_ROOT.as_posix())

    for member in (*archives.sdist_members, *archives.wheel_members):
        for leak in leaks:
            assert leak not in member, f"member name carries a local path: {member}"

    with tarfile.open(archives.sdist_path) as archive:
        for member in archive.getmembers():
            if not member.isfile() or Path(member.name).suffix not in {".txt", ".toml", ""}:
                continue
            extracted = archive.extractfile(member)
            assert extracted is not None
            text = extracted.read().decode("utf-8", errors="replace")
            for leak in leaks:
                assert leak not in text, f"{member.name} carries a local path"

    with zipfile.ZipFile(archives.wheel_path) as archive:
        for name in archives.wheel_members:
            if not name.endswith((".txt", "METADATA", "WHEEL", "RECORD")):
                continue
            text = archive.read(name).decode("utf-8", errors="replace")
            for leak in leaks:
                assert leak not in text, f"{name} carries a local path"


# --- one version source -----------------------------------------------------------


def test_the_manifest_names_the_package_attribute_as_its_only_version_source() -> None:
    """The declaration, not just the value.

    Asserting that two versions merely *agree* would pass again the moment a
    second static literal reappeared beside the first. This pins the wiring:
    there is no static version key, and the metadata is derived from the one
    attribute that owns it.
    """
    manifest = read_manifest()
    assert "version" not in manifest["project"], (
        "a static version literal is back in [project]; the package attribute is the source"
    )
    assert manifest["project"]["dynamic"] == ["version"]
    assert manifest["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "las.__version__"}


def test_both_archives_publish_the_version_the_package_declares(archives: Archives) -> None:
    """End to end: what setuptools resolved is what the archives carry."""
    with zipfile.ZipFile(archives.wheel_path) as archive:
        (member,) = [n for n in archives.wheel_members if n.endswith(".dist-info/METADATA")]
        wheel_version = metadata_version(archive.read(member).decode("utf-8"))

    with tarfile.open(archives.sdist_path) as archive:
        (member,) = [
            name
            for name in archives.sdist_members
            if name.endswith("/PKG-INFO") and name.count("/") == 1
        ]
        extracted = archive.extractfile(member)
        assert extracted is not None
        sdist_version = metadata_version(extracted.read().decode("utf-8"))

    assert wheel_version == las.__version__
    assert sdist_version == las.__version__
    assert wheel_version == sdist_version
