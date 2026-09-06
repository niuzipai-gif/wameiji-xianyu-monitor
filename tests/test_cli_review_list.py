import json
import subprocess
import sys


def test_cli_review_list_returns_recorded_decisions(tmp_path) -> None:
    db_path = tmp_path / "review-list.db"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "review",
            "--opportunity-id",
            "1",
            "--result",
            "rejected_other",
            "--note",
            "manual note",
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
            "review-list",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload[0]["result"] == "rejected_other"
    assert payload[0]["note"] == "manual note"
