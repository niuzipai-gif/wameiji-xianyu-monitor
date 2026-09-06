"""Test live-watchlist CLI command (uses subprocess for end-to-end validation)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_live_watchlist_command_is_registered():
    """Ensure `live-watchlist` subcommand is wired into argparse."""
    result = subprocess.run(
        [sys.executable, "-m", "cd_monitor.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert "live-watchlist" in result.stdout


def test_live_watchlist_help_shows_notify_flags():
    result = subprocess.run(
        [sys.executable, "-m", "cd_monitor.cli", "live-watchlist", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert "--notify" in result.stdout
    assert "--notify-channel" in result.stdout
    assert "--notify-dry-run" in result.stdout
    assert "feishu" in result.stdout
    assert "dingtalk" in result.stdout


def test_scan_live_html_help_shows_notify_flags():
    result = subprocess.run(
        [sys.executable, "-m", "cd_monitor.cli", "scan-live-html", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert "--notify" in result.stdout
    assert "--notify-channel" in result.stdout


def test_scan_once_help_shows_notify_flags():
    result = subprocess.run(
        [sys.executable, "-m", "cd_monitor.cli", "scan-once", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert "--notify" in result.stdout
    assert "--notify-channel" in result.stdout


def test_scan_once_notify_dry_run_with_mock(tmp_path: Path):
    """End-to-end: scan-once --notify --notify-dry-run should produce notifications key."""
    db = tmp_path / "scan.db"
    env = {
        **os.environ,
        "PYTHONPATH": "src",
        "FEISHU_WEBHOOK_URL": "https://example.test/hook",
    }
    result = subprocess.run(
        [
            sys.executable, "-m", "cd_monitor.cli", "scan-once",
            "--catalog-no", "SRCL-3520",
            "--db", str(db),
            "--notify", "--notify-dry-run",
            "--notify-channel", "feishu",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert result.returncode == 0, f"stderr={result.stderr[:500]}"
    payload = json.loads(result.stdout)
    assert "notifications" in payload
    assert len(payload["notifications"]) == 1
    n = payload["notifications"][0]
    assert n["channel"] == "feishu"
    assert n["status"] == "dry_run"


def test_scan_once_notify_without_webhook_reports_no_channel(tmp_path: Path):
    db = tmp_path / "scan.db"
    env = {
        **os.environ,
        "PYTHONPATH": "src",
    }
    env.pop("FEISHU_WEBHOOK_URL", None)
    env.pop("DINGTALK_WEBHOOK_URL", None)
    result = subprocess.run(
        [
            sys.executable, "-m", "cd_monitor.cli", "scan-once",
            "--catalog-no", "SRCL-3520",
            "--db", str(db),
            "--notify", "--notify-dry-run",
            "--notify-channel", "feishu",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert result.returncode == 0, f"stderr={result.stderr[:500]}"
    payload = json.loads(result.stdout)
    assert "notifications" in payload
    assert payload["notifications"][0]["status"] == "no_channel_configured"
