from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from cd_monitor.core.models import CostConfig, EvaluationConfig
from cd_monitor.services.imports import evaluate_html_files
from cd_monitor.services.live_browser_capture import capture_search_html


CaptureFunc = Callable[..., Awaitable[dict[str, Any]]]


async def capture_and_evaluate_live_html(
    catalog_no: str,
    db_path: str | Path,
    snapshot_dir: str | Path,
    cost_config: CostConfig,
    evaluation_config: EvaluationConfig,
    *,
    state_file: str | Path | None = None,
    profile_dir: str | Path | None = None,
    xianyu_profile_dir: str | Path | None = None,
    wameiji_state_file: str | Path | None = None,
    timeout_seconds: int = 30,
    headless: bool = False,
    capture_func: CaptureFunc = capture_search_html,
) -> dict[str, Any]:
    snapshot_root = Path(snapshot_dir)
    capture_root = snapshot_root / "live_html"
    screenshot_root = snapshot_root / "live_screenshots"
    network_root = snapshot_root / "live_network"
    capture_root.mkdir(parents=True, exist_ok=True)
    screenshot_root.mkdir(parents=True, exist_ok=True)
    network_root.mkdir(parents=True, exist_ok=True)
    safe_catalog = _safe_name(catalog_no)
    wameiji_html = capture_root / f"{safe_catalog}_wameiji.html"
    xianyu_html = capture_root / f"{safe_catalog}_xianyu.html"
    wameiji_screenshot = screenshot_root / f"{safe_catalog}_wameiji.png"
    xianyu_screenshot = screenshot_root / f"{safe_catalog}_xianyu.png"
    wameiji_network = network_root / f"{safe_catalog}_wameiji.network.json"
    xianyu_network = network_root / f"{safe_catalog}_xianyu.network.json"

    wameiji_capture = await capture_func(
        "wameiji",
        catalog_no,
        wameiji_html,
        profile_dir=profile_dir,
        wameiji_state_file=wameiji_state_file,
        screenshot_path=wameiji_screenshot,
        network_log_path=wameiji_network,
        timeout_seconds=timeout_seconds,
        headless=headless,
    )
    if wameiji_capture.get("status") != "ok":
        return _blocked_result(catalog_no, wameiji_capture=wameiji_capture)

    xianyu_capture = await capture_func(
        "xianyu",
        catalog_no,
        xianyu_html,
        state_file=state_file,
        profile_dir=profile_dir,
        xianyu_profile_dir=xianyu_profile_dir,
        screenshot_path=xianyu_screenshot,
        network_log_path=xianyu_network,
        timeout_seconds=timeout_seconds,
        headless=headless,
    )
    if xianyu_capture.get("status") != "ok":
        return _blocked_result(
            catalog_no,
            wameiji_capture=wameiji_capture,
            xianyu_capture=xianyu_capture,
        )

    opportunities, opportunity_ids, snapshot_path = evaluate_html_files(
        catalog_no,
        wameiji_html,
        xianyu_html,
        db_path,
        snapshot_root,
        cost_config,
        evaluation_config,
    )
    return {
        "catalog_no": catalog_no,
        "status": "ok",
        "opportunity_count": len(opportunities),
        "opportunity_ids": opportunity_ids,
        "snapshot_path": str(snapshot_path),
        "wameiji_capture": wameiji_capture,
        "xianyu_capture": xianyu_capture,
    }


def _blocked_result(
    catalog_no: str,
    *,
    wameiji_capture: dict[str, Any],
    xianyu_capture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "catalog_no": catalog_no,
        "status": "human_required",
        "opportunity_count": 0,
        "opportunity_ids": [],
        "snapshot_path": None,
        "wameiji_capture": wameiji_capture,
        "xianyu_capture": xianyu_capture,
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "catalog"
