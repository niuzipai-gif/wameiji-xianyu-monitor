import json
import sqlite3
import subprocess
import sys


def test_cli_scan_live_reports_disabled_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_ENABLED", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "scan-live",
            "--catalog-no",
            "SRCL-3520",
            "--db",
            str(tmp_path / "live.db"),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "disabled"
    assert payload["opportunity_count"] == 0
    assert payload["wameiji"]["status"] == "disabled"
    assert payload["xianyu"]["status"] == "disabled"


def test_cli_scan_live_records_source_status_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_ENABLED", raising=False)
    db_path = tmp_path / "live-recorded.db"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "scan-live",
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

    payload = json.loads(result.stdout)
    assert payload["search_run_ids"]

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source, keyword, status, error_type, finished_at
            FROM search_runs
            ORDER BY id
            """
        ).fetchall()

    assert [row[:4] for row in rows] == [
        ("wameiji_live_browser", "SRCL-3520", "disabled", "browser_disabled"),
        ("xianyu_live_browser", "SRCL-3520", "disabled", "browser_disabled"),
    ]
    assert all(row[4] for row in rows)


def test_cli_scan_live_reports_not_configured_when_browser_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "scan-live",
            "--catalog-no",
            "SRCL-3520",
            "--db",
            str(tmp_path / "live-enabled.db"),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "human_required"
    assert payload["opportunity_count"] == 0
    assert payload["wameiji"]["error_type"] == "not_configured"
    assert payload["xianyu"]["error_type"] == "not_configured"
