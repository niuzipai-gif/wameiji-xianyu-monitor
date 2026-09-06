"""Kuro Layer-A task generation service.

Mirrors Usagi `src/services/task_generation_*` + `src/services/task_generation_runner.py`
without pulling Layer B's Pydantic stack into the active runtime.

The Usagi / Layer-B pipeline spawns an async background job with six
visible steps (prepare / reference / prompt / llm / persist / task) and
writes `prompts/{keyword}_criteria.txt` before creating the watchlist row.

Kuro Layer A does the same, but:
- exposes plain dataclasses instead of Pydantic models,
- stores the job map in-process (thread-safe dict),
- persists the criteria file under `prompts/` (configurable),
- uses `add_watch(...)` from `storage.sqlite` to insert the row.
"""
from __future__ import annotations

import asyncio
import os
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from cd_monitor.core.models import WatchItem
from cd_monitor.services.scraper import _prompt_utils as _prompt_utils_module  # late-bind to allow monkey-patch
from cd_monitor.storage.sqlite import add_watch, init_db


DEFAULT_GENERATION_STEPS: tuple[tuple[str, str], ...] = (
    ("prepare", "接收创建请求"),
    ("reference", "读取参考文件"),
    ("prompt", "构建提示词"),
    ("llm", "调用 AI 生成标准"),
    ("persist", "保存分析标准"),
    ("task", "创建任务记录"),
)


@dataclass(slots=True)
class TaskGenerationStep:
    key: str
    label: str
    status: str = "pending"  # pending | running | completed | failed
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "message": self.message,
        }


@dataclass(slots=True)
class TaskGenerationJob:
    job_id: str
    task_name: str
    catalog_no: str = ""
    description: str = ""
    decision_mode: str = "ai"
    status: str = "running"  # running | completed | failed
    current_step: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    criteria_path: Optional[str] = None
    watch_id: Optional[int] = None
    steps: list[TaskGenerationStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "task_name": self.task_name,
            "catalog_no": self.catalog_no,
            "description": self.description,
            "decision_mode": self.decision_mode,
            "status": self.status,
            "current_step": self.current_step,
            "message": self.message,
            "error": self.error,
            "criteria_path": self.criteria_path,
            "watch_id": self.watch_id,
            "steps": [s.to_dict() for s in self.steps],
        }


def build_criteria_filename(keyword: str, prompts_dir: str = "prompts") -> str:
    safe = "".join(
        ch for ch in keyword.lower().replace(" ", "_")
        if ch.isalnum() or ch in "_-"
    ).rstrip("_") or "task"
    return os.path.join(prompts_dir, f"{safe}_criteria.txt")


class TaskGenerationService:
    """In-process, thread-safe registry of AI task-generation jobs."""

    def __init__(self, prompts_dir: str = "prompts") -> None:
        self.prompts_dir = prompts_dir
        self._jobs: dict[str, TaskGenerationJob] = {}
        self._lock = threading.Lock()

    def create_job(self, *, task_name: str, catalog_no: str = "",
                   description: str = "", decision_mode: str = "ai") -> TaskGenerationJob:
        job = TaskGenerationJob(
            job_id=uuid4().hex,
            task_name=task_name,
            catalog_no=catalog_no,
            description=description,
            decision_mode=decision_mode,
            steps=[
                TaskGenerationStep(key=k, label=label)
                for k, label in DEFAULT_GENERATION_STEPS
            ],
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return deepcopy(job)

    def get_job(self, job_id: str) -> Optional[TaskGenerationJob]:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def list_jobs(self) -> list[TaskGenerationJob]:
        with self._lock:
            return [deepcopy(j) for j in self._jobs.values()]

    def _advance(self, job_id: str, step_key: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            target_idx = next(
                (i for i, s in enumerate(job.steps) if s.key == step_key), None
            )
            if target_idx is None:
                return
            job.status = "running"
            job.current_step = step_key
            job.message = message
            for idx, step in enumerate(job.steps):
                if step.status == "failed":
                    continue
                if idx < target_idx:
                    step.status = "completed"
                elif idx == target_idx:
                    step.status = "running"
                    step.message = message
                elif step.status != "pending":
                    step.status = "pending"
                    step.message = ""

    def _complete(self, job_id: str, *, criteria_path: str,
                  watch_id: int, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "completed"
            job.current_step = None
            job.message = message
            job.error = None
            job.criteria_path = criteria_path
            job.watch_id = watch_id
            for step in job.steps:
                if step.status != "failed":
                    step.status = "completed"

    def _fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "failed"
            job.error = error
            job.message = error
            failed_step = job.current_step
            if failed_step:
                step = next((s for s in job.steps if s.key == failed_step), None)
                if step:
                    step.status = "failed"
                    step.message = error

    def track(self, coro, job_id: str) -> threading.Thread:
        """Spawn a daemon thread that runs the coroutine via asyncio.run()."""
        def _runner() -> None:
            try:
                asyncio.run(coro)
            except Exception as exc:
                self._fail(job_id, f"{type(exc).__name__}: {exc}")
        thread = threading.Thread(target=_runner, daemon=True, name=f"task-gen-{job_id[:8]}")
        thread.start()
        return thread

    async def run_ai_generation_job(
        self,
        *,
        job_id: str,
        reference_file_path: str,
        db_path: str,
    ) -> None:
        """Coroutine body: drives the 6 steps, persists file + watchlist row."""
        job = self.get_job(job_id)
        if not job:
            return
        output_filename = build_criteria_filename(job.catalog_no or job.task_name,
                                                  prompts_dir=self.prompts_dir)
        try:
            self._advance(job_id, "prepare", "已接收请求，开始准备分析标准。")

            async def _progress(step_key: str, message: str) -> None:
                self._advance(job_id, step_key, message)

            criteria_text = await _prompt_utils_module.generate_criteria(
                user_description=job.description or "",
                reference_file_path=reference_file_path,
                progress_callback=_progress,
            )

            self._advance(job_id, "persist", f"正在保存分析标准到 {output_filename}。")
            os.makedirs(self.prompts_dir, exist_ok=True)
            with open(output_filename, "w", encoding="utf-8") as fh:
                fh.write(criteria_text)

            self._advance(job_id, "task", "分析标准已生成，正在创建任务记录。")
            init_db(db_path)
            watch = WatchItem(
                catalog_no=job.catalog_no or job.task_name,
                artist=job.task_name,
                decision_mode=job.decision_mode or "ai",
                description=job.description or "",
                ai_prompt_base_file="prompts/base_prompt.txt",
                ai_prompt_criteria_file=output_filename,
            )
            watch_id = add_watch(db_path, watch)
            self._complete(
                job_id,
                criteria_path=output_filename,
                watch_id=watch_id,
                message=f"任务「{job.task_name}」创建完成。",
            )
        except Exception as exc:
            if os.path.exists(output_filename):
                try:
                    os.remove(output_filename)
                except OSError:
                    pass
            self._fail(job_id, f"AI 任务生成失败: {exc}")


__all__ = [
    "DEFAULT_GENERATION_STEPS",
    "TaskGenerationJob",
    "TaskGenerationService",
    "TaskGenerationStep",
    "build_criteria_filename",
]
