import subprocess
import sys


def test_cli_report_writes_manual_review_report(tmp_path) -> None:
    db_path = tmp_path / "report.db"
    snapshot_dir = tmp_path / "snapshots"
    report_path = tmp_path / "report.md"
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
            "report",
            "--db",
            str(db_path),
            "--output",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    report = report_path.read_text(encoding="utf-8")
    assert "CD 价差人工复核报告" in report
    assert "SRCL-3520" in report
    assert "rejected_liquidity_poor" in report
