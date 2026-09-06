"""Tests for db fallback when main DB is corrupted/unusable."""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from cd_monitor.config import load_config
from cd_monitor.cli import _resolve_healthy_db_path


def test_resolve_healthy_db_path_returns_preferred_when_ok(tmp_path):
    p = tmp_path / "ok.db"
    # Use immutable init by running init_db through storage
    from cd_monitor.storage.sqlite import init_db
    init_db(p)
    # Add a row to confirm the file is healthy.
    with sqlite3.connect(str(p)) as c:
        c.execute("SELECT 1").fetchone()
    result = _resolve_healthy_db_path(str(p))
    assert result == str(p)


def test_resolve_healthy_db_path_falls_back_when_corrupted(tmp_path):
    # Create a fake "corrupted" db file that SQLite cannot open with normal mode.
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite database at all")
    # Also create a stray journal file alongside so quick_check trips.
    (tmp_path / "bad.db-journal").write_bytes(b"\x00" * 32)
    # Now try to open it; we expect DatabaseError
    raised = False
    try:
        with sqlite3.connect(str(bad), timeout=2) as c:
            c.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError:
        raised = True
    assert raised, "expected DatabaseError for fake db"
    # Our resolver should fall back gracefully.
    fallback = _resolve_healthy_db_path(str(bad))
    assert fallback != str(bad)
    assert os.path.exists(fallback)
    # Fallback should be a healthy db.
    with sqlite3.connect(fallback) as c:
        c.execute("PRAGMA quick_check").fetchone()


def test_resolve_healthy_db_path_uses_temp_runtime_when_unwritable(tmp_path, monkeypatch):
    """When preferred path is not writable (sandbox blocks it), fallback to temp runtime db."""
    # Create a fake path in a directory that does not exist / is unwritable
    bad = "Z:/no/such/dir/db.db"
    fallback = _resolve_healthy_db_path(bad)
    assert fallback != bad
    assert os.path.exists(fallback)
    # Should be in tempdir/cd_monitor_runtime
    assert "cd_monitor_runtime" in fallback
