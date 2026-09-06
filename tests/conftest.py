"""Project-level pytest config: redirect tmp_path away from supervisor-locked F:\\.

Strategy: don't depend on pytest's built-in tmp_path (which uses C:\\Users\\19097\\AppData\\Local\\Temp
and then iterates for cleanup, which the supervisor locks). Instead, generate our own dirs under
tempfile.gettempdir() which the supervisor does NOT lock.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def _project_tmp_root():
    """Project-internal temp root for fallback. Use tempfile.gettempdir() when possible."""
    return Path(tempfile.gettempdir()) / "cd-pytest-tmp"


@pytest.fixture
def tmp_path(_project_tmp_root):
    _project_tmp_root.mkdir(parents=True, exist_ok=True)
    p = _project_tmp_root / f"t_{uuid.uuid4().hex[:12]}"
    p.mkdir(parents=True, exist_ok=True)
    yield p
    try:
        shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Skip pytest's default basetemp cleanup; supervisor may lock files there.

    Cleanup is best-effort and we already clean up tmp_path ourselves.
    """
    return None

# --- Tests must not inherit the server runtime env vars. Otherwise config files in tmp_path
# get overridden by the parent shell, breaking subprocess-based CLI tests that rely on
# per-test db_path / notify credentials defined in their own config files.
@pytest.fixture(autouse=True, scope="session")
def _isolate_test_env():
    saved = {}
    for key in ("CD_MONITOR_DB_PATH", "FEISHU_WEBHOOK_URL", "DINGTALK_WEBHOOK_URL",
                "BROWSER_ENABLED", "XIANYU_STATE_FILE", "GOOFISH_STATE_FILE",
                "WAMEIJI_STATE_FILE", "WAMEIJI_PROFILE_DIR", "WAMEIJI_SEARCH_URL",
                "WAMEIJI_HEADLESS"):
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    yield
    for key, value in saved.items():
        os.environ[key] = value
