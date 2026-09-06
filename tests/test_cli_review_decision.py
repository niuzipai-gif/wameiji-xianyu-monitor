import json
import subprocess
import sys


def test_cli_review_records_manual_decision(tmp_path) -> None:
    db_path = tmp_path / "review-cli.db"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "review",
            "--opportunity-id",
            "1",
            "--result",
            "rejected_low_profit",
            "--note",
            "manual check",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["id"] == 1
    assert payload["result"] == "rejected_low_profit"
