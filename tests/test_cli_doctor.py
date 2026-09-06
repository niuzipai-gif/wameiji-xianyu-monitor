import json
import subprocess
import sys


def test_cli_doctor_reports_local_health(tmp_path) -> None:
    db_path = tmp_path / "doctor.db"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "doctor",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["checks"]["python"]["ok"] is True
    assert payload["checks"]["mock_wameiji"]["ok"] is True
    assert payload["checks"]["mock_xianyu"]["ok"] is True
    assert payload["checks"]["db_parent"]["ok"] is True
    assert payload["checks"]["browser"]["status"] == "disabled"
    assert payload["checks"]["data_sources"]["mock"]["ok"] is True
    assert payload["checks"]["data_sources"]["manual_snapshots"]["ok"] is True
    assert payload["checks"]["data_sources"]["manual_snapshots"]["commands"] == [
        "import-html",
        "import-csv",
        "evaluate-json",
        "evaluate-files",
        "evaluate-html",
    ]
    assert payload["checks"]["data_sources"]["manual_snapshots"]["web_actions"] == [
        "Evaluate HTML/CSV",
        "Evaluate HTML/HTML",
        "Evaluate Pasted HTML",
    ]
    assert payload["checks"]["data_sources"]["wameiji_live_browser"]["status"] == "disabled"
    assert payload["checks"]["data_sources"]["xianyu_live_browser"]["status"] == "disabled"
