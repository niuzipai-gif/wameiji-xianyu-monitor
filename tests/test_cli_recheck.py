import json
import sqlite3
import subprocess
import sys


def test_cli_recheck_plan_list_and_resolve(tmp_path) -> None:
    db_path = tmp_path / "recheck-cli.db"
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
    with sqlite3.connect(db_path) as conn:
        opportunity_id = conn.execute("SELECT id FROM opportunities LIMIT 1").fetchone()[0]

    planned = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "recheck-plan",
            "--db",
            str(db_path),
            "--opportunity-id",
            str(opportunity_id),
            "--delay-seconds",
            "1",
            "--reason",
            "cli second pass",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    planned_payload = json.loads(planned.stdout)
    assert planned_payload["opportunity_id"] == opportunity_id
    assert planned_payload["status"] == "pending"

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "recheck-list",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    listed_payload = json.loads(listed.stdout)
    assert listed_payload[0]["reason"] == "cli second pass"

    resolved = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "recheck-resolve",
            "--db",
            str(db_path),
            "--recheck-id",
            str(planned_payload["id"]),
            "--status",
            "confirmed",
            "--reason",
            "cli confirmed",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    resolved_payload = json.loads(resolved.stdout)
    assert resolved_payload["status"] == "confirmed"
    assert resolved_payload["reason"] == "cli confirmed"

    history = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "recheck-list",
            "--db",
            str(db_path),
            "--status",
            "all",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    history_payload = json.loads(history.stdout)
    assert history_payload[0]["status"] == "confirmed"
