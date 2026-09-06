from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cd_monitor.config import BrowserConfig
from cd_monitor.core.models import AdapterStatus, WatchItem
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter
from cd_monitor.storage.sqlite import finish_search_run, insert_search_run


@dataclass(slots=True)
class LiveScanStatus:
    catalog_no: str
    status: str
    opportunity_count: int
    wameiji: dict[str, Any]
    xianyu: dict[str, Any]
    search_run_ids: list[int] = field(default_factory=list)


def scan_live_status(catalog_no: str, browser_config: BrowserConfig) -> LiveScanStatus:
    watch = WatchItem(catalog_no=catalog_no)
    if browser_config.wameiji_profile_dir:
        from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapterWithRunner
        wameiji = WameijiBrowserAdapterWithRunner(
            enabled=browser_config.enabled,
            profile_dir=browser_config.wameiji_profile_dir,
            headless=browser_config.wameiji_headless,
            search_url=browser_config.wameiji_search_url,
        ).search_status(watch)
    else:
        wameiji = WameijiBrowserAdapter(enabled=browser_config.enabled).search_status(watch)
    xianyu = XianyuBrowserAdapter(
        enabled=browser_config.enabled,
        state_file=browser_config.xianyu_state_file,
    ).search_status(watch)
    return LiveScanStatus(
        catalog_no=catalog_no,
        status=_combined_status(wameiji, xianyu),
        opportunity_count=0,
        wameiji=_status_dict(wameiji),
        xianyu=_status_dict(xianyu),
    )


def record_live_scan_status(db_path: str | Path, status: LiveScanStatus) -> list[int]:
    ids = [
        _record_source_status(db_path, "wameiji_live_browser", status.catalog_no, status.wameiji),
        _record_source_status(db_path, "xianyu_live_browser", status.catalog_no, status.xianyu),
    ]
    status.search_run_ids = ids
    return ids


def _combined_status(wameiji: AdapterStatus, xianyu: AdapterStatus) -> str:
    if wameiji.status == "disabled" or xianyu.status == "disabled":
        return "disabled"
    if wameiji.status == "human_required" or xianyu.status == "human_required":
        return "human_required"
    if wameiji.status != "ok" or xianyu.status != "ok":
        return "error"
    return "ok"


def _status_dict(status: AdapterStatus) -> dict[str, Any]:
    return asdict(status) | {"item_count": len(status.items)}


def _record_source_status(
    db_path: str | Path,
    source: str,
    catalog_no: str,
    status: dict[str, Any],
) -> int:
    run_id = insert_search_run(
        db_path,
        source=source,
        keyword=catalog_no,
        status=str(status.get("status") or "unknown"),
        error_type=status.get("error_type"),
        error_message=status.get("error_message"),
        screenshot_path=status.get("screenshot_path"),
        raw_snapshot_path=status.get("raw_snapshot_path"),
    )
    finish_search_run(
        db_path,
        run_id,
        status=str(status.get("status") or "unknown"),
        error_type=status.get("error_type"),
        error_message=status.get("error_message"),
        screenshot_path=status.get("screenshot_path"),
        raw_snapshot_path=status.get("raw_snapshot_path"),
    )
    return run_id
