"""Model-level validation behavior: defaults, ranges, unknown keys, errors."""

import pytest
from pydantic import ValidationError

from las.config import CANONICAL_FIELDS, FeedConfig, RunConfig


def assert_invalid(config: dict, *expected_fragments: str) -> None:
    """RunConfig must reject ``config`` with all fragments in the message."""
    with pytest.raises(ValidationError) as excinfo:
        RunConfig.model_validate(config)
    message = str(excinfo.value)
    for fragment in expected_fragments:
        assert fragment in message


# --- valid configuration and defaults -------------------------------------


def test_minimal_config_applies_defaults(base_config: dict) -> None:
    config = RunConfig.model_validate(base_config)
    assert config.seed == 1
    assert config.output_dir == "output"
    assert config.log_level == "INFO"
    assert config.simulator.fault_profile == "none"
    assert config.simulator.visible_rows == 12
    assert config.validation.required_fields == ["record_id", "account_ref", "amount"]
    assert config.match.fuzzy_threshold == pytest.approx(0.87)
    assert config.match.review_threshold == pytest.approx(0.70)
    assert config.report.formats == ["xlsx", "json", "html"]
    assert config.report.recipients == ["ops-team@example.com"]
    assert config.report.variance_bands == [0.0, 1.0, 5.0]


def test_feed_aliases_accept_canonical_targets(base_config: dict) -> None:
    base_config["feeds"][0]["header_aliases"] = {"Raw Header": "record_id"}
    config = RunConfig.model_validate(base_config)
    assert config.feeds[0].header_aliases == {"Raw Header": "record_id"}


# --- missing required fields ----------------------------------------------


def test_missing_seed_is_rejected(base_config: dict) -> None:
    del base_config["seed"]
    assert_invalid(base_config, "seed", "required")


def test_missing_feeds_is_rejected(base_config: dict) -> None:
    del base_config["feeds"]
    assert_invalid(base_config, "feeds", "required")


def test_feed_without_path_is_rejected(base_config: dict) -> None:
    del base_config["feeds"][0]["path"]
    assert_invalid(base_config, "feeds.0.path", "required")


# --- invalid types ---------------------------------------------------------


def test_non_integer_seed_is_rejected(base_config: dict) -> None:
    base_config["seed"] = "not-a-number"
    assert_invalid(base_config, "seed")


def test_feeds_must_be_a_list(base_config: dict) -> None:
    base_config["feeds"] = "feed_a"
    assert_invalid(base_config, "feeds")


def test_log_level_must_be_known(base_config: dict) -> None:
    base_config["log_level"] = "CHATTY"
    assert_invalid(base_config, "log_level")


# --- invalid ranges and thresholds -----------------------------------------


def test_negative_seed_is_rejected(base_config: dict) -> None:
    base_config["seed"] = -1
    assert_invalid(base_config, "seed")


def test_zero_visible_rows_is_rejected(base_config: dict) -> None:
    base_config["simulator"] = {"visible_rows": 0}
    assert_invalid(base_config, "simulator.visible_rows")


def test_busy_delay_min_above_max_is_rejected(base_config: dict) -> None:
    base_config["simulator"] = {"busy_delay_min_ms": 100, "busy_delay_max_ms": 10}
    assert_invalid(base_config, "busy_delay_min_ms must be <= busy_delay_max_ms")


def test_fuzzy_threshold_above_one_is_rejected(base_config: dict) -> None:
    base_config["match"] = {"fuzzy_threshold": 1.5}
    assert_invalid(base_config, "match.fuzzy_threshold")


def test_review_threshold_above_fuzzy_is_rejected(base_config: dict) -> None:
    base_config["match"] = {"fuzzy_threshold": 0.8, "review_threshold": 0.9}
    assert_invalid(base_config, "review_threshold must be <= fuzzy_threshold")


def test_amount_min_not_below_max_is_rejected(base_config: dict) -> None:
    base_config["validation"] = {"amount_min": 10.0, "amount_max": 10.0}
    assert_invalid(base_config, "amount_min must be strictly less than amount_max")


def test_streaming_row_threshold_out_of_range_is_rejected(base_config: dict) -> None:
    base_config["report"] = {"streaming_row_threshold": 10}
    assert_invalid(base_config, "report.streaming_row_threshold")


# --- unknown keys -----------------------------------------------------------


def test_unknown_top_level_key_is_rejected(base_config: dict) -> None:
    base_config["surprise"] = True
    assert_invalid(base_config, "surprise")


def test_unknown_nested_key_is_rejected(base_config: dict) -> None:
    base_config["simulator"] = {"fault_profile": "none", "turbo_mode": True}
    assert_invalid(base_config, "simulator.turbo_mode")


def test_unknown_feed_key_is_rejected(base_config: dict) -> None:
    base_config["feeds"][0]["retries"] = 3
    assert_invalid(base_config, "feeds.0.retries")


# --- fault profile ----------------------------------------------------------


def test_unknown_fault_profile_is_rejected(base_config: dict) -> None:
    base_config["simulator"] = {"fault_profile": "chaotic"}
    assert_invalid(base_config, "simulator.fault_profile")


@pytest.mark.parametrize("profile", ["none", "mild", "hostile"])
def test_known_fault_profiles_are_accepted(base_config: dict, profile: str) -> None:
    base_config["simulator"] = {"fault_profile": profile}
    assert RunConfig.model_validate(base_config).simulator.fault_profile == profile


# --- feeds: names, paths, aliases -------------------------------------------


def test_duplicate_feed_names_are_rejected(base_config: dict) -> None:
    base_config["feeds"].append({"name": "feed_a", "path": "data/samples/other.csv"})
    assert_invalid(base_config, "unique names", "feed_a")


def test_empty_feed_list_is_rejected(base_config: dict) -> None:
    base_config["feeds"] = []
    assert_invalid(base_config, "feeds")


def test_feed_name_with_invalid_characters_is_rejected(base_config: dict) -> None:
    base_config["feeds"][0]["name"] = "Feed A!"
    assert_invalid(base_config, "feeds.0.name")


@pytest.mark.parametrize(
    "path",
    [
        "C:\\data\\feed.csv",
        "/var/data/feed.csv",
        "\\\\host\\share\\feed.csv",
        "../outside/feed.csv",
    ],
)
def test_non_relative_feed_paths_are_rejected(base_config: dict, path: str) -> None:
    base_config["feeds"][0]["path"] = path
    assert_invalid(base_config, "feeds.0.path")


def test_unsupported_feed_extension_is_rejected(base_config: dict) -> None:
    base_config["feeds"][0]["path"] = "data/samples/feed.parquet"
    assert_invalid(base_config, "feeds.0.path", ".csv or .xlsx")


def test_alias_to_unknown_canonical_field_is_rejected(base_config: dict) -> None:
    base_config["feeds"][0]["header_aliases"] = {"Raw": "warehouse_code"}
    assert_invalid(base_config, "warehouse_code", "canonical")


def test_empty_alias_key_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        FeedConfig.model_validate(
            {"name": "feed_a", "path": "data/f.csv", "header_aliases": {"": "record_id"}}
        )
    assert "non-empty header names" in str(excinfo.value)


# --- validation section ------------------------------------------------------


def test_non_canonical_required_field_is_rejected(base_config: dict) -> None:
    base_config["validation"] = {"required_fields": ["record_id", "warehouse_code"]}
    assert_invalid(base_config, "warehouse_code", "canonical")


def test_duplicate_required_fields_are_rejected(base_config: dict) -> None:
    base_config["validation"] = {"required_fields": ["record_id", "record_id"]}
    assert_invalid(base_config, "duplicate")


def test_empty_duplicate_key_fields_are_rejected(base_config: dict) -> None:
    base_config["validation"] = {"duplicate_key_fields": []}
    assert_invalid(base_config, "at least one canonical field")


def test_invalid_record_id_regex_is_rejected(base_config: dict) -> None:
    base_config["validation"] = {"record_id_pattern": "EXT-("}
    assert_invalid(base_config, "record_id_pattern", "regular expression")


# --- report section ----------------------------------------------------------


def test_unknown_report_format_is_rejected(base_config: dict) -> None:
    base_config["report"] = {"formats": ["pdf"]}
    assert_invalid(base_config, "report.formats")


def test_duplicate_report_formats_are_rejected(base_config: dict) -> None:
    base_config["report"] = {"formats": ["xlsx", "xlsx"]}
    assert_invalid(base_config, "duplicate formats")


def test_non_synthetic_recipient_is_rejected(base_config: dict) -> None:
    base_config["report"] = {"recipients": ["someone@other-domain.com"]}
    assert_invalid(base_config, "example.com")


def test_unsorted_variance_bands_are_rejected(base_config: dict) -> None:
    base_config["report"] = {"variance_bands": [5.0, 1.0]}
    assert_invalid(base_config, "strictly ascending")


def test_negative_variance_band_is_rejected(base_config: dict) -> None:
    base_config["report"] = {"variance_bands": [-1.0, 1.0]}
    assert_invalid(base_config, "non-negative")


# --- output_dir --------------------------------------------------------------


def test_absolute_output_dir_is_rejected(base_config: dict) -> None:
    base_config["output_dir"] = "C:\\runs\\output"
    assert_invalid(base_config, "output_dir", "relative")


# --- canonical field constant -------------------------------------------------


def test_canonical_fields_are_stable() -> None:
    expected = {"record_id", "account_ref", "amount", "unit", "period"}
    assert frozenset(expected) == CANONICAL_FIELDS
