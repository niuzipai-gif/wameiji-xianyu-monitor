import json
import subprocess
import sys


def test_cli_alert_list_returns_sent_alerts(tmp_path) -> None:
    db_path = tmp_path / "alerts.db"
    snapshot_dir = tmp_path / "snapshots"
    subprocess.run(
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
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "notify-dry-run",
            "--opportunity-id",
            "1",
            "--channel",
            "feishu",
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
            "alert-list",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload[0]["channel"] == "feishu"
    assert payload[0]["status"] == "dry_run"
