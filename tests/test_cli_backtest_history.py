import json
import subprocess
import sys


def test_cli_backtest_can_replay_database_history(tmp_path) -> None:
    db_path = tmp_path / "history-cli.db"
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

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "backtest",
            "--db",
            str(db_path),
            "--catalog-no",
            "SRCL-3520",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload[0]["catalog_no"] == "SRCL-3520"
