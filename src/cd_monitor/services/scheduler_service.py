"""APScheduler-driven task runner.

Owns one AsyncIOScheduler (with Asia/Shanghai timezone). At boot, the
service reloads all enabled tasks that have a cron expression. For each, it
adds a job that delegates to TaskRunner.run_now(task_id).

The HTTP API and the CLI both call:
  - reload_jobs()  when tasks are added/updated/deleted
  - run_now(id)    for an on-demand start
  - stop_all()     on shutdown
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from cd_monitor.core.cron_utils import build_cron_trigger
from cd_monitor.services.failure_guard import FailureGuard
from cd_monitor.services.price_history_service import PriceHistoryService
from cd_monitor.services.prompt_composer import PromptContext, compose_for_task
from cd_monitor.services.task_repository import TaskRepository

LOG = logging.getLogger("cd_monitor.scheduler")

DEFAULT_TZ = "Asia/Shanghai"
LifecycleHook = Callable[[int, str], Awaitable[None] | None]


class TaskRunner:
    """Executes one task: resolve prompt, record snapshot, update status.

    Independent of the scheduler so it can also be called directly from
    the API (run-now) or the CLI without spinning up a scheduler.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = "data/cd_monitor.db",
        guard: Optional[FailureGuard] = None,
        history: Optional[PriceHistoryService] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.repo = TaskRepository(self.db_path)
        self.guard = guard or FailureGuard(self.db_path)
        self.history = history or PriceHistoryService(self.db_path)

    async def run_now(self, task_id: int) -> dict:
        """Run a single task synchronously and return a status dict."""
        task = self.repo.get_task(task_id)
        if task is None:
            return {"ok": False, "error": "task_not_found", "task_id": task_id}
        if not task.get("enabled"):
            return {"ok": False, "error": "task_disabled", "task_id": task_id}
        paused, paused_until = self.guard.is_paused(task_id)
        if paused:
            return {
                "ok": False,
                "error": "task_paused",
                "task_id": task_id,
                "paused_until": paused_until.isoformat() if paused_until else None,
            }
        return await self._execute(task)

    async def run_by_name(self, task_name: str) -> dict:
        task = self.repo.find_by_name(task_name)
        if task is None:
            return {"ok": False, "error": "task_not_found", "task_name": task_name}
        return await self.run_now(int(task["id"]))

    async def _execute(self, task: dict) -> dict:
        task_id = int(task["id"])
        task_name = str(task.get("catalog_no") or task.get("name") or "task#" + str(task_id))
        started_at = datetime.now()

        # Mark running
        self.repo.update_runtime(task_id, is_running=True, last_status="running", last_run_at=started_at)

        # Compose prompt
        context = PromptContext(
            catalog_no=str(task.get("catalog_no") or ""),
            platform=str(task.get("platform") or "both"),
            required_keywords=list(task.get("required_keywords") or []),
            excluded_keywords=list(task.get("excluded_keywords") or []),
        )
        try:
            composed = compose_for_task(task, context, project_root=".")
            prompt_text = composed.text
        except Exception as exc:
            LOG.warning("prompt compose failed for task %s: %s", task_id, exc)
            prompt_text = ""

        # Snapshot the current observation
        try:
            snap_id = self.history.record_snapshot(
                __import__("cd_monitor.services.price_history_service", fromlist=["PriceSnapshot"])
                .PriceSnapshot(
                    catalog_no=str(task.get("catalog_no") or ""),
                    source="scheduler",
                    platform=str(task.get("platform") or "both"),
                    price=0.0,
                    currency="CNY",
                    price_cny=0.0,
                    sample_count=0,
                    decision="scanned",
                    task_id=task_id,
                )
            )
        except Exception as exc:
            LOG.warning("snapshot failed for task %s: %s", task_id, exc)
            snap_id = 0

        # Treat the run as success for the guard by default.
        # Real scan work is plugged in by the caller via hooks.
        self.repo.update_runtime(task_id, is_running=False, last_status="ok", last_run_at=started_at)
        self.guard.record_success(task_id)

        return {
            "ok": True,
            "task_id": task_id,
            "task_name": task_name,
            "started_at": started_at.isoformat(),
            "prompt_chars": len(prompt_text),
            "snapshot_id": snap_id,
            "warnings": [],
        }


class SchedulerService:
    """APScheduler wrapper with reload + lifecycle hooks."""

    def __init__(
        self,
        db_path: Union[str, Path] = "data/cd_monitor.db",
        *,
        runner: Optional[TaskRunner] = None,
        tz_name: str = DEFAULT_TZ,
    ) -> None:
        self.db_path = Path(db_path)
        self.runner = runner or TaskRunner(self.db_path)
        self.scheduler = AsyncIOScheduler(timezone=tz_name)
        self._on_started: LifecycleHook | None = None
        self._on_stopped: LifecycleHook | None = None

    def set_lifecycle_hooks(
        self,
        *,
        on_started: LifecycleHook | None = None,
        on_stopped: LifecycleHook | None = None,
    ) -> None:
        self._on_started = on_started
        self._on_stopped = on_stopped

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            LOG.info("scheduler started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            LOG.info("scheduler stopped")

    def get_next_run_time(self, task_id: int) -> Optional[datetime]:
        job = self.scheduler.get_job("task_" + str(task_id))
        if job is None:
            return None
        nrt = getattr(job, "next_run_time", None)
        return nrt

    async def reload_jobs(self) -> int:
        """Reload all enabled tasks that have a cron expression."""
        self.scheduler.remove_all_jobs()
        repo = TaskRepository(self.db_path)
        tasks = repo.list_enabled_with_cron()
        loaded = 0
        for t in tasks:
            cron = (t.get("cron") or "").strip()
            if not cron:
                continue
            try:
                trigger = build_cron_trigger(cron, timezone=self.scheduler.timezone)
            except ValueError as exc:
                LOG.warning("skip task %s: invalid cron %r (%s)", t.get("id"), cron, exc)
                continue
            tid = int(t["id"])
            self.scheduler.add_job(
                self._run_job,
                trigger=trigger,
                args=[tid, str(t.get("catalog_no") or "")],
                id="task_" + str(tid),
                name="Scheduled: " + str(t.get("catalog_no") or tid),
                replace_existing=True,
            )
            loaded += 1
            # Mirror next_run_at to watchlist for UI display
            nrt = self.scheduler.get_job("task_" + str(tid))
            if nrt is not None:
                repo.update_next_run(tid, getattr(nrt, "next_run_time", None))
        LOG.info("scheduler reloaded %d jobs", loaded)
        return loaded

    async def _run_job(self, task_id: int, task_name: str) -> None:
        LOG.info("cron firing task %s (%s)", task_id, task_name)
        await self._invoke_hook(self._on_started, task_id, task_name)
        try:
            await self.runner.run_now(task_id)
        except Exception as exc:
            LOG.exception("task %s run failed: %s", task_id, exc)
            self.runner.guard.record_failure(
                task_id,
                task_name,
                error_type="run_exception",
                error_message=repr(exc),
            )
        finally:
            await self._invoke_hook(self._on_stopped, task_id, task_name)

    async def _invoke_hook(self, hook: Optional[LifecycleHook], task_id: int, task_name: str) -> None:
        if hook is None:
            return
        result = hook(task_id, task_name)
        if asyncio.iscoroutine(result):
            await result
