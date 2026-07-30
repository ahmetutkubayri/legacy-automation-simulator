"""Publishing an artifact directory as one transaction.

Why a directory, not files
--------------------------
Writing three files one at a time into the final directory has a failure mode
with no good answer: the second write fails and a reader finds one real report
beside one missing one, with nothing to say which run they came from. So the
whole set is staged in a sibling directory, verified byte for byte, and then
the *directory* is renamed into place in a single operation. Either the output
directory exists with all three files, or it does not exist at all.

Why an existing directory is refused
------------------------------------
Because there is no safe way to merge, replace or empty one. A directory that
is already there may hold a run somebody still needs; deciding otherwise is not
this task's to make. Conflict is an error, and the existing directory is not
read, listed, moved or touched.

The staging name is internal
----------------------------
It carries randomness so two concurrent runs cannot collide, and it never
appears in a result, a message, a log line or a filename. It is removed on
every failure path.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from las.fixtures import (
    ACCOUNTS_FILENAME,
    FEED_ALPHA_FILENAME,
    FEED_BETA_FILENAME,
    LINKS_FILENAME,
    generate_fixtures,
)
from las.pipeline.errors import (
    ArtifactSetError,
    ArtifactWriteError,
    CommitError,
    FixtureTransactionError,
    OutputConflictError,
    OutputParentError,
    StagedByteError,
    StagingError,
)
from las.pipeline.models import WrittenArtifact, filename_for
from las.report import RenderedArtifact

#: Prefix for the private staging directory. A leading dot keeps it out of
#: ordinary listings while it briefly exists.
STAGING_PREFIX = ".las-staging-"


def check_destination(output_dir: Path) -> None:
    """Refuse to publish unless the destination is a clean, reachable slot."""
    if output_dir.exists():
        raise OutputConflictError(
            "the configured output directory already exists"
        )
    parent = output_dir.parent
    if not parent.exists() or not parent.is_dir():
        raise OutputParentError(
            "the parent of the configured output directory is not a directory"
        )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_one(staging: Path, artifact: RenderedArtifact) -> Path:
    """Write one artifact into staging, in binary, with no conversion."""
    name = filename_for(artifact.output_format)
    path = staging / name
    try:
        with path.open("wb") as handle:
            handle.write(artifact.content)
            handle.flush()
    except OSError:
        raise ArtifactWriteError(
            "a report artifact could not be written to the staging directory",
            output_format=artifact.output_format.value,
        ) from None
    return path


def _verify_one(path: Path, artifact: RenderedArtifact) -> None:
    """Read the staged file back and insist it is exactly what was rendered."""
    try:
        written = path.read_bytes()
    except OSError:
        raise StagedByteError(
            "a staged report artifact could not be read back",
            output_format=artifact.output_format.value,
        ) from None
    if len(written) != len(artifact.content):
        raise StagedByteError(
            "a staged report artifact has the wrong byte count",
            output_format=artifact.output_format.value,
        )
    if _digest(written) != _digest(artifact.content):
        raise StagedByteError(
            "a staged report artifact does not match its rendered digest",
            output_format=artifact.output_format.value,
        )
    if written != artifact.content:  # pragma: no cover - the digests already agree
        raise StagedByteError(
            "a staged report artifact differs from the rendered bytes",
            output_format=artifact.output_format.value,
        )


def _verify_set(staging: Path, expected: Sequence[str]) -> None:
    """The staging directory must hold exactly the expected names, and nothing else."""
    try:
        present = sorted(entry.name for entry in staging.iterdir())
    except OSError:
        raise ArtifactSetError(
            "the staging directory could not be listed before commit"
        ) from None
    if present != sorted(expected):
        raise ArtifactSetError(
            "the staging directory does not hold exactly the expected artifacts",
            count=len(present),
        )


def publish(output_dir: Path, artifacts: Sequence[RenderedArtifact]) -> tuple[
    WrittenArtifact, ...
]:
    """Stage, verify and atomically commit the whole artifact set.

    Returns one description per artifact, in the order they were given, which
    is the configured format order.
    """
    check_destination(output_dir)

    try:
        staging = Path(
            tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=output_dir.parent)
        )
    except OSError:
        raise StagingError(
            "a private staging directory could not be created"
        ) from None

    # ``finally`` rather than an exception handler: this only needs to know
    # whether the rename happened, and catching in order to re-raise would
    # mean catching everything, including what must never be caught.
    committed = False
    try:
        written: list[WrittenArtifact] = []
        for artifact in artifacts:
            path = _write_one(staging, artifact)
            _verify_one(path, artifact)
            written.append(
                WrittenArtifact(
                    output_format=artifact.output_format,
                    filename=path.name,
                    media_type=artifact.media_type,
                    byte_count=len(artifact.content),
                    sha256=_digest(artifact.content),
                )
            )
        _verify_set(staging, [item.filename for item in written])

        # Re-checked immediately before the rename: a directory that appeared
        # while this run was staging is still somebody else's, and renaming
        # onto a non-empty directory is not portable anyway.
        if output_dir.exists():
            raise OutputConflictError(
                "the configured output directory already exists"
            )
        try:
            os.rename(staging, output_dir)
        except OSError:
            raise CommitError(
                "the staged report directory could not be committed"
            ) from None
        committed = True
    finally:
        # Nothing is left behind on any failure path: no staging directory,
        # and no final directory, because the rename is the only thing that
        # ever creates one.
        if not committed:
            shutil.rmtree(staging, ignore_errors=True)

    return tuple(written)


#: The four files a fixture directory must contain, and nothing else.
FIXTURE_FILENAMES: tuple[str, ...] = (
    ACCOUNTS_FILENAME,
    LINKS_FILENAME,
    FEED_ALPHA_FILENAME,
    FEED_BETA_FILENAME,
)


def publish_fixtures(output_dir: Path, seed: int) -> tuple[str, ...]:
    """Generate the four fixtures as one directory transaction.

    The same shape as :func:`publish`: stage in a sibling directory, verify the
    set, rename once. Either all four fixtures appear together or the directory
    does not exist, so a half-generated dataset can never be mistaken for a
    real one.
    """
    check_destination(output_dir)

    try:
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=output_dir.parent))
    except OSError:
        raise StagingError("a private staging directory could not be created") from None

    committed = False
    try:
        try:
            generate_fixtures(output_dir=staging, seed=seed)
        except OSError:
            raise FixtureTransactionError(
                "the synthetic fixtures could not be generated"
            ) from None

        present = sorted(entry.name for entry in staging.iterdir())
        if present != sorted(FIXTURE_FILENAMES):
            raise ArtifactSetError(
                "the staging directory does not hold exactly the expected fixtures",
                count=len(present),
            )

        if output_dir.exists():
            raise OutputConflictError("the configured output directory already exists")
        try:
            os.rename(staging, output_dir)
        except OSError:
            raise CommitError(
                "the staged fixture directory could not be committed"
            ) from None
        committed = True
    finally:
        if not committed:
            shutil.rmtree(staging, ignore_errors=True)

    return tuple(sorted(FIXTURE_FILENAMES))
