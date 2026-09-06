"""Tests for cd_monitor.services.price_history_service."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from cd_monitor.services.price_history_service import (
    PriceHistoryService,
    PriceSnapshot,
    PriceTrend,
)
from cd_monitor.storage.sqlite import init_db


def _setup(tmp_path: Path) -> PriceHistoryService:
    db = tmp_path / "ph.db"
    init_db(db)
    return PriceHistoryService(db)


def _snap(catalog, price, *, source="scan", platform="wameiji",
          currency="JPY", price_cny=None, decision=None, sample_count=1,
          task_id=None):
    return PriceSnapshot(
        catalog_no=catalog,
        source=source,
        platform=platform,
        price=price,
        currency=currency,
        price_cny=price_cny,
        sample_count=sample_count,
        decision=decision,
        task_id=task_id,
    )


class TestRecordSnapshot:
    def test_returns_id(self, tmp_path):
        svc = _setup(tmp_path)
        sid = svc.record_snapshot(_snap("CAT-1", 1000.0, price_cny=50.0))
        assert sid > 0

    def test_bulk_insert(self, tmp_path):
        svc = _setup(tmp_path)
        snaps = [_snap("CAT-B", 100 + i) for i in range(5)]
        n = svc.record_snapshots(snaps)
        assert n == 5
        rows = svc.get_recent("CAT-B")
        assert len(rows) == 5

    def test_bulk_empty_returns_zero(self, tmp_path):
        svc = _setup(tmp_path)
        assert svc.record_snapshots([]) == 0


class TestGetRecent:
    def test_orders_newest_first(self, tmp_path):
        svc = _setup(tmp_path)
        for i in range(3):
            svc.record_snapshot(_snap("CAT-REC", 100 + i))
        rows = svc.get_recent("CAT-REC", limit=10)
        assert len(rows) == 3
        times = [r["captured_at"] for r in rows]
        assert times == sorted(times, reverse=True)

    def test_respects_limit(self, tmp_path):
        svc = _setup(tmp_path)
        for i in range(10):
            svc.record_snapshot(_snap("CAT-LIM", 100 + i))
        rows = svc.get_recent("CAT-LIM", limit=3)
        assert len(rows) == 3

    def test_unknown_catalog_returns_empty(self, tmp_path):
        svc = _setup(tmp_path)
        assert svc.get_recent("DOES-NOT-EXIST") == []


class TestGetTrend:
    def test_returns_none_when_empty(self, tmp_path):
        svc = _setup(tmp_path)
        assert svc.get_trend("NOPE") is None

    def test_aggregates_stats(self, tmp_path):
        svc = _setup(tmp_path)
        for p in [100, 200, 300, 400, 500]:
            svc.record_snapshot(_snap("CAT-TR", p, decision="buy" if p == 300 else None))
        t = svc.get_trend("CAT-TR")
        assert t is not None
        assert t.sample_count == 5
        assert t.min_price == 100
        assert t.max_price == 500
        assert t.avg_price == 300
        assert t.latest_price == 500
        assert t.latest_decision is None

    def test_since_filter(self, tmp_path):
        svc = _setup(tmp_path)
        svc.record_snapshots([
            _snap("CAT-SINCE", 100),
            _snap("CAT-SINCE", 200),
        ])
        cutoff = datetime.now() + timedelta(seconds=1)
        t = svc.get_trend("CAT-SINCE", since=cutoff)
        assert t is None

        cutoff = datetime.now() - timedelta(days=1)
        t = svc.get_trend("CAT-SINCE", since=cutoff)
        assert t is not None
        assert t.sample_count == 2


class TestGetAllTrends:
    def test_returns_per_catalog_summary(self, tmp_path):
        svc = _setup(tmp_path)
        for cat in ["A", "B", "C"]:
            for p in [100, 200]:
                svc.record_snapshot(_snap(cat, p))
        trends = svc.get_all_trends()
        catalogs = {t["catalog_no"] for t in trends}
        assert catalogs == {"A", "B", "C"}
        for t in trends:
            assert t["sample_count"] == 2
            assert t["min_price"] == 100
            assert t["max_price"] == 200

    def test_empty_returns_empty_list(self, tmp_path):
        svc = _setup(tmp_path)
        assert svc.get_all_trends() == []


class TestPriceTrendDataclass:
    def test_to_dict_round_trip(self):
        t = PriceTrend(
            catalog_no="X",
            sample_count=3,
            min_price=1.0,
            max_price=9.0,
            avg_price=5.0,
            latest_price=9.0,
            latest_decision="buy",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-02T00:00:00",
        )
        d = t.to_dict()
        assert d["catalog_no"] == "X"
        assert d["min_price"] == 1.0
        assert d["latest_decision"] == "buy"


class TestPriceSnapshotDataclass:
    def test_to_row_includes_all_fields(self):
        snap = PriceSnapshot(
            catalog_no="C",
            source="x",
            platform="y",
            price=1.0,
            currency="CNY",
            price_cny=1.0,
            sample_count=2,
            decision="ok",
            opportunity_id=10,
            task_id=20,
            run_id=30,
            captured_at=datetime(2026, 1, 1),
        )
        row = snap.to_row()
        assert row[0] == "C"
        assert row[1] == "x"
        assert row[2] == "y"
        assert row[3] == 1.0
        assert row[4] == "CNY"
        assert row[5] == 1.0
        assert row[6] == 2
        assert row[7] == "ok"
        assert row[8] == 10
        assert row[9] == 20
        assert row[10] == 30
        # P5.5 per-catalog / per-scan semantics (P0 #8):
        # row[11] = source_kind (defaults to 'mock'),
        # row[12] = scan_run_id,
        # row[13] = notes,
        # row[14] = captured_at.
        assert row[11] == 'mock'
        assert row[12] is None
        assert row[13] is None
        assert row[14] == datetime(2026, 1, 1)