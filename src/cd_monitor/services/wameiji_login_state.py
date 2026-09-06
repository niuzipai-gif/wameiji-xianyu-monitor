from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_OUTPUT = "data/wameiji_state.json"
STATE_FILE_FORMAT = "wameiji-storage-state-v1"
_AUTH_DOMAIN_TOKENS = ("meruki.cn",)
_AUTH_DOMAIN_KEYS = ("meruki.cn", "wameiji")


class WameijiLoginStateError(ValueError):
    """Raised when the Wameiji login state JSON cannot be validated or persisted."""


def _to_int_expires(value: Any) -> int:
    """Chrome extension cookies use seconds-since-epoch (float). Playwright wants int (-1 for session)."""
    if value is None or value == "":
        return -1
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return -1
    if seconds <= 0:
        return -1
    return int(seconds)


def _normalize_cookie(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not name:
        return None
    domain = str(raw.get("domain", "")).strip()
    if not domain:
        return None
    path = str(raw.get("path", "/")).strip() or "/"
    return {
        "name": name,
        "value": str(raw.get("value", "")),
        "domain": domain,
        "path": path,
        "expires": _to_int_expires(raw.get("expires")),
        "httpOnly": bool(raw.get("httpOnly", False)),
        "secure": bool(raw.get("secure", False)),
        "sameSite": str(raw.get("sameSite", "Lax")).capitalize() if raw.get("sameSite") else "Lax",
    }


def _origin_for_cookie(cookie: dict[str, Any], fallback_origin: str) -> str:
    domain = str(cookie.get("domain", "")).strip().lstrip(".")
    if not domain:
        return fallback_origin
    scheme = "https" if cookie.get("secure") else "https"
    return f"{scheme}://{domain}"


def _origin_from_page_url(page_url: str) -> str | None:
    if not page_url:
        return None
    try:
        parsed = urlparse(page_url)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def extension_snapshot_to_playwright_state(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Convert a Chrome-extension Wameiji snapshot into a Playwright storage_state.

    Input shape (extension):
        {
          "capturedAt": "ISO",
          "pageUrl": "https://meruki.cn/...",
          "page": {...},
          "env": {...},
          "storage": {"local": {...}, "session": {...}},
          "meta": {...},
          "headers": {...},
          "cookies": [{"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}, ...]
        }

    Output shape (Playwright):
        {
          "cookies": [...],
          "origins": [
            {"origin": "https://meruki.cn", "localStorage": [{"name", "value"}, ...]},
            ...
          ]
        }
    """
    if not isinstance(snapshot, dict):
        raise WameijiLoginStateError("snapshot must be a JSON object")
    cookies_raw = snapshot.get("cookies") or []
    if not isinstance(cookies_raw, list):
        raise WameijiLoginStateError("snapshot.cookies must be a list")
    cookies = [c for c in (_normalize_cookie(item) for item in cookies_raw) if c is not None]
    if not cookies:
        raise WameijiLoginStateError("snapshot must contain at least one valid cookie")

    storage = snapshot.get("storage") or {}
    if not isinstance(storage, dict):
        storage = {}
    local_items = (storage.get("local") or {}) if isinstance(storage.get("local"), dict) else {}
    session_items = (storage.get("session") or {}) if isinstance(storage.get("session"), dict) else {}

    fallback_origin = _origin_from_page_url(str(snapshot.get("pageUrl", ""))) or "https://meruki.cn"
    origin_set: dict[str, dict[str, list[dict[str, str]]]] = {}

    def _bucket(origin: str) -> None:
        origin_set.setdefault(origin, {"localStorage": [], "sessionStorage": []})

    _bucket(fallback_origin)
    for key, value in local_items.items():
        origin_set[fallback_origin]["localStorage"].append(
            {"name": str(key), "value": "" if value is None else str(value)}
        )
    for key, value in session_items.items():
        origin_set[fallback_origin]["sessionStorage"].append(
            {"name": str(key), "value": "" if value is None else str(value)}
        )

    # If we have cookies for additional subdomains, add origins for them too.
    seen_origins = {fallback_origin}
    for cookie in cookies:
        origin = _origin_for_cookie(cookie, fallback_origin)
        if origin not in seen_origins:
            _bucket(origin)
            seen_origins.add(origin)

    origins = []
    for origin, payloads in origin_set.items():
        entry: dict[str, Any] = {"origin": origin}
        if payloads["localStorage"]:
            entry["localStorage"] = payloads["localStorage"]
        if payloads["sessionStorage"]:
            entry["sessionStorage"] = payloads["sessionStorage"]
        origins.append(entry)

    return {"cookies": cookies, "origins": origins}


def parse_extension_snapshot(content: str | bytes) -> dict[str, Any]:
    """Parse + minimal-validate the extension JSON. Raises WameijiLoginStateError."""
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = str(content or "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WameijiLoginStateError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WameijiLoginStateError("snapshot must be a JSON object")
    cookies = data.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        raise WameijiLoginStateError("snapshot must include a non-empty cookies list")
    return data


def save_wameiji_login_state(
    content: str | bytes | dict[str, Any],
    output_path: str | Path = DEFAULT_OUTPUT,
    *,
    keep_raw_snapshot: bool = True,
) -> dict[str, Any]:
    """Persist a Wameiji login state JSON.

    Accepts either:
      - raw extension JSON text/bytes
      - already-parsed extension snapshot dict

    Returns a summary dict: output_path, cookie_count, origins, wameiji_cookies_domains.
    """
    if isinstance(content, dict):
        snapshot = content
    else:
        snapshot = parse_extension_snapshot(content)

    playwright_state = extension_snapshot_to_playwright_state(snapshot)
    wameiji_domains = sorted({
        str(cookie.get("domain", "")).strip()
        for cookie in playwright_state["cookies"]
        if _is_wameiji_auth_domain(str(cookie.get("domain", "")))
    })

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "format": STATE_FILE_FORMAT,
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
        "wameiji_cookie_domains": wameiji_domains,
        "login_state_ready": bool(playwright_state["cookies"]),
    }


def load_wameiji_login_state(path: str | Path) -> dict[str, Any]:
    """Load and return the Playwright storage_state portion of a saved wameiji_state.json.

    Raises WameijiLoginStateError if missing/malformed.
    """
    state_path = Path(path)
    if not state_path.exists() or not state_path.is_file():
        raise WameijiLoginStateError(f"wameiji state file does not exist: {state_path}")
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WameijiLoginStateError(f"wameiji state file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WameijiLoginStateError("wameiji state file must be a JSON object")
    state = data.get("playwright_storage_state")
    if not isinstance(state, dict):
        raise WameijiLoginStateError("wameiji state file missing playwright_storage_state")
    cookies = state.get("cookies")
    origins = state.get("origins", [])
    if not isinstance(cookies, list) or not cookies:
        raise WameijiLoginStateError("wameiji state file has empty cookies list")
    if not isinstance(origins, list):
        raise WameijiLoginStateError("wameiji state file has invalid origins list")
    return state


def inspect_wameiji_login_state(path: str | Path) -> dict[str, Any]:
    """Inspect a wameiji state file (mirrors xianyu _inspect_storage_state).

    Returns: {status, error_type?, error_message?, cookie_domains?}
      status ∈ {"ready", "missing", "invalid", "not_configured"}
    """
    if not path:
        return {"status": "not_configured", "cookie_domains": []}
    state_path = Path(path)
    if not state_path.exists() or not state_path.is_file():
        return {
            "status": "missing",
            "error_type": "state_file_missing",
            "error_message": "Configured Wameiji storage_state file does not exist.",
            "cookie_domains": [],
        }
    try:
        state = load_wameiji_login_state(state_path)
    except WameijiLoginStateError as exc:
        return {
            "status": "invalid",
            "error_type": "invalid_state_file",
            "error_message": str(exc),
            "cookie_domains": [],
        }
    domains = sorted({
        str(cookie.get("domain", "")).strip()
        for cookie in state.get("cookies", [])
        if isinstance(cookie, dict) and str(cookie.get("domain", "")).strip()
    })
    if not any(_is_wameiji_auth_domain(d) for d in domains):
        return {
            "status": "invalid",
            "error_type": "invalid_state_file",
            "error_message": "Wameiji state file does not contain meruki.cn auth domains.",
            "cookie_domains": domains,
            "cookies_total": 0, "expired": 0, "expiring_7d": 0,
            "oldest_expires_iso": None, "newest_expires_iso": None,
        }
    import time as _t
    cookies = state.get("cookies", [])
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


def _is_wameiji_auth_domain(domain: str) -> bool:
    lowered = domain.lower()
    return any(token in lowered for token in _AUTH_DOMAIN_TOKENS)


__all__ = [
    "DEFAULT_OUTPUT",
    "STATE_FILE_FORMAT",
    "WameijiLoginStateError",
    "extension_snapshot_to_playwright_state",
    "inspect_wameiji_login_state",
    "load_wameiji_login_state",
    "parse_extension_snapshot",
    "save_wameiji_login_state",
]
