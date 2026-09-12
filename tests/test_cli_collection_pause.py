"""Regression coverage for the CLI dual-market collection pause."""
from __future__ import annotations

import json

import pytest

from cd_monitor import cli
from cd_monitor.core.models import WatchItem


@pytest.mark.parametrize("pause_value", [None, "true"])
@pytest.mark.parametrize(
    ("command", "capture_name"),
    [
        ("capture-live-html", "capture_search_html"),
        ("scan-live-html", "capture_and_evaluate_live_html"),
        ("live-watchlist", "capture_and_evaluate_live_html"),
    ],
)
def test_cli_live_collection_commands_fail_closed_before_capture(
    tmp_path, monkeypatch, capsys, command: str, capture_name: str, pause_value: str | None
) -> None:
    if pause_value is None:
        monkeypatch.delenv("DUAL_MARKET_COLLECTION_PAUSED", raising=False)
    else:
        monkeypatch.setenv("DUAL_MARKET_COLLECTION_PAUSED", pause_value)

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def forbidden_capture(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("paused CLI command attempted browser capture")

    monkeypatch.setattr(cli, capture_name, forbidden_capture)
    db_path = tmp_path / "monitor.db"
    argv = ["--db", str(db_path), command]
    if command == "capture-live-html":
        argv.extend(
            [
                "--source",
                "wameiji",
                "--catalog-no",
                "SRCL-3520",
                "--output",
                str(tmp_path / "wameiji.html"),
            ]
        )
    elif command == "scan-live-html":
        argv.extend(["--catalog-no", "SRCL-3520"])
    else:
        monkeypatch.setattr(
            cli, "list_watch", lambda _db_path: [WatchItem(catalog_no="SRCL-3520")]
        )

    exit_code = cli.main(argv)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert payload["error"] == "collector_paused"
    assert calls == []
