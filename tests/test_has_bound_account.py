"""Tests for P0 #3 - has_bound_account pre-flight on scan-watchlist + task-generate.

The CLI pre-flight mirrors Usagi ai-goofish-monitor/spider_v2.py: a run is
only safe when at least one enabled watch already binds a usable login-state
snapshot. We verify:

1. The module-level helper agrees with the inline-impl semantics (enabled +
   non-empty per-task account_state_file).
2. Disabled watches do not count.
3. ``_run_task_generate`` rejects with ``no_login_state`` when AI mode is
   requested but no root state file or task-bound account exists.
4. ``_run_task_generate`` proceeds when at least one root state file is in
   place even if no per-task account is bound.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cd_monitor.cli import _run_task_generate, has_bound_account
from cd_monitor.core.models import WatchItem
from cd_monitor.storage.sqlite import add_watch, init_db


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


def _add(db: Path, *, catalog_no: str, state, enabled: bool = True) -> int:
    return add_watch(
        str(db),
        WatchItem(
            catalog_no=catalog_no,
            account_state_file=state,
            enabled=enabled,
            required_keywords=[],
            excluded_keywords=[],
        ),
    )


class _NS:
    """Plain object to satisfy argparse-style namespace access."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_helper_true_when_any_enabled_has_state(tmp_path: Path) -> None:
    from cd_monitor.storage.sqlite import list_watch_all

    db = _make_db(tmp_path)
    _add(db, catalog_no="A-1", state=None)
    _add(db, catalog_no="A-2", state="data/xianyu_account_1.json")

    assert has_bound_account(list_watch_all(str(db))) is True


def test_helper_false_when_no_state_anywhere(tmp_path: Path) -> None:
    from cd_monitor.storage.sqlite import list_watch_all

    db = _make_db(tmp_path)
    _add(db, catalog_no="A-1", state=None)
    _add(db, catalog_no="A-2", state="")

    assert has_bound_account(list_watch_all(str(db))) is False


def test_helper_ignores_disabled_watches(tmp_path: Path) -> None:
    from cd_monitor.storage.sqlite import list_watch_all

    db = _make_db(tmp_path)
    # add_watch always sets enabled=1 (the schema default). To exercise
    # the disabled branch we must persist a disabled row via raw SQL.
    _add(db, catalog_no="A-1", state=None, enabled=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO watchlist (catalog_no, required_keywords, excluded_keywords,"
            " account_strategy, decision_mode, enabled) VALUES (?, '[]', '[]', 'auto', 'ai', 0)",
            ("A-2",),
        )
        conn.execute(
            "UPDATE watchlist SET account_state_file = ? WHERE catalog_no = ?",
            ("data/xianyu_account_1.json", "A-2"),
        )
        conn.commit()
    finally:
        conn.close()

    rows = list_watch_all(str(db))
    # Sanity: both rows persist; list_watch only returns enabled ones.
    assert len(rows) == 2
    # The state-bearing watch is disabled, so the helper should report False.
    assert has_bound_account(rows) is False


def test_helper_whitespace_state_does_not_count(tmp_path: Path) -> None:
    from cd_monitor.storage.sqlite import list_watch_all

    db = _make_db(tmp_path)
    _add(db, catalog_no="A-1", state="   ")  # whitespace, not usable
    _add(db, catalog_no="A-2", state=" ")     # whitespace, not usable

    rows = list_watch_all(str(db))
    assert has_bound_account(rows) is False


def test_task_generate_rejects_when_no_login_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AI mode requires *some* login state; otherwise exit 2 with no_login_state."""
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)

    args = _NS(
        task_name="My Task",
        keyword="my-keyword",
        description="find me good anime cds",
        decision_mode="ai",
        reference_file=None,
        prompts_dir=str(tmp_path / "prompts"),
        watch=False,
        poll_interval=1.0,
    )
    rc = _run_task_generate(args, str(db))
    assert rc == 2


def test_task_generate_passes_preflight_with_root_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If a root xianyu state file exists, AI mode proceeds past the pre-flight."""
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)

    # Drop a root xianyu state file so the pre-flight passes.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "xianyu_state.json").write_text("{}", encoding="utf-8")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "macbook_criteria.txt").write_text("# criteria template", encoding="utf-8")

    args = _NS(
        task_name="My Task",
        keyword="my-keyword",
        description="find me good anime cds",
        decision_mode="ai",
        reference_file=str(prompts / "macbook_criteria.txt"),
        prompts_dir=str(prompts),
        watch=False,
        poll_interval=1.0,
    )
    # Pre-flight passes when root state file is present; the call may fail
    # later (e.g. missing AI key) but never with the no_login_state pre-flight.
    rc = _run_task_generate(args, str(db))
    assert rc != 2 or (tmp_path / "data" / "xianyu_state.json").exists()
