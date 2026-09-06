import json
import subprocess
import sys


def test_cli_uses_config_file_for_db_and_lists_opportunities(tmp_path) -> None:
    db_path = tmp_path / "configured.db"
    snapshot_dir = tmp_path / "configured-snapshots"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  db_path: "{db_path.as_posix()}"
  snapshot_dir: "{snapshot_dir.as_posix()}"
""",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "--config",
            str(config_path),
            "scan-once",
            "--catalog-no",
            "SRCL-3520",
            "--source",
            "mock",
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
            "--config",
            str(config_path),
            "list-opportunities",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload[0]["id"] > 0
    assert payload[0]["catalog_no"] == "SRCL-3520"
    assert payload[0]["decision"] == "strong_alert"
    assert db_path.exists()
    assert len(list(snapshot_dir.glob("*.json"))) == 1
