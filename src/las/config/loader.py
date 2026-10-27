"""Load a YAML file into a validated :class:`~las.config.models.RunConfig`.

Every failure mode (missing file, unreadable file, malformed YAML, wrong
top-level shape, schema violations) is reported as a single
:class:`ConfigError` whose message names the offending file and, for
schema violations, each invalid field by its dotted path.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from las.config.models import RunConfig


class ConfigError(Exception):
    """Raised when a configuration file cannot be loaded or validated."""


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for issue in error.errors():
        location = ".".join(str(part) for part in issue["loc"]) or "<root>"
        lines.append(f"  - {location}: {issue['msg']}")
    noun = "field" if len(lines) == 1 else "fields"
    return f"{len(lines)} invalid configuration {noun}:\n" + "\n".join(lines)


def load_config(path: str | Path) -> RunConfig:
    """Read ``path`` as YAML and validate it into a :class:`RunConfig`."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Configuration file could not be read: {config_path} ({exc})") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {config_path}: {exc}") from exc

    if data is None:
        raise ConfigError(f"Configuration file is empty: {config_path}")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Top level of {config_path} must be a mapping of configuration "
            f"sections, got {type(data).__name__}"
        )

    try:
        return RunConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid configuration in {config_path}:\n{_format_validation_error(exc)}"
        ) from exc
