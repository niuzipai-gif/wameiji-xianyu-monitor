"""Tests for the Wameiji env-var wiring + scan_live_status / search_status integration."""
from __future__ import annotations

import os

import pytest

from cd_monitor.config import BrowserConfig, ProjectConfig, _apply_env
from cd_monitor.services.live_scan import scan_live_status
from cd_monitor.sources.wameiji_browser import (
    WameijiBrowserAdapter,
    WameijiBrowserAdapterWithRunner,
)


def _watch(catalog: str = "SRCL-3520"):
    from cd_monitor.core.models import WatchItem
    return WatchItem(catalog_no=catalog)


# === env var wiring =================================================
def test_wameiji_profile_dir_env_overrides_default(monkeypatch):
    monkeypatch.setenv("WAMEIJI_PROFILE_DIR", "C:/chrome/profiles/wameiji")
    c = ProjectConfig()
    _apply_env(c)
    assert c.browser.wameiji_profile_dir == "C:/chrome/profiles/wameiji"


def test_wameiji_search_url_env_overrides_default(monkeypatch):
    monkeypatch.setenv("WAMEIJI_SEARCH_URL", "https://meruki.cn/?q=test")
    c = ProjectConfig()
    _apply_env(c)
    assert c.browser.wameiji_search_url == "https://meruki.cn/?q=test"


def test_wameiji_headless_env_truthy_and_falsy(monkeypatch):
    monkeypatch.setenv("WAMEIJI_HEADLESS", "true")
    c = ProjectConfig()
    _apply_env(c)
    assert c.browser.wameiji_headless is True
    monkeypatch.setenv("WAMEIJI_HEADLESS", "false")
    c2 = ProjectConfig()
    _apply_env(c2)
    assert c2.browser.wameiji_headless is False


def test_wameiji_env_absent_keeps_defaults(monkeypatch):
    monkeypatch.delenv("WAMEIJI_PROFILE_DIR", raising=False)
    monkeypatch.delenv("WAMEIJI_SEARCH_URL", raising=False)
    monkeypatch.delenv("WAMEIJI_HEADLESS", raising=False)
    c = ProjectConfig()
    _apply_env(c)
    assert c.browser.wameiji_profile_dir == ""
    assert c.browser.wameiji_search_url == "https://meruki.cn/search"
    assert c.browser.wameiji_headless is False


# === scan_live_status dispatch ======================================
def test_scan_live_status_uses_runner_when_profile_dir_set():
    bc = BrowserConfig(enabled=True, wameiji_profile_dir="C:/fake/profile")
    s = scan_live_status("SRCL-3520", bc)
    assert s.wameiji["status"] == "human_required"
    assert s.wameiji["error_type"] == "async_capture_required"
    assert "WAMEIJI_PROFILE_DIR" in s.wameiji["error_message"] or "profile" in s.wameiji["error_message"]
    assert s.wameiji["capture_instruction"]
    assert "capture-live-html" in s.wameiji["capture_instruction"]


def test_scan_live_status_legacy_when_profile_dir_empty():
    bc = BrowserConfig(enabled=True)
    s = scan_live_status("SRCL-3520", bc)
    assert s.wameiji["status"] == "human_required"
    assert s.wameiji["error_type"] == "not_configured"


def test_scan_live_status_disabled_when_browser_off():
    bc = BrowserConfig(enabled=False, wameiji_profile_dir="C:/fake/profile")
    s = scan_live_status("SRCL-3520", bc)
    # Profile dir takes precedence in this dispatcher but disabled flag wins for the WithRunner.search_status path
    assert s.wameiji["status"] == "disabled"


def test_scan_live_status_xianyu_unaffected():
    bc = BrowserConfig(enabled=True, wameiji_profile_dir="C:/fake/profile")
    s = scan_live_status("SRCL-3520", bc)
    # Xianyu path is independent
    assert s.xianyu["status"] == "human_required"
    assert s.xianyu["error_type"] == "not_configured"


# === search_status override on WithRunner ==========================
def test_with_runner_search_status_disabled():
    a = WameijiBrowserAdapterWithRunner(enabled=False, profile_dir="C:/x")
    status = a.search_status(_watch())
    assert status.status == "disabled"
    assert status.error_type == "browser_disabled"


def test_with_runner_search_status_async_path_message():
    a = WameijiBrowserAdapterWithRunner(enabled=True, profile_dir="C:/x")
    status = a.search_status(_watch())
    assert status.status == "human_required"
    assert status.error_type == "async_capture_required"
    assert status.search_entry_url and "SRCL-3520" in status.search_entry_url


def test_with_runner_search_status_falls_back_when_no_profile():
    a = WameijiBrowserAdapterWithRunner(enabled=True, profile_dir=None)
    status = a.search_status(_watch())
    # Falls through to legacy not_configured message
    assert status.status == "human_required"
    assert status.error_type == "not_configured"
