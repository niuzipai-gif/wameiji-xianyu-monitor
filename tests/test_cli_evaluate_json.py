import json
import subprocess
import sys


def test_cli_evaluate_json_combines_wameiji_and_xianyu_json(tmp_path) -> None:
    db_path = tmp_path / "json.db"
    snapshot_dir = tmp_path / "snapshots"
    wameiji_path = tmp_path / "wameiji.json"
    xianyu_path = tmp_path / "xianyu.json"
    wameiji_path.write_text(
        json.dumps(
            [
                {
                    "source": "wameiji",
                    "source_site": "mercari",
                    "external_item_id": "w1",
                    "title": "Artist SRCL-3520 初回限定 帯付き",
                    "price": 1200,
                    "currency": "JPY",
                    "availability": "available",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    xianyu_path.write_text(
        json.dumps(
            [
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 260},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 280},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 300},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "evaluate-json",
            "--catalog-no",
            "SRCL-3520",
            "--wameiji-json",
            str(wameiji_path),
            "--xianyu-json",
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
    assert payload["snapshot_path"]
