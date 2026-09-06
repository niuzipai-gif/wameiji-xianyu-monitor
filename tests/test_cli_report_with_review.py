import subprocess
import sys


def test_cli_report_includes_review_decisions(tmp_path) -> None:
    db_path = tmp_path / "report-review.db"
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
            "review",
            "--db",
            str(db_path),
            "--opportunity-id",
            "1",
            "--result",
            "rejected_condition_bad",
            "--note",
            "case cracked",
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
    assert "rejected_condition_bad" in report
    assert "case cracked" in report


def test_cli_report_includes_recheck_history(tmp_path) -> None:
    db_path = tmp_path / "report-recheck.db"
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
    planned = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "recheck-plan",
            "--db",
            str(db_path),
            "--opportunity-id",
            "1",
            "--reason",
            "report second pass",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    recheck_id = __import__("json").loads(planned.stdout)["id"]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "recheck-resolve",
            "--db",
            str(db_path),
            "--recheck-id",
            str(recheck_id),
            "--status",
            "confirmed",
            "--reason",
            "report confirmed",
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
    assert "已记录二次复核" in report
    assert "confirmed" in report
    assert "report confirmed" in report
