"""Pydantic models for the Task (formerly watchlist) domain.

The Task is the unit of work the scheduler runs. A Task watches a catalog
on 闲鱼 / 挖煤姬, optionally on a cron schedule, and has its own AI prompt.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskStatus(str, Enum):
    """Runtime status of a task. Persisted in the DB as TEXT."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"  # auto-paused by FailureGuard
    SCHEDULED = "scheduled"  # has a cron, waiting for next run


class TaskPlatform(str, Enum):
    BOTH = "both"
    XIANYU = "xianyu"
    WAMEIJI = "wameiji"


class TaskBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    catalog_no: str = Field(min_length=1, max_length=64)
    jan: Optional[str] = Field(default=None, max_length=32)
    artist: Optional[str] = Field(default=None, max_length=128)
    title_jp: Optional[str] = Field(default=None, max_length=256)
    title_cn: Optional[str] = Field(default=None, max_length=256)
    edition: Optional[str] = Field(default=None, max_length=128)

    required_keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)

    priority: int = Field(default=1, ge=0, le=10)
    enabled: bool = True
    scan_interval_minutes: int = Field(default=60, ge=1, le=24 * 60)
    expected_holding_days: int = Field(default=30, ge=1, le=365)
    min_margin: float = Field(default=0.30, ge=0.0, le=1.0)
    min_diff: float = Field(default=1500.0, ge=0.0)
    notify_channel: str = "none"
    platform: TaskPlatform = TaskPlatform.BOTH

    # --- New fields (P5.1+): cron + per-task prompt ---
    cron: Optional[str] = Field(
        default=None,
        description=(
            "5- or 6-field cron expression. Supports aliases: "
            "@hourly @daily @weekly @monthly @yearly. None = manual only."
        ),
    )
    ai_prompt_base_file: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Path to base AI prompt template. Falls back to global default.",
    )
    ai_prompt_criteria_file: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Path to task-specific criteria text appended after the base.",
    )

    @field_validator("catalog_no")
    @classmethod
    def _catalog_no_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("catalog_no must not be empty")
        return v.strip()

    @field_validator("required_keywords", "excluded_keywords", mode="before")
    @classmethod
    def _split_keywords(cls, v):
        """Accept comma/newline separated strings, dedupe, lowercase-stable."""
        if v is None:
            return []
        if isinstance(v, str):
            import re
            parts = re.split(r"[\n,]+", v)
        elif isinstance(v, (list, tuple, set)):
            parts = list(v)
        else:
            parts = [v]
        out, seen = [], set()
        for raw in parts:
            t = str(raw).strip()
            if not t:
                continue
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
        return out

    @model_validator(mode="after")
    def _no_keyword_overlap(self) -> "TaskBase":
        req = {k.lower() for k in self.required_keywords}
        exc = {k.lower() for k in self.excluded_keywords}
        if req & exc:
            raise ValueError(
                "required_keywords and excluded_keywords must not overlap: "
                + ", ".join(sorted(req & exc))
            )
        return self


class Task(TaskBase):
    """Full Task row, as returned from storage."""

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_running: bool = False
    failure_count: int = 0
    paused_until: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    """Partial update; only set fields are applied."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    catalog_no: Optional[str] = Field(default=None, min_length=1, max_length=64)
    jan: Optional[str] = None
    artist: Optional[str] = None
    title_jp: Optional[str] = None
    title_cn: Optional[str] = None
    edition: Optional[str] = None
    required_keywords: Optional[list[str]] = None
    excluded_keywords: Optional[list[str]] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10)
    enabled: Optional[bool] = None
    scan_interval_minutes: Optional[int] = Field(default=None, ge=1, le=24 * 60)
    expected_holding_days: Optional[int] = Field(default=None, ge=1, le=365)
    min_margin: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    min_diff: Optional[float] = Field(default=None, ge=0.0)
    notify_channel: Optional[str] = None
    platform: Optional[TaskPlatform] = None
    cron: Optional[str] = None
    ai_prompt_base_file: Optional[str] = None
    ai_prompt_criteria_file: Optional[str] = None

    def to_db_dict(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for k, v in self.model_dump(exclude_unset=True).items():
            if isinstance(v, TaskPlatform):
                out[k] = v.value
            else:
                out[k] = v
        return out
