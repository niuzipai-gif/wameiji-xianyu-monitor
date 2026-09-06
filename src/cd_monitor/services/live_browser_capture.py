from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cd_monitor.core.models import AdapterStatus, WatchItem
from cd_monitor.services.xianyu_login_state import _maybe_async_context, _resolve_playwright_factory
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter


async def capture_search_html(
    source: str,
    catalog_no: str,
    output_path: str | Path,
    *,
    state_file: str | Path | None = None,
    profile_dir: str | Path | None = None,
    xianyu_profile_dir: str | Path | None = None,
    wameiji_state_file: str | Path | None = None,
    screenshot_path: str | Path | None = None,
    network_log_path: str | Path | None = None,
    timeout_seconds: int = 30,
    headless: bool = False,
    playwright_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    watch = WatchItem(catalog_no=catalog_no)
    output = Path(output_path)
    adapter = _adapter_for_source(
        source,
        state_file,
        profile_dir=profile_dir,
        wameiji_state_file=wameiji_state_file,
        snapshot_dir=output.parent,
        headless=headless,
    )
    entry_status = adapter.search_status(watch)
    if not entry_status.search_entry_url:
        raise ValueError(f"No search entry URL for source: {source}")
    screenshot = Path(screenshot_path) if screenshot_path else None
    network_log = Path(network_log_path) if network_log_path else None
    network_entries: list[dict[str, Any]] = []
    network_tasks: list[asyncio.Task] = []
    # `profile_dir` remains the Wameiji-compatible generic flag. Xianyu gets
    # an explicit profile override so its logged-in browser data is never
    # confused with a Wameiji profile or a copied storage_state file.
    source_profile_dir = xianyu_profile_dir if source == "xianyu" and xianyu_profile_dir else profile_dir
    profile = Path(source_profile_dir) if source_profile_dir else None
    # The generic profile flag historically served both sources. For Xianyu,
    # prefer its source-specific state file when one is supplied, so a Wameiji
    # profile cannot accidentally replace the Xianyu login state. Wameiji
    # keeps profile_dir as the preferred path because it is a real browser
    # profile; its state file remains the fallback.
    use_persistent_profile = bool(
        profile
        and not (source == "xianyu" and state_file and not xianyu_profile_dir)
    )
    browser = None
    context = None
    playwright_context = _resolve_playwright_factory(playwright_factory)
    try:
        async with _maybe_async_context(playwright_context) as playwright:
            try:
                if use_persistent_profile:
                    profile.mkdir(parents=True, exist_ok=True)
                    context = await playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile),
                        headless=headless,
                    )
                else:
                    browser = await playwright.chromium.launch(headless=headless)
                    context_kwargs: dict[str, Any] = {}
                    if source == "xianyu" and state_file:
                        context_kwargs["storage_state"] = str(state_file)
                    elif source == "wameiji" and wameiji_state_file:
                        context_kwargs["storage_state"] = str(wameiji_state_file)
                    context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                if network_log is not None:
                    page.on(
                        "response",
                        lambda response: network_tasks.append(
                            asyncio.create_task(_capture_network_response(source, response, network_entries))
                        ),
                    )
                await page.goto(
                    entry_status.search_entry_url,
                    wait_until="domcontentloaded",
                    timeout=max(1, timeout_seconds) * 1000,
                )
                # Both sites render cards client-side. Wait for the visible
                # result surface when the page supports Playwright's selector
                # API; the small fallback keeps the fake/test page contract
                # and still captures challenge/login HTML when no card ever
                # appears. Xianyu can take ~20 seconds on a fresh profile.
                await _wait_for_result_surface(page, source, timeout_seconds)
                html = await _read_page_content(page)
                if screenshot is not None:
                    screenshot.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(screenshot), full_page=True)
                if network_tasks:
                    await asyncio.gather(*network_tasks)
            finally:
                if use_persistent_profile and context is not None:
                    await context.close()
                elif browser is not None:
                    await browser.close()
    except Exception as exc:
        # Capture any failure (including pre-launch subprocess PermissionError) as a structured
        # human_required result so callers can distinguish "real capture failed" from bugs.
        from cd_monitor.core.models import AdapterStatus
        # Build a minimal failed AdapterStatus and convert to capture summary
        status = AdapterStatus(
            status="not_configured",
            error_type="runner_error",
            error_message=f"{type(exc).__name__}: {exc}",
            search_entry_url=entry_status.search_entry_url if "entry_status" in dir() else None,
        )
        return _capture_summary(
            source, catalog_no, output, screenshot, network_log,
            len(network_entries), status.search_entry_url or "", status,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    if network_log is not None:
        network_log.parent.mkdir(parents=True, exist_ok=True)
        network_log.write_text(
            json.dumps(network_entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    parsed = adapter.parse_search_html(html, watch)
    return _capture_summary(
        source,
        catalog_no,
        output,
        screenshot,
        network_log,
        len(network_entries),
        entry_status.search_entry_url,
        parsed,
    )


async def _read_page_content(page: Any) -> str:
    """Read content after redirects settle, without hiding navigation errors."""
    last_error: Exception | None = None
    for _attempt in range(4):
        try:
            return await page.content()
        except Exception as exc:
            last_error = exc
            if "navigat" not in str(exc).lower():
                raise
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            await page.wait_for_timeout(500)
    assert last_error is not None
    raise last_error


async def _wait_for_result_surface(page: Any, source: str, timeout_seconds: int) -> None:
    selectors = {
        "wameiji": ".goods-item, [data-item-card], .goods-list",
        "xianyu": "[class^='feeds-item-wrap-'], [class*='feeds-item-wrap-'], [data-xianyu-card], .item-card",
    }
    selector = selectors.get(source)
    wait_for_selector = getattr(page, "wait_for_selector", None)
    if selector and callable(wait_for_selector):
        try:
            timeout_ms = min(max(1, timeout_seconds) * 1000, 20000)
            await wait_for_selector(selector, state="attached", timeout=timeout_ms)
            await page.wait_for_timeout(300)
            return
        except Exception:
            # Keep the resulting HTML as evidence when the site shows a
            # challenge, an empty result set, or a slow network response.
            pass
    await page.wait_for_timeout(5000 if source == "wameiji" else min(max(1, timeout_seconds) * 1000, 2000))


def _adapter_for_source(
    source: str,
    state_file: str | Path | None,
    *,
    profile_dir: str | Path | None = None,
    wameiji_state_file: str | Path | None = None,
    snapshot_dir: str | Path | None = None,
    headless: bool = False,
) -> Any:
    """根据 source + profile_dir + wameiji_state_file 选 adapter。

    - Wameiji：profile_dir 不为空时用 WameijiBrowserAdapterWithRunner 走 persistent profile
      否则若 wameiji_state_file 不为空则同一 adapter 走 Playwright storage_state 路径
      否则退回旧的安全骨架（仅返回 disabled / not_configured / parse_search_html）
    - Xianyu：维持原行为；xianyu_profile_dir 由调用方在 capture_search_html 中选择
    """
    if source == "wameiji":
        if profile_dir or wameiji_state_file:
            from cd_monitor.sources.wameiji_browser import (
                WameijiBrowserAdapterWithRunner,
            )
            return WameijiBrowserAdapterWithRunner(
                enabled=True,
                profile_dir=str(profile_dir) if profile_dir else None,
                state_file=str(wameiji_state_file) if wameiji_state_file else None,
                headless=headless,
                snapshot_dir=snapshot_dir,
            )
        return WameijiBrowserAdapter(enabled=True)
    if source == "xianyu":
        return XianyuBrowserAdapter(enabled=True, state_file=state_file)
    raise ValueError("source must be 'wameiji' or 'xianyu'")


def _capture_summary(
    source: str,
    catalog_no: str,
    output: Path,
    screenshot: Path | None,
    network_log: Path | None,
    network_response_count: int,
    search_entry_url: str,
    status: AdapterStatus,
) -> dict[str, Any]:
    return {
        "source": source,
        "catalog_no": catalog_no,
        "status": status.status,
        "error_type": status.error_type,
        "error_message": status.error_message,
        "item_count": len(status.items),
        "search_entry_url": search_entry_url,
        "snapshot_path": str(output),
        "screenshot_path": str(screenshot) if screenshot else None,
        "network_log_path": str(network_log) if network_log else None,
        "network_response_count": network_response_count,
    }


_NETWORK_BODY_LIMIT = 8000
_NETWORK_RESPONSE_LIMIT = 30
_SOURCE_DOMAINS = {
    "xianyu": ("goofish.com", "xianyu", "taobao.com", "alibaba.com"),
    "wameiji": ("meruki.cn", "wameiji", "wameiji.com"),
}


async def _capture_network_response(
    source: str,
    response: Any,
    entries: list[dict[str, Any]],
) -> None:
    if len(entries) >= _NETWORK_RESPONSE_LIMIT:
        return
    url = str(getattr(response, "url", ""))
    if not _is_source_response(source, url):
        return
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("content-type", ""))
    if not _is_text_response(content_type):
        return
    try:
        body = await response.text()
    except Exception:
        return
    entries.append(
        {
            "url": url,
            "status": int(getattr(response, "status", 0) or 0),
            "content_type": content_type,
            "body_truncated": len(body) > _NETWORK_BODY_LIMIT,
            "body": body[:_NETWORK_BODY_LIMIT],
        }
    )


def _is_source_response(source: str, url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in _SOURCE_DOMAINS.get(source, ()))


def _is_text_response(content_type: str) -> bool:
    lowered = content_type.lower()
    return any(token in lowered for token in ("json", "text", "javascript", "html"))
