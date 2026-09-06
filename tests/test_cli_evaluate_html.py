import json
import subprocess
import sys


def test_cli_evaluate_html_combines_wameiji_and_xianyu_html(tmp_path) -> None:
    db_path = tmp_path / "html.db"
    snapshot_dir = tmp_path / "snapshots"
    wameiji_path = tmp_path / "wameiji.html"
    xianyu_path = tmp_path / "xianyu.html"
    wameiji_path.write_text(
        """
        <div data-item-card>
          <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定 帯付き</a>
          <span data-price>¥1,200</span>
          <span data-source-site>mercari</span>
        </div>
        """,
        encoding="utf-8",
    )
    xianyu_path.write_text(
        """
        <div data-xianyu-card>
          <a href="https://example.invalid/x/1">Artist SRCL-3520 初回限定</a>
          <span data-price>￥260</span>
        </div>
        <div data-xianyu-card>
          <a href="https://example.invalid/x/2">Artist SRCL-3520 通常盤</a>
          <span data-price>￥280</span>
        </div>
        <div data-xianyu-card>
          <a href="https://example.invalid/x/3">Artist SRCL-3520 帯付き</a>
          <span data-price>￥300</span>
        </div>
        """,
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "evaluate-html",
            "--catalog-no",
            "SRCL-3520",
            "--wameiji-html",
            str(wameiji_path),
            "--xianyu-html",
            str(xianyu_path),
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
