import json
import subprocess
import sys


def test_cli_import_html_snapshot_for_wameiji(tmp_path) -> None:
    html_path = tmp_path / "wameiji.html"
    snapshot_dir = tmp_path / "snapshots"
    html_path.write_text(
        """
        <div data-item-card>
          <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定</a>
          <span data-price>¥1,200</span>
          <span data-source-site>mercari</span>
        </div>
        """,
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "import-html",
            "--source",
            "wameiji",
            "--catalog-no",
            "SRCL-3520",
            "--html",
            str(html_path),
            "--snapshot-dir",
            str(snapshot_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["item_count"] == 1
    assert len(list(snapshot_dir.glob("*.json"))) == 1


def test_cli_import_html_snapshot_for_xianyu_security_check(tmp_path) -> None:
    html_path = tmp_path / "xianyu.html"
    snapshot_dir = tmp_path / "snapshots"
    html_path.write_text("请完成 CAPTCHA 安全验证后继续", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "import-html",
            "--source",
            "xianyu",
            "--catalog-no",
            "SRCL-3520",
            "--html",
            str(html_path),
            "--snapshot-dir",
            str(snapshot_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "human_required"
    assert payload["error_type"] == "security_check"
    assert len(list(snapshot_dir.glob("*.json"))) == 1
