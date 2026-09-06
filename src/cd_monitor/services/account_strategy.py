"""Account strategy helpers (P5.3+, ported from Usagi ai-goofish-monitor).

Each watchlist task carries two pieces of account routing:
* account_strategy in {auto, fixed, rotate}
* account_state_file  relative path to a stored login state

The single source of truth for the routing decision lives in
resolve_account_runtime_plan, so that CLI/UI/API all converge on the
same answer.
"""
from __future__ import annotations

from typing import Optional

ACCOUNT_STRATEGIES = {"auto", "fixed", "rotate"}


def clean_account_state_file(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"null", "undefined"}:
        return None
    return text


def normalize_account_strategy(strategy, account_state_file=None):
    raw = str(strategy or "").strip().lower()
    if raw in ACCOUNT_STRATEGIES:
        return raw
    if clean_account_state_file(account_state_file):
        return "fixed"
    return "auto"


def resolve_account_runtime_plan(*, strategy, account_state_file, has_root_state_file, available_account_files):
    normalized_strategy = normalize_account_strategy(strategy, account_state_file)
    cleaned_account = clean_account_state_file(account_state_file)
    has_account_pool = bool(available_account_files)

    if normalized_strategy == "fixed":
        return {"strategy": normalized_strategy, "forced_account": cleaned_account, "use_account_pool": False, "prefer_root_state": False}
    if normalized_strategy == "rotate":
        return {"strategy": normalized_strategy, "forced_account": None, "use_account_pool": has_account_pool, "prefer_root_state": False}
    return {"strategy": normalized_strategy, "forced_account": None, "use_account_pool": (not has_root_state_file) and has_account_pool, "prefer_root_state": has_root_state_file}


def assert_strategy_consistent(strategy, account_state_file):
    normalized = normalize_account_strategy(strategy, account_state_file)
    if normalized == "fixed":
        cleaned = clean_account_state_file(account_state_file)
        if not cleaned:
            raise ValueError("固定账号模式下必须选择账号。")


__all__ = ["ACCOUNT_STRATEGIES", "clean_account_state_file", "normalize_account_strategy", "resolve_account_runtime_plan", "assert_strategy_consistent"]
