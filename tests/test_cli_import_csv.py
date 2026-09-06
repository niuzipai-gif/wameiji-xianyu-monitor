import json
import subprocess
import sys


def test_cli_import_xianyu_csv_estimates_reference_price(tmp_path) -> None:
    csv_path = tmp_path / "xianyu.csv"
    snapshot_dir = tmp_path / "snapshots"
    csv_path.write_text(
        "title,price_cny,url\n"
        "Artist SRCL-3520,260,https://example.invalid/1\n"
        "Artist SRCL-3520,280,https://example.invalid/2\n"
        "Artist SRCL-3520,300,https://example.invalid/3\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "import-csv",
            "--source",
            "xianyu",
            "--catalog-no",
            "SRCL-3520",
            "--csv",
            str(csv_path),
            "--snapshot-dir",
            str(snapshot_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["sample_count"] == 3
    assert payload["estimate"]["reference_price_cny"] == 280
    assert len(list(snapshot_dir.glob("*.json"))) == 1


def test_cli_import_xianyu_csv_parses_common_price_text(tmp_path) -> None:
    csv_path = tmp_path / "xianyu-price-text.csv"
    snapshot_dir = tmp_path / "snapshots"
    csv_path.write_text(
        "标题,价格,链接\n"
        "Artist SRCL-3520,260元,https://example.invalid/1\n"
        "Artist SRCL-3520,CNY 280,https://example.invalid/2\n"
        "Artist SRCL-3520,￥300,https://example.invalid/3\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "import-csv",
            "--source",
            "xianyu",
            "--catalog-no",
            "SRCL-3520",
            "--csv",
            str(csv_path),
            "--snapshot-dir",
            str(snapshot_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["sample_count"] == 3
    assert payload["estimate"]["reference_price_cny"] == 280
