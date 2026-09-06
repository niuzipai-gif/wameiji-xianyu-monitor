"""P5.4+: AI task generation wiring.

Confirms the Kuro Layer-A implementation of Usagi's
`POST /api/tasks/generate` flow:

- An in-process `TaskGenerationService` tracks 6 jobs through
  prepare / reference / prompt / llm / persist / task steps.
- The criteria file written under `prompts/` matches what
  `build_criteria_filename` would produce.
- After completion, the watchlist row is persisted in the DB with
  `ai_prompt_criteria_file` pointing at the freshly written file.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def _post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.getcode(), json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Service-layer: drives the coroutine end-to-end with a mocked AI call.
# ---------------------------------------------------------------------------


def test_service_run_ai_job_completes_and_persists(monkeypatch, tmp_path) -> None:
    """Drive the full 6-step job with a mocked AI; assert file + watchlist."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    # Replace `generate_criteria` with a stub so we never hit the network.
    from cd_monitor.services import scraper
    from cd_monitor.services.scraper import _prompt_utils

    async def _fake_generate_criteria(*, user_description, reference_file_path, progress_callback=None):
        # Advance through the reference / prompt / llm steps so the job
        # registry sees a complete state-machine walk.
        if progress_callback:
            await progress_callback("reference", "fake reference")
            await progress_callback("prompt", "fake prompt")
            await progress_callback("llm", "fake llm")
        return f"# CRITERIA for {user_description}\nbody"

    monkeypatch.setattr(_prompt_utils, "generate_criteria", _fake_generate_criteria)

    # The fake generate_criteria does NOT touch the reference_file_path,
    # so an existing real one is not required. But to keep the contract
    # honest we write a stub reference file next to db_path.
    (tmp_path / "macbook.txt").write_text("# fake reference body", encoding="utf-8")

    db_path = tmp_path / "ai-job.db"
    prompts_dir = tmp_path / "prompts"

    from cd_monitor.services.task_generate import (
        TaskGenerationService,
        build_criteria_filename,
    )

    service = TaskGenerationService(prompts_dir=str(prompts_dir))
    job = service.create_job(
        task_name="Sony A7M4",
        catalog_no="A7M4",
        description="Looking for clean body, no scratches, original box",
        decision_mode="ai",
    )
    asyncio.run(
        service.run_ai_generation_job(
            job_id=job.job_id,
            reference_file_path=str(tmp_path / "macbook.txt"),
            db_path=db_path,
        )
    )

    final = service.get_job(job.job_id)
    assert final is not None
    assert final.status == "completed", final.to_dict()
    expected_path = str(prompts_dir / "a7m4_criteria.txt")
    assert final.criteria_path == expected_path
    assert os.path.exists(expected_path)
    on_disk = open(expected_path, encoding="utf-8").read()
    assert "Looking for clean body" in on_disk

    # Watchlist row should now exist with ai_prompt_criteria_file pointing
    # at the freshly written file.
    from cd_monitor.storage.sqlite import list_watch_all
    items = list_watch_all(db_path)
    assert len(items) == 1
    assert items[0].catalog_no == "A7M4"
    assert items[0].ai_prompt_criteria_file == expected_path
    assert items[0].description.startswith("Looking for clean body")
    assert items[0].decision_mode == "ai"

    # Every step should be completed.
    statuses = [s.status for s in final.steps]
    assert statuses == ["completed"] * 6, statuses


# ---------------------------------------------------------------------------
# HTTP-layer: POST /api/watchlist/generate returns 202 + job_id, body works.
# ---------------------------------------------------------------------------


def test_post_generate_rejects_missing_description(tmp_path) -> None:
    from cd_monitor.storage.sqlite import init_db
    from cd_monitor.web_server import create_server
    db_path = tmp_path / "http-reject.db"
    prompts_dir = tmp_path / "prompts"
    init_db(db_path)
    srv = create_server("127.0.0.1", 0, db_path, static_dir="web",
                        )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://{srv.server_address[0]}:{srv.server_address[1]}"
    try:
        code, body = _post(
            base + "/api/watchlist/generate",
            {"task_name": "x", "decision_mode": "ai"},
        )
        assert code == 400, body
        assert "AI 判断模式下" in body["error"]
    finally:
        srv.shutdown(); srv.server_close()


def test_post_generate_ai_returns_202_with_job_id(tmp_path, monkeypatch) -> None:
    """POST /api/watchlist/generate returns 202 + job_id, then completes."""
    from cd_monitor.storage.sqlite import init_db, list_watch_all
    from cd_monitor.web_server import create_server
    from cd_monitor.services.scraper import _prompt_utils

    async def _fake_generate(*, user_description, reference_file_path, progress_callback=None):
        if progress_callback:
            await progress_callback("reference", "fake ref")
            await progress_callback("prompt", "fake prompt")
            await progress_callback("llm", "fake llm")
        return f"# {user_description}\nstub"

    monkeypatch.setattr(_prompt_utils, "generate_criteria", _fake_generate)

    (tmp_path / "macbook.txt").write_text("# fake reference body", encoding="utf-8")

    db_path = tmp_path / "http-202.db"
    prompts_dir = tmp_path / "prompts"
    static_dir = Path(__file__).resolve().parents[1] / "web"
    # The HTTP service writes to cwd/prompts; contain its stub output in this test.
    monkeypatch.chdir(tmp_path)
    init_db(db_path)
    srv = create_server("127.0.0.1", 0, db_path, static_dir=static_dir)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://{srv.server_address[0]}:{srv.server_address[1]}"
    try:
        code, body = _post(
            base + "/api/watchlist/generate",
            {
                "task_name": "SonyA7M4",
                "keyword": "A7M4",
                "description": "clean body, original box",
                "decision_mode": "ai",
                "reference_file_path": str(tmp_path / "macbook.txt"),
            },
        )
        assert code == 202, body
        job_id = body["job_id"]
        assert body["status"] == "running"
        assert len(body["steps"]) == 6

        # Poll until the background job completes (with timeout).
        deadline = time.monotonic() + 10
        final_status = None
        while time.monotonic() < deadline:
            with urllib.request.urlopen(
                base + "/api/watchlist/generate/" + job_id, timeout=3
            ) as r:
                final = json.loads(r.read())
            final_status = final["status"]
            if final_status in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert final_status == "completed", final
        assert final["watch_id"] is not None
        # The criteria file must stay inside this test's temporary prompts directory.
        assert final["criteria_path"] and os.path.exists(final["criteria_path"])
        criteria_path = Path(final["criteria_path"]).resolve()
        assert criteria_path == (prompts_dir / "a7m4_criteria.txt").resolve()
        # Watchlist row should be persisted.
        items = list_watch_all(db_path)
        assert len(items) == 1
        assert items[0].ai_prompt_criteria_file == final["criteria_path"]
    finally:
        srv.shutdown(); srv.server_close()


def test_post_generate_keyword_creates_watch_directly(tmp_path, monkeypatch) -> None:
    """Keyword mode: just create the watchlist row; no AI call fires."""
    from cd_monitor.services.scraper import _prompt_utils
    from cd_monitor.storage.sqlite import init_db, list_watch_all
    from cd_monitor.web_server import create_server
    called = {"ai": 0}

    async def _should_not_be_called(**kwargs):
        called["ai"] += 1
        return "won't be used"

    monkeypatch.setattr(_prompt_utils, "generate_criteria", _should_not_be_called)

    db_path = tmp_path / "http-keyword.db"
    init_db(db_path)
    srv = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://{srv.server_address[0]}:{srv.server_address[1]}"
    try:
        code, body = _post(
            base + "/api/watchlist/generate",
            {
                "task_name": "Keywords",
                "keyword": "KEYWORD-001",
                "decision_mode": "keyword",
                "required_keywords": ["foo", "bar"],
            },
        )
        assert code == 200, body
        assert called["ai"] == 0
        items = list_watch_all(db_path)
        assert len(items) == 1
        assert items[0].decision_mode == "keyword"
        assert items[0].required_keywords == ["foo", "bar"]
    finally:
        srv.shutdown(); srv.server_close()
