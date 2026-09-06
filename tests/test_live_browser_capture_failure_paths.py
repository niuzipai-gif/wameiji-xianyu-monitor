"""Tests for capture_search_html failure paths that previously crashed uncaught."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from cd_monitor.services.live_browser_capture import capture_search_html


class _BrokenContext:
    """Stand-in for async_playwright() whose __aenter__ raises (e.g. PermissionError)."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def __aenter__(self):
        raise self.exc

    async def __aexit__(self, *args):
        return None


def _broken_playwright_factory() -> Any:
    return _BrokenContext(PermissionError(5, "Access is denied."))


@pytest.mark.asyncio
async def test_capture_search_html_returns_not_configured_on_subprocess_failure(tmp_path: Path):
    out = tmp_path / "out.html"
    result = await capture_search_html(
        "xianyu",
        "SRCL-3520",
        out,
        state_file=None,
        profile_dir=None,
        headless=True,
        playwright_factory=_broken_playwright_factory,
    )
    assert result["status"] == "not_configured"
    assert result["error_type"] == "runner_error"
    assert "PermissionError" in result["error_message"] or "denied" in result["error_message"].lower()
    assert result["catalog_no"] == "SRCL-3520"


@pytest.mark.asyncio
async def test_capture_search_html_returns_not_configured_on_os_error(tmp_path: Path):
    """OSError during subprocess pipe creation should be captured gracefully."""
    out = tmp_path / "out.html"
    result = await capture_search_html(
        "xianyu",
        "SRCL-3520",
        out,
        state_file=None,
        profile_dir=None,
        headless=True,
        playwright_factory=lambda: _BrokenContext(OSError(5, "denied")),
    )
    assert result["status"] == "not_configured"
    assert result["error_type"] == "runner_error"


@pytest.mark.asyncio
async def test_capture_search_html_does_not_raise_on_browser_launch_error(tmp_path: Path):
    """Even with a broken playwright, the function returns a structured result instead of raising."""
    out = tmp_path / "out.html"
    try:
        result = await capture_search_html(
            "xianyu",
            "SRCL-3520",
            out,
            state_file=None,
            profile_dir=None,
            headless=True,
            playwright_factory=_broken_playwright_factory,
        )
    except Exception as exc:  # noqa: BLE001 - we want NO exception to leak
        pytest.fail(f"capture_search_html raised {type(exc).__name__}: {exc}")
    assert result["status"] in {"not_configured", "human_required", "error"}
