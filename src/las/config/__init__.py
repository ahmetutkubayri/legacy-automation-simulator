"""Typed configuration for the Legacy Automation Simulator."""

from las.config.loader import ConfigError, load_config
from las.config.models import (
    CANONICAL_FIELDS,
    FeedConfig,
    MatchConfig,
    ReportConfig,
    RunConfig,
    SimulatorConfig,
    ValidationConfig,
)

__all__ = [
    "CANONICAL_FIELDS",
    "ConfigError",
    "FeedConfig",
    "MatchConfig",
    "ReportConfig",
    "RunConfig",
    "SimulatorConfig",
    "ValidationConfig",
    "load_config",
]
