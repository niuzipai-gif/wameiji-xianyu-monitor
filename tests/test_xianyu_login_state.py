import asyncio
import json

import pytest

from cd_monitor.services.xianyu_login_state import LoginStateExportError, export_xianyu_login_state


def test_export_xianyu_login_state_writes_playwright_storage_state_without_returning_values(tmp_path) -> None:
    output = tmp_path / "xianyu_state.json"
    fake_playwright = _FakePlaywright(
        cookies=[
            {
                "name": "tracknick",
                "value": "SECRET-COOKIE",
                "domain": ".goofish.com",
                "path": "/",
            }
        ]
    )

    result = asyncio.run(
        export_xianyu_login_state(
            output,
            timeout_seconds=1,
            poll_interval_seconds=0,
            playwright_factory=lambda: fake_playwright,
        )
    )

    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["cookies"][0]["value"] == "SECRET-COOKIE"
    assert result == {
        "output": str(output),
        "cookie_count": 1,
        "domains": [".goofish.com"],
        "login_state_ready": True,
    }
    assert "SECRET-COOKIE" not in json.dumps(result)
    assert fake_playwright.chromium.browser.closed is True


def test_export_xianyu_login_state_times_out_without_auth_cookie(tmp_path) -> None:
    output = tmp_path / "xianyu_state.json"
    fake_playwright = _FakePlaywright(cookies=[])

    with pytest.raises(LoginStateExportError, match="No Goofish/Xianyu login cookies"):
        asyncio.run(
            export_xianyu_login_state(
                output,
                timeout_seconds=0,
                poll_interval_seconds=0,
                playwright_factory=lambda: fake_playwright,
            )
        )

    assert not output.exists()
    assert fake_playwright.chromium.browser.closed is True


def test_export_xianyu_login_state_rejects_anonymous_cookie_bundle(tmp_path) -> None:
    """Anonymous Goofish pages set these cookies before a user signs in."""
    output = tmp_path / "xianyu_state.json"
    fake_playwright = _FakePlaywright(
        cookies=[
            {"name": "cookie2", "value": "anonymous", "domain": ".goofish.com"},
            {"name": "_m_h5_tk", "value": "anonymous", "domain": ".goofish.com"},
            {"name": "_m_h5_tk_enc", "value": "anonymous", "domain": ".goofish.com"},
            {"name": "tfstk", "value": "anonymous", "domain": ".goofish.com"},
        ]
    )

    with pytest.raises(LoginStateExportError, match="No Goofish/Xianyu login cookies"):
        asyncio.run(
            export_xianyu_login_state(
                output,
                timeout_seconds=0,
                poll_interval_seconds=0,
                playwright_factory=lambda: fake_playwright,
            )
        )

    assert not output.exists()
    assert fake_playwright.chromium.browser.closed is True


class _FakePlaywright:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self.chromium = _FakeChromium(cookies)


class _FakeChromium:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self.browser = _FakeBrowser(cookies)

    async def launch(self, **_kwargs):
        return self.browser


class _FakeBrowser:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self.context = _FakeContext(cookies)
        self.closed = False

    async def new_context(self):
        return self.context

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, cookies: list[dict[str, object]]) -> None:
        self.cookies_payload = cookies
        self.page = _FakePage()

    async def new_page(self):
        return self.page

    async def cookies(self):
        return self.cookies_payload

    async def storage_state(self):
        return {"cookies": self.cookies_payload, "origins": []}


class _FakePage:
    async def goto(self, _url: str, **_kwargs) -> None:
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None



# === Extension JSON import path (Phase S) ============================
import json as _json
from cd_monitor.services.xianyu_login_state import (
    DEFAULT_OUTPUT_XIANYU,
    STATE_FILE_FORMAT_XIANYU,
    XianyuLoginStateImportError,
    inspect_xianyu_login_state,
    load_xianyu_login_state,
    save_xianyu_login_state,
)


def _sample_xianyu_snapshot() -> dict:
    return {
        "capturedAt": "2026-06-26T00:00:00Z",
        "pageUrl": "https://www.goofish.com/personal",
        "page": {"pageUrl": "https://www.goofish.com/personal", "referrer": None, "visibilityState": "visible"},
        "env": {"navigator": {"userAgent": "Mozilla/5.0"}},
        "storage": {
            "local": {"_m_h5_tk": "SECRET-LOCAL"},
            "session": {"csrf": "X"},
        },
        "meta": {"droppedStorageKeys": {"local": [], "session": []}},
        "headers": {"user-agent": "Mozilla/5.0"},
        "cookies": [
            {
                "name": "tracknick",
                "value": "SECRET-COOKIE",
                "domain": ".goofish.com",
                "path": "/",
                "expires": 9999999999,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "tracking",
                "value": "ABC",
                "domain": ".goofish.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": False,
                "sameSite": "None",
            },
        ],
    }


def test_save_xianyu_login_state_writes_playwright_storage(tmp_path) -> None:
    snapshot = _sample_xianyu_snapshot()
    output = tmp_path / "xianyu_state.json"
    summary = save_xianyu_login_state(snapshot, output, keep_raw_snapshot=False)
    assert summary["output"] == str(output)
    assert summary["cookie_count"] == 2
    assert summary["origin_count"] >= 1
    assert summary["login_state_ready"] is True
    assert ".goofish.com" in summary["xianyu_cookie_domains"]
    data = _json.loads(output.read_text(encoding="utf-8"))
    assert data["format"] == STATE_FILE_FORMAT_XIANYU
    assert "raw_extension_snapshot" not in data
    assert data["playwright_storage_state"]["cookies"][0]["value"] == "SECRET-COOKIE"


def test_save_xianyu_login_state_accepts_json_text(tmp_path) -> None:
    snapshot = _sample_xianyu_snapshot()
    output = tmp_path / "xianyu_state.json"
    save_xianyu_login_state(_json.dumps(snapshot), output)
    assert output.exists()


def test_load_xianyu_login_state_roundtrip(tmp_path) -> None:
    snapshot = _sample_xianyu_snapshot()
    output = tmp_path / "xianyu_state.json"
    save_xianyu_login_state(snapshot, output)
    state = load_xianyu_login_state(output)
    assert state["cookies"][0]["name"] == "tracknick"
    assert state["cookies"][0]["value"] == "SECRET-COOKIE"
    assert any(o["origin"] == "https://www.goofish.com" for o in state["origins"])


def test_load_xianyu_login_state_missing_file(tmp_path) -> None:
    with pytest.raises(XianyuLoginStateImportError, match="does not exist"):
        load_xianyu_login_state(tmp_path / "absent.json")


def test_load_xianyu_login_state_invalid_json(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid", encoding="utf-8")
    with pytest.raises(XianyuLoginStateImportError, match="not valid JSON"):
        load_xianyu_login_state(bad)


def test_load_xianyu_login_state_missing_storage_state(tmp_path) -> None:
    p = tmp_path / "wrong.json"
    p.write_text(_json.dumps({"format": "x", "cookies": []}), encoding="utf-8")
    with pytest.raises(XianyuLoginStateImportError, match="playwright_storage_state"):
        load_xianyu_login_state(p)


def test_inspect_xianyu_login_state_ready(tmp_path) -> None:
    output = tmp_path / "xianyu_state.json"
    save_xianyu_login_state(_sample_xianyu_snapshot(), output)
    insp = inspect_xianyu_login_state(output)
    assert insp["status"] == "ready"
    assert ".goofish.com" in insp["cookie_domains"]


def test_inspect_xianyu_login_state_missing(tmp_path) -> None:
    insp = inspect_xianyu_login_state(tmp_path / "absent.json")
    assert insp["status"] == "missing"
    assert insp["error_type"] == "state_file_missing"


def test_inspect_xianyu_login_state_invalid_domain(tmp_path) -> None:
    p = tmp_path / "wrong_domain.json"
    bad = {"format": STATE_FILE_FORMAT_XIANYU, "playwright_storage_state": {
        "cookies": [{"name": "x", "value": "y", "domain": ".example.com", "path": "/",
                     "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}],
        "origins": [],
    }}
    p.write_text(_json.dumps(bad), encoding="utf-8")
    insp = inspect_xianyu_login_state(p)
    assert insp["status"] == "invalid"
    assert "goofish" in insp["error_message"].lower() or "xianyu" in insp["error_message"].lower()


def test_inspect_xianyu_login_state_rejects_anonymous_cookie_bundle(tmp_path) -> None:
    p = tmp_path / "anonymous.json"
    p.write_text(_json.dumps({"cookies": [
        {"name": "cookie2", "value": "anonymous", "domain": ".goofish.com"},
        {"name": "_m_h5_tk", "value": "anonymous", "domain": ".goofish.com"},
        {"name": "_m_h5_tk_enc", "value": "anonymous", "domain": ".goofish.com"},
    ], "origins": []}), encoding="utf-8")

    insp = inspect_xianyu_login_state(p)

    assert insp["status"] == "invalid"
    assert insp["error_type"] == "invalid_state_file"


def test_inspect_xianyu_login_state_not_configured() -> None:
    insp = inspect_xianyu_login_state("")
    assert insp["status"] == "not_configured"


def test_save_xianyu_login_state_rejects_empty_cookies(tmp_path) -> None:
    with pytest.raises(XianyuLoginStateImportError):
        save_xianyu_login_state({"cookies": []}, tmp_path / "out.json")


def test_default_output_xianyu_constant() -> None:
    assert DEFAULT_OUTPUT_XIANYU == "data/xianyu_state.json"
