import json
import subprocess
import sys


def test_cli_add_watch_accepts_full_watch_fields(tmp_path) -> None:
    db_path = tmp_path / "watch-fields.db"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "add-watch",
            "--catalog-no",
            "SRCL-3520",
            "--jan",
            "4988009123456",
            "--artist",
            "Artist",
            "--title-jp",
            "Album JP",
            "--title-cn",
            "专辑 CN",
            "--edition",
            "初回限定",
            "--required-keyword",
            "帯付き",
            "--excluded-keyword",
            "レンタル",
            "--priority",
            "0",
            "--holding-days",
            "45",
            "--db",
            str(db_path),
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
            "list-watch",
            "--db",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    watch = json.loads(result.stdout)[0]
    assert watch["title_jp"] == "Album JP"
    assert watch["title_cn"] == "专辑 CN"
    assert watch["required_keywords"] == ["帯付き"]
    assert watch["excluded_keywords"] == ["レンタル"]
    assert watch["priority"] == 0
    assert watch["expected_holding_days"] == 45
