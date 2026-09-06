import sqlite3

from cd_monitor.core.models import WatchItem
from cd_monitor.services.scan import scan_once_mock
from cd_monitor.storage.sqlite import init_db


def test_scan_once_mock_persists_run_items_samples_and_opportunity(tmp_path) -> None:
    db_path = tmp_path / "scan.db"
    snapshot_dir = tmp_path / "snapshots"
    init_db(db_path)

    result = scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=snapshot_dir,
    )

    assert result.opportunities[0].catalog_no == "SRCL-3520"
    assert result.opportunity_ids[0] > 0
    assert result.search_run_id > 0
    assert result.snapshot_path.exists()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM market_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM xianyu_price_samples").fetchone()[0] == 7
        assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1


def test_scan_once_mock_applies_watch_edition_to_xianyu_estimate(tmp_path) -> None:
    result = scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=tmp_path / "scan-edition.db",
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
        watch_item=WatchItem(catalog_no="SRCL-3520", edition="初回限定"),
    )

    assert result.opportunities[0].valid_xianyu_sample_count == 2
    assert result.opportunities[0].liquidity_status == "poor"
    assert "liquidity_poor" in result.opportunities[0].risk_labels
