"""Manual watchlist action service.

Mirrors Usagi `src/services/task_generation_*.py` + the start/stop pattern
from `src/api/routes/tasks.py:start_task` / `stop_task`. Each action is
submitted as an in-process background job tracked in a thread-safe
registry, and the HTTP layer just gets a 202 + job_id back.

Two actions ship initially:

* ``run`` — manual "run once now" (PATCH/POST /api/watchlist/{id}/start).
  Wraps ``SchedulerService.TaskRunner.run_now`` so it composes the prompt,
  records a price-history snapshot, and updates ``is_running`` runtime
  state. Skips gracefully when the watch is disabled or paused.

* ``regenerate_criteria`` — manual "rerun AI criteria" (POST
  /api/watchlist/{id}/regenerate-criteria). Builds a new
  ``prompts/{keyword}_criteria.txt`` via ``generate_criteria``, persists
  the new ``ai_prompt_criteria_file`` on the watch row, all in a daemon
  thread so the HTTP request does not block on the AI call.

These HTTP endpoints make round #3 of USAGI_STUDY.md actionable: Kuro
previously relied on PATCH /api/watchlist/{id} alone to side-effect
regenerate (and it blocked the request via ``asyncio.run``). The
explicit endpoints let the Web UI offer a "Regenerate" button and a
"Run Now" button without freezing the page.
"""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import uuid4

from cd_monitor.services.broadcast_bus import BroadcastBus
from cd_monitor.services.scheduler_service import TaskRunner
from cd_monitor.services.task_generate import build_criteria_filename
from cd_monitor.storage.sqlite import update_watch


ACTION_RUN = "run"
ACTION_REGENERATE_CRITERIA = "regenerate_criteria"


@dataclass(slots=True)
class WatchActionJob:
    job_id: str
    watch_id: int
    action: str
    status: str = "queued"  # queued | running | completed | failed | cancelled
    error: Optional[str] = None
    message: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    cancelled: bool = False
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "watch_id": self.watch_id,
            "action": self.action,
            "status": self.status,
            "error": self.error,
            "message": self.message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "cancelled": self.cancelled,
            "result": dict(self.result),
        }


class WatchActionService:
    """In-process, thread-safe registry of manual watchlist actions.

    Lighter weight than ``TaskGenerationService``: no per-step progress,
    just queued -> running -> completed/failed/cancelled. The HTTP layer
    polls ``get_job`` for terminal status.
    """

    _bus_cache: dict[str, BroadcastBus] = {}
    _bus_cache_lock = threading.Lock()

    def __init__(
        self,
        db_path: str,
        *,
        runner_factory: Optional[Callable[[str], TaskRunner]] = None,
        prompts_dir: str = "prompts",
        bus: Optional[BroadcastBus] = None,
    ) -> None:
        self.db_path = db_path
        self.prompts_dir = prompts_dir
        self._jobs: dict[str, WatchActionJob] = {}
        self._lock = threading.RLock()
        self._runner_factory = runner_factory or (lambda p: TaskRunner(p))
        # 2-worker pool: enough that one slow AI call does not block another
        # manual run. Daemon threads so server shutdown is not held by an in-flight job.
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="WatchAction",
        )
        self.bus: BroadcastBus = bus if bus is not None else self._get_or_create_bus(db_path)

    @classmethod
    def _get_or_create_bus(cls, db_path: str) -> BroadcastBus:
        with cls._bus_cache_lock:
            bus = cls._bus_cache.get(db_path)
            if bus is None:
                bus = BroadcastBus()
                cls._bus_cache[db_path] = bus
            return bus

    @classmethod
    def reset_bus_cache(cls) -> None:
        with cls._bus_cache_lock:
            cls._bus_cache.clear()

    # -- registry --------------------------------------------------------
    def _new_job(self, watch_id: int, action: str) -> WatchActionJob:
        job = WatchActionJob(
            job_id=uuid4().hex,
            watch_id=watch_id,
            action=action,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[WatchActionJob]:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def list_jobs(self, watch_id: Optional[int] = None) -> list[WatchActionJob]:
        with self._lock:
            jcs = list(self._jobs.values())
        if watch_id is not None:
            jcs = [j for j in jcs if j.watch_id == watch_id]
        return [deepcopy(j) for j in jcs]

    def cancel_job(self, job_id: str) -> bool:
        """Best-effort cancel: marks the job, the worker checks the flag."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status not in ("queued", "running"):
                return False
            job.cancelled = True
            job.status = "cancelled"
            job.finished_at = datetime.now()
            return True

    # -- submit ---------------------------------------------------------
    def submit_run(self, watch_id: int) -> WatchActionJob:
        job = self._new_job(watch_id, ACTION_RUN)
        self._track(self._execute_run(job), job.job_id)
        return deepcopy(job)

    def submit_regenerate_criteria(self, watch_id: int) -> WatchActionJob:
        job = self._new_job(watch_id, ACTION_REGENERATE_CRITERIA)
        self._track(self._execute_regenerate_criteria(job), job.job_id)
        return deepcopy(job)

    # -- workers --------------------------------------------------------
    def _track(self, coro, job_id: str):
        """Submit the coroutine to a thread pool and return a Future.

        Each worker manages its own asyncio loop so there is no
        interaction with whatever loop the caller / test runner holds
        in the main thread.
        """
        return self._executor.submit(self._run_in_worker, coro, job_id)

    def _run_in_worker(self, coro, job_id: str) -> None:
        """Worker body: advance job state, run coro on a fresh loop."""
        self._update(job_id, status="running", started_at=datetime.now(),
                     message="running")
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
            with self._lock:
                job = self._jobs.get(job_id)
                if job and job.status == "running":
                    self._update(job_id, status="completed",
                                 finished_at=datetime.now(),
                                 message="completed")
        except Exception as exc:
            self._update(job_id, status="failed",
                         finished_at=datetime.now(),
                         error=f"{type(exc).__name__}: {exc}")
        finally:
            loop.close()

    def _update(self, job_id: str, **kw: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in kw.items():
                setattr(job, k, v)
        # Broadcast status transitions (queued -> running -> terminal).
        if "status" in kw:
            payload = {
                "job_id": job.job_id,
                "watch_id": job.watch_id,
                "action": job.action,
                "status": job.status,
                "error": job.error,
                "message": job.message,
            }
            event_type = (
                "watch_action_terminal"
                if job.status in {"completed", "failed", "cancelled"}
                else "watch_action"
            )
            try:
                self.bus.publish(event_type, payload)
            except Exception:
                # Broadcasting must never break the job state machine.
                pass

    async def _execute_run(self, job: WatchActionJob) -> None:
        runner = self._runner_factory(self.db_path)
        result = await runner.run_now(job.watch_id)
        if self._is_cancelled(job.job_id):
            return
        if not result.get("ok"):
            error = result.get("error", "task_run_failed")
            extra = ""
            if error == "task_paused":
                paused = result.get("paused_until")
                extra = f" (paused until {paused})" if paused else " (paused)"
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error=error + extra)
            return
        self._update(
            job.job_id,
            result={
                "ok": True,
                "task_id": result.get("task_id"),
                "task_name": result.get("task_name"),
                "started_at": result.get("started_at"),
                "snapshot_id": result.get("snapshot_id"),
            },
        )

    async def _execute_regenerate_criteria(self, job: WatchActionJob) -> None:
        watch = self._find_watch(job.watch_id)
        if watch is None:
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error="watch_not_found")
            return
        decision_mode = (watch.get("decision_mode") or "ai").strip().lower()
        if decision_mode != "ai":
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error="decision_mode_not_ai")
            return
        description = (watch.get("description") or "").strip()
        if not description:
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error="description_required")
            return
        reference = watch.get("ai_prompt_base_file") or "prompts/macbook_criteria.txt"
        if not os.path.exists(reference):
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error=f"reference_not_found:{reference}")
            return
        try:
            from cd_monitor.services.scraper._prompt_utils import (
                generate_criteria as _ai_generate_criteria,
            )
            body = await asyncio.wait_for(
                _ai_generate_criteria(
                    user_description=description,
                    reference_file_path=reference,
                ),
                timeout=45.0,
            )
        except asyncio.TimeoutError:
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error="ai_generate_timeout")
            return
        except Exception as exc:
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error=f"ai_generate_failed:{type(exc).__name__}:{exc}")
            return
        if self._is_cancelled(job.job_id):
            return
        criteria_rel = build_criteria_filename(watch.get("catalog_no") or str(job.watch_id), self.prompts_dir)
        try:
            os.makedirs(os.path.dirname(criteria_rel) or ".", exist_ok=True)
            with open(criteria_rel, "w", encoding="utf-8") as fh:
                fh.write(body)
        except OSError as exc:
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error=f"write_failed:{exc}")
            return
        try:
            update_watch(self.db_path, job.watch_id,
                         {"ai_prompt_criteria_file": criteria_rel})
        except Exception as exc:
            self._update(job.job_id, status="failed",
                         finished_at=datetime.now(),
                         error=f"db_update_failed:{type(exc).__name__}:{exc}")
            return
        self._update(
            job.job_id,
            result={
                "criteria_file": os.path.abspath(criteria_rel),
                "bytes_written": len(body),
            },
        )

    # -- helpers --------------------------------------------------------
    def _find_watch(self, watch_id: int):
        # Use the repository facade so we get a dict with the row id baked
        # in (WatchItem itself does not carry the primary key, so we
        # cannot just do `w.id`).
        from cd_monitor.services.task_repository import TaskRepository
        repo = TaskRepository(self.db_path)
        return repo.get_task(int(watch_id))

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.cancelled)


__all__ = [
    "ACTION_RUN",
    "ACTION_REGENERATE_CRITERIA",
    "WatchActionJob",
    "WatchActionService",
]
