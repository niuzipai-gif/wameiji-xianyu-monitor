"""Tests for P0 #8: price_snapshots adapts to per-catalog / per-scan semantics."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cd_monitor.services.price_history_service import (
    PriceHistoryService,
    PriceSnapshot,
)
from cd_monitor.storage.sqlite import init_db


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "price.db"
    init_db(str(db))
    return db


def test_migration_adds_p55_columns(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(price_snapshots)").fetchall()}
    assert "source_kind" in cols
    assert "scan_run_id" in cols
    assert "notes" in cols


def test_migration_creates_scan_run_index(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        idx = {
            row[1]
            for row in conn.execute("PRAGMA index_list(price_snapshots)").fetchall()
        }
    assert "idx_price_snapshots_scan_run" in idx


def test_record_snapshot_carries_p55_fields(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    svc = PriceHistoryService(db)
    snap = PriceSnapshot(
        catalog_no="ABC-123",
        source="wameiji",
        platform="wameiji",
        price=1980.0,
        currency="JPY",
        price_cny=95.5,
        source_kind="live",
        scan_run_id="run-001",
        notes="AI ok",
    )
    sid = svc.record_snapshot(snap)
    rows = svc.get_recent("ABC-123")
    assert len(rows) == 1
    row = rows[0]
    assert row["source_kind"] == "live"
    assert row["scan_run_id"] == "run-001"
    assert row["notes"] == "AI ok"
    assert row["id"] == sid


def test_record_snapshot_defaults_source_kind(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    svc = PriceHistoryService(db)
    snap = PriceSnapshot(
        catalog_no="DEF-456",
        source="wameiji",
        platform="wameiji",
        price=500.0,
        currency="JPY",
    )
    svc.record_snapshot(snap)
    rows = svc.get_recent("DEF-456")
    assert rows[0]["source_kind"] == "mock"
    assert rows[0]["scan_run_id"] is None
    assert rows[0]["notes"] is None


def test_list_by_scan_run_returns_full_pass(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    svc = PriceHistoryService(db)
    # First scan pass
    svc.record_snapshots(
        [
            PriceSnapshot(
                catalog_no="G-1", source="wameiji", platform="wameiji",
                price=1000.0, currency="JPY", scan_run_id="pass-1",
            ),
            PriceSnapshot(
                catalog_no="G-1", source="xianyu", platform="xianyu",
                price=50.0, currency="CNY", scan_run_id="pass-1",
            ),
        ]
    )
    # Second scan pass with a different uuid
    svc.record_snapshots(
        [
            PriceSnapshot(
                catalog_no="G-1", source="wameiji", platform="wameiji",
                price=1100.0, currency="JPY", scan_run_id="pass-2",
            ),
        ]
    )
    rows = svc.list_by_scan_run("pass-1")
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"wameiji", "xianyu"}
    assert {r["scan_run_id"] for r in rows} == {"pass-1"}
    assert svc.list_by_scan_run("pass-2") and len(svc.list_by_scan_run("pass-2")) == 1
    assert svc.list_by_scan_run("missing") == []


def test_trend_still_works_with_new_columns(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    svc = PriceHistoryService(db)
    svc.record_snapshots(
        [
            PriceSnapshot(catalog_no="H-1", source="wameiji", platform="wameiji",
                          price=200.0, currency="JPY", scan_run_id="a"),
            PriceSnapshot(catalog_no="H-1", source="wameiji", platform="wameiji",
                          price=300.0, currency="JPY", scan_run_id="a"),
        ]
    )
    trend = svc.get_trend("H-1")
    assert trend is not None
    assert trend.sample_count == 2
    assert trend.min_price == 200.0
    assert trend.max_price == 300.0
    assert trend.avg_price == 250.0
