"""Tests for the account strategy helper (P5.3+, ported from Usagi)."""
import pytest

from cd_monitor.services.account_strategy import (
    ACCOUNT_STRATEGIES,
    assert_strategy_consistent,
    clean_account_state_file,
    normalize_account_strategy,
    resolve_account_runtime_plan,
)


class TestNormalizeStrategy:
    def test_none_strategy_defaults_to_auto(self):
        assert normalize_account_strategy(None) == "auto"
        assert normalize_account_strategy("") == "auto"

    def test_unknown_strategy_with_empty_file_defaults_to_auto(self):
        assert normalize_account_strategy("weird", None) == "auto"

    def test_unknown_strategy_with_file_implies_fixed(self):
        assert normalize_account_strategy(None, "xianyu/a.json") == "fixed"

    def test_case_insensitive(self):
        assert normalize_account_strategy("AUTO") == "auto"
        assert normalize_account_strategy("Fixed") == "fixed"


class TestCleanFile:
    def test_none_returns_none(self):
        assert clean_account_state_file(None) is None

    def test_blank_returns_none(self):
        assert clean_account_state_file("") is None
        assert clean_account_state_file("   ") is None
        assert clean_account_state_file("null") is None
        assert clean_account_state_file("undefined") is None

    def test_real_value(self):
        assert clean_account_state_file("  xianyu/a.json ") == "xianyu/a.json"


class TestResolvePlan:
    def test_fixed_strategy(self):
        plan = resolve_account_runtime_plan(
            strategy="fixed",
            account_state_file="wameiji/u1.json",
            has_root_state_file=True,
            available_account_files=["wameiji/u1.json", "xianyu/u2.json"],
        )
        assert plan["strategy"] == "fixed"
        assert plan["forced_account"] == "wameiji/u1.json"
        assert plan["use_account_pool"] is False

    def test_rotate_strategy(self):
        plan = resolve_account_runtime_plan(
            strategy="rotate",
            account_state_file=None,
            has_root_state_file=False,
            available_account_files=["xianyu/a.json"],
        )
        assert plan["strategy"] == "rotate"
        assert plan["forced_account"] is None
        assert plan["use_account_pool"] is True

    def test_auto_prefers_root_when_present(self):
        plan = resolve_account_runtime_plan(
            strategy="auto",
            account_state_file=None,
            has_root_state_file=True,
            available_account_files=["xianyu/a.json"],
        )
        assert plan["strategy"] == "auto"
        assert plan["prefer_root_state"] is True
        assert plan["use_account_pool"] is False

    def test_auto_falls_back_to_pool_when_no_root(self):
        plan = resolve_account_runtime_plan(
            strategy="auto",
            account_state_file=None,
            has_root_state_file=False,
            available_account_files=["xianyu/a.json"],
        )
        assert plan["strategy"] == "auto"
        assert plan["prefer_root_state"] is False
        assert plan["use_account_pool"] is True


class TestAssertConsistent:
    def test_fixed_without_file_raises(self):
        with pytest.raises(ValueError, match="固定账号"):
            assert_strategy_consistent("fixed", None)
        with pytest.raises(ValueError, match="固定账号"):
            assert_strategy_consistent("fixed", "  ")

    def test_fixed_with_file_ok(self):
        assert_strategy_consistent("fixed", "xianyu/a.json")

    def test_other_strategies_free(self):
        for strategy in ACCOUNT_STRATEGIES - {"fixed"}:
            assert_strategy_consistent(strategy, None)
