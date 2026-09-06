import json
import subprocess
import sys


def test_cli_evaluate_files_combines_wameiji_html_and_xianyu_csv(tmp_path) -> None:
    db_path = tmp_path / "files.db"
    snapshot_dir = tmp_path / "snapshots"
    html_path = tmp_path / "wameiji.html"
    csv_path = tmp_path / "xianyu.csv"
    html_path.write_text(
        """
        <div data-item-card>
          <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定 帯付き</a>
          <span data-price>¥1,200</span>
          <span data-source-site>mercari</span>
        </div>
        """,
        encoding="utf-8",
    )
    csv_path.write_text(
        "title,price_cny\n"
        "Artist SRCL-3520,260\n"
        "Artist SRCL-3520,280\n"
        "Artist SRCL-3520,300\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "evaluate-files",
            "--catalog-no",
            "SRCL-3520",
            "--wameiji-html",
            str(html_path),
            "--xianyu-csv",
            str(csv_path),
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

    payload = json.loads(result.stdout)
    assert payload["opportunities"][0]["id"] > 0
    assert payload["opportunities"][0]["catalog_no"] == "SRCL-3520"
    assert payload["opportunities"][0]["decision"] in {
        "strong_alert",
        "weak_alert",
        "review_only",
        "reject",
    }
    assert payload["snapshot_path"]
