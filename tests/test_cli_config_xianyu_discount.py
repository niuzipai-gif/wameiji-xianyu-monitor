import json
import subprocess
import sys


def test_cli_scan_once_uses_configured_xianyu_discount(tmp_path) -> None:
    db_path = tmp_path / "discount.db"
    snapshot_dir = tmp_path / "snapshots"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  db_path: "{db_path.as_posix()}"
  snapshot_dir: "{snapshot_dir.as_posix()}"
xianyu:
  negotiation_discount: 0.5
  liquidity_discount_default: 0.8
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
