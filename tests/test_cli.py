import json
import subprocess
import sys


def test_cli_scan_once_uses_service_and_writes_snapshot(tmp_path) -> None:
    db_path = tmp_path / "cli.db"
    snapshot_dir = tmp_path / "snapshots"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "scan-once",
            "--catalog-no",
            "SRCL-3520",
            "--source",
            "mock",
            "--db",
            str(db_path),
            "--snapshot-dir",
            str(snapshot_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["search_run_id"] > 0
    assert payload["opportunities"][0]["id"] > 0
    assert payload["opportunities"][0]["catalog_no"] == "SRCL-3520"
    assert payload["opportunities"][0]["xianyu_reference_price"] > 0
    assert payload["opportunities"][0]["landed_cost"] > 0
    assert payload["opportunities"][0]["turnover_adjusted_roi"] > 0
    assert payload["opportunities"][0]["valid_xianyu_sample_count"] >= 3
    assert payload["opportunities"][0]["liquidity_status"]
    assert payload["opportunities"][0]["match_confidence"] > 0
    assert payload["opportunities"][0]["item_url"]
    assert payload["opportunities"][0]["source_site"]
    assert len(list(snapshot_dir.glob("*.json"))) == 1
