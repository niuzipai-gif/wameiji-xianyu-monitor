import json

from cd_monitor import cli


def test_cli_xianyu_login_state_exports_state_summary_without_cookie_values(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "xianyu_state.json"

    async def fake_export(output_path, **kwargs):
        return {
            "output": str(output_path),
            "cookie_count": 2,
            "domains": [".goofish.com"],
            "login_state_ready": True,
            "secret": kwargs.get("secret_cookie_value", ""),
        }

    monkeypatch.setattr(cli, "export_xianyu_login_state", fake_export)

    exit_code = cli.main(
        [
            "xianyu-login-state",
            "--output",
            str(output),
            "--timeout-seconds",
            "5",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["output"] == str(output)
    assert payload["cookie_count"] == 2
    assert payload["domains"] == [".goofish.com"]
    assert "SECRET" not in json.dumps(payload)



# === xianyu-login-state-import (Phase S) =============================
import json as _json2
from pathlib import Path as _P2


def _sample_xianyu_snapshot() -> dict:
    return {
        "capturedAt": "2026-06-26T00:00:00Z",
        "pageUrl": "https://www.goofish.com/",
        "cookies": [
            {"name": "cookie2", "value": "SECRET", "domain": ".goofish.com",
             "path": "/", "expires": 9999999999, "httpOnly": True, "secure": True, "sameSite": "Lax"}
        ],
        "storage": {"local": {"_m_h5_tk": "SECRET-LOCAL"}, "session": {}},
    }


def test_cli_xianyu_login_state_import_writes_state_file_without_secret_leak(
    tmp_path: _P2, capsys: object,
) -> None:
    input_path = tmp_path / "snapshot.json"
    output_path = tmp_path / "xianyu_state.json"
    input_path.write_text(_json2.dumps(_sample_xianyu_snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = cli.main([
        "xianyu-login-state-import",
        str(input_path),
        "--output", str(output_path),
        "--no-keep-raw",
    ])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = _json2.loads(captured.out)

    assert exit_code == 0
    assert payload["cookie_count"] == 1
    assert payload["login_state_ready"] is True
    assert payload["output"] == str(output_path)

    saved = _json2.loads(output_path.read_text(encoding="utf-8"))
    assert saved["format"] == "xianyu-storage-state-v1"
    assert "raw_extension_snapshot" not in saved
    assert saved["playwright_storage_state"]["cookies"][0]["value"] == "SECRET"
    # CLI stdout does not leak raw secret values
    assert "SECRET" not in payload["output"]
    assert "SECRET-LOCAL" not in payload["output"]
    assert "SECRET" not in _json2.dumps(payload)


def test_cli_xianyu_login_state_import_rejects_missing_file(
    tmp_path: _P2, capsys: object,
) -> None:
    missing = tmp_path / "absent.json"
    exit_code = cli.main(["xianyu-login-state-import", str(missing)])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = _json2.loads(captured.out)
    assert exit_code == 1
    assert payload["error"] == "file_not_found"


def test_cli_xianyu_login_state_import_rejects_invalid_snapshot(
    tmp_path: _P2, capsys: object,
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(_json2.dumps({"cookies": []}), encoding="utf-8")
    exit_code = cli.main(["xianyu-login-state-import", str(bad)])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = _json2.loads(captured.out)
    assert exit_code == 1
    assert payload["error"] == "invalid_state"
