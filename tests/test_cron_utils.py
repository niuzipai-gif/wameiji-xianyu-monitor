"""Tests for cd_monitor.core.cron_utils."""
from __future__ import annotations

import pytest

from cd_monitor.core.cron_utils import (
    CRON_ALIASES,
    build_cron_trigger,
    normalize_cron_expression,
    validate_cron_expression,
)


class TestNormalize:
    def test_none_returns_none(self):
        assert normalize_cron_expression(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_cron_expression("") is None
        assert normalize_cron_expression("   ") is None

    def test_collapses_whitespace(self):
        assert normalize_cron_expression("  */15   *  *  *  *  ") == "*/15 * * * *"

    def test_alias_hourly(self):
        assert normalize_cron_expression("@hourly") == "0 * * * *"

    def test_alias_daily(self):
        assert normalize_cron_expression("@daily") == "0 0 * * *"

    def test_alias_weekly(self):
        assert normalize_cron_expression("@weekly") == "0 0 * * 0"

    def test_alias_monthly(self):
        assert normalize_cron_expression("@monthly") == "0 0 1 * *"

    def test_alias_yearly(self):
        assert normalize_cron_expression("@yearly") == "0 0 1 1 *"

    def test_alias_is_case_insensitive(self):
        assert normalize_cron_expression("@DAILY") == "0 0 * * *"

    def test_passthrough_explicit_expression(self):
        assert normalize_cron_expression("0 8 * * *") == "0 8 * * *"


class TestValidate:
    def test_valid_5_field(self):
        assert validate_cron_expression("*/15 * * * *") == "*/15 * * * *"

    def test_valid_6_field(self):
        assert validate_cron_expression("0 0 8 * * *") == "0 0 8 * * *"

    def test_valid_alias(self):
        assert validate_cron_expression("@daily") == "0 0 * * *"

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_cron_expression("not a cron")

    def test_invalid_out_of_range_raises(self):
        with pytest.raises(ValueError):
            validate_cron_expression("99 99 99 99 99")

    def test_none_returns_none(self):
        assert validate_cron_expression(None) is None

    def test_empty_returns_none(self):
        assert validate_cron_expression("") is None


class TestBuildTrigger:
    def test_5_field_builds_trigger(self):
        trig = build_cron_trigger("0 8 * * *")
        assert trig is not None

    def test_6_field_builds_trigger(self):
        trig = build_cron_trigger("0 0 8 * * *")
        assert trig is not None

    def test_alias_builds_trigger(self):
        trig = build_cron_trigger("@hourly")
        assert trig is not None

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            build_cron_trigger("bogus")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            build_cron_trigger(None)

    def test_timezone_pass_through(self):
        # Should not raise — timezone is just passed through.
        trig = build_cron_trigger("0 8 * * *", timezone="Asia/Shanghai")
        assert trig is not None


class TestAliases:
    def test_all_aliases_have_5_field_value(self):
        for alias, expansion in CRON_ALIASES.items():
            parts = expansion.split()
            assert len(parts) == 5, f"{alias} -> {expansion} should have 5 fields"
