from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from cd_monitor.config import ProjectConfig
from cd_monitor.core.models import WatchItem
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter


def run_doctor(config: ProjectConfig, db_path: str | Path) -> dict[str, Any]:
    checks = {
        "python": {
            "ok": sys.version_info >= (3, 11),
            "version": sys.version.split()[0],
        },
        "mock_wameiji": _file_check(Path("data/mock/wameiji_items.sample.json")),
        "mock_xianyu": _file_check(Path("data/mock/xianyu_samples.sample.json")),
        "db_parent": _writable_dir_check(Path(db_path).parent),
        "snapshot_dir": _writable_dir_check(Path(config.app.snapshot_dir)),
        "browser": {
            "ok": True,
            "status": "enabled" if config.browser.enabled else "disabled",
            "profile_name": config.browser.profile_name,
            "wameiji": {
                "profile_dir_configured": bool(config.browser.wameiji_profile_dir),
                "profile_dir": config.browser.wameiji_profile_dir or None,
                "search_url": config.browser.wameiji_search_url,
                "headless": config.browser.wameiji_headless,
                "max_consecutive_failures": config.browser.wameiji_max_consecutive_failures,
                "max_retries_per_action": config.browser.wameiji_max_retries_per_action,
            },
        },
        "notify": {
            "ok": True,
            "feishu_configured": bool(config.notify.feishu_webhook_url),
            "dingtalk_configured": bool(config.notify.dingtalk_webhook_url),
        },
        "data_sources": _data_source_checks(config),
    }
    return {
        "ok": all(check.get("ok", False) for check in checks.values()),
        "checks": checks,
    }


def _file_check(path: Path) -> dict[str, Any]:
    return {"ok": path.exists() and path.is_file(), "path": str(path)}


def _writable_dir_check(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        # Best-effort cleanup: some sandboxed environments deny unlink but the write itself
        # is sufficient proof that the directory is writable. We swallow unlink errors and
        # still report ok=True as long as the write succeeded.
        try:
            probe.unlink()
        except OSError:
            pass
        return {"ok": True, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "path": str(path), "error": str(exc)}


def _data_source_checks(config: ProjectConfig) -> dict[str, Any]:
    watch = WatchItem(catalog_no="STATUS-CHECK")
    if config.browser.wameiji_profile_dir:
        from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapterWithRunner
        wameiji_status = WameijiBrowserAdapterWithRunner(
            enabled=config.browser.enabled,
            profile_dir=config.browser.wameiji_profile_dir,
            headless=config.browser.wameiji_headless,
            search_url=config.browser.wameiji_search_url,
        ).search_status(watch)
    else:
        wameiji_status = WameijiBrowserAdapter(enabled=config.browser.enabled).search_status(watch)
    xianyu_status = XianyuBrowserAdapter(
        enabled=config.browser.enabled,
        state_file=config.browser.xianyu_state_file,
    ).search_status(watch)
    return {
        "ok": True,
        "mock": {
            "ok": True,
            "mode": "local_replay",
            "wameiji_path": "data/mock/wameiji_items.sample.json",
            "xianyu_path": "data/mock/xianyu_samples.sample.json",
        },
        "manual_snapshots": {
            "ok": True,
            "mode": "user_provided_json_csv_html",
            "commands": [
                "import-html",
                "import-csv",
                "evaluate-json",
                "evaluate-files",
                "evaluate-html",
            ],
            "web_actions": [
                "Evaluate HTML/CSV",
                "Evaluate HTML/HTML",
                "Evaluate Pasted HTML",
            ],
        },
        "wameiji_live_browser": _live_status(wameiji_status),
        "xianyu_live_browser": _live_status(xianyu_status),
    }


def _live_status(status: Any) -> dict[str, Any]:
    return {
        "ok": status.status == "ok",
        "status": status.status,
        "error_type": status.error_type,
        "error_message": status.error_message,
        "login_state_ready": getattr(status, "login_state_ready", False),
        "state_file_status": getattr(status, "state_file_status", None),
        "state_file_path": getattr(status, "state_file_path", None),
        "state_cookie_domains": getattr(status, "state_cookie_domains", []),
    }
