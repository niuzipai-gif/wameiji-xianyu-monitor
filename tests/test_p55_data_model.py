"""Tests for P5.5 data-model parity: scan filters, opportunity columns, runtime reset."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cd_monitor.core.models import WatchItem
from cd_monitor.services.task_repository import TaskRepository
from cd_monitor.storage.sqlite import (
    add_watch,
    init_db,
    reset_all_is_running,
    update_watch,
)


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "p55.db"
    init_db(str(db))
    return db


# -------- watchlist filter columns --------


def test_watchlist_p55_columns_present(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    for col in ("region", "personal_only", "analyze_images", "min_price", "max_price", "max_pages"):
        assert col in cols, f"missing column {col}"


def test_add_watch_round_trip_p55_fields(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = add_watch(
        str(db),
        WatchItem(
            catalog_no="P55-1",
            title_jp="t",
            region="jp",
            personal_only=True,
            analyze_images=False,
            min_price=100.0,
            max_price=500.0,
            max_pages=3,
        ),
    )
    rows = TaskRepository(str(db)).list_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["region"] == "jp"
    assert bool(row["personal_only"]) is True
    assert bool(row["analyze_images"]) is False
    assert abs(row["min_price"] - 100.0) < 1e-6
    assert abs(row["max_price"] - 500.0) < 1e-6
    assert int(row["max_pages"]) == 3


def test_update_watch_p55_fields(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = add_watch(str(db), WatchItem(catalog_no="P55-2", title_jp="t"))
    update_watch(str(db), wid, {
        "region": "hk",
        "personal_only": True,
        "max_pages": 7,
        "min_price": 50.0,
    })
    rows = TaskRepository(str(db)).list_all()
    assert rows[0]["region"] == "hk"
    assert bool(rows[0]["personal_only"]) is True
    assert int(rows[0]["max_pages"]) == 7
    assert abs(rows[0]["min_price"] - 50.0) < 1e-6


def test_update_watch_can_set_is_running(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = add_watch(str(db), WatchItem(catalog_no="P55-3", title_jp="t"))
    update_watch(str(db), wid, {"is_running": 1})
    rows = TaskRepository(str(db)).list_all()
    assert bool(rows[0]["is_running"]) is True


# -------- is_running startup reset --------


def test_reset_all_is_running_clears_every_row(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    for cat in ("R-1", "R-2", "R-3"):
        add_watch(str(db), WatchItem(catalog_no=cat, title_jp="t"))
    # Mark each as running
    rows = TaskRepository(str(db)).list_all()
    for r in rows:
        update_watch(str(db), r["id"], {"is_running": 1, "last_status": "running"})
    assert all(bool(r["is_running"]) for r in TaskRepository(str(db)).list_all())
    n = reset_all_is_running(str(db))
    assert n == 3
    # All reset; last_status records the boot-reset reason.
    after = TaskRepository(str(db)).list_all()
    assert all(not bool(r["is_running"]) for r in after)
    assert all((r["last_status"] == "reset_on_boot") for r in after)


# -------- opportunity p55 columns --------


def test_opportunities_p55_columns_present(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
    for col in ("analysis_source", "status", "link_unique_key", "seller_nickname", "publish_time"):
        assert col in cols, f"missing column {col}"


def test_opportunities_link_unique_key_index(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        rows = conn.execute("PRAGMA index_list(opportunities)").fetchall()
        names = {r[1] for r in rows}
    assert "idx_opportunities_catalog_link" in names
