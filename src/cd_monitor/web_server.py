from __future__ import annotations



import json
import mimetypes
import os
import datetime
import pathlib
import re
import sqlite3

# ---------- SQLite UDF: title token-overlap (jaccard on char bigrams) ----------
def _register_title_overlap_udf(conn: sqlite3.Connection) -> None:
    import re as _re
    _bad = _re.compile(r"[\s\u3000\u300c\u300d\u300e\u300f\u3010\u3011\uff08\uff09\u3001\u3002\uff01\uff1f.,;:!?]+")
    def _bigrams(s):
        if not s:
            return set()
        s = _bad.sub("", str(s)).lower()
        if len(s) < 2:
            return {s} if s else set()
        return {s[i:i+2] for i in range(len(s) - 1)}
    def _overlap(a, b):
        A, B = _bigrams(a), _bigrams(b)
        if not A or not B:
            return 0.0
        return len(A & B) / len(A | B)
    conn.create_function("_title_overlap", 2, _overlap)

import base64
import gzip
import hashlib
import queue
import socket
import sys
import time
from hmac import compare_digest
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cd_monitor.config import load_config
from cd_monitor.services.failure_guard import FailureGuard
from cd_monitor.services.price_history_service import PriceHistoryService
from cd_monitor.services.result_blacklist_service import ResultBlacklistService
from cd_monitor.services.scheduler_service import SchedulerService
from cd_monitor.web_p51 import P51Router
from cd_monitor.core.models import WatchItem
from cd_monitor.notify.base import send_and_record
from cd_monitor.notify.bark import BarkClient
from cd_monitor.notify.dingtalk import DingTalkNotifier
from cd_monitor.notify.feishu import FeishuNotifier
from cd_monitor.review.backtest import backtest_database_history, backtest_snapshots
from cd_monitor.review.opportunity_report import render_opportunity_report
from cd_monitor.scheduler.recheck_candidate import next_recheck_time
from cd_monitor.services.doctor import run_doctor
import asyncio

from cd_monitor.services.imports import evaluate_files, evaluate_html_files, evaluate_html_texts, evaluate_json_files
from cd_monitor.services.live_capture_scan import capture_and_evaluate_live_html
from cd_monitor.services.wameiji_login_state import (
    WameijiLoginStateError,
    inspect_wameiji_login_state,
    save_wameiji_login_state,
)
from cd_monitor.services.xianyu_login_state import (
    XianyuLoginStateImportError,
    inspect_xianyu_login_state,
    save_xianyu_login_state,
)
from cd_monitor.services.account_repository import AccountError, AccountRepository
from cd_monitor.services.broadcast_bus import BroadcastBus
from cd_monitor.services.task_generate import (
    DEFAULT_GENERATION_STEPS,
    TaskGenerationService,
    build_criteria_filename,
)
from cd_monitor.services.watch_action_service import WatchActionService

from cd_monitor.services.live_scan import record_live_scan_status, scan_live_status
from cd_monitor.services.notify_dispatcher import notify_opportunities
from cd_monitor.services.reference_memory import (
    list_discovery_candidate_direction_evidence,
    list_reference_candidate_matches,
    list_reference_direction_summary,
    list_reference_market_observations,
    list_reference_product_profiles,
    reference_memory_status,
)
from cd_monitor.services.scan import scan_once_mock
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter
from cd_monitor.storage.sqlite import (
    ALLOWED_REVIEW_RESULTS,
    add_watch,
    delete_watch,
    disable_watch,
    enable_watch,
    get_opportunity,
    get_user_settings,
    init_db,
    insert_review_decision,
    insert_selection_preference_feedback,
    list_opportunities,
    list_candidate_rechecks,
    list_opportunity_ids,
    list_review_decisions,
    list_selection_preference_feedback,
    list_sent_alerts,
    list_watch,
    list_watch_all,
    schedule_candidate_recheck,
    search_opportunities,
    set_user_settings,
    update_candidate_recheck_status,
    update_watch,
    count_watch_using_catalog,
    complete_collector_command,
    create_collector_command,
    discovery_summary,
    delete_opportunities_for_catalog,
    delete_price_snapshots_for_catalog,
    fetch_watch_min,
    list_collector_commands,
    list_discovery_keywords,
    list_discovery_opportunities,
    list_discovery_pools,
    list_discovery_research_candidates,
    list_discovery_runs,
    selection_preference_feedback_status,
)


def _maybe_regenerate_criteria_on_update(
    db_path: str | Path,
    watch_id: int,
    payload: dict[str, Any],
    existing: dict[str, Any],
    update_result: bool,
    *,
    svc: WatchActionService | None = None,
) -> dict[str, object]:
    """Re-run AI criteria generation when an AI watch's description changed.

    Mirrors Usagi ai-goofish-monitor (PUT /api/tasks/{id} also regenerates
    criteria when description is mutated). Best-effort: any failure is
    captured in the returned dict so the HTTP layer can surface it without
    rolling back the watch update itself.

    Conditions (all must hold):
      * the update_watch() call succeeded
      * the new and old decision_mode are both "ai"
      * description is present in the payload AND differs from the row
      * ai_prompt_base_file resolves to a readable reference file

    Returns a summary dict with keys:
      regenerated: bool
      criteria_file: str | None   (absolute path)
      error: str | None
    """
    summary: dict[str, object] = {
        "regenerated": False,
        "criteria_file": None,
        "error": None,
    }
    if not update_result:
        return summary
    new_mode = payload.get("decision_mode") or existing.get("decision_mode") or "ai"
    if new_mode != "ai":
        return summary
    if "description" not in payload:
        return summary
    new_description = _optional_text(payload.get("description"))
    old_description = _optional_text(existing.get("description"))
    if new_description == old_description:
        return summary
    if not new_description:
        return summary
    # Only auto-regenerate when the previous description was already set.
    # When a keyword -> ai switch seeds the first description, treat that
    # as initial creation (not an edit) and skip the AI call.
    if not old_description:
        return summary
    reference = _optional_text(
        payload.get("ai_prompt_base_file")
        or existing.get("ai_prompt_base_file")
        or "prompts/macbook_criteria.txt"
    )
    if not Path(reference).exists():
        # Cannot run criteria generation without a reference; stay quiet
        # rather than erroring the whole PUT request.
        summary["error"] = f"reference_not_found: {reference}"
        return summary
    keyword = (
        _optional_text(payload.get("catalog_no"))
        or _optional_text(existing.get("catalog_no"))
        or "task"
    )
    # P0 #7: defer the actual AI call to WatchActionService so the PUT request
    # does not block on the LLM. The Web UI polls /api/watchlist/jobs/{job_id}
    # for the eventual criteria_file. When no service is supplied (e.g. CLI
    # paths that want the inline result), we still return the queued summary
    # so the caller can construct its own watcher.
    if svc is None:
        from cd_monitor.services.watch_action_service import (
            WatchActionService as _WAS,
        )
        svc = _get_watch_action_service(str(db_path))
    try:
        job = svc.submit_regenerate_criteria(int(watch_id))
    except Exception as exc:  # noqa: BLE001 - best-effort only
        summary["error"] = f"queue_failed: {exc!r}"
        return summary
    summary["queued"] = True
    summary["job_id"] = job.job_id
    return summary


def _resolve_watch_id_or_404(route: str):
    """Strip the prefix/suffix and return the int watch_id, or None."""
    tail = route[len("/api/watchlist/"):]
    tail = tail.removesuffix("/start")
    tail = tail.removesuffix("/stop")
    tail = tail.removesuffix("/regenerate-criteria")
    if not tail.isdigit():
        return None
    return int(tail)


def _hard_delete_with_cascade(
    db_path: str | Path,
    watch_id: int,
) -> tuple[bool, dict[str, int]]:
    """Delete a watch and cascade-clean orphaned catalog artifacts.

    Returns (deleted, cascade_summary). cascade_summary contains counts of
    opportunities, price_snapshots and log_files affected (best-effort: any
    cleanup error is swallowed so the watchlist delete itself is never
    blocked by cascading cleanup).
    """
    summary: dict[str, int] = {
        "opportunities_deleted": 0,
        "price_snapshots_deleted": 0,
        "log_files_deleted": 0,
    }
    pre = fetch_watch_min(db_path, watch_id)
    if pre is None:
        return False, summary
    catalog_no = pre["catalog_no"]
    try:
        if not delete_watch(db_path, watch_id):
            return False, summary
        remaining = count_watch_using_catalog(
            db_path, catalog_no, exclude_watch_id=watch_id
        )
        if remaining == 0:
            summary["opportunities_deleted"] = (
                delete_opportunities_for_catalog(db_path, catalog_no)
            )
            summary["price_snapshots_deleted"] = (
                delete_price_snapshots_for_catalog(db_path, catalog_no)
            )
        try:
            log_dir = Path(db_path).resolve().parent / "logs"
            stable = (catalog_no or "").strip()
            for candidate in (f"{watch_id}.log", f"{watch_id}_{stable}.log"):
                p = log_dir / candidate
                if p.exists():
                    p.unlink()
                    summary["log_files_deleted"] += 1
        except OSError:
            pass
        return True, summary
    except sqlite3.Error:
        return True, summary


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATIC_DIR = PROJECT_ROOT / "web"
DEFAULT_WAMEIJI = PROJECT_ROOT / "data/mock/wameiji_items.sample.json"
DEFAULT_XIANYU = PROJECT_ROOT / "data/mock/xianyu_samples.sample.json"


class BadJsonRequest(ValueError):
    pass


class BadIntegerField(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


def create_server(
    host: str,
    port: int,
    db_path: str | Path,
    static_dir: str | Path = DEFAULT_STATIC_DIR,
    xianyu_state_file: str | Path | None = None,
    wameiji_state_file: str | Path | None = None,
    account_dir: str | Path | None = None,
) -> ThreadingHTTPServer:
    init_db(db_path)
    handler = _build_handler(
        Path(db_path), Path(static_dir),
        xianyu_state_file=Path(xianyu_state_file) if xianyu_state_file else None,
        wameiji_state_file=Path(wameiji_state_file) if wameiji_state_file else None,
        account_dir=Path(account_dir) if account_dir else None,
    )
    return ThreadingHTTPServer((host, port), handler)


def run_web_server(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    static_dir: str | Path = DEFAULT_STATIC_DIR,
) -> None:
    # P5.5: reset is_running on every watchlist row at boot, mirroring
    # the Usagi lifespan shutdown hook (`process_service.stop_all()`).
    # A crashed prior run would otherwise leave tasks stuck in "running".
    try:
        from cd_monitor.storage.sqlite import reset_all_is_running
        n = reset_all_is_running(db_path)
        if n:
            print(f"cd-monitor boot: reset is_running on {n} watch rows")
    except Exception as _exc:
        print("reset_all_is_running failed:", _exc)
    server = create_server(host, port, db_path, static_dir)
    print(f"cd-monitor web listening on http://{host}:{port}")
    if os.environ.get("CD_MONITOR_SCHEDULER", "0") == "1":
        try:
            from cd_monitor.services.scheduler_service import SchedulerService
            _sched = SchedulerService(db_path)
            import asyncio as _asyncio
            _asyncio.get_event_loop().run_until_complete(_sched.reload_jobs())
            _sched.start()
            print("cd-monitor scheduler started")
        except Exception as _sched_exc:
            print("scheduler boot failed:", _sched_exc)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _build_handler(
    db_path: Path,
    static_dir: Path,
    xianyu_state_file: Path | None = None,
    wameiji_state_file: Path | None = None,
    account_dir: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    static_root = static_dir.resolve()
    # A hosted replica receives complete SQLite database uploads from the
    # collector.  Commands originate on the hosted dashboard, so keeping that
    # small queue in its own database prevents an upload from deleting a
    # pending command before the collector's next poll.  Local deployments do
    # not set this environment variable and retain the single-database layout.
    configured_command_db = os.environ.get("CD_COMMAND_DB_PATH", "").strip()
    command_db_path = (
        Path(configured_command_db).expanduser().resolve()
        if configured_command_db
        else db_path
    )
    if command_db_path != db_path:
        init_db(command_db_path)
    # Keep scraper sidecars and helper scripts beside the checked-out data
    # directory on Windows while retaining the Docker ``/app/data`` layout.
    # ``CD_DATA_DIR`` is an explicit escape hatch for deployments that mount
    # data elsewhere; otherwise the project-local data directory is used.
    runtime_data_dir = Path(
        os.environ.get("CD_DATA_DIR") or (PROJECT_ROOT / "data")
    ).expanduser().resolve()
    runtime_data_dir.mkdir(parents=True, exist_ok=True)
    cycle_script = runtime_data_dir / "run_one_cycle.py"
    python_executable = sys.executable

    _p51_guard = FailureGuard(db_path)
    _p51_history = PriceHistoryService(db_path)
    _p51_scheduler = None  # type: SchedulerService | None
    try:
        _p51_scheduler = SchedulerService(db_path, runner=None)
    except Exception as _p51_init_exc:  # pragma: no cover
        print("scheduler init failed:", _p51_init_exc)
    _p51_router = P51Router(
        db_path=db_path,
        guard=_p51_guard,
        history=_p51_history,
        scheduler=_p51_scheduler,
    )
    if _p51_scheduler is not None:
        _p51_scheduler.runner = _p51_router.runner
    # P5.4+: in-process registry for AI task-generation jobs. Mirrors
    # Usagi `src/services/task_generation_service.py::TaskGenerationService`
    # but stays inside Layer A (stdlib + asyncio) instead of Layer B (FastAPI/Pydantic).
    _task_generation_service = TaskGenerationService(prompts_dir="prompts")


    class MonitorRequestHandler(BaseHTTPRequestHandler):
        server_version = "CDMonitorWeb/0.1"

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._do_GET_impl()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                # Client hung up before the response was fully flushed.
                # Drop silently; otherwise do_GET would fall through and
                # emit a stray fallback body on the now-dead socket.
                return
            return

        def _do_GET_impl(self) -> None:
            route = urlparse(self.path).path
            # WebSocket short-circuit (P0 #11). The handshake lives in
            # _serve_websocket at module level so the request handler
            # class stays a thin facade.
            if route == "/ws" and "websocket" in str(self.headers.get("Upgrade", "")).lower():
                _serve_websocket(self, db_path)
                return
            # Render's health probe cannot attach the user access token. Keep
            # this endpoint deliberately unauthenticated and return no data
            # beyond process/database liveness; all other API routes remain
            # protected by WEB_ACCESS_TOKEN below.
            if route == "/api/health":
                self._json({"ok": True, "database": str(db_path), "static_dir": str(static_root)})
                return
            if route == "/api/discovery/collector/commands":
                if not self._collector_authorized():
                    self._json({"error": "sync_unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                    return
                self._json(
                    {
                        "items": list_collector_commands(
                            command_db_path,
                            statuses=("pending", "accepted", "running"),
                            limit=100,
                        )
                    }
                )
                return
            if not self._request_authorized():
                self._json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if route.startswith("/api/tasks/") or route.startswith("/api/price-history"):
                handled = _p51_router.handle_get(route, self._json)
                if handled:
                    return

                self._json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if route == "/api/scrape/full-status":
                _payload = {"running": False}
                try:
                    import json as _jsfs
                    _full_status_path = runtime_data_dir / "_full_cycle.status.json"
                    if _full_status_path.exists():
                        _payload = _jsfs.loads(_full_status_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
                self._json(_payload)
                return
            if route == "/api/scrape/match-status":
                _mstatus_path = str(db_path) + ".match_status.json"
                _payload = {"running": False}
                try:
                    if os.path.exists(_mstatus_path):
                        import json as _json3
                        _payload = _json3.loads(open(_mstatus_path, "r", encoding="utf-8").read())
                except Exception:
                    pass
                self._json(_payload)
                return
            if route in ("/api/scrape/status", "/api/scraper-status"):
                _route = route  # marker
                # Read heartbeat files written by the scraper daemon and the
                # most recent manual / scheduled cycle.
                import json as _json2
                import os as _os2
                def _read_hb(p):
                    try:
                        return _json2.loads(_os2.read(_os2.fdopen(_os2.open(p, _os2.O_RDONLY)))) if False else None
                    except Exception:
                        return None
                def _safe_read(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            return _json2.loads(f.read())
                    except Exception:
                        return None
                hb_daemon = _safe_read(runtime_data_dir / "_scraper_daemon.log.read")  # not used
                # Use log tail to determine last cycle
                def _tail_log(p, n=20):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                        return lines[-n:]
                    except Exception:
                        return []
                daemon_lines = _tail_log(runtime_data_dir / "_scraper_daemon.log", 10)
                cycle_lines = _tail_log(runtime_data_dir / "_one_cycle.log", 10)
                mall_lines = _tail_log(runtime_data_dir / "_mall_heartbeat.json", 10)
                hb_daemon = _safe_read(runtime_data_dir / "_scraper_heartbeat.json")
                hb_mall = _safe_read(runtime_data_dir / "_mall_heartbeat.json")
                # Last-scrape stats from DB
                try:
                    with sqlite3.connect(db_path, timeout=10) as conn:
                        cnt = conn.execute("SELECT COUNT(*) FROM market_items WHERE source='wameiji' AND fetched_at >= datetime('now','-1 hour')").fetchone()[0]
                        last_fetch = conn.execute("SELECT MAX(fetched_at) FROM market_items WHERE source='wameiji'").fetchone()[0]
                except Exception:
                    cnt = -1
                    last_fetch = None
                # Frontend expects legacy "scraper-status" shape: {status, last_run, count}
                legacy_status = "not_running"
                if hb_daemon:
                    legacy_status = hb_daemon.get("status", "idle")
                elif hb_mall:
                    legacy_status = hb_mall.get("status", "idle")
                legacy_count = (hb_daemon or {}).get("count") or 0
                legacy_last_run = (hb_daemon or {}).get("last_run") or ""
                payload = {
                    "daemon_log": daemon_lines,
                    "cycle_log": cycle_lines,
                    "mall_heartbeat": hb_mall,
                    "daemon_heartbeat": hb_daemon,
                    "new_in_last_hour": cnt,
                    "last_wameiji_fetch": last_fetch,
                    "status": legacy_status,
                    "last_run": legacy_last_run,
                    "count": legacy_count,
                    "msg": (hb_daemon or {}).get("msg", ""),
                }
                self._json(payload)
                return
            if route == "/api/summary":
                self._json(_summary(db_path))
                return
            if route == "/api/reference-memory/status":
                self._json(reference_memory_status(db_path))
                return
            if route == "/api/reference-memory/directions":
                try:
                    self._json(list_reference_direction_summary(db_path))
                except (TypeError, ValueError, sqlite3.Error):
                    self._json(
                        {"error": "reference_directions_unavailable"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                return
            if route == "/api/reference-memory/candidate-directions":
                raw_limit = _query_param(urlparse(self.path).query, "limit")
                if raw_limit is None:
                    reference_limit = 100
                elif re.fullmatch(r"[0-9]{1,3}", raw_limit) is None:
                    self._json({"error": "invalid_limit"}, status=HTTPStatus.BAD_REQUEST)
                    return
                else:
                    reference_limit = int(raw_limit)
                    if not 1 <= reference_limit <= 200:
                        self._json({"error": "invalid_limit"}, status=HTTPStatus.BAD_REQUEST)
                        return
                try:
                    items = list_discovery_candidate_direction_evidence(
                        db_path, limit=reference_limit
                    )
                except (TypeError, ValueError, sqlite3.Error):
                    self._json(
                        {"error": "reference_candidate_directions_unavailable"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._json({"items": items})
                return
            if route == "/api/reference-memory/profiles":
                raw_limit = _query_param(urlparse(self.path).query, "limit")
                if raw_limit is None:
                    reference_limit = 100
                elif re.fullmatch(r"[0-9]{1,3}", raw_limit) is None:
                    self._json({"error": "invalid_limit"}, status=HTTPStatus.BAD_REQUEST)
                    return
                else:
                    reference_limit = int(raw_limit)
                    if not 1 <= reference_limit <= 200:
                        self._json({"error": "invalid_limit"}, status=HTTPStatus.BAD_REQUEST)
                        return
                try:
                    items = list_reference_product_profiles(db_path, limit=reference_limit)
                except (ValueError, sqlite3.Error):
                    self._json({"error": "reference_profiles_unavailable"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._json({"items": items})
                return
            if route == "/api/reference-memory/matches":
                raw_limit = _query_param(urlparse(self.path).query, "limit")
                try:
                    reference_limit = int(raw_limit) if raw_limit is not None else 100
                    items = list_reference_candidate_matches(db_path, limit=reference_limit)
                except (TypeError, ValueError):
                    self._json(
                        {"error": "invalid_limit"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                self._json({"items": items})
                return
            if route == "/api/reference-memory/observations":
                raw_limit = _query_param(urlparse(self.path).query, "limit")
                try:
                    reference_limit = int(raw_limit) if raw_limit is not None else 100
                    items = list_reference_market_observations(db_path, limit=reference_limit)
                except (TypeError, ValueError):
                    self._json(
                        {"error": "invalid_limit"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                self._json({"items": items})
                return
            if route == "/api/selection-feedback/status":
                try:
                    self._json(selection_preference_feedback_status(db_path))
                except (ValueError, sqlite3.Error):
                    self._json(
                        {"error": "selection_feedback_unavailable"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                return
            if route == "/api/selection-feedback":
                raw_query = urlparse(self.path).query
                raw_limit = _query_param(raw_query, "limit")
                raw_current = _query_param(raw_query, "current")
                raw_candidate_id = _query_param(raw_query, "candidate_id")
                if raw_limit is not None and re.fullmatch(r"[0-9]{1,3}", raw_limit) is None:
                    self._json({"error": "invalid_selection_feedback"}, status=HTTPStatus.BAD_REQUEST)
                    return
                feedback_limit = int(raw_limit) if raw_limit is not None else 100
                if raw_current not in (None, "0", "1"):
                    self._json({"error": "invalid_selection_feedback"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if raw_candidate_id is not None and re.fullmatch(r"[1-9][0-9]*", raw_candidate_id) is None:
                    self._json({"error": "invalid_selection_feedback"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    items = list_selection_preference_feedback(
                        db_path,
                        candidate_id=int(raw_candidate_id) if raw_candidate_id is not None else None,
                        limit=feedback_limit,
                        current_only=raw_current == "1",
                    )
                except ValueError:
                    self._json({"error": "invalid_selection_feedback"}, status=HTTPStatus.BAD_REQUEST)
                    return
                except sqlite3.Error:
                    self._json(
                        {"error": "selection_feedback_unavailable"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._json({"items": items})
                return
            if route == "/api/discovery/board":
                self._json(
                    {
                        "summary": discovery_summary(db_path),
                        "pools": _discovery_pool_views(db_path),
                        "opportunities": _discovery_opportunity_views(db_path),
                        "research_candidates": _discovery_research_candidate_views(db_path),
                        "runs": list_discovery_runs(db_path, limit=30),
                    }
                )
                return
            if route == "/api/discovery/pools":
                self._json({"items": _discovery_pool_views(db_path)})
                return
            if route == "/api/discovery/commands":
                self._json({"items": list_collector_commands(command_db_path, limit=100)})
                return
            if route == "/api/opportunities":
                parsed_q = urlparse(self.path).query
                opp_limit = _safe_int(_query_param(parsed_q, "limit"), 80)
                opp_limit = max(1, min(opp_limit, 200))
                opp_offset = _safe_int(_query_param(parsed_q, "offset"), 0)
                opp_offset = max(0, opp_offset)
                opp_random = bool(_safe_int(_query_param(parsed_q, "random"), 0)) or bool(_query_param(parsed_q, "seed"))
                payload = _opportunities_paged(db_path, opp_limit, opp_offset, opp_random)
                self._json(payload)
                return
            if route.startswith("/api/opportunities/"):
                opportunity_id = _route_int(route, "/api/opportunities/")
                if opportunity_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                detail = _opportunity_detail(db_path, opportunity_id)
                if detail is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._json(detail)
                return
            if route.startswith("/api/accounts/") and route.endswith("/delete"):
                self._handle_accounts_delete(route)
                return
            if route == "/api/user-settings":
                self._json({"items": get_user_settings(db_path)})
                return
            if route == "/api/settings/notifications":
                # P0 #5: return the notification channel config. Reads the
                # bark_url from user_settings (UI override) and the
                # feishu/dingtalk webhook URLs from the live config.
                self._json(_notification_settings_view(db_path))
                return
            if route == "/api/settings/ai":
                # P0 #6: return the AI provider config. The api_key is
                # masked (only its presence is exposed).
                self._json(_ai_settings_view(db_path))
                return
            if route == "/api/search":
                q = _query_param(urlparse(self.path).query, "q") or ""
                self._json({"items": search_opportunities(db_path, q, limit=80)})
                return
            if route == "/api/watchlist/generate":
                jobs = [j.to_dict() for j in _task_generation_service.list_jobs()]
                self._json({"jobs": jobs, "step_specs": list(DEFAULT_GENERATION_STEPS)})
                return
            if route.startswith("/api/watchlist/generate/"):
                tail = route[len("/api/watchlist/generate/"):]
                job_id = tail.strip("/")
                if not job_id:
                    self._json({"error": "job_id_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                job = _task_generation_service.get_job(job_id)
                if job is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._json(job.to_dict())
                return
            # Round-3 P0 #3 + #9: manual action jobs (start / regenerate-criteria).
            # Mirrors the existing /generate/{job_id} GET but reads from
            # WatchActionService so manual-run jobs are pollable.
            if route.startswith("/api/watchlist/jobs/") and not route.endswith("/cancel"):
                _job_id = route[len("/api/watchlist/jobs/"):].strip("/")
                if not _job_id:
                    self._json({"error": "job_id_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                _wasvc = _get_watch_action_service(str(db_path))
                _job = _wasvc.get_job(_job_id)
                if _job is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._json(_job.to_dict())
                return
            if route == "/api/watchlist/all":
                self._json({"items": _watchlist_all(db_path)})
                return
            if route == "/api/watchlist":
                self._json({"items": _watchlist(db_path)})
                return
            if route == "/api/search-runs":
                self._json({"items": _search_runs(db_path)})
                return
            if route == "/api/rechecks":
                self._json({"items": list_candidate_rechecks(db_path)})
                return
            if route == "/api/review/options":
                self._json(_review_options())
                return
            if route == "/api/reviews":
                self._json({"items": _table_rows(db_path, "review_decisions", "id DESC", 30)})
                return
            if route == "/api/alerts":
                self._json({"items": _table_rows(db_path, "sent_alerts", "id DESC", 30)})
                return
            if route == "/api/doctor":
                config = load_config(None)
                self._json(run_doctor(config, db_path))
                return
            if route == "/api/config":
                self._json(_public_config(load_config(None), db_path))
                return
            if route == "/api/browser/status":
                self._json(_browser_status(load_config(None)))
                return
            if route == "/api/login-state/health":
                # Aggregate cookie health for xianyu + wameiji states.
                # Tells the frontend whether any cookie expires within 7 days.
                import time as _time
                cfg = load_config(None)
                results = {}
                for side in ("wameiji", "xianyu"):
                    state_file = (
                        str(getattr(self, "_" + side + "_state_file_override", None) or "")
                        or (getattr(cfg.browser, side + "_state_file", None) or "data/" + side + "_state.json")
                    )
                    summary = {
                        "status": "not_configured",
                        "state_file": state_file,
                        "cookies_total": 0,
                        "expiring_7d": 0,
                        "expired": 0,
                        "oldest_expires_iso": None,
                        "newest_expires_iso": None,
                        "captured_at": None,
                        "error_message": None,
                    }
                    p = pathlib.Path(state_file)
                    if not p.exists():
                        summary["status"] = "missing"
                    else:
                        try:
                            import json as _json
                            raw = _json.loads(p.read_text(encoding="utf-8"))
                            payload = raw.get("playwright_storage_state") if isinstance(raw, dict) else None
                            cookies = (payload or {}).get("cookies") if isinstance(payload, dict) else None
                            if cookies is None:
                                cookies = raw.get("cookies") or [] if isinstance(raw, dict) else []
                            now = _time.time()
                            expires = []
                            for c in cookies:
                                if not isinstance(c, dict):
                                    continue
                                exp = c.get("expires")
                                if isinstance(exp, (int, float)) and exp > 0:
                                    expires.append(float(exp))
                            summary["cookies_total"] = len(cookies)
                            summary["expired"] = sum(1 for e in expires if e < now)
                            summary["expiring_7d"] = sum(1 for e in expires if now <= e < now + 7 * 86400)
                            if expires:
                                summary["oldest_expires_iso"] = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(min(expires)))
                                summary["newest_expires_iso"] = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(max(expires)))
                            summary["captured_at"] = raw.get("captured_at") if isinstance(raw, dict) else None
                            summary["status"] = "ready" if cookies else "invalid"
                        except Exception as exc:
                            summary["status"] = "invalid"
                            summary["error_message"] = str(exc)[:200]
                    results[side] = summary
                self._json({"captured_at": _time.strftime("%Y-%m-%dT%H:%M:%S"), **results})
                return
            if route == "/api/login-state/wameiji":
                # Return inspection summary of the configured wameiji state file.
                cfg = load_config(None)
                state_file = (str(self._wameiji_state_file_override) if self._wameiji_state_file_override else (
                    cfg.browser.wameiji_state_file or "data/wameiji_state.json"
                ))
                self._json({
                    "state_file": state_file,
                    **inspect_wameiji_login_state(state_file),
                })
                return
            if route == "/api/login-state/xianyu":
                # Return inspection summary of the configured xianyu state file.
                cfg = load_config(None)
                state_file = (str(self._xianyu_state_file_override) if self._xianyu_state_file_override else (
                    cfg.browser.xianyu_state_file or "data/xianyu_state.json"
                ))
                self._json({
                    "state_file": state_file,
                    **inspect_xianyu_login_state(state_file),
                })
                return
            if route == "/api/accounts" or route.startswith("/api/accounts/"):
                self._handle_accounts_get(route)
                return
            if route.startswith("/api/watchlist/") and route.endswith("/blacklist-rules"):
                watch_id = _route_int(route.removesuffix("/blacklist-rules"), "/api/watchlist/")
                if watch_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                try:
                    rule = _blacklist_view(db_path, watch_id)
                except KeyError:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._json(rule)
                return
            if route.startswith("/api/"):
                self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._static(route)

        def do_DELETE(self) -> None:  # noqa: N802
            try:
                self._do_DELETE_impl()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                return
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            """Answer browser CORS preflight requests for a hosted UI.

            The collector remains closed to unlisted origins.  Set
            ``WEB_ALLOWED_ORIGINS`` to a comma-separated list (for example the
            GitHub Pages and Render origins) when the UI and API are deployed
            separately.
            """
            if not self._origin_allowed():
                self.send_response(HTTPStatus.FORBIDDEN)
                self.end_headers()
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._set_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _do_DELETE_impl(self) -> None:
            route = urlparse(self.path).path
            if not self._request_authorized():
                self._json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if not self._origin_allowed():
                self._json({"error": "cross_origin_forbidden"}, status=HTTPStatus.FORBIDDEN)
                return
            if route.startswith("/api/watchlist/") and route.endswith("/hard-delete"):
                watch_id = _route_int(route.removesuffix("/hard-delete"), "/api/watchlist/")
                if watch_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                deleted, cascade_summary = _hard_delete_with_cascade(db_path, watch_id)
                self._json(
                    {"deleted": deleted, "id": watch_id, "cascade": cascade_summary},
                )
                return
            if route == "/api/login-state/wameiji":
                cfg = load_config(None)
                state_file = (str(self._wameiji_state_file_override) if self._wameiji_state_file_override else (
                    cfg.browser.wameiji_state_file or "data/wameiji_state.json"
                ))
                p = Path(state_file)
                if p.exists():
                    try:
                        p.unlink()
                    except OSError as exc:
                        self._json({"error": "delete_failed", "detail": str(exc)},
                                   status=HTTPStatus.INTERNAL_SERVER_ERROR)
                        return
                    self._json({"deleted": True, "path": str(p)})
                    return
                self._json({"deleted": False, "path": str(p), "reason": "missing"})
                return
            if route == "/api/login-state/xianyu":
                cfg = load_config(None)
                state_file = (str(self._xianyu_state_file_override) if self._xianyu_state_file_override else (
                    cfg.browser.xianyu_state_file or "data/xianyu_state.json"
                ))
                p = Path(state_file)
                if p.exists():
                    try:
                        p.unlink()
                    except OSError as exc:
                        self._json({"error": "delete_failed", "detail": str(exc)},
                                   status=HTTPStatus.INTERNAL_SERVER_ERROR)
                        return
                    self._json({"deleted": True, "path": str(p)})
                    return
                self._json({"deleted": False, "path": str(p), "reason": "missing"})
                return
            if route.startswith("/api/accounts/"):
                self._handle_accounts_delete(route)
                return
            self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._do_POST_impl()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                return
            return

        def _do_POST_impl(self) -> None:
            route = urlparse(self.path).path
            # The local collector can replicate its SQLite source of truth to
            # the stateless Render API.  This route deliberately uses a
            # separate secret and bypasses browser-origin checks; it is meant
            # for the local publisher process, never the hosted UI.
            if route == "/api/sync/database":
                self._handle_database_sync()
                return
            if route.startswith("/api/discovery/collector/commands/"):
                if not self._collector_authorized():
                    self._json({"error": "sync_unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                    return
                try:
                    payload = self._read_json()
                except BadJsonRequest:
                    self._json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                    return
                suffix = "/complete"
                if not route.endswith(suffix):
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                command_id = _route_int(route.removesuffix(suffix), "/api/discovery/collector/commands/")
                if command_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                result = payload.get("result")
                if result is not None and not isinstance(result, dict):
                    self._json({"error": "result_must_be_object"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._json(
                        complete_collector_command(
                            command_db_path,
                            command_id,
                            result,
                            status=str(payload.get("status") or "completed"),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if not self._request_authorized():
                self._json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if route.startswith("/api/tasks/") or route == "/api/scheduler/reload" or route == "/api/price-history/snapshot":
                try:
                    payload = self._read_json()
                except BadJsonRequest:
                    self._json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                    return
                handled = _p51_router.handle_post(route, payload, self._json)
                if handled:
                    return

            route = urlparse(self.path).path
            if not self._request_authorized():
                self._json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            if not self._origin_allowed():
                self._json({"error": "cross_origin_forbidden"}, status=HTTPStatus.FORBIDDEN)
                return
            try:
                if route == "/api/watchlist":
                    payload = self._read_json()
                elif route.startswith("/api/watchlist/") and not route.endswith("/disable"):
                    payload = self._read_json()
                elif route not in {"/api/scan/watchlist"} and route.startswith("/api/"):
                    payload = self._read_json()
                else:
                    payload = {}
            except BadJsonRequest:
                self._json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._handle_post(route, payload)
            except BadIntegerField as exc:
                self._json(
                    {"error": "invalid_integer", "field": exc.field},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        def _handle_database_sync(self) -> None:
            token = os.getenv("CD_SYNC_TOKEN", "").strip()
            supplied = self.headers.get("X-CD-Sync-Token", "").strip()
            if not token or not supplied or not compare_digest(supplied, token):
                self._json({"error": "sync_unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                length = 0
            max_bytes = 64 * 1024 * 1024
            if length <= 0 or length > max_bytes:
                self._json({"error": "invalid_sync_size"}, status=HTTPStatus.BAD_REQUEST)
                return
            payload = self.rfile.read(length)
            if self.headers.get("Content-Encoding", "").lower() == "gzip":
                try:
                    payload = gzip.decompress(payload)
                except (OSError, EOFError) as exc:
                    self._json({"error": "invalid_sync_payload", "detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
            if len(payload) > max_bytes:
                self._json({"error": "invalid_sync_size"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not payload.startswith(b"SQLite format 3\x00"):
                self._json({"error": "invalid_sqlite_database"}, status=HTTPStatus.BAD_REQUEST)
                return
            tmp = db_path.with_name(db_path.name + ".sync.tmp")
            try:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(payload)
                conn = sqlite3.connect(tmp, timeout=10)
                try:
                    conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
                finally:
                    conn.close()
                # Copy through SQLite's backup API instead of replacing the
                # target file.  Windows can keep a short-lived handle to the
                # service database; backup works with that open handle and
                # keeps readers from seeing a half-written file.
                source_conn = sqlite3.connect(tmp, timeout=10)
                target_conn = sqlite3.connect(db_path, timeout=10)
                try:
                    source_conn.backup(target_conn, pages=100, sleep=0.1)
                    target_conn.commit()
                finally:
                    source_conn.close()
                    target_conn.close()
                tmp.unlink(missing_ok=True)
            except (OSError, sqlite3.Error) as exc:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                self._json({"error": "sync_write_failed", "detail": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._json({"ok": True, "bytes": len(payload)})

        # ---------- /api/accounts multi-account support ----------
        def _account_repo(self) -> AccountRepository:
            return AccountRepository(db_path, account_dir=Path(getattr(self, "_account_dir_override", None)) if getattr(self, "_account_dir_override", None) else None)

        def _handle_accounts_get(self, route: str) -> None:
            repo = self._account_repo()
            if route == "/api/accounts":
                self._json({"items": repo.list_all()})
                return
            tail = route[len("/api/accounts/"):]
            if tail.endswith("/status"):
                key = tail[:-len("/status")]
                # /api/accounts/{id}/status: numeric id wins,
                # fall back to name lookup across both platforms.
                if key.isdigit():
                    account = repo.get(int(key))
                else:
                    account = repo.find("xianyu", key) or repo.find("wameiji", key)
                if account is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._json({"account": account, "state": repo.state_status(account["id"])})
                return
            if tail.isdigit():
                account = repo.get(int(tail))
                if account is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                state = repo.read_state(account["id"])
                self._json({"account": account, "state": state})
                return
            self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return

        def _handle_accounts_post(self, route: str, payload: dict[str, Any]) -> None:
            repo = self._account_repo()
            payload = payload if isinstance(payload, dict) else {}
            if route == "/api/accounts":
                name = payload.get("name")
                platform = payload.get("platform")
                # Accept either pre-parsed state_payload or raw snapshot/content strings.
                state_payload = payload.get("state_payload")
                if state_payload is None:
                    snapshot_text = payload.get("snapshot") or payload.get("content")
                    if isinstance(snapshot_text, str) and snapshot_text.strip():
                        try:
                            state_payload = json.loads(snapshot_text)
                        except json.JSONDecodeError as exc:
                            self._json({"error": "invalid_json", "detail": str(exc)},
                                       status=HTTPStatus.BAD_REQUEST)
                            return
                if not name or not platform or not isinstance(state_payload, dict):
                    self._json({"error": "name_platform_payload_required"},
                               status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    account = repo.create(
                        name=str(name),
                        platform=str(platform),
                        state_payload=state_payload,
                        notes=payload.get("notes"),
                        enabled=bool(payload.get("enabled", True)),
                    )
                except AccountError as exc:
                    self._json({"error": "create_failed", "detail": str(exc)},
                               status=HTTPStatus.BAD_REQUEST)
                    return
                self._json({"account": account, "created": True})
                return
            # PUT-style update: /api/accounts/{id} with {"state_payload": {...}} or {"content": "..."}
            if route.startswith("/api/accounts/") and not route.endswith("/delete"):
                tail = route[len("/api/accounts/"):]
                if tail.isdigit():
                    account_id = int(tail)
                    existing = repo.get(account_id)
                    if existing is None:
                        self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                        return
                    updated = False
                    if "state_payload" in payload or "snapshot" in payload or "content" in payload:
                        state_payload = payload.get("state_payload")
                        if state_payload is None:
                            text = payload.get("snapshot") or payload.get("content")
                            if not isinstance(text, str) or not text.strip():
                                self._json({"error": "content_required"},
                                           status=HTTPStatus.BAD_REQUEST)
                                return
                            try:
                                state_payload = json.loads(text)
                            except json.JSONDecodeError as exc:
                                self._json({"error": "invalid_json", "detail": str(exc)},
                                           status=HTTPStatus.BAD_REQUEST)
                                return
                        try:
                            repo.update_content(account_id, state_payload)
                        except AccountError as exc:
                            self._json({"error": "update_failed", "detail": str(exc)},
                                       status=HTTPStatus.BAD_REQUEST)
                            return
                        updated = True
                    if "notes" in payload or "enabled" in payload:
                        repo.update_meta(
                            account_id,
                            notes=payload.get("notes"),
                            enabled=payload.get("enabled"),
                        )
                        updated = True
                    if not updated:
                        self._json({"error": "nothing_to_update"},
                                   status=HTTPStatus.BAD_REQUEST)
                        return
                    self._json({"account": repo.get(account_id), "updated": True})
                    return
            self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return

        def _handle_accounts_delete(self, route: str) -> None:
            repo = self._account_repo()
            tail = route[len("/api/accounts/"):].removesuffix("/delete")
            if not tail.isdigit():
                self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            account_id = int(tail)
            deleted = repo.delete(account_id)
            self._json({"deleted": deleted, "id": account_id})
            return

        def _handle_post(self, route: str, payload: dict[str, Any]) -> None:
            if route == "/api/discovery/commands":
                command_type = str(payload.get("command_type") or "").strip()
                if command_type not in {"scan_now", "set_pool", "set_keywords"}:
                    self._json({"error": "unsupported_command_type"}, status=HTTPStatus.BAD_REQUEST)
                    return
                command_payload = {
                    key: value
                    for key, value in payload.items()
                    if key != "command_type"
                }
                pool_id = command_payload.get("pool_id")
                dedupe_key = None
                if command_type == "scan_now":
                    dedupe_key = f"scan_now:{pool_id if pool_id is not None else 'all'}"
                command = create_collector_command(
                    command_db_path,
                    command_type,
                    command_payload,
                    dedupe_key=dedupe_key,
                )
                self._json(command, status=HTTPStatus.ACCEPTED)
                return
            if route == "/api/accounts" or route.startswith("/api/accounts/"):
                self._handle_accounts_post(route, payload)
                return
            # Login-state manual upload (xianyu / wameiji).
            # Mirrors the reference project pattern: the user pastes a Playwright
            # storage_state JSON (or a Chrome-extension snapshot) into the UI and
            # we validate + write it to disk. The output_path field is optional
            # and is used by tests to redirect writes to a tmp location.
            if route in ("/api/login-state/xianyu", "/api/login-state/wameiji"):
                platform = "xianyu" if route.endswith("/xianyu") else "wameiji"
                payload = payload if isinstance(payload, dict) else {}
                cfg = load_config(None)
                if platform == "xianyu":
                    default_path = (str(self._xianyu_state_file_override) if self._xianyu_state_file_override
                                    else (cfg.browser.xianyu_state_file or "data/xianyu_state.json"))
                else:
                    default_path = (str(self._wameiji_state_file_override) if self._wameiji_state_file_override
                                    else (cfg.browser.wameiji_state_file or "data/wameiji_state.json"))
                output_path = payload.get("output_path") or default_path
                # Two accepted input shapes:
                #   1. snapshot: Chrome-extension format (validated by save_*_login_state)
                #   2. content:  raw Playwright storage_state JSON
                if "snapshot" in payload:
                    snapshot_text = payload.get("snapshot")
                    if not isinstance(snapshot_text, str) or not snapshot_text.strip():
                        self._json({"error": "content_required"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    try:
                        if platform == "xianyu":
                            summary = save_xianyu_login_state(snapshot_text, output_path)
                        else:
                            summary = save_wameiji_login_state(snapshot_text, output_path)
                    except (XianyuLoginStateImportError, WameijiLoginStateError) as exc:
                        self._json({"error": "invalid_state", "detail": str(exc)},
                                   status=HTTPStatus.BAD_REQUEST)
                        return
                    self._json({
                        "saved": True,
                        "platform": platform,
                        "path": summary.get("output_path", output_path),
                        "cookie_count": int(summary.get("cookie_count", 0)),
                        "login_state_ready": bool(summary.get("login_state_ready", False)),
                    })
                    return
                # Fallback: raw storage_state JSON in `content`.
                content_text = payload.get("content")
                if not isinstance(content_text, str) or not content_text.strip():
                    self._json({"error": "content_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    parsed = json.loads(content_text)
                except json.JSONDecodeError as exc:
                    self._json({"error": "invalid_json", "detail": str(exc)},
                               status=HTTPStatus.BAD_REQUEST)
                    return
                # Normalize: flat shape -> wrapped.
                if isinstance(parsed, dict) and "playwright_storage_state" not in parsed and isinstance(parsed.get("cookies"), list):
                    parsed = {"playwright_storage_state": parsed}
                inner = parsed.get("playwright_storage_state", parsed) if isinstance(parsed, dict) else None
                cookies = inner.get("cookies") if isinstance(inner, dict) else None
                if not isinstance(cookies, list) or not cookies:
                    self._json({"error": "invalid_state_file",
                               "detail": "JSON must include a non-empty cookies list"},
                               status=HTTPStatus.BAD_REQUEST)
                    return
                state_path = Path(output_path)
                try:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    serialized = json.dumps(parsed, ensure_ascii=False, indent=2)
                    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
                    tmp_path.write_text(serialized, encoding="utf-8")
                    tmp_path.replace(state_path)
                except OSError as exc:
                    self._json({"error": "write_failed", "detail": str(exc)},
                               status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                login_state_ready = (
                    inspect_xianyu_login_state(state_path).get("status") == "ready"
                    if platform == "xianyu"
                    else True
                )
                self._json({
                    "saved": True,
                    "platform": platform,
                    "path": str(state_path),
                    "cookie_count": len(cookies),
                    "login_state_ready": login_state_ready,
                })
                return
            if route == "/api/user-settings":
                count = set_user_settings(db_path, {str(k): str(v) for k, v in payload.items() if v is not None})
                self._json({"saved": count})
                return
            if route == "/api/settings/notifications":
                # P0 #5: persist the notification channel config. Only the
                # bark_url is stored in user_settings; feishu/dingtalk
                # webhook URLs come from env / config and are read-only here.
                bark_url = _optional_text(payload.get("bark_url"))
                if bark_url is not None:
                    set_user_settings(db_path, {"bark_url": bark_url})
                self._json(_notification_settings_view(db_path))
                return
            if route == "/api/settings/notifications/test":
                # P0 #5: send a one-shot test push through the chosen channel.
                # dry_run defaults to True so the UI can preview the payload
                # without hitting Bark. Setting dry_run=False calls the real
                # endpoint.
                # P0 #5 inline test handler
                _test_channel = str(payload.get("channel") or "feishu").strip().lower()
                _test_dry_run = bool(payload.get("dry_run", True))
                _test_title = str(payload.get("title") or "Kuro test")
                _test_body = str(payload.get("body") or "Notification channel OK")
                try:
                    _test_cfg = load_config(None)
                    # P0 #5: pass the per-request bark_url so the factory
                    # does not have to guess which db holds the override.
                    _test_bark_url = _bark_url_from_settings(db_path)
                    _test_notifier = _notifier_for_channel(_test_channel, _test_cfg, bark_url=_test_bark_url)
                    if not hasattr(_test_notifier, "send_text"):
                        self._json({
                            "ok": False,
                            "channel": _test_channel,
                            "error": "live_send_not_supported_for_channel",
                            "dry_run": True,
                        }, status=HTTPStatus.BAD_REQUEST)
                        return
                    _test_result = _test_notifier.send_text(_test_title, _test_body, dry_run=_test_dry_run)
                    self._json({"ok": True, "channel": _test_channel, "dry_run": _test_dry_run, "result": _test_result})
                    return
                except ValueError as exc:
                    self._json({"ok": False, "channel": _test_channel, "error": str(exc)},
                               status=HTTPStatus.BAD_REQUEST)
                    return
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "channel": _test_channel, "error": f"{type(exc).__name__}:{exc}"},
                               status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
            if route == "/api/settings/ai":
                # P0 #6: persist the AI provider config. Only the editable
                # fields are stored; the live read merges env + override.
                count = _persist_ai_settings(db_path, payload)
                self._json({"saved": count, "view": _ai_settings_view(db_path)})
                return
            if route == "/api/settings/ai/test":
                # P0 #6: cheap configuration check. dry_run=True just reports
                # whether the provider is configured (no API call). live=True
                # would dispatch a tiny test generation; we keep it off by
                # default since the Web UI uses this for a status indicator.
                self._json(_ai_test_view(db_path, payload))
                return
            if route.startswith("/api/watchlist/") and route.endswith("/blacklist-rules"):
                # P0 #10: per-watchlist result blacklist keyword rules.
                # Mirrors Usagi's PUT /results/{filename}/blacklist-rules but
                # uses watch_id as the key (Kuro's per-task model).
                watch_id = _route_int(route.removesuffix("/blacklist-rules"), "/api/watchlist/")
                if watch_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                keywords = payload.get("keywords", [])
                if not isinstance(keywords, (list, str)):
                    self._json({"error": "keywords must be list or string"},
                               status=HTTPStatus.BAD_REQUEST)
                    return
                from cd_monitor.services.task_repository import TaskRepository
                if TaskRepository(db_path).get_task(int(watch_id)) is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                rule = _persist_blacklist(db_path, watch_id, keywords)
                self._json(rule)
                return
                return
            if route == "/api/watchlist":
                catalog_no = str(payload.get("catalog_no", "")).strip()
                if not catalog_no:
                    self._json({"error": "catalog_no_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                # decision_mode validation at create-time (mirrors Usagi
                # TaskGenerateRequest.validate_decision_mode_payload, lines 350-358
                # of src/domain/models/task.py). Two invariants enforced here,
                # matching Usagi's runtime behavior exactly so a future
                # migration from Usagi keeps the watchlist on the same contract:
                #   1. decision_mode == "ai" requires non-empty description.
                #   2. decision_mode == "keyword" requires non-empty required_keywords
                #      (Kuro's spelling of Usagi's keyword_rules list — both are OR-rules).
                _create_mode = _optional_text(payload.get("decision_mode")) or "ai"
                _create_desc = _optional_text(payload.get("description"))
                _create_keywords = _list_value(payload.get("required_keywords"))
                if _create_mode == "ai" and not (_create_desc and _create_desc.strip()):
                    self._json({"error": "AI 判断模式下，详细需求(description)不能为空。"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if _create_mode == "keyword" and not _create_keywords:
                    self._json({"error": "关键词判断模式下，至少需要一个关键词(required_keywords)。"}, status=HTTPStatus.BAD_REQUEST)
                    return
                def _float_or(payload, key, default):
                    v = payload.get(key)
                    if v is None or v == "":
                        return float(default)
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return float(default)
                try:
                    watch = WatchItem(
                        catalog_no=catalog_no,
                        jan=_optional_text(payload.get("jan")),
                        artist=_optional_text(payload.get("artist")),
                        title_jp=_optional_text(payload.get("title_jp")),
                        title_cn=_optional_text(payload.get("title_cn")),
                        edition=_optional_text(payload.get("edition")),
                        required_keywords=_list_value(payload.get("required_keywords")),
                        excluded_keywords=_list_value(payload.get("excluded_keywords")),
                        priority=_int_value(payload, "priority", 3),
                        expected_holding_days=_int_value(payload, "expected_holding_days", 30),
                        min_margin=_float_or(payload, "min_margin", 0.30),
                        min_diff=_float_or(payload, "min_diff", 1500.0),
                        notify_channel=_optional_text(payload.get("notify_channel")) or "none",
                        platform=_optional_text(payload.get("platform")) or "both",
                        account_state_file=_optional_text(payload.get("account_state_file")),
                        account_strategy=_optional_text(payload.get("account_strategy")) or "auto",
                        decision_mode=_optional_text(payload.get("decision_mode")) or "ai",
                        description=_optional_text(payload.get("description")),
                        ai_prompt_base_file=_optional_text(payload.get("ai_prompt_base_file")),
                        ai_prompt_criteria_file=_optional_text(payload.get("ai_prompt_criteria_file")),
                    )
                except ValueError as _exc:
                    self._json({"error": str(_exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                watch_id = add_watch(db_path, watch)
                self._json({"id": watch_id, "catalog_no": catalog_no})
                return
            if route == "/api/watchlist/generate":
                # P5.4+: AI task-generation endpoint. Mirrors Usagi
                # `src/api/routes/tasks.py::generate_task` (decision_mode=ai branch).
                # Only meaningful when decision_mode="ai" — keyword mode is
                # handled by the existing POST /api/watchlist branch.
                task_name = _optional_text(payload.get("task_name")) or _optional_text(payload.get("keyword"))
                catalog_no = _optional_text(payload.get("keyword")) or task_name
                description = _optional_text(payload.get("description"))
                mode = _optional_text(payload.get("decision_mode")) or "ai"
                if not task_name:
                    self._json({"error": "task_name_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                if mode == "ai":
                    if not description:
                        self._json({"error": "AI 判断模式下，详细需求(description)不能为空。"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    job = _task_generation_service.create_job(
                        task_name=task_name, catalog_no=catalog_no,
                        description=description, decision_mode=mode,
                    )
                    reference_path = _optional_text(
                        payload.get("reference_file_path")
                    ) or os.path.join("prompts", "macbook_criteria.txt")
                    _task_generation_service.track(
                        _task_generation_service.run_ai_generation_job(
                            job_id=job.job_id,
                            reference_file_path=reference_path,
                            db_path=db_path,
                        ),
                        job_id=job.job_id,
                    )
                    self._json(job.to_dict(), status=HTTPStatus.ACCEPTED)
                    return
                # keyword mode without AI: create the watchlist directly, mirror
                # the keyword branch of Usagi generate_task.
                try:
                    watch = WatchItem(
                        catalog_no=catalog_no, artist=task_name,
                        decision_mode="keyword", description=description,
                        required_keywords=_list_value(payload.get("required_keywords")),
                        ai_prompt_base_file=_optional_text(payload.get("ai_prompt_base_file")),
                        ai_prompt_criteria_file=_optional_text(payload.get("ai_prompt_criteria_file")),
                    )
                except ValueError as exc:
                    self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                wid = add_watch(db_path, watch)
                self._json({"id": wid, "catalog_no": catalog_no}, status=HTTPStatus.OK)
                return
            if route.startswith("/api/watchlist/") and route.endswith("/disable"):
                watch_id = _route_int(route.removesuffix("/disable"), "/api/watchlist/")
                if watch_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._json({"disabled": disable_watch(db_path, watch_id), "id": watch_id})
                return
            if route.startswith("/api/watchlist/") and route.endswith("/enable"):
                watch_id = _route_int(route.removesuffix("/enable"), "/api/watchlist/")
                if watch_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self._json({"enabled": enable_watch(db_path, watch_id), "id": watch_id})
                return
            if route.startswith("/api/watchlist/") and route.endswith("/hard-delete"):
                watch_id = _route_int(route.removesuffix("/hard-delete"), "/api/watchlist/")
                if watch_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                deleted, cascade_summary = _hard_delete_with_cascade(db_path, watch_id)
                self._json(
                    {"deleted": deleted, "id": watch_id, "cascade": cascade_summary},
                )
                return
            # Manual start / stop / regenerate-criteria (Round-3 P0 #3 + #9).
            # Each is fire-and-forget: returns 202 + job_id, worker thread
            # does the real work and updates the job state in-place.
            _watch_action = None
            for _suffix in ("/start", "/stop", "/regenerate-criteria"):
                if route.startswith("/api/watchlist/") and route.endswith(_suffix):
                    _watch_id = _route_int(
                        route.removesuffix(_suffix), "/api/watchlist/"
                    )
                    if _watch_id is None:
                        self._json(
                            {"error": "not_found"},
                            status=HTTPStatus.NOT_FOUND,
                        )
                        return
                    _watch_action = (_watch_id, _suffix)
                    break
            if _watch_action is not None:
                _watch_id, _suffix = _watch_action
                _existing = next(
                    (
                        w for w in _watchlist_all(db_path)
                        if int(w.get("id", 0)) == _watch_id
                    ),
                    None,
                )
                if _existing is None:
                    self._json(
                        {"error": "not_found"},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                _wasvc = _get_watch_action_service(str(db_path))
                if _suffix == "/start":
                    _job = _wasvc.submit_run(_watch_id)
                    self._json(
                        {
                            "accepted": True,
                            "action": "run",
                            "watch_id": _watch_id,
                            "job": _job.to_dict(),
                        },
                        status=HTTPStatus.ACCEPTED,
                    )
                    return
                if _suffix == "/regenerate-criteria":
                    _decision_mode = (
                        (_existing.get("decision_mode") or "ai").strip().lower()
                    )
                    if _decision_mode != "ai":
                        self._json(
                            {"error": "decision_mode_not_ai"},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    _job = _wasvc.submit_regenerate_criteria(_watch_id)
                    self._json(
                        {
                            "accepted": True,
                            "action": "regenerate_criteria",
                            "watch_id": _watch_id,
                            "job": _job.to_dict(),
                        },
                        status=HTTPStatus.ACCEPTED,
                    )
                    return
                # /stop: cancel any queued/running jobs for this watch.
                _cancelled = []
                for _j in _wasvc.list_jobs(watch_id=_watch_id):
                    if _j.status in ("queued", "running"):
                        if _wasvc.cancel_job(_j.job_id):
                            _cancelled.append(_j.job_id)
                self._json(
                    {
                        "stopped": True,
                        "watch_id": _watch_id,
                        "cancelled_jobs": _cancelled,
                    },
                )
                return
            # /api/watchlist/jobs/{job_id}/cancel (POST)
            if route.startswith("/api/watchlist/jobs/") and route.endswith(
                "/cancel"
            ):
                _job_id = route[len("/api/watchlist/jobs/"):].removesuffix(
                    "/cancel"
                )
                _wasvc = _get_watch_action_service(str(db_path))
                _cancelled = _wasvc.cancel_job(_job_id)
                if not _cancelled:
                    self._json(
                        {"error": "not_found_or_terminal"},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                self._json({"cancelled": True, "job_id": _job_id})
                return
            if route.startswith("/api/watchlist/"):
                watch_id = _route_int(route, "/api/watchlist/")
                if watch_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                # Fetch the existing row so the strategy validator can run
                # against the post-update final state, not just the payload.
                # _watchlist_all returns dict rows (with id field), unlike
                # list_watch_all which returns WatchItem dataclasses (no id).
                _existing = next(
                    (
                        w for w in _watchlist_all(db_path)
                        if int(w.get("id", 0)) == watch_id
                    ),
                    None,
                )
                if _existing is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                _strategy_error = _validate_account_strategy_update(
                    payload,
                    {
                        "account_strategy": _existing.get("account_strategy"),
                        "account_state_file": _existing.get("account_state_file"),
                    },
                )
                if _strategy_error is not None:
                    self._json({"error": _strategy_error}, status=HTTPStatus.BAD_REQUEST)
                    return
                _mode_error = _validate_decision_mode_update(
                    payload,
                    {
                        "decision_mode": _existing.get("decision_mode"),
                        "description": _existing.get("description"),
                        "required_keywords": _existing.get("required_keywords"),
                    },
                )
                if _mode_error is not None:
                    self._json({"error": _mode_error}, status=HTTPStatus.BAD_REQUEST)
                    return
                updated = update_watch(db_path, watch_id, _watch_updates(payload))
                criteria_summary = _maybe_regenerate_criteria_on_update(
                    db_path, watch_id, payload, _existing, updated,
                    svc=_get_watch_action_service(str(db_path)),
                )
                self._json(
                    {
                        "updated": updated,
                        "id": watch_id,
                        "criteria": criteria_summary,
                    },
                )
                return
            if route == "/api/selection-feedback":
                raw_candidate_id = payload.get("candidate_id")
                if isinstance(raw_candidate_id, int) and not isinstance(raw_candidate_id, bool):
                    candidate_id = raw_candidate_id
                elif isinstance(raw_candidate_id, str) and re.fullmatch(r"[1-9][0-9]*", raw_candidate_id):
                    candidate_id = int(raw_candidate_id)
                else:
                    candidate_id = 0
                outcome = str(payload.get("outcome", "")).strip()
                note = payload.get("note")
                if (
                    not candidate_id
                    or outcome not in {"keep", "source_pending", "not_fit"}
                    or (note is not None and not isinstance(note, str))
                ):
                    self._json({"error": "invalid_selection_feedback"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    feedback = insert_selection_preference_feedback(
                        db_path,
                        candidate_id=candidate_id,
                        outcome=outcome,
                        note=note,
                    )
                except KeyError:
                    self._json({"error": "candidate_not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                except ValueError:
                    self._json({"error": "invalid_selection_feedback"}, status=HTTPStatus.BAD_REQUEST)
                    return
                except sqlite3.Error:
                    self._json(
                        {"error": "selection_feedback_unavailable"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._json(feedback, status=HTTPStatus.CREATED)
                return
            if route == "/api/review":
                opportunity_id = _int_value(payload, "opportunity_id", 0)
                result = str(payload.get("result", "")).strip()
                if not opportunity_id or not result:
                    self._json(
                        {"error": "opportunity_id_and_result_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    review_id = insert_review_decision(
                        db_path,
                        opportunity_id,
                        result,
                        _optional_text(payload.get("note")),
                    )
                except ValueError as exc:
                    self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._json({"id": review_id, "opportunity_id": opportunity_id, "result": result})
                return
            if route == "/api/rechecks":
                opportunity_id = _int_value(payload, "opportunity_id", 0)
                if not opportunity_id:
                    self._json({"error": "opportunity_id_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                delay_seconds = _int_value(payload, "delay_seconds", 120)
                reason = _optional_text(payload.get("reason")) or "candidate_second_pass"
                scheduled_at = next_recheck_time(delay_seconds=delay_seconds)
                self._json(
                    schedule_candidate_recheck(
                        db_path,
                        opportunity_id,
                        scheduled_at,
                        reason,
                    )
                )
                return
            if route.startswith("/api/rechecks/") and route.endswith("/resolve"):
                recheck_id = _route_int(route.removesuffix("/resolve"), "/api/rechecks/")
                if recheck_id is None:
                    self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                    return
                status = str(payload.get("status", "")).strip()
                try:
                    self._json(
                        update_candidate_recheck_status(
                            db_path,
                            recheck_id,
                            status,
                            _optional_text(payload.get("reason")),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if route == "/api/scan/mock":
                catalog_no = str(payload.get("catalog_no", "")).strip()
                if not catalog_no:
                    self._json(
                        {"error": "catalog_no_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                config = load_config(None)
                result = scan_once_mock(
                    catalog_no=catalog_no,
                    db_path=db_path,
                    wameiji_path=DEFAULT_WAMEIJI,
                    xianyu_path=DEFAULT_XIANYU,
                    snapshot_dir=config.app.snapshot_dir,
                    cost_config=config.cost,
                    evaluation_config=config.evaluation,
                )
                self._json(
                    {
                        "catalog_no": catalog_no,
                        "search_run_id": result.search_run_id,
                        "snapshot_path": str(result.snapshot_path),
                        "opportunity_count": len(result.opportunities),
                        "opportunities": [
                            _opportunity_response_with_id(opportunity_id, item)
                            for opportunity_id, item in zip(
                                result.opportunity_ids,
                                result.opportunities,
                            )
                        ],
                    }
                )
                return
            if route == "/api/scan/watchlist":
                config = load_config(None)
                all_opportunities = []
                errors: list[dict[str, str]] = []
                missed_catalogs: list[str] = []
                watches = list_watch(db_path)
                for watch in watches:
                    try:
                        result = scan_once_mock(
                            catalog_no=watch.catalog_no,
                            db_path=db_path,
                            wameiji_path=DEFAULT_WAMEIJI,
                            xianyu_path=DEFAULT_XIANYU,
                            snapshot_dir=config.app.snapshot_dir,
                            cost_config=config.cost,
                            evaluation_config=config.evaluation,
                            watch_item=watch,
                        )
                    except Exception as exc:  # noqa: BLE001 - report per-watch failures to UI
                        errors.append({"catalog_no": watch.catalog_no, "error": str(exc)})
                    else:
                        all_opportunities.extend(result.opportunities)
                        if not result.opportunities:
                            missed_catalogs.append(watch.catalog_no)
                self._json(
                    {
                        "scanned_count": len(watches),
                        "opportunity_count": len(all_opportunities),
                        "missed_catalogs": missed_catalogs,
                        "errors": errors,
                    }
                )
                return
            if route == "/api/scan/live":
                catalog_no = str(payload.get("catalog_no", "")).strip()
                if not catalog_no:
                    self._json(
                        {"error": "catalog_no_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                config = load_config(None)
                result = scan_live_status(catalog_no, config.browser)
                record_live_scan_status(db_path, result)
                self._json(asdict(result))
                return
            if route == "/api/scan/live-html":
                # Real browser capture + evaluation + optional notify for one catalog.
                # Matches `cli scan-live-html --catalog-no X ... --notify ...`.
                catalog_no = str(payload.get("catalog_no", "")).strip()
                if not catalog_no:
                    self._json(
                        {"error": "catalog_no_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                config = load_config(None)
                do_notify = bool(payload.get("notify", False))
                channel_spec = str(payload.get("notify_channel", "feishu"))
                notify_dry_run = bool(payload.get("notify_dry_run", True))
                include_review_only = bool(payload.get("notify_include_review_only", False))
                state_file = _optional_text(payload.get("state_file")) or config.browser.xianyu_state_file
                profile_dir = _optional_text(payload.get("profile_dir")) or config.browser.wameiji_profile_dir or None
                xianyu_profile_dir = _optional_text(payload.get("xianyu_profile_dir")) or config.browser.xianyu_profile_dir or None
                wameiji_state_file = _optional_text(payload.get("wameiji_state_file")) or config.browser.wameiji_state_file or None
                timeout_seconds = int(payload.get("timeout_seconds", 30))
                headless = bool(payload.get("headless", config.browser.wameiji_headless))
                try:
                    result = asyncio.run(
                        capture_and_evaluate_live_html(
                            catalog_no,
                            db_path,
                            config.app.snapshot_dir,
                            config.cost,
                            config.evaluation,
                            state_file=state_file,
                            profile_dir=profile_dir,
                            xianyu_profile_dir=xianyu_profile_dir,
                            wameiji_state_file=wameiji_state_file,
                            timeout_seconds=timeout_seconds,
                            headless=headless,
                        )
                    )
                except Exception as exc:
                    self._json(
                        {"error": "live_capture_failed", "detail": f"{type(exc).__name__}: {exc}"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                if do_notify and result.get("status") == "ok":
                    opp_ids = list(result.get("opportunity_ids") or [])
                    opps = [get_opportunity(db_path, oid) for oid in opp_ids]
                    result["notifications"] = notify_opportunities(
                        db_path,
                        opp_ids,
                        opps,
                        channel_spec=channel_spec,
                        config=config,
                        dry_run=notify_dry_run,
                        include_review_only=include_review_only,
                    )
                self._json(result)
                return
            if route == "/api/scan/live-watchlist":
                # Real browser capture + evaluation + optional notify for every watch.
                # Matches `cli live-watchlist ... --notify ...`.
                config = load_config(None)
                do_notify = bool(payload.get("notify", False))
                channel_spec = str(payload.get("notify_channel", "feishu"))
                notify_dry_run = bool(payload.get("notify_dry_run", True))
                include_review_only = bool(payload.get("notify_include_review_only", False))
                state_file = _optional_text(payload.get("state_file")) or config.browser.xianyu_state_file
                profile_dir = _optional_text(payload.get("profile_dir")) or config.browser.wameiji_profile_dir or None
                xianyu_profile_dir = _optional_text(payload.get("xianyu_profile_dir")) or config.browser.xianyu_profile_dir or None
                wameiji_state_file = _optional_text(payload.get("wameiji_state_file")) or config.browser.wameiji_state_file or None
                timeout_seconds = int(payload.get("timeout_seconds", 30))
                headless = bool(payload.get("headless", config.browser.wameiji_headless))
                watches = list_watch(db_path)
                all_opportunity_ids: list[int] = []
                all_notifications: list[dict] = []
                blocked: list[dict] = []
                ok_count = 0
                for watch in watches:
                    try:
                        result = asyncio.run(
                            capture_and_evaluate_live_html(
                                watch.catalog_no,
                                db_path,
                                config.app.snapshot_dir,
                                config.cost,
                                config.evaluation,
                                state_file=state_file,
                                profile_dir=profile_dir,
                                xianyu_profile_dir=xianyu_profile_dir,
                                wameiji_state_file=wameiji_state_file,
                                timeout_seconds=timeout_seconds,
                                headless=headless,
                            )
                        )
                    except Exception as exc:
                        blocked.append(
                            {
                                "catalog_no": watch.catalog_no,
                                "status": "error",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        continue
                    if result.get("status") != "ok":
                        blocked.append(
                            {
                                "catalog_no": watch.catalog_no,
                                "status": result.get("status"),
                                "wameiji_status": result.get("wameiji_capture", {}).get("status"),
                                "wameiji_error_type": result.get("wameiji_capture", {}).get("error_type"),
                                "xianyu_status": (result.get("xianyu_capture") or {}).get("status"),
                            }
                        )
                        continue
                    ok_count += 1
                    ids = list(result.get("opportunity_ids") or [])
                    all_opportunity_ids.extend(ids)
                    if do_notify and ids:
                        opps = [get_opportunity(db_path, oid) for oid in ids]
                        all_notifications.extend(
                            notify_opportunities(
                                db_path,
                                ids,
                                opps,
                                channel_spec=channel_spec,
                                config=config,
                                dry_run=notify_dry_run,
                                include_review_only=include_review_only,
                            )
                        )
                self._json(
                    {
                        "scanned_count": len(watches),
                        "ok_count": ok_count,
                        "blocked_count": len(blocked),
                        "opportunity_count": len(all_opportunity_ids),
                        "blocked": blocked,
                        "notifications": all_notifications,
                    }
                )
                return
            if route == "/api/import/evaluate-json":
                catalog_no = str(payload.get("catalog_no", "")).strip()
                wameiji_json = _optional_text(payload.get("wameiji_json"))
                xianyu_json = _optional_text(payload.get("xianyu_json"))
                if not catalog_no or not wameiji_json or not xianyu_json:
                    self._json(
                        {"error": "catalog_no_wameiji_json_xianyu_json_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                import json as _jsonlib
                from pathlib import Path as _P
                config = load_config(None)
                snapshot_dir = _optional_text(payload.get("snapshot_dir")) or config.app.snapshot_dir
                snap_dir = _P(snapshot_dir); snap_dir.mkdir(parents=True, exist_ok=True)

                def _resolve_json_input(value: str, label: str) -> str:
                    p = _P(value)
                    if p.is_file():
                        return str(p)
                    try:
                        _jsonlib.loads(value)
                    except Exception as exc:
                        raise ValueError(f"{label} is neither an existing file path nor valid inline JSON: {exc}")
                    tmp_path = snap_dir / (f"inline-{label}-" + catalog_no + ".json")
                    tmp_path.write_text(value, encoding="utf-8")
                    return str(tmp_path)

                try:
                    wameiji_path = _resolve_json_input(wameiji_json, "wameiji")
                    xianyu_path = _resolve_json_input(xianyu_json, "xianyu")
                except ValueError as exc:
                    self._json({"error": "invalid_json", "detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                opportunities, opportunity_ids, snapshot_path = evaluate_json_files(
                    catalog_no,
                    wameiji_path,
                    xianyu_path,
                    db_path,
                    snapshot_dir,
                    config.cost,
                    config.evaluation,
                )
                self._json(
                    {
                        "catalog_no": catalog_no,
                        "snapshot_path": str(snapshot_path),
                        "opportunity_count": len(opportunities),
                        "opportunities": [
                            _opportunity_response_with_id(opportunity_id, item)
                            for opportunity_id, item in zip(opportunity_ids, opportunities)
                        ],
                    }
                )
                return
            if route == "/api/import/evaluate-files":
                catalog_no = str(payload.get("catalog_no", "")).strip()
                wameiji_html = _optional_text(payload.get("wameiji_html"))
                xianyu_csv = _optional_text(payload.get("xianyu_csv"))
                if not catalog_no or not wameiji_html or not xianyu_csv:
                    self._json(
                        {"error": "catalog_no_wameiji_html_xianyu_csv_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                # The backend evaluator reads paths from disk; if the request
                # supplies raw pasted content instead, spill it to a file in the
                # snapshot dir so the same evaluator works for both CLI (paths)
                # and web UI (content). Mirrors the inline-* pattern used by the
                # JSON endpoint above.
                config = load_config(None)
                _inline_snap_dir = _optional_text(payload.get("snapshot_dir")) or config.app.snapshot_dir
                from pathlib import Path as _InlineP
                snap_dir_for_inline = _InlineP(_inline_snap_dir)
                snap_dir_for_inline.mkdir(parents=True, exist_ok=True)
                def _resolve_inline(value: str, label: str, suffix: str) -> str:
                    p = _InlineP(value)
                    if p.exists() and p.is_file():
                        return str(p)
                    tmp_path = snap_dir_for_inline / ("inline-" + label + "-" + catalog_no + suffix)
                    tmp_path.write_text(value, encoding="utf-8")
                    return str(tmp_path)
                wameiji_html_path = _resolve_inline(wameiji_html, "wameiji", ".html")
                xianyu_csv_path = _resolve_inline(xianyu_csv, "xianyu", ".csv")
                config = load_config(None)
                snapshot_dir = _optional_text(payload.get("snapshot_dir")) or config.app.snapshot_dir
                opportunities, opportunity_ids, snapshot_path = evaluate_files(
                    catalog_no,
                    wameiji_html_path,
                    xianyu_csv_path,
                    db_path,
                    snapshot_dir,
                    config.cost,
                    config.evaluation,
                )
                self._json(
                    {
                        "catalog_no": catalog_no,
                        "snapshot_path": str(snapshot_path),
                        "opportunity_count": len(opportunities),
                        "opportunities": [
                            _opportunity_response_with_id(opportunity_id, item)
                            for opportunity_id, item in zip(opportunity_ids, opportunities)
                        ],
                    }
                )
                return
            if route == "/api/import/evaluate-html":
                catalog_no = str(payload.get("catalog_no", "")).strip()
                wameiji_html = _optional_text(payload.get("wameiji_html"))
                xianyu_html = _optional_text(payload.get("xianyu_html"))
                if not catalog_no or not wameiji_html or not xianyu_html:
                    self._json(
                        {"error": "catalog_no_wameiji_html_xianyu_html_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                config = load_config(None)
                snapshot_dir = _optional_text(payload.get("snapshot_dir")) or config.app.snapshot_dir
                opportunities, opportunity_ids, snapshot_path = evaluate_html_files(
                    catalog_no,
                    wameiji_html,
                    xianyu_html,
                    db_path,
                    snapshot_dir,
                    config.cost,
                    config.evaluation,
                )
                self._json(
                    {
                        "catalog_no": catalog_no,
                        "snapshot_path": str(snapshot_path),
                        "opportunity_count": len(opportunities),
                        "opportunities": [
                            _opportunity_response_with_id(opportunity_id, item)
                            for opportunity_id, item in zip(opportunity_ids, opportunities)
                        ],
                    }
                )
                return
            if route == "/api/import/evaluate-html-text":
                catalog_no = str(payload.get("catalog_no", "")).strip()
                # Accept single html_text (UI paste one snippet) OR separate wameiji_html_text + xianyu_html_text
                wameiji_html = _optional_text(payload.get("wameiji_html_text")) or _optional_text(payload.get("html_text"))
                xianyu_html = _optional_text(payload.get("xianyu_html_text")) or _optional_text(payload.get("html_text"))
                if not catalog_no or not wameiji_html or not xianyu_html:
                    self._json(
                        {"error": "catalog_no_and_html_text_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                config = load_config(None)
                snapshot_dir = _optional_text(payload.get("snapshot_dir")) or config.app.snapshot_dir
                opportunities, opportunity_ids, snapshot_path = evaluate_html_texts(
                    catalog_no,
                    wameiji_html,
                    xianyu_html,
                    db_path,
                    snapshot_dir,
                    config.cost,
                    config.evaluation,
                )
                self._json(
                    {
                        "catalog_no": catalog_no,
                        "snapshot_path": str(snapshot_path),
                        "opportunity_count": len(opportunities),
                        "opportunities": [
                            _opportunity_response_with_id(opportunity_id, item)
                            for opportunity_id, item in zip(opportunity_ids, opportunities)
                        ],
                    }
                )
                return
            if route == "/api/backtest":
                catalog_no = _optional_text(payload.get("catalog_no"))
                snapshot_dir = _optional_text(payload.get("snapshot_dir"))
                if catalog_no:
                    config = load_config(None)
                    opportunities = backtest_database_history(
                        db_path,
                        catalog_no,
                        config.cost,
                        config.evaluation,
                    )
                elif snapshot_dir:
                    config = load_config(None)
                    opportunities = backtest_snapshots(snapshot_dir, config.cost, config.evaluation)
                else:
                    self._json(
                        {"error": "catalog_no_or_snapshot_dir_required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                self._json(
                    {
                        "opportunity_count": len(opportunities),
                        "opportunities": [_opportunity_response(item) for item in opportunities],
                    }
                )
                return
            if route == "/api/report":
                output = _optional_text(payload.get("output"))
                if not output:
                    self._json({"error": "output_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                limit = _int_value(payload, "limit", 50)
                output_path = Path(output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                opportunity_ids = list_opportunity_ids(db_path, limit)
                opportunities = list_opportunities(db_path, limit)
                output_path.write_text(
                    render_opportunity_report(
                        opportunities,
                        list_review_decisions(db_path),
                        list_candidate_rechecks(db_path, status=None),
                        opportunity_ids,
                    ),
                    encoding="utf-8",
                )
                self._json({"output": str(output_path), "opportunity_count": len(opportunities)})
                return
            if route == "/api/notify":
                opportunity_id = _int_value(payload, "opportunity_id", 0)
                channel = str(payload.get("channel") or "feishu").strip()
                dry_run = bool(payload.get("dry_run", True))
                if not opportunity_id:
                    self._json({"error": "opportunity_id_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    opportunity = get_opportunity(db_path, opportunity_id)
                    notifier = _notifier_for_channel(channel, load_config(None))
                    response = send_and_record(
                        str(db_path),
                        opportunity_id,
                        opportunity,
                        notifier,
                        dry_run=dry_run,
                    )
                except (KeyError, ValueError) as exc:
                    self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._json(response)
                return
            if route == "/api/login-state/wameiji":
                # Receive extension JSON pasted by the user, convert to Playwright
                # storage_state, and persist to wameiji_state.json.
                content = str(payload.get("snapshot") or payload.get("content") or "")
                cfg = load_config(None)
                output_path = (
                    _optional_text(payload.get("output_path"))
                    or cfg.browser.wameiji_state_file
                    or "data/wameiji_state.json"
                )
                if not content.strip():
                    self._json({"error": "content_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    summary = save_wameiji_login_state(content, output_path)
                except WameijiLoginStateError as exc:
                    self._json({"error": "invalid_state", "detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                except OSError as exc:
                    self._json({"error": "write_failed", "detail": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._json(summary)
                return
            if route == "/api/login-state/xianyu":
                # Receive extension JSON pasted by the user, convert to Playwright
                # storage_state, and persist to xianyu_state.json.
                content = str(payload.get("snapshot") or payload.get("content") or "")
                cfg = load_config(None)
                output_path = (
                    _optional_text(payload.get("output_path"))
                    or cfg.browser.xianyu_state_file
                    or "data/xianyu_state.json"
                )
                if not content.strip():
                    self._json({"error": "content_required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    summary = save_xianyu_login_state(content, output_path)
                except XianyuLoginStateImportError as exc:
                    self._json({"error": "invalid_state", "detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                except OSError as exc:
                    self._json({"error": "write_failed", "detail": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._json(summary)
                return
            # One-shot scrape trigger. Runs run_one_cycle.py in background
            # so the API call returns immediately while the scraper fetches
            # fresh data. The frontend polls /api/scrape/status.
            if route == "/api/scrape/now":
                import subprocess as _sp
                import threading as _thr
                import time as _t
                _status = {"running": False, "started_at": None, "finished_at": None,
                           "new_count": 0, "per_source": {}, "log_lines": []}
                def _run_cycle():
                    try:
                        _status["running"] = True
                        _status["started_at"] = _t.strftime("%Y-%m-%d %H:%M:%S")
                        env = os.environ.copy()
                        env.update({
                            "CD_DB": os.environ.get("CD_DB", str(db_path)),
                            "ENABLED_SOURCES": "mercari_jp,yahoo_auctions,suruga_ya",
                            "PYTHONPATH": os.pathsep.join(
                                [str(runtime_data_dir), str(PROJECT_ROOT / "src")]
                            ),
                        })
                        proc = _sp.Popen(
                            [python_executable, "-u", str(cycle_script)],
                            stdout=_sp.PIPE, stderr=_sp.STDOUT,
                            cwd=str(runtime_data_dir), env=env, text=True, bufsize=1,
                        )
                        log_path = runtime_data_dir / "_manual_cycle.log"
                        with open(log_path, "w", encoding="utf-8") as f:
                            for line in proc.stdout:
                                f.write(line)
                                _status["log_lines"].append(line.rstrip())
                                if len(_status["log_lines"]) > 200:
                                    _status["log_lines"] = _status["log_lines"][-200:]
                        proc.wait()
                        # Parse the result line
                        for ln in _status["log_lines"][::-1]:
                            if "[one_cycle] result=" in ln:
                                try:
                                    payload = ln.split("result=", 1)[1].split(" killed_chrome")[0]
                                    import ast as _ast
                                    data = _ast.literal_eval(payload)
                                    _status["new_count"] = data.get("new", 0)
                                    _status["per_source"] = data.get("per_source", {})
                                except Exception:
                                    pass
                                break
                    except Exception as e:
                        _status["log_lines"].append("ERROR: " + str(e))
                    finally:
                        _status["running"] = False
                        _status["finished_at"] = _t.strftime("%Y-%m-%d %H:%M:%S")
                # Run in background thread
                t = _thr.Thread(target=_run_cycle, daemon=True)
                t.start()
                self._json({"triggered": True, "started_at": _status["started_at"]})
                return
            # /api/scrape/match: run match_real in-process (no separate python3
            # subprocess, so no 9p / WAL contention). Two phases:
            #   1) Sync xianyu market_items -> xianyu_price_samples
            #   2) Call match_real.match_real_market_items
            # Returns counts so the frontend can show "新增 N 个机会".
            # /api/scrape/full: scrape + match in sequence (single user-facing action).
            # 1) Spawn run_one_cycle.py (scrape only - it now skips matching).
            # 2) When done, run /api/scrape/match in-process to generate opportunities.
            if route == "/api/scrape/full":
                import subprocess as _sp2
                import threading as _thr3
                import time as _t3
                _full_status = {"running": False, "started_at": None, "finished_at": None,
                                "scrape": {}, "sync": {}, "match": {}, "log_lines": []}
                _full_log_path = runtime_data_dir / "_full_cycle.log"
                def _run_full():
                    try:
                        _full_status["running"] = True
                        _full_status["started_at"] = _t3.strftime("%Y-%m-%d %H:%M:%S")
                        # Step 1: scrape
                        env = os.environ.copy()
                        env.update({
                            "CD_DB": os.environ.get("CD_DB", str(db_path)),
                            "ENABLED_SOURCES": "mercari_jp,yahoo_auctions,suruga_ya",
                            "PYTHONPATH": os.pathsep.join(
                                [str(runtime_data_dir), str(PROJECT_ROOT / "src")]
                            ),
                        })
                        proc = _sp2.Popen(
                            [python_executable, "-u", str(cycle_script)],
                            stdout=_sp2.PIPE, stderr=_sp2.STDOUT,
                            cwd=str(runtime_data_dir), env=env, text=True, bufsize=1,
                        )
                        with open(_full_log_path, "w", encoding="utf-8") as f:
                            for line in proc.stdout:
                                f.write(line)
                                _full_status["log_lines"].append(line.rstrip())
                                if len(_full_status["log_lines"]) > 300:
                                    _full_status["log_lines"] = _full_status["log_lines"][-300:]
                        proc.wait()
                        # Parse scrape result
                        for ln in reversed(_full_status["log_lines"]):
                            if "[one_cycle] result=" in ln:
                                try:
                                    payload = ln.split("result=", 1)[1].split(" killed_chrome")[0]
                                    import ast as _ast2
                                    data = _ast2.literal_eval(payload)
                                    _full_status["scrape"] = data
                                except Exception:
                                    pass
                                break
                        # Step 2: sync xianyu market_items -> samples (in-process)
                        with sqlite3.connect(db_path, timeout=60) as _conn2:
                            try:
                                _conn2.execute("PRAGMA journal_mode=WAL")
                                _conn2.execute("PRAGMA busy_timeout=60000")
                            except Exception:
                                pass
                            _rows2 = _conn2.execute(
                                """SELECT id, catalog_no, title, price, currency, url, image_url, raw_text
                                   FROM market_items
                                   WHERE source='xianyu'
                                     AND catalog_no IS NOT NULL AND catalog_no NOT IN ("", "AUTO")
                                     AND price IS NOT NULL AND price > 0"""
                            ).fetchall()
                            _ins = 0; _dup = 0; _inv = 0
                            for _r in _rows2:
                                _title = _r[2] or '"'"''"'"'
                                _price = float(_r[3] or 0)
                                if len(_title.strip()) < 6 or _price < 1 or _price > 5000:
                                    _inv += 1
                                    continue
                                _url = _r[5] or '"'"''"'"'
                                if _conn2.execute(
                                    "SELECT 1 FROM xianyu_price_samples WHERE catalog_no=? AND url IS NOT NULL AND url=? LIMIT 1",
                                    (_r[1], _url),
                                ).fetchone():
                                    _dup += 1
                                    continue
                                try:
                                    _conn2.execute(
                                        """INSERT INTO xianyu_price_samples
                                           (catalog_no, title, price_cny, url, image_url, seller_text, raw_text, is_valid)
                                           VALUES (?, ?, ?, ?, ?, NULL, ?, 1)""",
                                        (_r[1], _title, _price, _url, _r[6] or '"'"''"'"', _r[7] or '"'"''"'"'),
                                    )
                                    _ins += 1
                                except Exception:
                                    _dup += 1
                            _conn2.commit()
                        _full_status["sync"] = {"scanned": len(_rows2), "inserted": _ins,
                                                  "skipped_dup": _dup, "skipped_invalid": _inv}
                        # Step 3: match_real in-process
                        try:
                            import sys as _sys2
                            _runtime_data = str(runtime_data_dir)
                            if _runtime_data not in _sys2.path:
                                _sys2.path.insert(0, _runtime_data)
                            import match_real
                            _full_status["match"] = match_real.match_real_market_items(str(db_path))
                        except Exception as _me:
                            _full_status["match"] = {"error": str(_me)[:200]}
                    except Exception as _e:
                        _full_status["log_lines"].append("ERROR: " + str(_e)[:200])
                    finally:
                        _full_status["running"] = False
                        _full_status["finished_at"] = _t3.strftime("%Y-%m-%d %H:%M:%S")
                        try:
                            import json as _jsf
                            with open(runtime_data_dir / "_full_cycle.status.json", "w", encoding="utf-8") as _sf:
                                _sf.write(_jsf.dumps(_full_status, ensure_ascii=False))
                        except Exception:
                            pass
                _ft = _thr3.Thread(target=_run_full, daemon=True)
                _ft.start()
                self._json({"triggered": True, "started_at": _full_status["started_at"]})
                return
            if route == "/api/scrape/full-status":
                _payload = {"running": False}
                try:
                    import json as _jsfs
                    _full_status_path = runtime_data_dir / "_full_cycle.status.json"
                    if _full_status_path.exists():
                        _payload = _jsfs.loads(_full_status_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
                self._json(_payload)
                return
            if route == "/api/scrape/match":
                import threading as _thr2
                import time as _t2
                _match_status = {"running": False, "started_at": None,
                                 "finished_at": None,
                                 "sync": {}, "match": {}, "error": None}
                def _run_match():
                    try:
                        _match_status["running"] = True
                        _match_status["started_at"] = _t2.strftime("%Y-%m-%d %H:%M:%S")
                        # Phase 1: sync
                        with sqlite3.connect(db_path, timeout=60) as _conn:
                            try:
                                _conn.execute("PRAGMA journal_mode=WAL")
                                _conn.execute("PRAGMA busy_timeout=60000")
                            except Exception:
                                pass
                            _rows = _conn.execute(
                                """SELECT id, catalog_no, title, price, currency, url, image_url, raw_text
                                   FROM market_items
                                   WHERE source='xianyu'
                                     AND catalog_no IS NOT NULL AND catalog_no NOT IN ("", "AUTO")
                                     AND price IS NOT NULL AND price > 0"""
                            ).fetchall()
                            _ins = 0
                            _dup = 0
                            _inv = 0
                            for _r in _rows:
                                _title = _r[2] or '"'"''"'"'
                                _price = float(_r[3] or 0)
                                if len(_title.strip()) < 6 or _price < 1 or _price > 5000:
                                    _inv += 1
                                    continue
                                _url = _r[5] or '"'"''"'"'
                                if _conn.execute(
                                    "SELECT 1 FROM xianyu_price_samples WHERE catalog_no=? AND url IS NOT NULL AND url=? LIMIT 1",
                                    (_r[1], _url),
                                ).fetchone():
                                    _dup += 1
                                    continue
                                try:
                                    _conn.execute(
                                        """INSERT INTO xianyu_price_samples
                                           (catalog_no, title, price_cny, url, image_url, seller_text, raw_text, is_valid)
                                           VALUES (?, ?, ?, ?, ?, NULL, ?, 1)""",
                                        (_r[1], _title, _price, _url, _r[6] or '"'"''"'"', _r[7] or '"'"''"'"'),
                                    )
                                    _ins += 1
                                except Exception:
                                    _dup += 1
                            _conn.commit()
                        _match_status["sync"] = {"scanned": len(_rows), "inserted": _ins, "skipped_dup": _dup, "skipped_invalid": _inv}
                        # Phase 2: match_real
                        try:
                            import sys as _sys
                            _data_dir = str(runtime_data_dir)
                            if _data_dir not in _sys.path:
                                _sys.path.insert(0, _data_dir)
                            import match_real_v2 as match_real
                            _match_status["match"] = match_real.match_real_market_items(str(db_path))
                            try:
                                _cleanup = match_real._auto_cleanup_opportunities(str(db_path))
                                _match_status["cleanup"] = _cleanup
                            except Exception:
                                pass
                        except Exception as _me:
                            _match_status["error"] = "match_real: " + str(_me)[:200]
                    except Exception as _e:
                        _match_status["error"] = str(_e)[:200]
                    finally:
                        _match_status["running"] = False
                        _match_status["finished_at"] = _t2.strftime("%Y-%m-%d %H:%M:%S")
                # Persist status sidecar so /api/scrape/match-status can poll it
                def _write_sidecar():
                    try:
                        import json as _js
                        with open(str(db_path) + ".match_status.json", "w", encoding="utf-8") as _sf:
                            _sf.write(_js.dumps(_match_status, ensure_ascii=False))
                    except Exception:
                        pass
                def _run_match_with_sidecar():
                    _write_sidecar()
                    try:
                        _run_match()
                    finally:
                        _write_sidecar()
                _mt = _thr2.Thread(target=_run_match_with_sidecar, daemon=True)
                _mt.start()
                self._json({"triggered": True, "started_at": _match_status["started_at"]})
                return
            if route == "/api/scrape/match-status":
                # Companion status endpoint so the frontend can poll matching progress.
                # match thread persists a JSON sidecar at .match_status.json when it starts/ends.
                _mstatus_path = str(db_path) + ".match_status.json"
                _payload = {"running": False}
                try:
                    if os.path.exists(_mstatus_path):
                        import json as _json3
                        _payload = _json3.loads(open(_mstatus_path, "r", encoding="utf-8").read())
                except Exception:
                    pass
                self._json(_payload)
                return
            if route.startswith("/api/"):
                self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._json({"error": "method_not_allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length == 0:
                return {}
            body = self.rfile.read(length).decode("utf-8")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise BadJsonRequest("invalid_json") from exc
            return payload if isinstance(payload, dict) else {}

        def _origin_allowed(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            host = self.headers.get("Host", "")
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"}:
                return False
            normalized = origin.rstrip("/")
            configured = {
                value.strip().rstrip("/")
                for value in os.getenv("WEB_ALLOWED_ORIGINS", "").split(",")
                if value.strip()
            }
            return normalized in configured or parsed.netloc == host

        def _request_authorized(self) -> bool:
            token = os.getenv("WEB_ACCESS_TOKEN", "").strip()
            if not token:
                return True
            parsed = urlparse(self.path)
            query_token = _query_param(parsed.query, "access_token")
            cookie_token = _cookie_value(self.headers.get("Cookie", ""), "cd_monitor_access")
            return any(
                candidate is not None and compare_digest(candidate, token)
                for candidate in [query_token, cookie_token]
            )

        def _collector_authorized(self) -> bool:
            token = os.getenv("CD_SYNC_TOKEN", "").strip()
            supplied = self.headers.get("X-CD-Sync-Token", "").strip()
            return bool(token and supplied and compare_digest(supplied, token))

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:

            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self._set_cors_headers()
            self._set_access_cookie_if_requested()
            self.end_headers()
            self.wfile.write(data)

        def _static(self, route: str) -> None:
            relative = "index.html" if route in {"", "/"} else route.lstrip("/")
            target = (static_root / relative).resolve()
            if static_root not in target.parents and target != static_root:
                self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            if not target.exists() or not target.is_file():
                self._json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            content = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(str(target))[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(content)))
            self._set_cors_headers()
            self._set_access_cookie_if_requested()
            self.end_headers()
            self.wfile.write(content)

        def _set_cors_headers(self) -> None:
            origin = self.headers.get("Origin")
            if not origin or not self._origin_allowed():
                return
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            requested = self.headers.get("Access-Control-Request-Headers")
            self.send_header(
                "Access-Control-Allow-Headers",
                requested or "Content-Type, Authorization",
            )
            self.send_header("Vary", "Origin")

        def _set_access_cookie_if_requested(self) -> None:
            token = os.getenv("WEB_ACCESS_TOKEN", "").strip()
            if not token:
                return
            query_token = _query_param(urlparse(self.path).query, "access_token")
            if query_token and compare_digest(query_token, token):
                self.send_header(
                    "Set-Cookie",
                    f"cd_monitor_access={token}; Path=/; HttpOnly; SameSite=Lax",
                )

    # Bind per-server state-file overrides as class attributes so the
    # inner-class methods can reach them via self (no closure capture quirks).
    MonitorRequestHandler._xianyu_state_file_override = xianyu_state_file
    MonitorRequestHandler._wameiji_state_file_override = wameiji_state_file
    MonitorRequestHandler._account_dir_override = account_dir
    return MonitorRequestHandler

def _get_broadcast_bus(db_path: str) -> BroadcastBus:
    """Return the per-db BroadcastBus used by the WebSocket feed.

    Mirrors the per-db cache used by ``_get_watch_action_service``;
    a single bus instance is shared by ``WatchActionService``,
    ``SchedulerService``, and the WebSocket handler so all of them
    land on the same subscriber queue.
    """
    return WatchActionService._get_or_create_bus(db_path)


def _get_watch_action_service(db_path: str) -> WatchActionService:
    """Lazily construct (and cache) the manual-action service.

    Cached on a module-level attribute so the in-memory job registry
    survives across requests inside the same server process.
    """
    cache = getattr(_get_watch_action_service, "_cache", None)
    if cache is None or cache.db_path != db_path:
        cache = WatchActionService(db_path=db_path)
        _get_watch_action_service._cache = cache
    return cache


def _discovery_pool_views(db_path: str | Path) -> list[dict[str, object]]:
    """Serialize a pool with its editable keyword list for the remote UI."""
    views: list[dict[str, object]] = []
    for pool in list_discovery_pools(db_path):
        view = asdict(pool)
        assert pool.id is not None
        view["keywords"] = [
            asdict(keyword) for keyword in list_discovery_keywords(db_path, pool.id)
        ]
        views.append(view)
    return views


def _discovery_opportunity_views(db_path: str | Path) -> list[dict[str, object]]:
    """Make source-detail links usable from the separate GitHub Pages UI."""
    views = list_discovery_opportunities(db_path, limit=100)
    for view in views:
        raw_url = str(view.get("url") or view.get("source_url") or "")
        canonical_url = _canonical_wameiji_url("wameiji", raw_url)
        if canonical_url:
            view["url"] = canonical_url
            view["source_url"] = canonical_url
    return views


def _discovery_research_candidate_views(db_path: str | Path) -> list[dict[str, object]]:
    """Make source links usable without adding price data to research cards."""
    views = list_discovery_research_candidates(db_path, limit=12)
    for view in views:
        canonical_url = _canonical_wameiji_url("wameiji", str(view.get("source_url") or ""))
        if canonical_url:
            view["source_url"] = canonical_url
    return views





def _query_param(query: str, key: str) -> str | None:
    for part in query.split("&"):
        if not part:
            continue
        name, _, value = part.partition("=")
        if name == key:
            return value
    return None


def _safe_int(value, default):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cookie_value(cookie_header: str, key: str) -> str | None:
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == key:
            return value
    return None


def _summary(db_path: Path) -> dict[str, Any]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM watchlist WHERE enabled = 1) AS watch_count,
              (SELECT COUNT(*) FROM opportunities) AS opportunity_count,
              (SELECT COUNT(*) FROM opportunities WHERE decision = 'strong_alert') AS strong_alert_count,
              (SELECT COUNT(*) FROM sent_alerts) AS alert_count,
              COALESCE((SELECT MAX(expected_profit) FROM opportunities), 0) AS top_expected_profit,
              COALESCE((SELECT AVG(match_confidence) FROM opportunities), 0) AS avg_match_confidence,
              (SELECT COUNT(*) FROM opportunities WHERE substr(created_at, 1, 10) = :today) AS today_opportunity_count,
              COALESCE((SELECT SUM(expected_profit) FROM opportunities WHERE substr(created_at, 1, 10) = :today), 0) AS today_expected_profit
            """,
            {"today": datetime.date.today().isoformat()},
        ).fetchone()
    return dict(row)


def _public_config(config: Any, db_path: Path) -> dict[str, Any]:
    source_status = _browser_status(config)
    return {
        "app": {
            "database": str(db_path),
            "snapshot_dir": config.app.snapshot_dir,
            "screenshot_dir": config.app.screenshot_dir,
        },
        "browser": {
            "enabled": config.browser.enabled,
            "profile_name": config.browser.profile_name,
            "xianyu_state_file_configured": bool(config.browser.xianyu_state_file),
            "xianyu_profile_dir_configured": bool(config.browser.xianyu_profile_dir),
            "wameiji_state_file_configured": bool(config.browser.wameiji_state_file),
            "wameiji_profile_dir_configured": bool(config.browser.wameiji_profile_dir),
            "min_delay_seconds": config.browser.min_delay_seconds,
            "max_delay_seconds": config.browser.max_delay_seconds,
            "max_retries_per_action": config.browser.max_retries_per_action,
            "stop_on_captcha": config.browser.stop_on_captcha,
            "stop_on_security_check": config.browser.stop_on_security_check,
              "wameiji_profile_dir_configured": bool(config.browser.wameiji_profile_dir),
              "wameiji_profile_dir": config.browser.wameiji_profile_dir or None,
              "wameiji_search_url": config.browser.wameiji_search_url,
              "wameiji_headless": config.browser.wameiji_headless,
              "wameiji_max_consecutive_failures": config.browser.wameiji_max_consecutive_failures,
        },
        "notify": {
            "feishu_configured": bool(config.notify.feishu_webhook_url),
            "dingtalk_configured": bool(config.notify.dingtalk_webhook_url),
        },
        "security": {
            "access_token_required": bool(os.getenv("WEB_ACCESS_TOKEN", "").strip()),
        },
        "cost": {
            "wameiji_exchange_rate": config.cost.wameiji_exchange_rate,
            "international_shipping_per_cd_cny": config.cost.international_shipping_per_cd_cny,
            "china_reship_cost_cny": config.cost.china_reship_cost_cny,
            "risk_reserve_min_cny": config.cost.risk_reserve_min_cny,
            "annual_capital_rate": config.cost.annual_capital_rate,
        },
        "evaluation": {
            "min_valid_price_cny": config.evaluation.min_valid_price_cny,
            "max_valid_price_cny": config.evaluation.max_valid_price_cny,
            "sample_limit": config.evaluation.sample_limit,
            "strong_profit_min_cny": config.evaluation.strong_profit_min_cny,
            "strong_margin_min": config.evaluation.strong_margin_min,
            "weak_profit_min_cny": config.evaluation.weak_profit_min_cny,
            "weak_margin_min": config.evaluation.weak_margin_min,
            "min_match_confidence_strong": config.evaluation.min_match_confidence_strong,
            "min_match_confidence_weak": config.evaluation.min_match_confidence_weak,
            "negotiation_discount": config.evaluation.negotiation_discount,
            "liquidity_discount_default": config.evaluation.liquidity_discount_default,
        },
        "data_sources": {
            "mock": {
                "ok": True,
                "mode": "local_replay",
            },
            "manual_snapshots": {
                "ok": True,
                "mode": "user_provided_json_csv_html",
                "commands": [
                    "import-html",
                    "import-csv",
                    "evaluate-json",
                    "evaluate-files",
                    "evaluate-html",
                ],
                "web_actions": [
                    "Evaluate HTML/CSV",
                    "Evaluate HTML/HTML",
                    "Evaluate Pasted HTML",
                ],
            },
            "wameiji_live_browser": source_status["wameiji"],
            "xianyu_live_browser": source_status["xianyu"],
        },
    }


def _browser_status(config: Any) -> dict[str, Any]:
    watch = WatchItem(catalog_no="STATUS-CHECK")
    from cd_monitor.services.wameiji_login_state import inspect_wameiji_login_state
    wameiji_state_file = config.browser.wameiji_state_file or "data/wameiji_state.json"
    wameiji_state_inspect = inspect_wameiji_login_state(wameiji_state_file)
    wameiji = WameijiBrowserAdapter(enabled=config.browser.enabled)
    xianyu = XianyuBrowserAdapter(
        enabled=config.browser.enabled,
        state_file=config.browser.xianyu_state_file,
    )
    wameiji_status = _adapter_status(wameiji.search_status(watch), wameiji.prohibited_actions)
    if wameiji_state_inspect.get("status") == "ready":
        wameiji_status["login_state_ready"] = True
        wameiji_status["state_file_status"] = "ready"
        wameiji_status["state_file_path"] = wameiji_state_file
        wameiji_status["state_cookie_domains"] = wameiji_state_inspect.get("cookie_domains") or []
    elif wameiji_state_inspect.get("status") == "missing":
        wameiji_status["state_file_status"] = "missing"
        wameiji_status["state_file_path"] = wameiji_state_file
    elif wameiji_state_inspect.get("status") in {"invalid", "not_configured"}:
        wameiji_status["state_file_status"] = wameiji_state_inspect.get("status")
        wameiji_status["state_file_path"] = wameiji_state_file
    # Xianyu state inspection: surface the storage_state status into the xianyu adapter_status
    from cd_monitor.services.xianyu_login_state import inspect_xianyu_login_state
    xianyu_state_file = config.browser.xianyu_state_file or "data/xianyu_state.json"
    xianyu_state_inspect = inspect_xianyu_login_state(xianyu_state_file)
    xianyu_status = _adapter_status(xianyu.search_status(watch), xianyu.prohibited_actions)
    if xianyu_state_inspect.get("status") == "ready":
        xianyu_status["login_state_ready"] = True
        xianyu_status["state_file_status"] = "ready"
        xianyu_status["state_file_path"] = xianyu_state_file
        xianyu_status["state_cookie_domains"] = xianyu_state_inspect.get("cookie_domains") or []
    elif xianyu_state_inspect.get("status") == "missing":
        xianyu_status["state_file_status"] = "missing"
        xianyu_status["state_file_path"] = xianyu_state_file
    elif xianyu_state_inspect.get("status") in {"invalid", "not_configured"}:
        xianyu_status["state_file_status"] = xianyu_state_inspect.get("status")
        xianyu_status["state_file_path"] = xianyu_state_file
    return {
        "enabled": config.browser.enabled,
        "profile_name": config.browser.profile_name,
        "wameiji": wameiji_status,
        "wameiji_state_file": wameiji_state_file,
        "wameiji_state": wameiji_state_inspect,
        "xianyu": xianyu_status,
        "xianyu_state_file": xianyu_state_file,
        "xianyu_profile_dir": config.browser.xianyu_profile_dir or None,
        "xianyu_state": xianyu_state_inspect,
        "safety": {
            "stop_on_captcha": config.browser.stop_on_captcha,
            "stop_on_security_check": config.browser.stop_on_security_check,
            "max_retries_per_action": config.browser.max_retries_per_action,
        },
    }


def _adapter_status(status: Any, prohibited_actions: set[str]) -> dict[str, Any]:
    return {
        "status": status.status,
        "error_type": status.error_type,
        "error_message": status.error_message,
        "search_entry_url": status.search_entry_url,
        "capture_instruction": status.capture_instruction,
        "login_state_ready": status.login_state_ready,
        "state_file_status": status.state_file_status,
        "state_file_path": status.state_file_path,
        "state_cookie_domains": status.state_cookie_domains,
        "prohibited_actions": sorted(prohibited_actions),
    }


def _opportunities(
    db_path: Path, limit: int = 80, randomize: bool = False
) -> list[dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        order_clause = "ORDER BY RANDOM()" if randomize else "ORDER BY o.id DESC"
        rows = conn.execute(
            f"""
            SELECT
              o.id, o.catalog_no, o.xianyu_reference_price, o.expected_sale_price,
              o.expected_profit, o.net_margin, o.turnover_adjusted_roi,
              o.match_confidence, o.valid_xianyu_sample_count, o.liquidity_status,
              o.decision, o.risk_labels, o.created_at,
              m.title AS item_title, m.price AS purchase_price_jpy, m.url, m.image_url,
              m.availability, m.condition_text
            FROM opportunities o
            LEFT JOIN market_items m ON m.id = o.wameiji_item_id
            LEFT JOIN market_items xm ON xm.id = (
              SELECT xmi.id FROM market_items xmi
              WHERE xmi.catalog_no = o.catalog_no AND xmi.source = 'xianyu'
              ORDER BY _title_overlap(m.title, xmi.title) DESC,
                       xmi.fetched_at DESC, xmi.id DESC
              LIMIT 1
            )
            ORDER BY _title_overlap(m.title, xm.title) DESC, o.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]

def _opportunities_paged(db_path: Path, limit: int, offset: int, randomize: bool = False) -> dict[str, Any]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # match_confidence was overwritten by scripts/backfill_overlap_v2.py
        # with the char-bigram Jaccard between wameiji and xianyu titles.
        # Anything below 0.10 is artist-level only ("Ado 残夢" -> "Ado 狂言")
        # or worse, and silently inflates the feed with bogus profit numbers.
        # expected_profit > 0 implements the user-spec "positive profit only"
        # implicit filter (the explicit pill button was removed earlier).
        # Merch filter: drop clear non-CD merch rows that slip past heuristics.
        # Tiny product-thumbnail shots of merch look image-similar to a CD
        # and inflate the feed (acrylic keychain <-> CD jacket, etc.).
        _MERCH_PATTERNS = (
            "%アクリルキーホルダー%",
            "%缶バッジ%", "%ステッカー%",
            "%ポスター%", "%うちわ%",
            "%カレンダー%", "%ランヤード%",
            "%ストラップ%", "%クリアカード%",
            "%マスキングテープ%",
            "%ペンライト%", "%アクスタ%",
            "%テンソリ%", "%パーカー%",
            "%タオル%", "%ポーチ%",
            "%ぬいぐるみ%", "%マスコット%",
            "%フィギュア%", "%キーホルダー%",
            "%ブロマイド%",
        )
        _merch_clause_w = " AND NOT (" + " OR ".join(
            "m.title LIKE '" + p + "'" for p in _MERCH_PATTERNS
        ) + ")"
        # xm.id can be NULL (no display_sample_id); guard so the merch filter
        # doesn't accidentally exclude the whole row.
        _merch_clause_x = " AND (xm.id IS NULL OR NOT (" + " OR ".join(
            "xm.title LIKE '" + p + "'" for p in _MERCH_PATTERNS
        ) + "))"
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM opportunities o "
            "LEFT JOIN market_items m ON m.id = o.wameiji_item_id "
            "LEFT JOIN market_items xm ON xm.id = CASE WHEN o.xianyu_display_sample_id IS NOT NULL AND o.xianyu_display_sample_id > 0 THEN o.xianyu_display_sample_id END AND xm.catalog_no = o.catalog_no AND xm.source = 'xianyu' "
            "WHERE o.expected_profit > 0 AND o.match_confidence >= 0.10"
            + _merch_clause_w + _merch_clause_x
        ).fetchone()["c"]
        if randomize:
            order_clause = "ORDER BY RANDOM()"
        else:
            # Best album-level matches first; id as tiebreaker for stability.
            order_clause = "ORDER BY o.match_confidence DESC, o.id DESC"
        rows = conn.execute(
            f"""
            SELECT
              o.id, o.catalog_no, o.xianyu_reference_price, o.expected_sale_price,
              o.expected_profit, o.net_margin, o.turnover_adjusted_roi,
              o.match_confidence, o.valid_xianyu_sample_count, o.liquidity_status,
              o.decision, o.risk_labels, o.created_at,
              m.source AS wameiji_source, m.source_site AS wameiji_source_site,
              m.title AS item_title, m.price AS purchase_price_jpy, m.url, m.image_url,
              m.availability, m.condition_text,
              xm.source AS xianyu_source, xm.source_site AS xianyu_source_site,
              xm.title AS xianyu_item_title, xm.price AS xianyu_price_cny,
              xm.url AS xianyu_url, xm.image_url AS xianyu_image_url,
              xm.availability AS xianyu_availability,
              (SELECT COUNT(*) FROM xianyu_price_samples xps WHERE xps.catalog_no = o.catalog_no) AS xianyu_sample_rows
            FROM opportunities o
            LEFT JOIN market_items m ON m.id = o.wameiji_item_id
            LEFT JOIN market_items xm ON xm.id = CASE WHEN o.xianyu_display_sample_id IS NOT NULL AND o.xianyu_display_sample_id > 0 THEN o.xianyu_display_sample_id END AND xm.catalog_no = o.catalog_no AND xm.source = 'xianyu'
            WHERE o.expected_profit > 0 AND o.match_confidence >= 0.10
            {_merch_clause_w}{_merch_clause_x}
            {order_clause}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    items = [_row_to_dict(row) for row in rows]
    if randomize:
        has_more = bool(items)
    else:
        has_more = (offset + len(items)) < total
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
    }
def _opportunity_detail(db_path: Path, opportunity_id: int) -> dict[str, Any] | None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        opportunity = conn.execute(
            """
            SELECT
              o.id, o.catalog_no, o.xianyu_reference_price, o.expected_sale_price,
              o.landed_cost, o.expected_revenue, o.expected_profit, o.net_margin,
              o.turnover_adjusted_roi, o.match_confidence, o.valid_xianyu_sample_count,
              o.liquidity_status, o.decision, o.risk_labels, o.created_at,
              m.title AS item_title, m.price AS purchase_price_jpy, m.url, m.image_url,
              m.availability, m.condition_text
            FROM opportunities o
            LEFT JOIN market_items m ON m.id = o.wameiji_item_id
            WHERE o.id = ?
            """,
            (opportunity_id,),
        ).fetchone()
        if opportunity is None:
            return None
        catalog_no = opportunity["catalog_no"]
        market_items = conn.execute(
            """
            SELECT source, source_site, external_item_id, catalog_no, jan, title, price,
              currency, price_cny_display, japan_domestic_shipping_jpy, proxy_fee_jpy, fees_hint,
              url, image_url, availability, condition_text,
              raw_text, fetched_at
            FROM market_items
            WHERE catalog_no = ? OR title LIKE ? OR raw_text LIKE ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (catalog_no, f"%{catalog_no}%", f"%{catalog_no}%"),
        ).fetchall()
        xianyu_samples = conn.execute(
            """
            SELECT catalog_no, title, price_cny, url, image_url, seller_text, raw_text,
              is_valid, invalid_reason, fetched_at
            FROM xianyu_price_samples
            WHERE catalog_no = ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (catalog_no,),
        ).fetchall()
    return {
        "opportunity": _row_to_dict(opportunity),
        "market_items": [_row_to_dict(row) for row in market_items],
        "xianyu_samples": [_row_to_dict(row) for row in xianyu_samples],
        "review_decisions": list_review_decisions(db_path, opportunity_id, 20),
        "sent_alerts": list_sent_alerts(db_path, opportunity_id, 20),
        "candidate_rechecks": list_candidate_rechecks(
            db_path,
            status=None,
            limit=20,
            opportunity_id=opportunity_id,
        ),
    }


def _review_options() -> dict[str, Any]:
    ordered_results = [
        ("accepted_for_personal_collection", "Accepted for personal collection"),
        ("rejected_version_mismatch", "Rejected: version mismatch"),
        ("rejected_xianyu_noise", "Rejected: Xianyu noise"),
        ("rejected_low_profit", "Rejected: low profit"),
        ("rejected_sold_out", "Rejected: sold out"),
        ("rejected_condition_bad", "Rejected: condition bad"),
        ("rejected_liquidity_poor", "Rejected: liquidity poor"),
        ("rejected_other", "Rejected: other"),
    ]
    return {
        "results": [
            {"value": value, "label": label}
            for value, label in ordered_results
            if value in ALLOWED_REVIEW_RESULTS
        ],
        "checklist": [
            {"id": "availability", "label": "确认仍可购买，未售出、未下架、未被锁单"},
            {"id": "identity", "label": "核对标题、目录号、JAN、版本、特典、初回/通常盘信息"},
            {"id": "condition", "label": "检查品相、缺件、盘面、盒裂、日文描述里的瑕疵"},
            {"id": "landed_cost", "label": "复核汇率、国际运费、国内转寄、平台费和风险准备金"},
            {"id": "xianyu_samples", "label": "排除闲鱼噪声样本，只保留同版本、同品相、真实在售/成交参考"},
            {"id": "sale_estimate", "label": "估算可成交价、周转天数、资金占用和议价空间"},
            {"id": "decision", "label": "记录接受或拒绝原因，必要时写入人工备注"},
        ],
    }


def _search_runs(db_path: Path, limit: int = 40) -> list[dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _register_title_overlap_udf(conn)
        rows = conn.execute(
            """
            SELECT id, source, keyword, status, started_at, finished_at, error_type,
              error_message, screenshot_path, raw_snapshot_path
            FROM search_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _watchlist(db_path: Path) -> list[dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, catalog_no, jan, artist, title_jp, title_cn, edition,
              required_keywords, excluded_keywords, priority, expected_holding_days,
              min_margin, min_diff, notify_channel, platform,
              account_state_file, account_strategy,
              decision_mode, description,
              ai_prompt_base_file, ai_prompt_criteria_file,
              enabled, created_at
            FROM watchlist
            WHERE enabled = 1
            ORDER BY priority DESC, id DESC
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _watchlist_all(db_path: Path) -> list[dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, catalog_no, jan, artist, title_jp, title_cn, edition,
              required_keywords, excluded_keywords, priority, expected_holding_days,
              min_margin, min_diff, notify_channel, platform,
              account_state_file, account_strategy,
              decision_mode, description,
              ai_prompt_base_file, ai_prompt_criteria_file,
              enabled, created_at
            FROM watchlist
            ORDER BY priority DESC, id DESC
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _table_rows(db_path: Path, table: str, order_by: str, limit: int) -> list[dict[str, Any]]:
    init_db(db_path)
    allowed = {"review_decisions", "sent_alerts"}
    if table not in allowed:
        raise ValueError(f"Unsupported table: {table}")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order_by} LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(row) for row in rows]


def _canonical_wameiji_url(source_site: str | None, url: str | None) -> str | None:
    """Return a clean, absolute, clickable URL for the wameiji side.
    Heuristics per source_site, since scrapers historically stored
    meruki-proxy URLs or hex-encoded/relative URLs that the frontend
    cannot open as target=_blank.
    """
    import urllib.parse as _up, binascii as _bx
    s = (source_site or "").strip().lower()
    raw = (url or "").strip()
    if not raw:
        return raw
    # Already https:// ... not a meruki proxy => trust it.
    if raw.startswith("https://") and "meruki.cn" not in raw and "example.invalid" not in raw:
        return raw
    if raw.startswith("http://") and "meruki.cn" not in raw:
        return raw
    if s in ("wameiji", "meruki", "doorzo"):
        if raw.startswith("/"):
            return "https://meruki.cn" + raw
        return raw
    if s in ("mercari_jp", "mercari"):
        # relative "/item/mxxxxx" => jp.mercari.com
        m = re.match(r"^/item/(m\d+)$", raw)
        if m:
            return "https://jp.mercari.com/item/" + m.group(1)
        # hex encoded https://...
        if all(ch in "0123456789abcdefABCDEF" for ch in raw) and len(raw) % 2 == 0 and len(raw) >= 20:
            try:
                d = _bx.unhexlify(raw).decode("utf-8", errors="replace")
                if d.startswith("http"):
                    return d
            except Exception:
                pass
        # meruki proxy with hex tail
        m2 = re.match(r"^https?://www\.meruki\.cn/mall/mercari/detail/([0-9a-fA-F]+)$", raw)
        if m2 and len(m2.group(1)) % 2 == 0:
            try:
                d = _bx.unhexlify(m2.group(1)).decode("utf-8", errors="replace")
                if d.startswith("http"):
                    return d
            except Exception:
                pass
        return raw
    if s == "rakuten":
        # Decoded inner URL embedded after /mall/rakuten/detail/
        m = re.match(r"^https?://www\.meruki\.cn/mall/rakuten/detail/(.+)$", raw)
        if m:
            inner = _up.unquote(m.group(1))
            if inner.startswith("http"):
                return inner
        # raw may itself be URL-encoded https://item.rakuten.co.jp/...
        inner = _up.unquote(raw)
        if inner.startswith("http") and "rakuten.co.jp" in inner:
            return inner
        return raw
    if s == "lashinbang":
        # Try extracting the numeric detail id from meruki proxy.
        m = re.match(r"^https?://www\.meruki\.cn/mall/lashinbang/detail/(\d+)(?:\?.*)?$", raw)
        if m:
            return "https://www.lashinbang.com/product/detail/" + m.group(1)
        return raw
    return raw

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("risk_labels", "required_keywords", "excluded_keywords"):
        if key in data and isinstance(data[key], str):
            try:
                data[key] = json.loads(data[key] or "[]")
            except (json.JSONDecodeError, ValueError):
                data[key] = []
    # When no valid xianyu sample is available (display_sample_id is NULL or the
    # JOIN returned no row), leave xianyu_* fields empty so the frontend shows a
    # clear "no xianyu sample" placeholder instead of mistakenly duplicating the
    # wameiji card data. This was the root cause of the "items don't match"
    # complaint: wameiji data was being echoed back into xianyu_* fields and the
    # user saw the same item on both sides.
    # (no fallback to image_url/item_title/url here on purpose)
    # Canonicalize wameiji-side URL so frontend gets an absolute, clickable link.
    src = data.get("wameiji_source_site") or data.get("source_site")
    if "url" in data and data["url"]:
        canon_url = _canonical_wameiji_url(src, data["url"])
        if canon_url:
            data["url"] = canon_url
    if "xianyu_url" in data and data["xianyu_url"]:
        # xianyu URLs are typically full goofish links already, but if a
        # proxy was stored, recanonicalize.
        canon_x = _canonical_wameiji_url(data.get("xianyu_source_site"), data["xianyu_url"])
        if canon_x:
            data["xianyu_url"] = canon_x
    # Old notifications stored response_text as Python repr which is not
    # valid JSON; tolerate it so /api/alerts does not break.
    if "response_text" in data and isinstance(data["response_text"], str):
        rt = data["response_text"]
        try:
            data["response_text"] = json.loads(rt)
        except (json.JSONDecodeError, ValueError):
            data["response_text"] = {"raw": rt}
    return data


def _opportunity_response(opportunity: Any) -> dict[str, Any]:
    return {
        "catalog_no": opportunity.catalog_no,
        "decision": opportunity.decision,
        "xianyu_reference_price": opportunity.xianyu_reference_price,
        "expected_sale_price": opportunity.expected_sale_price,
        "landed_cost": opportunity.landed_cost,
        "expected_revenue": opportunity.expected_revenue,
        "expected_profit": opportunity.expected_profit,
        "net_margin": opportunity.net_margin,
        "turnover_adjusted_roi": opportunity.turnover_adjusted_roi,
        "valid_xianyu_sample_count": opportunity.valid_xianyu_sample_count,
        "liquidity_status": opportunity.liquidity_status,
        "match_confidence": opportunity.match_confidence,
        "risk_labels": opportunity.risk_labels,
        "item_title": opportunity.item.title,
        "item_price": opportunity.item.price,
        "item_currency": opportunity.item.currency,
        "source_site": opportunity.item.source_site,
        "item_url": opportunity.item.url,
        "image_url": opportunity.item.image_url,
        "availability": opportunity.item.availability,
        "condition_text": opportunity.item.condition_text,
        "review_advice": opportunity.review_advice,
    }


def _opportunity_response_with_id(opportunity_id: int, opportunity: Any) -> dict[str, Any]:
    payload = _opportunity_response(opportunity)
    payload["id"] = opportunity_id
    return payload


def _notifier_for_channel(channel: str, config: Any, *, bark_url: str | None = None) -> Any:
    if channel == "feishu":
        return FeishuNotifier(config.notify.feishu_webhook_url)
    if channel == "dingtalk":
        return DingTalkNotifier(config.notify.dingtalk_webhook_url)
    if channel == "bark":
        # P0 #5: accept a caller-supplied bark_url (preferred, since the
        # caller knows the right db_path) and fall back to the module-level
        # helper + BARK_URL env var.
        if not bark_url:
            bark_url = _bark_url_from_settings() or os.getenv("BARK_URL")
        return BarkClient(bark_url=bark_url)
    raise ValueError(f"Unsupported notify channel: {channel}")


def _bark_url_from_settings(db_path=None) -> str | None:
    """Read the persisted Bark URL from the user_settings table.

    Lazy by design: web_server is stdlib-only at import time. Any
    failure here is swallowed so the rest of the request can still
    respond (e.g. the factory must never 500 just because sqlite is
    momentarily locked).
    """
    try:
        from cd_monitor.storage.sqlite import get_user_settings
        if db_path is None:
            db_path = "data/cd_monitor.db"
        settings = get_user_settings(db_path)
        return settings.get("bark_url") or None
    except Exception:  # noqa: BLE001 - never let config read break callers
        return None


def _notification_settings_view(db_path) -> dict[str, Any]:
    """Build the dict shape returned by GET /api/settings/notifications.

    Reads the bark_url from user_settings (UI override) and the
    feishu/dingtalk webhook URLs from the live config (env / yaml).
    """
    from cd_monitor.storage.sqlite import get_user_settings
    user = get_user_settings(db_path)
    cfg = None
    try:
        cfg = load_config(None)
    except Exception:  # noqa: BLE001
        cfg = None
    feishu_url = None
    dingtalk_url = None
    if cfg is not None and getattr(cfg, "notify", None) is not None:
        feishu_url = getattr(cfg.notify, "feishu_webhook_url", None)
        dingtalk_url = getattr(cfg.notify, "dingtalk_webhook_url", None)
    return {
        "channel": user.get("notify_channel") or "feishu",
        "bark_url": user.get("bark_url") or None,
        "feishu_webhook": feishu_url or None,
        "dingtalk_webhook": dingtalk_url or None,
        "supported_channels": ["feishu", "dingtalk", "bark"],
    }



def _ai_settings_view(db_path) -> dict[str, Any]:
    """Build the dict shape returned by GET /api/settings/ai.

    Reads AI provider config from user_settings (UI override) and
    merges with the env-derived AISettings for fields the UI has not
    set. The api_key is masked so the UI never sees a raw secret.
    """
    from cd_monitor.storage.sqlite import get_user_settings
    from cd_monitor.infrastructure.config.settings import (
        ai_settings as _ai_settings, fallback_ai_settings as _fb_settings,
    )
    user = get_user_settings(db_path)
    base_url = user.get("ai_base_url") or _ai_settings.base_url
    model_name = user.get("ai_model_name") or _ai_settings.model_name
    proxy_url = user.get("ai_proxy_url") or _ai_settings.proxy_url
    has_user_api_key = bool(user.get("ai_api_key"))
    has_env_api_key = bool(_ai_settings.api_key)
    return {
        "base_url": base_url,
        "model_name": model_name,
        "proxy_url": proxy_url or None,
        "api_key_configured": has_user_api_key or has_env_api_key,
        "api_key_source": "user_settings" if has_user_api_key else ("env" if has_env_api_key else None),
        "is_configured": bool(base_url and model_name and (has_user_api_key or has_env_api_key)),
        "fallback_enabled": _fb_settings.enabled,
        "fallback_configured": _fb_settings.is_configured(),
        "supported_providers": ["openai", "deepseek", "custom"],
    }


def _persist_ai_settings(db_path, payload: dict[str, Any]) -> int:
    """Persist the AI settings that were passed in the payload.

    Only the fields the UI can edit are stored: base_url, model_name,
    proxy_url, api_key. Empty strings clear the override. Returns
    the number of keys written.
    """
    from cd_monitor.storage.sqlite import set_user_settings
    writable = {"ai_base_url", "ai_model_name", "ai_proxy_url", "ai_api_key"}
    updates: dict[str, str] = {}
    for k in writable:
        if k in payload:
            v = payload[k]
            updates[k] = "" if v is None else str(v).strip()
    if not updates:
        return 0
    return set_user_settings(db_path, updates)


def _ai_test_view(db_path, payload: dict[str, Any]) -> dict[str, Any]:
    """Probe whether the AI provider is reachable without making a paid call.

    The cheap check is the env/override merge + is_configured(). The expensive
    check (issuing a real request) is gated behind ``live=True`` so the
    Web UI status pill stays free. The check re-reads user_settings after
    a possible write so the user immediately sees their new key take effect.
    """
    view = _ai_settings_view(db_path)
    live = bool(payload.get("live", False))
    if not view["is_configured"]:
        return {
            "ok": False,
            "reason": "not_configured",
            "view": view,
        }
    if not live:
        # Cheap probe: just report the merged view. The UI can show a green pill.
        return {
            "ok": True,
            "reason": "configured",
            "view": view,
        }
    # Live probe: check that the AI client is importable / available.
    try:
        from cd_monitor.services.scraper._prompt_utils import is_ai_available
        if not is_ai_available():
            return {"ok": False, "reason": "client_unavailable", "view": view}
        return {"ok": True, "reason": "client_available", "view": view}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"probe_failed:{type(exc).__name__}", "view": view, "detail": str(exc)}


def _blacklist_view(db_path, watch_id: int) -> dict[str, Any]:
    """Return the per-watchlist blacklist rule for a given watch_id.

    The result is a dict matching ``ResultBlacklistRule.to_dict()``. If no
    row exists yet, we still return the watch_id with an empty keyword
    list so the Web UI can render a fresh form.

    Raises ``KeyError`` if the watch_id does not exist in the watchlist
    so the HTTP handler can return 404.
    """
    from cd_monitor.services.task_repository import TaskRepository
    task = TaskRepository(db_path).get_task(int(watch_id))
    if task is None:
        raise KeyError(f"watch_id {watch_id} not found")
    repo = ResultBlacklistService(db_path)
    rule = repo.get_rule(int(watch_id))
    return rule.to_dict()


def _persist_blacklist(db_path, watch_id: int, keywords) -> dict[str, Any]:
    """Persist the per-watchlist blacklist keywords and return the saved rule.

    ``keywords`` may be a list of strings (each normalized via
    ``normalize_blacklist_keywords``) or a single comma/newline-separated
    string (which the normalizer also accepts).
    """
    repo = ResultBlacklistService(db_path)
    saved = repo.set_keywords(int(watch_id), keywords)
    rule = repo.get_rule(int(watch_id))
    payload = rule.to_dict()
    payload["saved_count"] = len(saved)
    return payload


def _rotation_settings_view(db_path: str) -> dict[str, Any]:
    """Per-account-pool / rotation tunables (Usagi parity, P5.5)."""
    from cd_monitor.storage.sqlite import get_user_settings
    overrides = get_user_settings(db_path) or {}
    return {
        "policy": overrides.get("rotation_policy") or "auto",
        "account_blacklist_ttl_sec": int(overrides.get("account_blacklist_ttl_sec") or 300),
        "proxy_blacklist_ttl_sec": int(overrides.get("proxy_blacklist_ttl_sec") or 300),
        "use_account_pool": (overrides.get("use_account_pool") or "0") == "1",
        "supported_policies": ["auto", "fixed", "rotate"],
        "supported_flags": {
            "rotation_pool_enabled": False,
            "compliance_only_single_account": True,
        },
    }


def _persist_rotation(db_path: str, payload: dict[str, Any]) -> int:
    from cd_monitor.storage.sqlite import set_user_settings
    writable = {
        "rotation_policy",
        "account_blacklist_ttl_sec",
        "proxy_blacklist_ttl_sec",
        "use_account_pool",
    }
    updates: dict[str, str] = {}
    for k in writable:
        if k in payload:
            v = payload[k]
            if v is None:
                updates[k] = ""
            elif k == "use_account_pool":
                updates[k] = "1" if bool(v) else "0"
            else:
                updates[k] = str(v).strip()
    if not updates:
        return 0
    return set_user_settings(db_path, updates)


def _system_status_view(db_path: str) -> dict[str, Any]:
    from cd_monitor.services.task_repository import TaskRepository
    repo = TaskRepository(db_path)
    tasks = repo.list_all()
    running = [t for t in tasks if bool(t.get("is_running"))]
    failed_paused = [t for t in tasks if (t.get("last_status") in {"failed", "paused"})]
    summary: dict[str, Any] = {
        "ok": True,
        "database": db_path,
        "task_count": len(tasks),
        "running_task_count": len(running),
        "failed_or_paused_task_count": len(failed_paused),
        "running_task_ids": [t["id"] for t in running][:10],
    }
    try:
        ai_view = _ai_settings_view(db_path)
        summary["ai"] = {
            "configured": bool(ai_view.get("is_configured")),
            "source": ai_view.get("api_key_source"),
            "model": ai_view.get("model_name"),
        }
    except Exception as exc:
        summary["ai"] = {"configured": False, "error": type(exc).__name__}
    try:
        notif_view = _notification_settings_view(db_path)
        summary["notifications"] = {
            "channel": notif_view.get("channel"),
            "supported_channels": notif_view.get("supported_channels", []),
        }
    except Exception as exc:
        summary["notifications"] = {"error": type(exc).__name__}
    return summary


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _watch_updates(payload: dict[str, Any]) -> dict[str, object]:
    allowed = {
        "jan",
        "artist",
        "title_jp",
        "title_cn",
        "edition",
        "required_keywords",
        "excluded_keywords",
        "priority",
        "expected_holding_days",
        "min_margin",
        "min_diff",
        "notify_channel",
        "platform",
        # P5.3: task-level account binding.
        "account_state_file",
        "account_strategy",
        # P5.4: AI/keyword mode + per-task AI prompt.
        "decision_mode",
        "description",
        "ai_prompt_base_file",
        "ai_prompt_criteria_file",
    }
    updates: dict[str, object] = {}
    for key in allowed:
        if key not in payload:
            continue
        value = payload[key]
        if key in {"priority", "expected_holding_days"}:
            updates[key] = _int_value(payload, key, 0)
        elif key in {"min_margin", "min_diff"}:
            updates[key] = _float_value(payload, key, 0.0)
        elif key in {"required_keywords", "excluded_keywords"}:
            updates[key] = _list_value(value)
        else:
            updates[key] = _optional_text(value)
    return updates


def _validate_decision_mode_update(
    payload: dict[str, Any],
    existing_watch: dict[str, Any] | None = None,
) -> str | None:
    """Return None if OK, otherwise the human-readable error message.

    Mirrors Usagi TaskUpdate.validate_partial_keyword_payload
    (src/domain/models/task.py:303-310). Unlike the create path which
    enforces BOTH invariants up-front, the partial-update path only
    re-checks the invariant for the field actually being changed:
      - if `decision_mode` is in payload: validate both invariants
        against the merged final state (catches e.g. switching a
        description-less existing watch into ai mode).
      - else if `description` is in payload and the resolved final
        decision_mode is "ai": reject empty description.
      - else if `required_keywords` is in payload and the resolved
        final decision_mode is "keyword": reject empty keywords.
      - otherwise: nothing to validate on this PUT.

    This mirrors Usagi's precise semantics so PUT-style PATCHes don't
    spuriously break legacy watches whose description / keyword list
    was empty before P5.4.
    """
    existing = existing_watch or {}
    existing_mode = (_optional_text(existing.get("decision_mode")) or "ai")
    existing_desc = existing.get("description")
    existing_keywords = existing.get("required_keywords") or []
    if not isinstance(existing_keywords, list):
        existing_keywords = []

    touches_mode = "decision_mode" in payload
    touches_desc = "description" in payload
    touches_keywords = "required_keywords" in payload
    if not any((touches_mode, touches_desc, touches_keywords)):
        return None

    payload_mode = payload.get("decision_mode")
    final_mode = (
        _optional_text(payload_mode) if payload_mode is not None else existing_mode
    ) or "ai"

    # Switch-mode path: re-validate both invariants against merged state.
    if touches_mode:
        payload_desc = payload.get("description")
        final_desc = (
            _optional_text(payload_desc) if payload_desc is not None else existing_desc
        )
        if final_mode == "ai" and not (final_desc and str(final_desc).strip()):
            return "AI 判断模式下，详细需求(description)不能为空。"
        payload_keywords = payload.get("required_keywords")
        if payload_keywords is not None:
            final_keywords = _list_value(payload_keywords)
        else:
            final_keywords = existing_keywords
        if final_mode == "keyword" and not final_keywords:
            return "关键词判断模式下，至少需要一个关键词(required_keywords)。"
        return None

    # Single-field PATCH paths: re-check only the matching invariant.
    if touches_desc and final_mode == "ai":
        candidate = _optional_text(payload.get("description"))
        if not candidate or not candidate.strip():
            return "AI 判断模式下，详细需求(description)不能为空。"
    if touches_keywords and final_mode == "keyword":
        candidate = _list_value(payload.get("required_keywords"))
        if not candidate:
            return "关键词判断模式下，至少需要一个关键词(required_keywords)。"
    return None


def _validate_account_strategy_update(
    payload: dict[str, Any],
    existing_watch: dict[str, Any] | None = None,
) -> str | None:
    """Return None if OK, otherwise the human-readable error message.

    Mirrors Usagi ai-goofish-monitor's
    src/api/routes/tasks.py::_validate_final_account_strategy: when the
    PUT payload only updates one of the two fields, the *other* field
    is sourced from the existing DB row so the final-state invariant
    `fixed strategy requires a non-empty account_state_file` always
    holds after the update.
    """
    if "account_strategy" not in payload and "account_state_file" not in payload:
        return None
    from cd_monitor.services.account_strategy import (
        assert_strategy_consistent,
        clean_account_state_file,
        normalize_account_strategy,
    )
    existing_strategy = (existing_watch or {}).get("account_strategy") or "auto"
    existing_state_file = clean_account_state_file(
        (existing_watch or {}).get("account_state_file")
    )
    payload_strategy = payload.get("account_strategy")
    payload_state_file = payload.get("account_state_file")
    final_strategy = (
        _optional_text(payload_strategy)
        if payload_strategy is not None
        else existing_strategy
    )
    final_state_file = (
        clean_account_state_file(payload_state_file)
        if payload_state_file is not None
        else existing_state_file
    )
    final_strategy = normalize_account_strategy(final_strategy, final_state_file)
    try:
        assert_strategy_consistent(final_strategy, final_state_file)
    except ValueError as exc:
        return str(exc)
    return None


def _float_value(payload: dict[str, Any], field: str, default: float) -> float:
    value = payload.get(field)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _route_int(route: str, prefix: str) -> int | None:
    value = route.removeprefix(prefix).strip("/")
    if not value.isdigit():
        return None
    return int(value)


def _int_value(payload: dict[str, Any], field: str, default: int) -> int:
    value = payload.get(field)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BadIntegerField(field) from exc
# ---- WebSocket (P0 #11) ----

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_send_text(sock: socket.socket, payload: str) -> None:
    data = payload.encode("utf-8")
    length = len(data)
    header = bytearray([0x81])  # FIN + text opcode
    if length < 126:
        header.append(length)  # MASK=0, 7-bit length
    elif length < (1 << 16):
        header.append(126)
        header.extend(length.to_bytes(2, "big"))
    else:
        header.append(127)
        header.extend(length.to_bytes(8, "big"))
    sock.sendall(bytes(header) + data)


def _ws_send_ping(sock: socket.socket) -> None:
    """Best-effort ping frame; the client is expected to pong but we
    ignore the response ? we just want a keep-alive on the wire."""
    try:
        sock.sendall(b"\x89\x80")  # FIN + ping opcode, MASK=0, length 0
    except OSError:
        pass


def _ws_read_client_frames(sock: socket.socket) -> None:
    """Drain incoming client frames so the recv buffer doesn't grow
    unbounded. We do not act on client messages; the close frame just
    breaks the pump loop."""
    try:
        sock.settimeout(0.0)
        while True:
            head = sock.recv(2)
            if not head or len(head) < 2:
                return
            b1, b2 = head[0], head[1]
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                ext = sock.recv(2)
                length = int.from_bytes(ext, "big")
            elif length == 127:
                ext = sock.recv(8)
                length = int.from_bytes(ext, "big")
            mask = sock.recv(4) if (b2 & 0x80) else b""
            payload_remaining = length
            while payload_remaining:
                chunk = sock.recv(payload_remaining)
                if not chunk:
                    return
                payload_remaining -= len(chunk)
            if opcode == 0x8:  # close
                return
    except (BlockingIOError, OSError):
        return
    finally:
        try:
            sock.settimeout(None)
        except OSError:
            pass


def _serve_websocket(handler, db_path) -> None:
    """Server-side WebSocket handler. Pushed into ``MonitorRequestHandler``
    via ``_do_GET_impl`` when ``/ws`` arrives with the ``Upgrade`` header.

    Subscribes to the per-db ``BroadcastBus`` and writes one text frame
    per published event. Performs the RFC 6455 handshake inline.
    """
    headers = handler.headers
    upgrade = str(headers.get("Upgrade", "")).lower()
    if "websocket" not in upgrade:
        handler.send_response(HTTPStatus.UPGRADE_REQUIRED)
        handler.end_headers()
        return
    if not handler._origin_allowed():
        handler.send_response(HTTPStatus.FORBIDDEN)
        handler.end_headers()
        return
    if not handler._request_authorized():
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.end_headers()
        return
    key = str(headers.get("Sec-WebSocket-Key", "")).strip()
    if not key:
        handler.send_response(HTTPStatus.BAD_REQUEST)
        handler.end_headers()
        return
    accept = _ws_accept_key(key)
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()

    bus = _get_broadcast_bus(str(db_path))
    sid = bus.subscribe()
    sock = handler.connection
    try:
        # Initial hello so the client knows the link is up.
        _ws_send_text(
            sock,
            json.dumps({"type": "hello", "seq": 0, "payload": {"sid": sid}}, ensure_ascii=False),
        )
        last_ping = time.monotonic()
        while True:
            q = bus.queue_for(sid)
            if q is None:
                return
            try:
                event = q.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_ping > 15.0:
                    _ws_send_ping(sock)
                    last_ping = time.monotonic()
                    _ws_read_client_frames(sock)
                continue
            if event is None:
                return
            try:
                _ws_send_text(sock, json.dumps(event, ensure_ascii=False, default=str))
            except (OSError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                return
    finally:
        bus.unsubscribe(sid)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
