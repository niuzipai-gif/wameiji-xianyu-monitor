import json

from cd_monitor import cli


def test_cli_capture_live_html_prints_capture_summary(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DUAL_MARKET_COLLECTION_PAUSED", "0")
    output = tmp_path / "xianyu.html"

    async def fake_capture(source, catalog_no, output_path, **kwargs):
        return {
            "source": source,
            "catalog_no": catalog_no,
            "status": "ok",
            "item_count": 3,
            "snapshot_path": str(output_path),
            "screenshot_path": kwargs.get("screenshot_path"),
            "network_log_path": kwargs.get("network_log_path"),
            "state_file": kwargs.get("state_file"),
            "profile_dir": kwargs.get("profile_dir"),
            "xianyu_profile_dir": kwargs.get("xianyu_profile_dir"),
        }

    monkeypatch.setattr(cli, "capture_search_html", fake_capture)

    exit_code = cli.main(
        [
            "capture-live-html",
            "--source",
            "xianyu",
            "--catalog-no",
            "SRCL-3520",
            "--output",
            str(output),
            "--screenshot-output",
            str(tmp_path / "xianyu.png"),
            "--network-output",
            str(tmp_path / "xianyu.network.json"),
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
    assert payload["source"] == "xianyu"
    assert payload["catalog_no"] == "SRCL-3520"
    assert payload["item_count"] == 3
    assert payload["snapshot_path"] == str(output)
    assert payload["screenshot_path"] == str(tmp_path / "xianyu.png")
    assert payload["network_log_path"] == str(tmp_path / "xianyu.network.json")
    assert payload["state_file"] == "data/xianyu_state.json"
    assert payload["profile_dir"] == str(tmp_path / "profile")
    assert payload["xianyu_profile_dir"] == str(tmp_path / "goofish-profile")
