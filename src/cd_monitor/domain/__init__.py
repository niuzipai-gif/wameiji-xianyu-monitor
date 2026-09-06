"""Domain layer: Pydantic models and core business types.

Distinct from `cd_monitor.core.models` (dataclasses used by storage/CLI):
domain models here are the *validated, structured* shapes used by the API
and the scheduler. They are independent of storage details.
"""
from cd_monitor.domain.models.task import (
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)

__all__ = ["Task", "TaskCreate", "TaskUpdate", "TaskStatus"]
