"""P5.1+ API endpoints: cron scheduling, failure guard, price history.

Mounted by web_server.py when the request handler sees one of the new
routes. Kept as a separate module so the legacy web_server.py stays small
and the new code is easy to test.

All routes are async-friendly at the data layer but the handler is sync
(we run the scheduler in the background, not in the request thread).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Optional

from cd_monitor.core.cron_utils import validate_cron_expression
from cd_monitor.services.failure_guard import FailureGuard
from cd_monitor.services.price_history_service import PriceHistoryService
from cd_monitor.services.scheduler_service import SchedulerService, TaskRunner
from cd_monitor.services.task_repository import TaskRepository


def _route_int(route: str, prefix: str) -> Optional[int]:
    tail = route[len(prefix):]
    if not tail or "/" in tail:
        return None
    try:
        return int(tail)
    except ValueError:
        return None


def _task_id_from_route(route: str) -> Optional[int]:
    """Extract the int id from /api/tasks/{id}/{action}."""
    prefix = "/api/tasks/"
    if not route.startswith(prefix):
        return None
    rest = route[len(prefix):]
    parts = rest.split("/")
    if not parts or not parts[0]:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


class P51Router:
    """Holds shared service instances and dispatches the new routes."""

    def __init__(
        self,
        db_path: Path,
        guard: Optional[FailureGuard] = None,
        history: Optional[PriceHistoryService] = None,
        scheduler: Optional[SchedulerService] = None,
    ) -> None:
        self.db_path = db_path
        self.guard = guard or FailureGuard(db_path)
        self.history = history or PriceHistoryService(db_path)
        self.scheduler = scheduler
        self.runner = TaskRunner(db_path, guard=self.guard, history=self.history)
        self.repo = TaskRepository(db_path)

    # --- GET routes ---

    def handle_get(self, route: str, respond: Callable[[Any, Optional[HTTPStatus]], None]) -> bool:
        if route == "/api/tasks/paused":
            respond({"items": self.guard.get_all_paused()})
            return True
        if route == "/api/price-history":
            respond({"items": self.history.get_all_trends()})
            return True
        if route == "/api/price-history/catalog":
            # /api/price-history/catalog?catalog_no=XXX&limit=50
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(route)  # route here is just the path; handler will pass full path
            return False
        if route.startswith("/api/price-history/"):
            catalog = route[len("/api/price-history/"):]
            if not catalog or "/" in catalog:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            trend = self.history.get_trend(catalog)
            recent = self.history.get_recent(catalog, limit=50)
            respond({"trend": trend.to_dict() if trend else None, "recent": recent})
            return True
        if route.startswith("/api/tasks/") and route.endswith("/failures"):
            tid = _task_id_from_route(route)
            if tid is None:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            respond({"items": self.guard.get_recent_failures(tid, limit=50)})
            return True
        if route.startswith("/api/tasks/") and route.endswith("/prompt-preview"):
            tid = _task_id_from_route(route)
            if tid is None:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            task = self.repo.get_task(tid)
            if task is None:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            from cd_monitor.services.prompt_composer import PromptContext, compose_for_task
            ctx = PromptContext(
                catalog_no=str(task.get("catalog_no") or ""),
                platform=str(task.get("platform") or "both"),
                required_keywords=list(task.get("required_keywords") or []),
                excluded_keywords=list(task.get("excluded_keywords") or []),
            )
            composed = compose_for_task(task, ctx, project_root=".")
            respond(composed.to_dict())
            return True
        return False

    # --- POST routes ---

    def handle_post(
        self,
        route: str,
        payload: dict,
        respond: Callable[[Any, Optional[HTTPStatus]], None],
    ) -> bool:
        if route == "/api/scheduler/reload":
            count = asyncio.run(self._reload())
            respond({"loaded": count})
            return True
        if route.startswith("/api/tasks/") and route.endswith("/schedule"):
            tid = _task_id_from_route(route)
            if tid is None:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            cron = payload.get("cron")
            if cron is not None and str(cron).strip() != "":
                try:
                    normalized = validate_cron_expression(str(cron))
                except ValueError as exc:
                    respond({"error": "invalid_cron", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return True
            else:
                normalized = None
            self.repo.set_cron(tid, normalized)
            asyncio.run(self._reload())
            respond({"task_id": tid, "cron": normalized})
            return True
        if route.startswith("/api/tasks/") and route.endswith("/run-now"):
            tid = _task_id_from_route(route)
            if tid is None:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            result = asyncio.run(self.runner.run_now(tid))
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            if result.get("error") == "task_paused":
                status = HTTPStatus.LOCKED
            respond(result, status=status)
            return True
        if route.startswith("/api/tasks/") and route.endswith("/clear-pause"):
            tid = _task_id_from_route(route)
            if tid is None:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            cleared = self.guard.clear_pause(tid)
            respond({"task_id": tid, "cleared": cleared})
            return True
        if route.startswith("/api/tasks/") and route.endswith("/prompt-files"):
            tid = _task_id_from_route(route)
            if tid is None:
                respond({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return True
            base = payload.get("ai_prompt_base_file")
            crit = payload.get("ai_prompt_criteria_file")
            self.repo.set_prompt_files(
                tid,
                str(base) if base is not None else None,
                str(crit) if crit is not None else None,
            )
            respond({"task_id": tid, "ai_prompt_base_file": base, "ai_prompt_criteria_file": crit})
            return True
        if route == "/api/price-history/snapshot":
            from cd_monitor.services.price_history_service import PriceSnapshot
            try:
                snap = PriceSnapshot(
                    catalog_no=str(payload.get("catalog_no") or "").strip(),
                    source=str(payload.get("source") or "manual"),
                    platform=str(payload.get("platform") or "both"),
                    price=float(payload.get("price") or 0.0),
                    currency=str(payload.get("currency") or "CNY"),
                    price_cny=float(payload["price_cny"]) if payload.get("price_cny") is not None else None,
                    sample_count=int(payload.get("sample_count") or 0),
                    decision=payload.get("decision"),
                    opportunity_id=int(payload["opportunity_id"]) if payload.get("opportunity_id") is not None else None,
                    task_id=int(payload["task_id"]) if payload.get("task_id") is not None else None,
                    run_id=int(payload["run_id"]) if payload.get("run_id") is not None else None,
                )
            except (KeyError, TypeError, ValueError) as exc:
                respond({"error": "invalid_payload", "message": repr(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            sid = self.history.record_snapshot(snap)
            respond({"id": sid, "catalog_no": snap.catalog_no})
            return True
        return False

    async def _reload(self) -> int:
        if self.scheduler is None:
            return 0
        return await self.scheduler.reload_jobs()
