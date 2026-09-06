"""CLI tests for `wameiji-login-state-import`."""
from __future__ import annotations

import json
from pathlib import Path

from cd_monitor import cli


def _sample_snapshot() -> dict:
    return {
        "capturedAt": "2026-06-25T00:00:00Z",
        "pageUrl": "https://meruki.cn/",
        "cookies": [
            {"name": "session", "value": "SECRET", "domain": ".meruki.cn",
             "path": "/", "expires": 9999999999, "httpOnly": True, "secure": True, "sameSite": "Lax"}
        ],
        "storage": {"local": {"token": "SECRET-LOCAL"}, "session": {}},
        "meta": {"droppedStorageKeys": {"local": [], "session": []}},
    }


def test_cli_wameiji_login_state_import_writes_state_file_without_secret_leak(
    tmp_path: Path,
    capsys: object,
) -> None:
    snapshot = _sample_snapshot()
    input_path = tmp_path / "snapshot.json"
    output_path = tmp_path / "wameiji_state.json"
    input_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = cli.main([
        "wameiji-login-state-import",
        str(input_path),
        "--output", str(output_path),
        "--no-keep-raw",
    ])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["cookie_count"] == 1
    assert payload["login_state_ready"] is True
    assert payload["output"] == str(output_path)

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["format"] == "wameiji-storage-state-v1"
    assert "raw_extension_snapshot" not in saved
    assert saved["playwright_storage_state"]["cookies"][0]["value"] == "SECRET"
    # CLI stdout does not leak the raw secret value
    assert "SECRET" not in payload["output"]
    assert "SECRET-LOCAL" not in payload["output"]
    assert "SECRET" not in json.dumps(payload)


def test_cli_wameiji_login_state_import_rejects_missing_file(
    tmp_path: Path,
    capsys: object,
) -> None:
    missing = tmp_path / "absent.json"
    exit_code = cli.main(["wameiji-login-state-import", str(missing)])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert payload["error"] == "file_not_found"


def test_cli_wameiji_login_state_import_rejects_invalid_snapshot(
    tmp_path: Path,
    capsys: object,
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    exit_code = cli.main(["wameiji-login-state-import", str(bad)])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert payload["error"] == "invalid_state"
