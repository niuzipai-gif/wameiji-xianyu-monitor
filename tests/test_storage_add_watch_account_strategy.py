"""Confirm storage.add_watch persists the P5.3 fields
account_state_file + account_strategy (regression for the SQL INSERT that
omitted the two columns)."""
from __future__ import annotations

from cd_monitor.core.models import WatchItem
from cd_monitor.storage.sqlite import add_watch, list_watch_all, init_db


def test_add_watch_persists_account_strategy_and_state_file(tmp_path) -> None:
    db_path = tmp_path / "p5_3_storage.db"
    init_db(db_path)
    wid = add_watch(
        db_path,
        WatchItem(
            catalog_no="FIXED-STORAGE",
            account_strategy="fixed",
            account_state_file="xianyu/main.json",
        ),
    )
    rows = list_watch_all(db_path)
    row = next(r for r in rows if r.catalog_no == "FIXED-STORAGE")
    assert wid > 0
    assert row.account_strategy == "fixed"
    assert row.account_state_file == "xianyu/main.json"


def test_add_watch_persists_default_auto_and_null_file(tmp_path) -> None:
    db_path = tmp_path / "p5_3_storage_defaults.db"
    init_db(db_path)
    add_watch(db_path, WatchItem(catalog_no="AUTO-STORAGE"))
    rows = list_watch_all(db_path)
    row = next(r for r in rows if r.catalog_no == "AUTO-STORAGE")
    assert row.account_strategy == "auto"
    assert row.account_state_file in (None, "")


def test_add_watch_rejects_fixed_without_file(tmp_path) -> None:
    db_path = tmp_path / "p5_3_storage_fixed_reject.db"
    init_db(db_path)
    import pytest
    with pytest.raises(ValueError):
        add_watch(
            db_path,
            WatchItem(catalog_no="BAD-FIXED", account_strategy="fixed"),
        )
