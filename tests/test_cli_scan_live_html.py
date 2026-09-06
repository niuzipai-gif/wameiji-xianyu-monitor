import json

from cd_monitor import cli


def test_cli_scan_live_html_prints_capture_and_opportunity_summary(tmp_path, monkeypatch, capsys) -> None:
    async def fake_scan(catalog_no, db_path, snapshot_dir, _cost_config, _evaluation_config, **kwargs):
        return {
            "catalog_no": catalog_no,
            "db_path": str(db_path),
            "snapshot_dir": str(snapshot_dir),
            "state_file": kwargs.get("state_file"),
            "profile_dir": kwargs.get("profile_dir"),
            "xianyu_profile_dir": kwargs.get("xianyu_profile_dir"),
            "status": "ok",
            "opportunity_count": 1,
            "opportunity_ids": [7],
        }

    monkeypatch.setattr(cli, "capture_and_evaluate_live_html", fake_scan)

    exit_code = cli.main(
        [
            "scan-live-html",
            "--catalog-no",
            "SRCL-3520",
            "--db",
            str(tmp_path / "live.db"),
            "--snapshot-dir",
            str(tmp_path / "snapshots"),
            "--state-file",
            "data/xianyu_state.json",
            "--profile-dir",
            str(tmp_path / "profile"),
            "--xianyu-profile-dir",
            str(tmp_path / "goofish-profile"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["catalog_no"] == "SRCL-3520"
    assert payload["opportunity_ids"] == [7]
    assert payload["state_file"] == "data/xianyu_state.json"
    assert payload["profile_dir"] == str(tmp_path / "profile")
    assert payload["xianyu_profile_dir"] == str(tmp_path / "goofish-profile")
