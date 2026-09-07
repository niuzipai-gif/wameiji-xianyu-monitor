"""Local executor for the autonomous candidate-pool scanner."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from cd_monitor.config import ProjectConfig
from cd_monitor.core.models import MarketItem, WatchItem, XianyuPriceSample
from cd_monitor.services.discovery import (
    FetchWameiji,
    FetchWameijiDetail,
    FetchXianyu,
    ResolveTitleAliases,
    scan_discovery_keyword,
)
from cd_monitor.services.live_browser_capture import capture_page_html, capture_search_html
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter
from cd_monitor.storage.sqlite import (
    complete_collector_command,
    list_discovery_keywords,
    list_discovery_pools,
    list_due_discovery_keywords,
    mark_discovery_keyword_scanned,
    replace_discovery_keywords,
    update_discovery_pool,
    upsert_remote_collector_command,
)

LOGGER = logging.getLogger(__name__)


class CollectorCommandClient(Protocol):
    def fetch_pending(self) -> list[dict[str, Any]]: ...

    def complete(
        self, command_id: int, *, status: str, result: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class WorkerRunResult:
    scan_count: int
    command_count: int
    human_required_count: int = 0
    detail_query_count: int = 0
    xianyu_query_count: int = 0


class DiscoveryWorker:
    """Runs due pool keywords and consumes remote dashboard commands.

    Browser operations are injected so this coordinator remains deterministic
    under tests and only the local collector process supplies real profiles.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        fetch_wameiji: FetchWameiji,
        fetch_wameiji_detail: FetchWameijiDetail,
        fetch_xianyu: FetchXianyu,
        resolve_title_aliases: ResolveTitleAliases | None = None,
        command_client: CollectorCommandClient | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.fetch_wameiji = fetch_wameiji
        self.fetch_xianyu = fetch_xianyu
        self.fetch_wameiji_detail = fetch_wameiji_detail
        self.resolve_title_aliases = resolve_title_aliases
        self.command_client = command_client

    async def run_once(self) -> WorkerRunResult:
        commands = self._fetch_commands()
        forced_pool_ids, force_all = self._apply_non_scan_commands(commands)
        scan_commands = [
            command for command in commands if command.get("command_type") == "scan_now"
        ]
        for command in scan_commands:
            pool_id = _command_pool_id(command)
            if pool_id is None:
                force_all = True
            else:
                forced_pool_ids.add(pool_id)

        if scan_commands:
            pools = [
                pool
                for pool in list_discovery_pools(self.db_path)
                if pool.enabled and (force_all or pool.id in forced_pool_ids)
            ]
        else:
            pools = [pool for pool in list_discovery_pools(self.db_path) if pool.enabled]

        scan_results = []
        for pool in pools:
            assert pool.id is not None
            keywords = (
                list_discovery_keywords(self.db_path, pool.id)[: pool.keyword_budget]
                if scan_commands
                else list_due_discovery_keywords(self.db_path, pool.id)
            )
            for keyword in keywords:
                if not keyword.enabled:
                    continue
                result = await scan_discovery_keyword(
                    db_path=self.db_path,
                    pool_id=pool.id,
                    keyword=keyword.keyword,
                    fetch_wameiji=self.fetch_wameiji,
                    fetch_wameiji_detail=self.fetch_wameiji_detail,
                    fetch_xianyu=self.fetch_xianyu,
                    resolve_title_aliases=self.resolve_title_aliases,
                )
                scan_results.append(result)
                if result.status == "ok" and keyword.id is not None:
                    mark_discovery_keyword_scanned(self.db_path, keyword.id)

        self._complete_commands(commands, scan_results)
        return WorkerRunResult(
            scan_count=len(scan_results),
            command_count=len(commands),
            human_required_count=sum(
                1 for result in scan_results if result.status == "human_required"
            ),
            detail_query_count=sum(result.detail_query_count for result in scan_results),
            xianyu_query_count=sum(result.xianyu_query_count for result in scan_results),
        )

    def _fetch_commands(self) -> list[dict[str, Any]]:
        if self.command_client is None:
            return []
        try:
            remote_commands = self.command_client.fetch_pending()
        except Exception as exc:  # noqa: BLE001 - remote availability must not stop local scanning
            # The local schedule remains useful while Render is unavailable.
            LOGGER.warning("Could not fetch remote collector commands: %s", exc)
            return []
        commands: list[dict[str, Any]] = []
        for remote_command in remote_commands:
            try:
                mirrored = upsert_remote_collector_command(self.db_path, remote_command)
                command = dict(remote_command)
                command["_local_command_id"] = int(mirrored["id"])
                commands.append(command)
            except (KeyError, TypeError, ValueError):
                continue
        return commands

    def _apply_non_scan_commands(
        self, commands: list[dict[str, Any]]
    ) -> tuple[set[int], bool]:
        forced_pool_ids: set[int] = set()
        force_all = False
        for command in commands:
            command_type = command.get("command_type")
            payload = command.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            try:
                if command_type == "set_pool":
                    pool_id = _command_pool_id(command)
                    if pool_id is None:
                        raise ValueError("pool_id_required")
                    updates = payload.get("updates")
                    if not isinstance(updates, dict):
                        raise ValueError("updates_required")
                    update_discovery_pool(self.db_path, pool_id, updates)
                elif command_type == "set_keywords":
                    pool_id = _command_pool_id(command)
                    keywords = payload.get("keywords")
                    if pool_id is None or not isinstance(keywords, list):
                        raise ValueError("pool_id_and_keywords_required")
                    replace_discovery_keywords(
                        self.db_path,
                        pool_id,
                        [item for item in keywords if isinstance(item, dict)],
                    )
            except (KeyError, TypeError, ValueError) as exc:
                # Completion records the exact command as failed below; a bad
                # remote edit must not stop scans for the remaining pools.
                command["_worker_error"] = str(exc) or type(exc).__name__
                continue
        return forced_pool_ids, force_all

    def _complete_commands(self, commands: list[dict[str, Any]], scan_results: list[Any]) -> None:
        if self.command_client is None:
            return
        runs = len(scan_results)
        has_human_required = any(result.status == "human_required" for result in scan_results)
        default_status = "human_required" if has_human_required else "completed"
        default_result = {"runs": runs, "status": "human_required" if has_human_required else "ok"}
        for command in commands:
            command_id = command.get("id")
            if not isinstance(command_id, int):
                continue
            error = command.get("_worker_error")
            status = "failed" if isinstance(error, str) else default_status
            result = {"error": error} if isinstance(error, str) else default_result
            try:
                self.command_client.complete(command_id, status=status, result=result)
            except Exception as exc:  # noqa: BLE001 - leave local command pending for the next retry
                LOGGER.warning("Could not complete remote collector command %s: %s", command_id, exc)
            else:
                local_command_id = command.get("_local_command_id")
                if isinstance(local_command_id, int):
                    try:
                        complete_collector_command(
                            self.db_path, local_command_id, status=status, result=result
                        )
                    except KeyError:
                        LOGGER.warning("Mirrored collector command %s disappeared", local_command_id)


class RenderCollectorCommandClient:
    """Small stdlib client for the authenticated Render collector endpoints."""

    def __init__(self, base_url: str, sync_token: str, timeout_seconds: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.sync_token = sync_token
        self.timeout_seconds = timeout_seconds

    def fetch_pending(self) -> list[dict[str, Any]]:
        request = Request(
            f"{self.base_url}/api/discovery/collector/commands",
            headers={"X-CD-Sync-Token": self.sync_token},
            method="GET",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def complete(
        self, command_id: int, *, status: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        body = json.dumps({"status": status, "result": result}, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/discovery/collector/commands/{command_id}/complete",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-CD-Sync-Token": self.sync_token,
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}


class BrowserCaptureBlocked(RuntimeError):
    pass


def _command_pool_id(command: dict[str, Any]) -> int | None:
    payload = command.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("pool_id")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_browser_fetchers(
    config: ProjectConfig,
    snapshot_root: str | Path,
) -> tuple[FetchWameiji, FetchWameijiDetail, FetchXianyu]:
    """Bind a worker to the local, logged-in visible browser profiles."""
    root = Path(snapshot_root)

    async def fetch_wameiji(query: str) -> list[MarketItem]:
        result = await _capture_and_parse(
            "wameiji", query, config, root / "wameiji"
        )
        return [item for item in result if isinstance(item, MarketItem)]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem | None:
        return await _capture_and_parse_wameiji_detail(item, config, root / "wameiji-detail")

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        result = await _capture_and_parse("xianyu", query, config, root / "xianyu")
        return [item for item in result if isinstance(item, XianyuPriceSample)]

    return fetch_wameiji, fetch_wameiji_detail, fetch_xianyu


async def _capture_and_parse(
    source: str,
    query: str,
    config: ProjectConfig,
    output_root: Path,
) -> list[MarketItem] | list[XianyuPriceSample]:
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", query)[:80] or "query"
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output = output_root / f"{timestamp}-{safe}.html"
    screenshot = output.with_suffix(".png")
    network = output.with_suffix(".network.json")
    browser = config.browser
    result = await capture_search_html(
        source,
        query,
        output,
        state_file=browser.xianyu_state_file if source == "xianyu" else None,
        profile_dir=browser.wameiji_profile_dir if source == "wameiji" else None,
        xianyu_profile_dir=browser.xianyu_profile_dir if source == "xianyu" else None,
        wameiji_state_file=browser.wameiji_state_file if source == "wameiji" else None,
        screenshot_path=screenshot,
        network_log_path=network,
        timeout_seconds=max(1, browser.wameiji_result_timeout_ms // 1000),
        headless=browser.wameiji_headless,
    )
    if result.get("status") != "ok":
        raise BrowserCaptureBlocked(
            f"{source}:{result.get('error_type') or result.get('status')}"
        )
    html = output.read_text(encoding="utf-8")
    watch = WatchItem(catalog_no=query)
    adapter = (
        WameijiBrowserAdapter(enabled=True)
        if source == "wameiji"
        else XianyuBrowserAdapter(enabled=True, state_file=browser.xianyu_state_file)
    )
    parsed = adapter.parse_search_html(html, watch)
    if parsed.status != "ok":
        raise BrowserCaptureBlocked(f"{source}:{parsed.error_type or parsed.status}")
    return list(parsed.items)


async def _capture_and_parse_wameiji_detail(
    search_item: MarketItem,
    config: ProjectConfig,
    output_root: Path,
) -> MarketItem | None:
    """Open a Wameiji listing URL and return only a detail-verified item."""
    if not search_item.url:
        raise BrowserCaptureBlocked("wameiji_detail:missing_listing_url")
    target_url = urljoin("https://meruki.cn/", search_item.url)
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", str(search_item.external_item_id or "detail"))[:80]
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output = output_root / f"{timestamp}-{safe or 'detail'}.html"
    screenshot = output.with_suffix(".png")
    network = output.with_suffix(".network.json")
    browser = config.browser
    result = await capture_page_html(
        "wameiji",
        target_url,
        output,
        profile_dir=browser.wameiji_profile_dir,
        wameiji_state_file=browser.wameiji_state_file,
        screenshot_path=screenshot,
        network_log_path=network,
        timeout_seconds=max(1, browser.wameiji_result_timeout_ms // 1000),
        headless=browser.wameiji_headless,
    )
    if result.get("status") != "ok":
        raise BrowserCaptureBlocked(
            f"wameiji_detail:{result.get('error_type') or result.get('status')}"
        )
    final_url = str(result.get("final_url") or "")
    if "/detail/" not in final_url:
        raise BrowserCaptureBlocked("wameiji_detail:redirected_away_from_listing")
    html = output.read_text(encoding="utf-8")
    parsed = WameijiBrowserAdapter(enabled=True).parse_detail_html(html, search_item)
    if parsed.status != "ok" or not parsed.items:
        raise BrowserCaptureBlocked(
            f"wameiji_detail:{parsed.error_type or parsed.status}"
        )
    item = parsed.items[0]
    if not isinstance(item, MarketItem) or not item.detail_verified:
        raise BrowserCaptureBlocked("wameiji_detail:unverified_item")
    return item


def command_client_from_environment() -> RenderCollectorCommandClient | None:
    base_url = os.getenv("CD_REPLICA_URL", "").strip()
    sync_token = os.getenv("CD_SYNC_TOKEN", "").strip()
    if not base_url or not sync_token:
        return None
    return RenderCollectorCommandClient(base_url, sync_token)
