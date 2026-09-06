"""Tests for P0 #5 - PUT /api/watchlist/{id} auto-regenerates AI criteria.

When a watch whose decision_mode is ``ai`` has its ``description`` field
updated, the PUT handler should kick off an AI criteria regeneration,
write the resulting body to ``prompts/{catalog_no}_criteria.txt``, and
persist the new path on the watch row. Edit-only PUTs (no description
change) must NOT trigger an AI call. Any AI failure must NOT break the
underlying watch update.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cd_monitor.core.models import WatchItem
from cd_monitor.storage.sqlite import add_watch, init_db
from cd_monitor.web_server import _watchlist_all
from cd_monitor.web_server import (
    _maybe_regenerate_criteria_on_update,
    _watch_updates,
)


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


def _seed_ai_watch(
    db_path: Path,
    *,
    catalog_no: str = "SVWX-9",
    description: str = "old description",
    reference_text: str | None = "# reference template",
) -> int:
    """Insert an AI-mode watch plus a reference file on disk."""
    if reference_text is not None:
        ref = db_path.parent / "prompts" / "macbook_criteria.txt"
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(reference_text, encoding="utf-8")
    watch = WatchItem(
        catalog_no=catalog_no,
        description=description,
        decision_mode="ai",
        required_keywords=[],
        excluded_keywords=[],
    )
    return add_watch(str(db_path), watch)


def _existing_row(db_path: Path, watch_id: int) -> dict:
    for row in _watchlist_all(str(db_path)):
        if int(row.get("id", 0)) == watch_id:
            return row
    raise AssertionError(f"watch {watch_id} not found")


def test_regen_skips_when_description_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _make_db(tmp_path)
    wid = _seed_ai_watch(db, description="same text")
    monkeypatch.chdir(tmp_path)

    called: dict[str, int] = {"count": 0}

    async def _boom(*_args, **_kwargs):
        called["count"] += 1
        return "should-not-be-written"

    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _boom)

    existing = _existing_row(db, wid)
    payload = {"decision_mode": "ai", "description": "same text"}
    summary = _maybe_regenerate_criteria_on_update(
        str(db), wid, payload, existing, update_result=True,
    )
    assert summary["regenerated"] is False
    assert summary["error"] is None
    assert called["count"] == 0


def test_regen_skips_when_decision_mode_is_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _make_db(tmp_path)
    wid = _seed_ai_watch(db, description="old")
    monkeypatch.chdir(tmp_path)

    called: dict[str, int] = {"count": 0}

    async def _boom(*_args, **_kwargs):
        called["count"] += 1
        return "should-not-be-written"

    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _boom)

    existing = _existing_row(db, wid)
    payload = {"decision_mode": "keyword", "description": "new description"}
    summary = _maybe_regenerate_criteria_on_update(
        str(db), wid, payload, existing, update_result=True,
    )
    assert summary["regenerated"] is False
    assert called["count"] == 0


def test_regen_runs_when_description_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _make_db(tmp_path)
    wid = _seed_ai_watch(db, description="old description")
    monkeypatch.chdir(tmp_path)

    async def _fake_generate(user_description, reference_file_path):
        # The helper invokes generate_criteria with these keyword args
        # (matches Usagi prompt_utils signature).
        return f"# criteria for: {user_description} (ref={reference_file_path})"

    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _fake_generate)

    existing = _existing_row(db, wid)
    payload = {
        "decision_mode": "ai",
        "description": "new description with extra constraints",
    }
    from cd_monitor.services.watch_action_service import WatchActionService
    from cd_monitor.storage.sqlite import update_watch
    # The P0 #7 helper queues a job that reads description from the DB,
    # so we must persist the new description before queueing the regen.
    update_watch(str(db), wid, {"description": payload["description"]})
    svc = WatchActionService(db_path=str(db))
    summary = _maybe_regenerate_criteria_on_update(
        str(db), wid, payload, existing, update_result=True, svc=svc,
    )
    # P0 #7: AI regen is now async; the helper returns a queued summary
    # with a job_id. The actual file write + DB update happens in the
    # background job, so we wait for the job to settle before asserting.
    assert summary.get("queued") is True
    assert summary.get("job_id")
    job_id = summary["job_id"]
    import time as _t
    deadline = _t.monotonic() + 4
    while _t.monotonic() < deadline:
        j = svc.get_job(job_id)
        if j and j.status in {"completed", "failed"}:
            break
        _t.sleep(0.05)
    assert j is not None and j.status == "completed", j.error if j else None
    # Job result holds the criteria_file; the DB row reflects the update.
    crit_path = j.result.get("criteria_file")
    assert crit_path is not None
    on_disk = Path(crit_path)
    assert on_disk.exists()
    body = on_disk.read_text(encoding="utf-8")
    assert "new description with extra constraints" in body
    row = _existing_row(db, wid)
    assert row["ai_prompt_criteria_file"]
    assert "svwx-9" in row["ai_prompt_criteria_file"]


def test_regen_ai_failure_is_surfaced_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _make_db(tmp_path)
    wid = _seed_ai_watch(db, description="old description")
    monkeypatch.chdir(tmp_path)

    async def _broken(*_args, **_kwargs):
        raise RuntimeError("AI down for maintenance")

    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _broken)

    existing = _existing_row(db, wid)
    payload = {"decision_mode": "ai", "description": "totally new requirements"}
    from cd_monitor.services.watch_action_service import WatchActionService
    from cd_monitor.storage.sqlite import update_watch
    update_watch(str(db), wid, {"description": payload["description"]})
    svc = WatchActionService(db_path=str(db))
    summary = _maybe_regenerate_criteria_on_update(
        str(db), wid, payload, existing, update_result=True, svc=svc,
    )
    # The job is queued; the actual AI failure surfaces in the job state
    # (not the immediate summary), but the summary still says queued=True
    # so the HTTP layer can hand the job_id back to the caller.
    assert summary.get("queued") is True
    assert summary.get("job_id")
    import time as _t
    deadline = _t.monotonic() + 4
    while _t.monotonic() < deadline:
        j = svc.get_job(summary["job_id"])
        if j and j.status in {"completed", "failed"}:
            break
        _t.sleep(0.05)
    assert j is not None and j.status == "failed"
    assert "ai_generate_failed" in (j.error or "")


def test_regen_skips_when_update_result_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _make_db(tmp_path)
    wid = _seed_ai_watch(db, description="old description")
    monkeypatch.chdir(tmp_path)

    called: dict[str, int] = {"count": 0}

    async def _boom(*_args, **_kwargs):
        called["count"] += 1
        return "x"

    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _boom)

    existing = _existing_row(db, wid)
    payload = {"decision_mode": "ai", "description": "new description"}
    summary = _maybe_regenerate_criteria_on_update(
        str(db), wid, payload, existing, update_result=False,
    )
    # update_result False means the watch row update itself failed -
    # no point regenerating AI artifacts.
    assert summary["regenerated"] is False
    assert summary["error"] is None
    assert called["count"] == 0
