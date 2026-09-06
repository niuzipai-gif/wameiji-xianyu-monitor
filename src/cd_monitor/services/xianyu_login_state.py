from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Callable


DEFAULT_LOGIN_URL = "https://www.goofish.com"
_AUTH_DOMAINS = ("goofish.com", "xianyu", "taobao.com", "alibaba.com", "tmall.com")
# `tfstk` and similar cookies are issued to anonymous visitors as well. Wait
# for a login-session cookie so the interactive exporter cannot report success
# immediately on an unauthenticated Goofish page.
_AUTH_COOKIE_NAMES = {
    "cookie2", "_m_h5_tk", "_m_h5_tk_enc", "unb", "lgc", "tracknick",
    "cookie17", "skt", "sgcookie", "isg", "uc1", "uc3", "uc4",
}


class LoginStateExportError(RuntimeError):
    pass


async def export_xianyu_login_state(
    output_path: str | Path,
    *,
    login_url: str = DEFAULT_LOGIN_URL,
    timeout_seconds: int = 180,
    poll_interval_seconds: float = 1.0,
    headless: bool = False,
    playwright_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Open a user-controlled browser login page and export Playwright storage_state."""

    output = Path(output_path)
    browser = None
    playwright_context = _resolve_playwright_factory(playwright_factory)
    async with _maybe_async_context(playwright_context) as playwright:
        try:
            browser = await playwright.chromium.launch(headless=headless)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(login_url, wait_until="domcontentloaded")
            cookies = await _wait_for_auth_cookies(
                context,
                page,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            state = await context.storage_state()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            domains = _cookie_domains(cookies)
            return {
                "output": str(output),
                "cookie_count": len(cookies),
                "domains": domains,
                "login_state_ready": True,
            }
        finally:
            if browser is not None:
                await browser.close()


def _resolve_playwright_factory(playwright_factory: Callable[[], Any] | None) -> Any:
    if playwright_factory is not None:
        return playwright_factory()
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise LoginStateExportError(
            "Playwright is not installed. Install it with `python -m pip install playwright` "
            "and then run `python -m playwright install chromium`."
        ) from exc
    return async_playwright()


class _maybe_async_context:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        if hasattr(self.value, "__aenter__"):
            return await self.value.__aenter__()
        return self.value

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if hasattr(self.value, "__aexit__"):
            await self.value.__aexit__(exc_type, exc, tb)


async def _wait_for_auth_cookies(
    context: Any,
    page: Any,
    *,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        cookies = await context.cookies()
        if _has_auth_domain(cookies):
            return cookies
        if time.monotonic() >= deadline:
            raise LoginStateExportError(
                "No Goofish/Xianyu login cookies detected before timeout. "
                "Scan the QR code or complete login in the opened browser, then rerun."
            )
        await page.wait_for_timeout(int(max(0, poll_interval_seconds) * 1000))


def _has_auth_domain(
    cookies: list[dict[str, Any]],
    *,
    allow_generic_session: bool = False,
) -> bool:
    if any(_is_authenticated_cookie(cookie) for cookie in cookies):
        return True
    # Extension-imported states historically accepted any non-empty cookie on
    # a Goofish domain. Keep that compatibility only for the explicit wrapper
    # path; the interactive exporter remains strict so an anonymous tfstk
    # cookie cannot be mistaken for a logged-in session.
    return allow_generic_session and any(
        _is_auth_domain(str(cookie.get("domain", "")))
        and str(cookie.get("name", "")).strip()
        and str(cookie.get("value", "")).strip()
        for cookie in cookies
        if isinstance(cookie, dict)
    )


def _is_authenticated_cookie(cookie: dict[str, Any]) -> bool:
    if not isinstance(cookie, dict) or not _is_auth_domain(str(cookie.get("domain", ""))):
        return False
    name = str(cookie.get("name", "")).strip().lower()
    return name in _AUTH_COOKIE_NAMES


def _cookie_domains(cookies: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(cookie.get("domain", "")).strip()
            for cookie in cookies
            if _is_auth_domain(str(cookie.get("domain", "")))
        }
    )


def _is_auth_domain(domain: str) -> bool:
    lowered = domain.lower()
    return any(token in lowered for token in _AUTH_DOMAINS)


# === Extension JSON import path =====================================
# Mirrors wameiji_login_state.save_wameiji_login_state but targets the
# Xianyu/Goofish auth domains. The Chrome extension format is the same;
# only the auth-domain whitelist differs.

DEFAULT_OUTPUT_XIANYU = "data/xianyu_state.json"
STATE_FILE_FORMAT_XIANYU = "xianyu-storage-state-v1"
_XIANYU_IMPORT_AUTH_TOKENS = ("goofish.com", "xianyu", "taobao.com", "alibaba.com", "tmall.com")


class XianyuLoginStateImportError(ValueError):
    """Raised when the Xianyu extension snapshot cannot be validated or persisted."""


def save_xianyu_login_state(
    content,
    output_path="data/xianyu_state.json",
    *,
    keep_raw_snapshot=True,
):
    """Persist a Xianyu login state JSON captured by the Xianyu Chrome extension.

    Accepts either:
      - raw extension JSON text/bytes
      - already-parsed extension snapshot dict

    Returns a summary dict: output_path, cookie_count, origins, xianyu_cookie_domains.
    """
    from cd_monitor.services.wameiji_login_state import (
        extension_snapshot_to_playwright_state,
        parse_extension_snapshot,
    )

    from cd_monitor.services.wameiji_login_state import WameijiLoginStateError
    if isinstance(content, dict):
        snapshot = content
    else:
        try:
            snapshot = parse_extension_snapshot(content)
        except WameijiLoginStateError as exc:
            raise XianyuLoginStateImportError(str(exc)) from exc

    try:
        playwright_state = extension_snapshot_to_playwright_state(snapshot)
    except WameijiLoginStateError as exc:
        raise XianyuLoginStateImportError(str(exc)) from exc
    xianyu_domains = sorted({
        str(cookie.get("domain", "")).strip()
        for cookie in playwright_state["cookies"]
        if _is_xianyu_auth_domain(str(cookie.get("domain", "")))
    })

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "format": STATE_FILE_FORMAT_XIANYU,
        "captured_at": str(snapshot.get("capturedAt") or ""),
        "page_url": str(snapshot.get("pageUrl") or ""),
        "playwright_storage_state": playwright_state,
    }
    if keep_raw_snapshot:
        payload["raw_extension_snapshot"] = snapshot

    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "output": str(output),
        "cookie_count": len(playwright_state["cookies"]),
        "origin_count": len(playwright_state["origins"]),
        "xianyu_cookie_domains": xianyu_domains,
        "login_state_ready": _has_auth_domain(
            playwright_state["cookies"], allow_generic_session=True
        ),
    }


def load_xianyu_login_state(path):
    """Load and return the Playwright storage_state portion of a saved xianyu_state.json."""
    from cd_monitor.services.wameiji_login_state import WameijiLoginStateError
    state_path = Path(path)
    if not state_path.exists() or not state_path.is_file():
        raise XianyuLoginStateImportError(
            f"xianyu state file does not exist: {state_path}"
        )
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise XianyuLoginStateImportError(
            f"xianyu state file is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise XianyuLoginStateImportError("xianyu state file must be a JSON object")
    # `xianyu-login-state` writes Playwright's native storage_state directly,
    # while the extension-import path writes a small wrapper around it. Accept
    # both forms so a freshly exported login state can be used immediately.
    state = data.get("playwright_storage_state")
    if state is None and isinstance(data.get("cookies"), list) and data.get("cookies"):
        state = data
    if not isinstance(state, dict):
        raise XianyuLoginStateImportError("xianyu state file missing playwright_storage_state")
    cookies = state.get("cookies")
    origins = state.get("origins", [])
    if not isinstance(cookies, list) or not cookies:
        raise XianyuLoginStateImportError("xianyu state file has empty cookies list")
    if not isinstance(origins, list):
        raise XianyuLoginStateImportError("xianyu state file has invalid origins list")
    return state


def inspect_xianyu_login_state(path):
    """Inspect a xianyu state file.

    Returns: {status, error_type?, error_message?, cookie_domains?}
      status in {"ready", "missing", "invalid", "not_configured"}
    """
    from cd_monitor.services.wameiji_login_state import WameijiLoginStateError
    if not path:
        return {"status": "not_configured", "cookie_domains": []}
    state_path = Path(path)
    if not state_path.exists() or not state_path.is_file():
        return {
            "status": "missing",
            "error_type": "state_file_missing",
            "error_message": "Configured Xianyu storage_state file does not exist.",
            "cookie_domains": [],
        }
    try:
        state = load_xianyu_login_state(state_path)
    except XianyuLoginStateImportError as exc:
        return {
            "status": "invalid",
            "error_type": "invalid_state_file",
            "error_message": str(exc),
            "cookie_domains": [],
        }
    try:
        raw_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw_data = {}
    domains = sorted({
        str(cookie.get("domain", "")).strip()
        for cookie in state.get("cookies", [])
        if isinstance(cookie, dict) and str(cookie.get("domain", "")).strip()
    })
    cookies = state.get("cookies", [])
    is_extension_wrapper = isinstance(raw_data, dict) and isinstance(
        raw_data.get("playwright_storage_state"), dict
    )
    if not _has_auth_domain(cookies, allow_generic_session=is_extension_wrapper):
        return {
            "status": "invalid",
            "error_type": "invalid_state_file",
            "error_message": "Xianyu state file has no recognized authenticated session cookie.",
            "cookie_domains": domains,
            "cookies_total": 0, "expired": 0, "expiring_7d": 0,
            "oldest_expires_iso": None, "newest_expires_iso": None,
        }
    # Cookie expiry summary
    import time as _t
    now = _t.time()
    expires = [float(c.get("expires")) for c in cookies
               if isinstance(c, dict) and isinstance(c.get("expires"), (int, float)) and c.get("expires") > 0]
    return {
        "status": "ready",
        "cookie_domains": domains,
        "cookies_total": len(cookies),
        "expired": sum(1 for e in expires if e < now),
        "expiring_7d": sum(1 for e in expires if now <= e < now + 7 * 86400),
        "oldest_expires_iso": _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime(min(expires))) if expires else None,
        "newest_expires_iso": _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime(max(expires))) if expires else None,
    }


def _is_xianyu_auth_domain(domain):
    lowered = domain.lower()
    return any(token in lowered for token in _XIANYU_IMPORT_AUTH_TOKENS)


__all__ = [
    "DEFAULT_LOGIN_URL",
    "DEFAULT_OUTPUT_XIANYU",
    "LoginStateExportError",
    "STATE_FILE_FORMAT_XIANYU",
    "XianyuLoginStateImportError",
    "export_xianyu_login_state",
    "inspect_xianyu_login_state",
    "load_xianyu_login_state",
    "save_xianyu_login_state",
]
