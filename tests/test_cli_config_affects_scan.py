import json
import subprocess
import sys


def test_cli_scan_once_uses_configured_alert_thresholds(tmp_path) -> None:
    db_path = tmp_path / "thresholds.db"
    snapshot_dir = tmp_path / "snapshots"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  db_path: "{db_path.as_posix()}"
  snapshot_dir: "{snapshot_dir.as_posix()}"
alert:
  strong_profit_min_cny: 999
  weak_profit_min_cny: 999
""",
        encoding="utf-8",
    )

    result = subprocess.run(
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

    payload = json.loads(result.stdout)
    assert payload["opportunities"][0]["decision"] == "review_only"


def test_cli_scan_once_uses_configured_xianyu_price_bounds(tmp_path) -> None:
    db_path = tmp_path / "xianyu-bounds.db"
    snapshot_dir = tmp_path / "snapshots"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  db_path: "{db_path.as_posix()}"
  snapshot_dir: "{snapshot_dir.as_posix()}"
xianyu:
  min_valid_price_cny: 300
  max_valid_price_cny: 2000
""",
        encoding="utf-8",
    )

    result = subprocess.run(
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

    payload = json.loads(result.stdout)
    assert payload["opportunities"][0]["decision"] == "reject"
    assert "liquidity_poor" in payload["opportunities"][0]["risk_labels"]
