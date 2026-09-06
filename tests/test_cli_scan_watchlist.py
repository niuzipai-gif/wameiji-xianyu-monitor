import json
import subprocess
import sys


def test_cli_scan_watchlist_scans_enabled_items(tmp_path) -> None:
    db_path = tmp_path / "watchlist.db"
    snapshot_dir = tmp_path / "snapshots"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "add-watch",
            "--catalog-no",
            "SRCL-3520",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "scan-watchlist",
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
    assert payload["scanned_count"] == 1
    assert payload["opportunity_count"] == 1
    assert len(list(snapshot_dir.glob("*.json"))) == 1
