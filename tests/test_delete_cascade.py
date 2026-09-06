"""Tests for P0 #2 — DELETE cascade cleanup on /api/watchlist/{id}/hard-delete.

When the user hard-deletes the last watch that still references a given
catalog_no, we should also clean up the historical artifacts tied to that
catalog:

- ``opportunities`` rows (per-catalog match history)
- ``price_snapshots`` rows (price-time-series for that catalog)
- (best-effort) per-task log files in the project's ``logs/`` directory

If another watch still pins the same catalog, the cascade must skip — that
watch still needs the history.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cd_monitor.core.models import WatchItem
from cd_monitor.storage.sqlite import (
    add_watch,
    count_watch_using_catalog,
    delete_opportunities_for_catalog,
    delete_price_snapshots_for_catalog,
    delete_watch,
    fetch_watch_min,
    init_db,
)
from cd_monitor.web_server import _hard_delete_with_cascade


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


def _seed_watch(db_path: Path, catalog_no: str = "SVWX-1234") -> int:
    watch = WatchItem(
        catalog_no=catalog_no,
        required_keywords=[],
        excluded_keywords=[],
    )
    return add_watch(str(db_path), watch)


def _seed_opportunity(db_path: Path, catalog_no: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """
            INSERT INTO opportunities (
                catalog_no, xianyu_reference_price, expected_sale_price,
                landed_cost, expected_revenue, expected_profit, net_margin,
                turnover_adjusted_roi, match_confidence,
                valid_xianyu_sample_count, liquidity_status, decision,
                risk_labels, opportunity_hash
            ) VALUES (?, 100, 200, 50, 180, 130, 0.65, 0.5, 0.9, 3,
                      'healthy', 'BUY', '[]', ?)
            """,
            (catalog_no, f"hash-{catalog_no}"),
        )
        opp_id = int(cur.lastrowid)
    finally:
        conn.commit()
        conn.close()
    return opp_id


def _seed_snapshot(db_path: Path, catalog_no: str, price: float = 1500.0) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """
            INSERT INTO price_snapshots (
                catalog_no, source, platform, price, currency,
                price_cny, sample_count, decision
            ) VALUES (?, 'wameiji', 'xianyu', ?, 'JPY', NULL, 1, 'hold')
            """,
            (catalog_no, price),
        )
        snap_id = int(cur.lastrowid)
    finally:
        conn.commit()
        conn.close()
    return snap_id


def _count_rows(db_path: Path, table: str, where: str, value) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where} = ?", (value,)
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])


def test_delete_watch_cascades_when_last_for_catalog(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _seed_watch(db, catalog_no="ZABC-0001")
    opp_id = _seed_opportunity(db, "ZABC-0001")
    snap_id = _seed_snapshot(db, "ZABC-0001", price=1750.0)

    # Sanity: helpers exist and the cascade target rows are reachable.
    assert fetch_watch_min(db, wid)["catalog_no"] == "ZABC-0001"
    assert count_watch_using_catalog(db, "ZABC-0001") == 1

    deleted, summary = _hard_delete_with_cascade(db, wid)
    assert deleted is True
    assert summary["opportunities_deleted"] == 1
    assert summary["price_snapshots_deleted"] == 1
    assert _count_rows(db, "opportunities", "id", opp_id) == 0
    assert _count_rows(db, "price_snapshots", "id", snap_id) == 0


def test_cascade_skipped_when_other_watch_uses_same_catalog(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid_a = _seed_watch(db, catalog_no="MULTI-9")
    wid_b = _seed_watch(db, catalog_no="MULTI-9")
    opp_id = _seed_opportunity(db, "MULTI-9")
    snap_id = _seed_snapshot(db, "MULTI-9")

    deleted, summary = _hard_delete_with_cascade(db, wid_a)
    assert deleted is True
    # Cascade should NOT fire while another watch is still pinned.
    assert summary["opportunities_deleted"] == 0
    assert summary["price_snapshots_deleted"] == 0
    # Pinning watch still present; data must remain.
    assert fetch_watch_min(db, wid_b) is not None
    assert _count_rows(db, "opportunities", "id", opp_id) == 1
    assert _count_rows(db, "price_snapshots", "id", snap_id) == 1

    # Now delete the second watch — cascade IS safe.
    deleted, summary = _hard_delete_with_cascade(db, wid_b)
    assert deleted is True
    assert summary["opportunities_deleted"] == 1
    assert summary["price_snapshots_deleted"] == 1
    assert _count_rows(db, "opportunities", "id", opp_id) == 0
    assert _count_rows(db, "price_snapshots", "id", snap_id) == 0


def test_cascade_graceful_when_log_files_missing(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _seed_watch(db, catalog_no="LOGGONE-7")
    _seed_opportunity(db, "LOGGONE-7")
    _seed_snapshot(db, "LOGGONE-7")

    # No logs/ directory; cascade still completes cleanly, summary shows 0.
    deleted, summary = _hard_delete_with_cascade(db, wid)
    assert deleted is True
    assert summary["opportunities_deleted"] == 1
    assert summary["price_snapshots_deleted"] == 1
    assert summary["log_files_deleted"] == 0


def test_cascade_graceful_when_log_file_present(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _seed_watch(db, catalog_no="LOGCLEAN-1")

    # Force the helper's log_dir (parent of db) + logs/ subdir to exist,
    # then drop a per-watch log file there.
    db.parent.mkdir(parents=True, exist_ok=True)
    (db.parent / "logs").mkdir(exist_ok=True)
    log_path = db.parent / "logs" / f"{wid}.log"
    log_path.write_text("test log content", encoding="utf-8")
    assert log_path.exists()

    deleted, summary = _hard_delete_with_cascade(db, wid)
    assert deleted is True
    assert summary["log_files_deleted"] == 1
    assert not log_path.exists()


def test_cascade_handles_already_missing_watch(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    deleted, summary = _hard_delete_with_cascade(db, 999_999)
    assert deleted is False
    # Nothing to cascade; counters stay zero.
    assert summary == {
        "opportunities_deleted": 0,
        "price_snapshots_deleted": 0,
        "log_files_deleted": 0,
    }


def test_fetch_watch_min_returns_none_for_missing(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    assert fetch_watch_min(db, 999_999) is None


def test_count_watch_using_catalog_handles_exclusion(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    a = _seed_watch(db, catalog_no="C-1")
    b = _seed_watch(db, catalog_no="C-1")
    assert count_watch_using_catalog(db, "C-1") == 2
    assert count_watch_using_catalog(db, "C-1", exclude_watch_id=a) == 1
    assert count_watch_using_catalog(db, "C-1", exclude_watch_id=b) == 1
    assert count_watch_using_catalog(db, "C-1", exclude_watch_id=999) == 2
    assert count_watch_using_catalog(db, "other") == 0
