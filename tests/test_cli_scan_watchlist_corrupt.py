"""CLI scan-watchlist should refuse to start when the watchlist contains a
row whose strategy + state_file pair is inconsistent (e.g. fixed with no
file). The user sees a Chinese error and exit code 2, not a Python
traceback."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def _inject_corrupt_row(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO watchlist (catalog_no, catalog_no_compact, account_strategy, account_state_file, enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        ("CORRUPT-FIXED", "CORRUPT-FIXED", "fixed", None, 1),
    )
    conn.commit()
    conn.close()


def test_scan_watchlist_rejects_inconsistent_row_with_exit_2(tmp_path) -> None:
    db_path = tmp_path / "scan_corrupt.db"
    snapshot_dir = tmp_path / "snapshots"

    # 1) add a healthy watch first
    subprocess.run(
        [sys.executable, "-m", "cd_monitor.cli", "add-watch", "--catalog-no", "HEALTHY-AUTO", "--db", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    # 2) directly inject a corrupt row (bypassing the API)
    _inject_corrupt_row(db_path)

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
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    combined = result.stdout + result.stderr
    assert "固定账号" in combined
    # Should NOT have produced any snapshot file (failure halted execution).
    assert list(snapshot_dir.glob("*.json")) == []


def test_scan_watchlist_healthy_state_with_mock_passes(tmp_path) -> None:
    """Sanity check: with only healthy rows the mock scan completes."""
    db_path = tmp_path / "scan_healthy.db"
    snapshot_dir = tmp_path / "snapshots"
    subprocess.run(
        [sys.executable, "-m", "cd_monitor.cli", "add-watch", "--catalog-no", "SRCL-3520", "--db", str(db_path)],
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
