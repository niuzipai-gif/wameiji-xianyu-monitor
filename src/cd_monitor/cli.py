from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from cd_monitor.config import load_config
from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import WatchItem
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.notify.base import send_and_record
from cd_monitor.services.notify_dispatcher import notify_opportunities
from cd_monitor.notify.dingtalk import DingTalkNotifier
from cd_monitor.notify.feishu import FeishuNotifier
from cd_monitor.review.backtest import backtest_database_history, backtest_snapshots
from cd_monitor.review.opportunity_report import render_opportunity_report
from cd_monitor.scheduler.recheck_candidate import next_recheck_time
from cd_monitor.services.imports import (
    evaluate_html_files,
    evaluate_files,
    evaluate_json_files,
    import_html_snapshot,
    import_xianyu_csv,
)
from cd_monitor.services.doctor import run_doctor
from cd_monitor.services.live_capture_scan import capture_and_evaluate_live_html
from cd_monitor.services.live_browser_capture import capture_search_html
from cd_monitor.services.live_scan import record_live_scan_status, scan_live_status
from cd_monitor.services.scan import scan_once_mock
from cd_monitor.services.discovery_worker import (
    DiscoveryWorker,
    build_browser_fetchers,
    command_client_from_environment,
)
from cd_monitor.services.xianyu_login_state import export_xianyu_login_state
from cd_monitor.sources.mock import MockWameijiAdapter, MockXianyuAdapter
from cd_monitor.storage.sqlite import (
    add_watch,
    get_opportunity,
    init_db,
    insert_review_decision,
    list_candidate_rechecks,
    list_opportunity_ids,
    list_review_decisions,
    list_sent_alerts,
    list_opportunities,
    list_watch,
    schedule_candidate_recheck,
    update_candidate_recheck_status,
)
from cd_monitor.web_server import DEFAULT_STATIC_DIR, run_web_server

from cd_monitor.services.task_generate import TaskGenerationService


DEFAULT_DB = Path("data/cd_monitor.db")
DEFAULT_WAMEIJI = Path("data/mock/wameiji_items.sample.json")
DEFAULT_XIANYU = Path("data/mock/xianyu_samples.sample.json")



def _resolve_healthy_db_path(preferred: str) -> str:
    """Return a writable, healthy SQLite DB path. If preferred is unusable (e.g. corrupted
    journal or read-only file system), fall back to a fresh file under the system temp dir
    so the server can still serve traffic in degraded environments."""
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path
    p = Path(preferred)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(p), timeout=5) as conn:
            conn.execute("PRAGMA quick_check").fetchone()
        return str(p)
    except sqlite3.DatabaseError:
        # Corrupt journal or similar — fall back to a temp copy.
        fallback_dir = Path(tempfile.gettempdir()) / "cd_monitor_runtime"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback = fallback_dir / "cd_monitor.db"
        try:
            with sqlite3.connect(f"file:{p}?immutable=1", uri=True, timeout=5) as src:
                with sqlite3.connect(str(fallback), timeout=5) as dst:
                    src.backup(dst)
        except Exception:
            # Source unreadable too; init fresh schema
            from cd_monitor.storage.sqlite import init_db
            init_db(str(fallback))
        return str(fallback)
    except (sqlite3.OperationalError, OSError):
        # Permission / disk I/O error — use a writable temp path.
        fallback_dir = Path(tempfile.gettempdir()) / "cd_monitor_runtime"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback = fallback_dir / "cd_monitor.db"
        from cd_monitor.storage.sqlite import init_db
        init_db(str(fallback))
        return str(fallback)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="cd-monitor")
    parser.add_argument("--db", default=None)
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db")
    init.add_argument("--db", dest="command_db", default=None)
    add = sub.add_parser("add-watch")
    add.add_argument("--db", dest="command_db", default=None)
    add.add_argument("--catalog-no", required=True)
    add.add_argument("--jan")
    add.add_argument("--artist")
    add.add_argument("--title-jp")
    add.add_argument("--title-cn")
    add.add_argument("--edition")
    add.add_argument("--required-keyword", action="append", default=[])
    add.add_argument("--excluded-keyword", action="append", default=[])
    add.add_argument("--priority", type=int, default=1)
    add.add_argument("--holding-days", type=int, default=30)
    list_cmd = sub.add_parser("list-watch")
    list_cmd.add_argument("--db", dest="command_db", default=None)
    eval_mock = sub.add_parser("evaluate-mock")
    eval_mock.add_argument("--catalog-no", required=True)
    scan = sub.add_parser("scan-once")
    scan.add_argument("--db", dest="command_db", default=None)
    scan.add_argument("--catalog-no", required=True)
    scan.add_argument("--source", choices=["mock"], default="mock")
    scan.add_argument("--snapshot-dir", default=None)
    scan.add_argument("--notify", action="store_true", help="Send notifications for strong/weak_alert opportunities")
    scan.add_argument("--notify-channel", choices=["feishu", "dingtalk", "all"], default="feishu")
    scan.add_argument("--notify-dry-run", action="store_true", help="Do not actually POST; record dry_run alerts")
    scan.add_argument("--notify-include-review-only", action="store_true")
    scan_watchlist = sub.add_parser("scan-watchlist")
    scan_watchlist.add_argument("--db", dest="command_db", default=None)
    scan_watchlist.add_argument("--source", choices=["mock"], default="mock")
    scan_watchlist.add_argument("--snapshot-dir", default=None)
    scan_live = sub.add_parser("scan-live")
    scan_live.add_argument("--db", dest="command_db", default=None)
    scan_live.add_argument("--catalog-no", required=True)
    login_state = sub.add_parser("xianyu-login-state")
    login_state.add_argument("--output", default="data/xianyu_state.json")
    login_state.add_argument("--url", default="https://www.goofish.com")
    login_state.add_argument("--timeout-seconds", type=int, default=180)
    login_state.add_argument("--poll-interval-seconds", type=float, default=1.0)
    login_state.add_argument("--headless", action="store_true")
    wameiji_state_import = sub.add_parser("wameiji-login-state-import")
    wameiji_state_import.add_argument("json_file", help="Path to JSON captured by the Wameiji Chrome extension")
    wameiji_state_import.add_argument("--output", default="data/wameiji_state.json")
    wameiji_state_import.add_argument("--no-keep-raw", action="store_true",
        help="Do not include the raw extension snapshot inside the saved file")
    xianyu_state_import = sub.add_parser("xianyu-login-state-import")
    xianyu_state_import.add_argument("json_file", help="Path to JSON captured by the Xianyu Chrome extension")
    xianyu_state_import.add_argument("--output", default="data/xianyu_state.json")
    xianyu_state_import.add_argument("--no-keep-raw", action="store_true",
        help="Do not include the raw extension snapshot inside the saved file")
    capture_live = sub.add_parser("capture-live-html")
    capture_live.add_argument("--source", choices=["wameiji", "xianyu"], required=True)
    capture_live.add_argument("--catalog-no", required=True)
    capture_live.add_argument("--output", required=True)
    capture_live.add_argument("--screenshot-output", default=None)
    capture_live.add_argument("--network-output", default=None)
    capture_live.add_argument("--state-file", default=None)
    capture_live.add_argument("--profile-dir", default=None)
    capture_live.add_argument("--xianyu-profile-dir", default=None)
    capture_live.add_argument("--wameiji-state-file", default=None)
    capture_live.add_argument("--timeout-seconds", type=int, default=30)
    capture_live.add_argument("--headless", action="store_true")
    scan_live_html = sub.add_parser("scan-live-html")
    scan_live_html.add_argument("--db", dest="command_db", default=None)
    scan_live_html.add_argument("--catalog-no", required=True)
    scan_live_html.add_argument("--snapshot-dir", default=None)
    scan_live_html.add_argument("--state-file", default=None)
    scan_live_html.add_argument("--profile-dir", default=None)
    scan_live_html.add_argument("--xianyu-profile-dir", default=None)
    scan_live_html.add_argument("--wameiji-state-file", default=None)
    scan_live_html.add_argument("--timeout-seconds", type=int, default=30)
    scan_live_html.add_argument("--headless", action="store_true")
    scan_live_html.add_argument("--notify", action="store_true", help="Send notifications for strong/weak_alert opportunities")
    scan_live_html.add_argument("--notify-channel", choices=["feishu", "dingtalk", "all"], default="feishu")
    scan_live_html.add_argument("--notify-dry-run", action="store_true")
    scan_live_html.add_argument("--notify-include-review-only", action="store_true")

    live_watchlist = sub.add_parser("live-watchlist")
    live_watchlist.add_argument("--db", dest="command_db", default=None)
    live_watchlist.add_argument("--snapshot-dir", default=None)
    live_watchlist.add_argument("--state-file", default=None)
    live_watchlist.add_argument("--profile-dir", default=None)
    live_watchlist.add_argument("--xianyu-profile-dir", default=None)
    live_watchlist.add_argument("--wameiji-state-file", default=None)
    live_watchlist.add_argument("--timeout-seconds", type=int, default=30)
    live_watchlist.add_argument("--headless", action="store_true")
    live_watchlist.add_argument("--notify", action="store_true")
    live_watchlist.add_argument("--notify-channel", choices=["feishu", "dingtalk", "all"], default="feishu")
    live_watchlist.add_argument("--notify-dry-run", action="store_true")
    live_watchlist.add_argument("--notify-include-review-only", action="store_true")
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--db", dest="command_db", default=None)
    backtest.add_argument("--catalog-no", default=None)
    backtest.add_argument("--snapshot-dir", default=None)
    list_opps = sub.add_parser("list-opportunities")
    list_opps.add_argument("--db", dest="command_db", default=None)
    list_opps.add_argument("--limit", type=int, default=50)
    notify = sub.add_parser("notify-dry-run")
    notify.add_argument("--db", dest="command_db", default=None)
    notify.add_argument("--opportunity-id", type=int, required=True)
    notify.add_argument("--channel", choices=["feishu", "dingtalk"], default="feishu")
    notify_real = sub.add_parser("notify")
    notify_real.add_argument("--db", dest="command_db", default=None)
    notify_real.add_argument("--opportunity-id", type=int, required=True)
    notify_real.add_argument("--channel", choices=["feishu", "dingtalk"], default="feishu")
    import_html = sub.add_parser("import-html")
    import_html.add_argument("--source", choices=["wameiji", "xianyu"], required=True)
    import_html.add_argument("--catalog-no", required=True)
    import_html.add_argument("--html", required=True)
    import_html.add_argument("--snapshot-dir", default=None)
    import_csv = sub.add_parser("import-csv")
    import_csv.add_argument("--source", choices=["xianyu"], required=True)
    import_csv.add_argument("--catalog-no", required=True)
    import_csv.add_argument("--csv", required=True)
    import_csv.add_argument("--snapshot-dir", default=None)
    report = sub.add_parser("report")
    report.add_argument("--db", dest="command_db", default=None)
    report.add_argument("--output", required=True)
    report.add_argument("--limit", type=int, default=50)
    review = sub.add_parser("review")
    review.add_argument("--db", dest="command_db", default=None)
    review.add_argument("--opportunity-id", type=int, required=True)
    review.add_argument(
        "--result",
        choices=[
            "accepted_for_personal_collection",
            "rejected_version_mismatch",
            "rejected_xianyu_noise",
            "rejected_low_profit",
            "rejected_sold_out",
            "rejected_condition_bad",
            "rejected_liquidity_poor",
            "rejected_other",
        ],
        required=True,
    )
    review.add_argument("--note", default=None)
    review_list = sub.add_parser("review-list")
    review_list.add_argument("--db", dest="command_db", default=None)
    review_list.add_argument("--opportunity-id", type=int, default=None)
    review_list.add_argument("--limit", type=int, default=50)
    alert_list = sub.add_parser("alert-list")
    alert_list.add_argument("--db", dest="command_db", default=None)
    alert_list.add_argument("--opportunity-id", type=int, default=None)
    alert_list.add_argument("--limit", type=int, default=50)
    recheck_plan = sub.add_parser("recheck-plan")
    recheck_plan.add_argument("--db", dest="command_db", default=None)
    recheck_plan.add_argument("--opportunity-id", type=int, required=True)
    recheck_plan.add_argument("--delay-seconds", type=int, default=120)
    recheck_plan.add_argument("--reason", default="candidate_second_pass")
    recheck_list = sub.add_parser("recheck-list")
    recheck_list.add_argument("--db", dest="command_db", default=None)
    recheck_list.add_argument(
        "--status",
        choices=["pending", "confirmed", "rejected", "human_required", "all"],
        default="pending",
    )
    recheck_list.add_argument("--limit", type=int, default=50)
    recheck_resolve = sub.add_parser("recheck-resolve")
    recheck_resolve.add_argument("--db", dest="command_db", default=None)
    recheck_resolve.add_argument("--recheck-id", type=int, required=True)
    recheck_resolve.add_argument(
        "--status",
        choices=["confirmed", "rejected", "human_required"],
        required=True,
    )
    recheck_resolve.add_argument("--reason", default=None)
    evaluate_files_cmd = sub.add_parser("evaluate-files")
    evaluate_files_cmd.add_argument("--db", dest="command_db", default=None)
    evaluate_files_cmd.add_argument("--catalog-no", required=True)
    evaluate_files_cmd.add_argument("--wameiji-html", required=True)
    evaluate_files_cmd.add_argument("--xianyu-csv", required=True)
    evaluate_files_cmd.add_argument("--snapshot-dir", default=None)
    evaluate_html_cmd = sub.add_parser("evaluate-html")
    evaluate_html_cmd.add_argument("--db", dest="command_db", default=None)
    evaluate_html_cmd.add_argument("--catalog-no", required=True)
    evaluate_html_cmd.add_argument("--wameiji-html", required=True)
    evaluate_html_cmd.add_argument("--xianyu-html", required=True)
    evaluate_html_cmd.add_argument("--snapshot-dir", default=None)
    evaluate_json_cmd = sub.add_parser("evaluate-json")
    evaluate_json_cmd.add_argument("--db", dest="command_db", default=None)
    evaluate_json_cmd.add_argument("--catalog-no", required=True)
    evaluate_json_cmd.add_argument("--wameiji-json", required=True)
    evaluate_json_cmd.add_argument("--xianyu-json", required=True)
    evaluate_json_cmd.add_argument("--snapshot-dir", default=None)
    discovery_worker = sub.add_parser(
        "discovery-worker",
        help="Run the local browser-backed automatic selection collector",
    )
    discovery_worker.add_argument("--db", dest="command_db", default=None)
    discovery_worker.add_argument("--snapshot-dir", default=None)
    discovery_worker.add_argument("--once", action="store_true")
    discovery_worker.add_argument("--poll-seconds", type=int, default=60)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--db", dest="command_db", default=None)
    web = sub.add_parser("web")
    web.add_argument("--db", dest="command_db", default=None)
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--static-dir", default=str(DEFAULT_STATIC_DIR))
    tg = sub.add_parser("task-generate")
    tg.add_argument("--db", dest="command_db", default=None)
    tg.add_argument("--task-name", required=True)
    tg.add_argument("--keyword", default=None)
    tg.add_argument("--description", default=None)
    tg.add_argument("--decision-mode", default="ai", choices=["ai", "keyword"])
    tg.add_argument("--reference-file", default="prompts/macbook_criteria.txt")
    tg.add_argument("--prompts-dir", default="prompts")
    tg.add_argument("--watch", action="store_true", help="Wait for the job to complete and exit non-zero on failure.")
    tg.add_argument("--poll-interval", type=float, default=0.6, help="Polling interval for --watch (seconds).")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    db_path = getattr(args, "command_db", None) or args.db or config.app.db_path or str(DEFAULT_DB)
    # If the configured DB path is unusable (corrupt journal, locked file, read-only),
    # transparently fall back to a fresh DB under the system temp dir so the server still
    # boots in degraded environments.
    db_path = _resolve_healthy_db_path(db_path)

    if args.command == "init-db":
        init_db(db_path)
        print(f"initialized {db_path}")
        return 0
    if args.command == "add-watch":
        watch_id = add_watch(
            db_path,
            WatchItem(
                catalog_no=args.catalog_no,
                jan=args.jan,
                artist=args.artist,
                title_jp=args.title_jp,
                title_cn=args.title_cn,
                edition=args.edition,
                required_keywords=args.required_keyword,
                excluded_keywords=args.excluded_keyword,
                priority=args.priority,
                expected_holding_days=args.holding_days,
            ),
        )
        print(json.dumps({"id": watch_id}, ensure_ascii=False))
        return 0
    if args.command == "list-watch":
        print(json.dumps([_watch_dict(watch) for watch in list_watch(db_path)], ensure_ascii=False, indent=2))
        return 0
    if args.command in {"evaluate-mock", "scan-once"}:
        if args.command == "scan-once":
            result = scan_once_mock(
                args.catalog_no,
                db_path,
                DEFAULT_WAMEIJI,
                DEFAULT_XIANYU,
                args.snapshot_dir or config.app.snapshot_dir,
                config.cost,
                config.evaluation,
            )
            payload = {
                "search_run_id": result.search_run_id,
                "snapshot_path": str(result.snapshot_path),
                "opportunities": [
                    _opportunity_dict_with_id(opportunity_id, opportunity)
                    for opportunity_id, opportunity in zip(
                        result.opportunity_ids,
                        result.opportunities,
                    )
                ],
            }
            if getattr(args, "notify", False):
                payload["notifications"] = notify_opportunities(
                    db_path,
                    result.opportunity_ids,
                    result.opportunities,
                    channel_spec=args.notify_channel,
                    config=config,
                    dry_run=args.notify_dry_run,
                    include_review_only=args.notify_include_review_only,
                )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        opportunity = _evaluate_mock(args.catalog_no)
        print(json.dumps(_opportunity_dict(opportunity), ensure_ascii=False, indent=2))
        return 0
    if args.command == "scan-watchlist":
        try:
            watches = list_watch(db_path)
        except ValueError as _exc:
            # A row in the DB violates the strategy invariant (e.g. fixed
            # strategy with no state file). Surface a clear Chinese error
            # and exit 2 instead of dumping a Python traceback.
            print(f"错误: 任务表中存在账号策略不一致的记录: {_exc}")
            print("请先在 Web 上纠正该任务的 account_strategy/account_state_file。")
            return 2
        # P5.3 bare assertion (port from Usagi ai-goofish-monitor/spider_v2.py):
        # every enabled watch must have at least one usable login state,
        # either a per-platform root state file or a per-task bound account.
        from cd_monitor.services.account_strategy import assert_strategy_consistent, resolve_account_runtime_plan
        from cd_monitor.services.account_repository import AccountRepository

        _roots = {
            "xianyu": Path("data/xianyu_state.json"),
            "wameiji": Path("data/wameiji_state.json"),
        }
        try:
            _account_repo = AccountRepository(db_path)
        except Exception:
            _account_repo = None

        # Delegate to the module-level helper (see has_bound_account).

        def _has_any_state_file() -> bool:
            if any(pth.exists() for pth in _roots.values()):
                return True
            if _account_repo is None:
                return False
            try:
                return any(a.get("state_file") for a in _account_repo.list_all())
            except Exception:
                return False

        # Mock scans do not need a real login state, so only the strategy
        # consistency check (fixed-without-file) is enforced. The full login-state
        # availability check fires only when scanning with a live source.
        _requires_login_state = args.source != "mock"

        # Pre-flight bare assertion (port from Usagi ai-goofish-monitor/
        # spider_v2.py:75-93): if NO login state exists anywhere — no root
        # state file, no task-bound account, and no account in the pool —
        # bail with one clear Chinese error instead of looping pointlessly.
        if _requires_login_state and (
            not any(pth.exists() for pth in _roots.values())
            and not has_bound_account(watches)
            and not _has_any_state_file()
        ):
            print("错误: 未找到登录状态文件。请在 data/ 中添加账号或绑定根 state 文件。")
            return 2

        for _w in watches:
            if not _w.enabled:
                continue
            try:
                assert_strategy_consistent(_w.account_strategy, _w.account_state_file)
            except ValueError as _exc:
                print(f"错误: 任务 {_w.catalog_no} 账号策略不一致: {_exc}")
                return 2
            if not _requires_login_state:
                continue
            _plan = resolve_account_runtime_plan(
                strategy=_w.account_strategy,
                account_state_file=_w.account_state_file,
                has_root_state_file=any(p.exists() for p in _roots.values()),
                available_account_files=[
                    a["state_file"] for a in (_account_repo.list_all() if _account_repo else [])
                ],
            )
            _has_any = (
                _plan["forced_account"]
                or _plan["prefer_root_state"]
                or _plan["use_account_pool"]
            )
            if not _has_any:
                print(f"错误: 任务 {_w.catalog_no} 没有可用登录态。请先在 Web 上导入账号或绑定根 state 文件。")
                return 2
        all_opportunities = []
        all_opportunity_ids = []
        snapshot_dir = args.snapshot_dir or config.app.snapshot_dir
        for watch in watches:
            result = scan_once_mock(
                watch.catalog_no,
                db_path,
                DEFAULT_WAMEIJI,
                DEFAULT_XIANYU,
                snapshot_dir,
                config.cost,
                config.evaluation,
                watch,
            )
            all_opportunities.extend(result.opportunities)
            all_opportunity_ids.extend(result.opportunity_ids)
        print(
            json.dumps(
                {
                    "scanned_count": len(watches),
                    "opportunity_count": len(all_opportunities),
                    "opportunities": [
                        _opportunity_dict_with_id(opportunity_id, opportunity)
                        for opportunity_id, opportunity in zip(
                            all_opportunity_ids,
                            all_opportunities,
                        )
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "live-watchlist":
        watches = list_watch(db_path)
        all_opportunity_ids: list[int] = []
        all_notifications: list[dict] = []
        all_human_required: list[dict] = []
        blocked_count = 0
        for watch in watches:
            result = asyncio.run(
                capture_and_evaluate_live_html(
                    watch.catalog_no,
                    db_path,
                    args.snapshot_dir or config.app.snapshot_dir,
                    config.cost,
                    config.evaluation,
                    state_file=args.state_file or config.browser.xianyu_state_file,
                    profile_dir=args.profile_dir or config.browser.wameiji_profile_dir or None,
                    xianyu_profile_dir=args.xianyu_profile_dir or config.browser.xianyu_profile_dir or None,
                    wameiji_state_file=args.wameiji_state_file or config.browser.wameiji_state_file or None,
                    timeout_seconds=args.timeout_seconds,
                    headless=args.headless or config.browser.wameiji_headless,
                )
            )
            if result.get("status") != "ok":
                blocked_count += 1
                all_human_required.append(
                    {
                        "catalog_no": watch.catalog_no,
                        "status": result.get("status"),
                        "wameiji_status": result.get("wameiji_capture", {}).get("status"),
                        "wameiji_error_type": result.get("wameiji_capture", {}).get("error_type"),
                        "xianyu_status": (result.get("xianyu_capture") or {}).get("status"),
                    }
                )
                continue
            ids = list(result.get("opportunity_ids") or [])
            all_opportunity_ids.extend(ids)
            if args.notify and ids:
                opps = [get_opportunity(db_path, oid) for oid in ids]
                all_notifications.extend(
                    notify_opportunities(
                        db_path,
                        ids,
                        opps,
                        channel_spec=args.notify_channel,
                        config=config,
                        dry_run=args.notify_dry_run,
                        include_review_only=args.notify_include_review_only,
                    )
                )
        print(
            json.dumps(
                {
                    "scanned_count": len(watches),
                    "ok_count": len(watches) - blocked_count,
                    "blocked_count": blocked_count,
                    "opportunity_count": len(all_opportunity_ids),
                    "blocked": all_human_required,
                    "notifications": all_notifications,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    if args.command == "scan-live":
        result = scan_live_status(args.catalog_no, config.browser)
        record_live_scan_status(db_path, result)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "xianyu-login-state":
        result = asyncio.run(
            export_xianyu_login_state(
                args.output,
                login_url=args.url,
                timeout_seconds=args.timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                headless=args.headless,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "wameiji-login-state-import":
        from pathlib import Path as _P
        from cd_monitor.services.wameiji_login_state import (
            WameijiLoginStateError,
            save_wameiji_login_state,
        )
        input_path = _P(args.json_file)
        if not input_path.exists():
            print(json.dumps({"error": "file_not_found", "path": str(input_path)}, ensure_ascii=False, indent=2))
            return 1
        try:
            content = input_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"error": "read_failed", "detail": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        try:
            summary = save_wameiji_login_state(
                content,
                args.output,
                keep_raw_snapshot=not args.no_keep_raw,
            )
        except WameijiLoginStateError as exc:
            print(json.dumps({"error": "invalid_state", "detail": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "xianyu-login-state-import":
        from pathlib import Path as _P
        from cd_monitor.services.xianyu_login_state import (
            XianyuLoginStateImportError,
            save_xianyu_login_state,
        )
        input_path = _P(args.json_file)
        if not input_path.exists():
            print(json.dumps({"error": "file_not_found", "path": str(input_path)}, ensure_ascii=False, indent=2))
            return 1
        try:
            content = input_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"error": "read_failed", "detail": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        try:
            summary = save_xianyu_login_state(
                content,
                args.output,
                keep_raw_snapshot=not args.no_keep_raw,
            )
        except XianyuLoginStateImportError as exc:
            print(json.dumps({"error": "invalid_state", "detail": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "capture-live-html":
        result = asyncio.run(
            capture_search_html(
                args.source,
                args.catalog_no,
                args.output,
                state_file=args.state_file,
                profile_dir=args.profile_dir or config.browser.wameiji_profile_dir or None,
                xianyu_profile_dir=args.xianyu_profile_dir or config.browser.xianyu_profile_dir or None,
                wameiji_state_file=args.wameiji_state_file or config.browser.wameiji_state_file or None,
                screenshot_path=args.screenshot_output,
                network_log_path=args.network_output,
                timeout_seconds=args.timeout_seconds,
                headless=args.headless or config.browser.wameiji_headless,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "scan-live-html":
        result = asyncio.run(
            capture_and_evaluate_live_html(
                args.catalog_no,
                db_path,
                args.snapshot_dir or config.app.snapshot_dir,
                config.cost,
                config.evaluation,
                state_file=args.state_file or config.browser.xianyu_state_file,
                profile_dir=args.profile_dir or config.browser.wameiji_profile_dir or None,
                xianyu_profile_dir=args.xianyu_profile_dir or config.browser.xianyu_profile_dir or None,
                wameiji_state_file=args.wameiji_state_file or config.browser.wameiji_state_file or None,
                timeout_seconds=args.timeout_seconds,
                headless=args.headless or config.browser.wameiji_headless,
            )
        )
        if args.notify and result.get("status") == "ok":
            opp_ids = list(result.get("opportunity_ids") or [])
            opps = [get_opportunity(db_path, oid) for oid in opp_ids]
            result["notifications"] = notify_opportunities(
                db_path,
                opp_ids,
                opps,
                channel_spec=args.notify_channel,
                config=config,
                dry_run=args.notify_dry_run,
                include_review_only=args.notify_include_review_only,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "backtest":
        opportunities = (
            backtest_database_history(db_path, args.catalog_no, config.cost, config.evaluation)
            if args.catalog_no
            else backtest_snapshots(args.snapshot_dir or config.app.snapshot_dir, config.cost, config.evaluation)
        )
        print(json.dumps([_opportunity_dict(opp) for opp in opportunities], ensure_ascii=False, indent=2))
        return 0
    if args.command == "list-opportunities":
        opportunity_ids = list_opportunity_ids(db_path, args.limit)
        opportunities = list_opportunities(db_path, args.limit)
        print(
            json.dumps(
                [
                    _opportunity_dict_with_id(opportunity_id, opportunity)
                    for opportunity_id, opportunity in zip(opportunity_ids, opportunities)
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "notify-dry-run":
        opportunity = get_opportunity(db_path, args.opportunity_id)
        notifier = FeishuNotifier() if args.channel == "feishu" else DingTalkNotifier()
        response = send_and_record(db_path, args.opportunity_id, opportunity, notifier, dry_run=True)
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "notify":
        opportunity = get_opportunity(db_path, args.opportunity_id)
        notifier = (
            FeishuNotifier(config.notify.feishu_webhook_url)
            if args.channel == "feishu"
            else DingTalkNotifier(config.notify.dingtalk_webhook_url)
        )
        response = send_and_record(db_path, args.opportunity_id, opportunity, notifier, dry_run=False)
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "import-html":
        status, snapshot_path = import_html_snapshot(
            args.source,
            args.catalog_no,
            args.html,
            args.snapshot_dir or config.app.snapshot_dir,
        )
        print(
            json.dumps(
                {
                    "status": status.status,
                    "error_type": status.error_type,
                    "error_message": status.error_message,
                    "item_count": len(status.items),
                    "snapshot_path": str(snapshot_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "import-csv":
        samples, estimate, snapshot_path = import_xianyu_csv(
            args.catalog_no,
            args.csv,
            args.snapshot_dir or config.app.snapshot_dir,
        )
        print(
            json.dumps(
                {
                    "source": args.source,
                    "sample_count": len(samples),
                    "estimate": {
                        "reference_price_cny": estimate.reference_price_cny,
                        "valid_sample_count": estimate.valid_sample_count,
                        "liquidity_status": estimate.liquidity_status,
                        "expected_sale_price_cny": estimate.expected_sale_price_cny,
                    },
                    "snapshot_path": str(snapshot_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "report":
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        opportunity_ids = list_opportunity_ids(db_path, args.limit)
        opportunities = list_opportunities(db_path, args.limit)
        output_path.write_text(
            render_opportunity_report(
                opportunities,
                list_review_decisions(db_path),
                list_candidate_rechecks(db_path, status=None),
                opportunity_ids,
            ),
            encoding="utf-8",
        )
        print(json.dumps({"output": str(output_path), "opportunity_count": len(opportunities)}, ensure_ascii=False))
        return 0
    if args.command == "review":
        review_id = insert_review_decision(
            db_path,
            args.opportunity_id,
            args.result,
            args.note,
        )
        print(json.dumps({"id": review_id, "result": args.result}, ensure_ascii=False))
        return 0
    if args.command == "review-list":
        rows = list_review_decisions(db_path, args.opportunity_id, args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "alert-list":
        rows = list_sent_alerts(db_path, args.opportunity_id, args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "recheck-plan":
        row = schedule_candidate_recheck(
            db_path,
            args.opportunity_id,
            next_recheck_time(delay_seconds=args.delay_seconds),
            args.reason,
        )
        print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "recheck-list":
        status = None if args.status == "all" else args.status
        rows = list_candidate_rechecks(db_path, status=status, limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "recheck-resolve":
        row = update_candidate_recheck_status(
            db_path,
            args.recheck_id,
            args.status,
            args.reason,
        )
        print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "evaluate-files":
        opportunities, opportunity_ids, snapshot_path = evaluate_files(
            args.catalog_no,
            args.wameiji_html,
            args.xianyu_csv,
            db_path,
            args.snapshot_dir or config.app.snapshot_dir,
            config.cost,
            config.evaluation,
        )
        print(
            json.dumps(
                {
                    "snapshot_path": str(snapshot_path),
                    "opportunities": [
                        _opportunity_dict_with_id(opportunity_id, opportunity)
                        for opportunity_id, opportunity in zip(
                            opportunity_ids,
                            opportunities,
                        )
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "evaluate-html":
        opportunities, opportunity_ids, snapshot_path = evaluate_html_files(
            args.catalog_no,
            args.wameiji_html,
            args.xianyu_html,
            db_path,
            args.snapshot_dir or config.app.snapshot_dir,
            config.cost,
            config.evaluation,
        )
        print(
            json.dumps(
                {
                    "snapshot_path": str(snapshot_path),
                    "opportunities": [
                        _opportunity_dict_with_id(opportunity_id, opportunity)
                        for opportunity_id, opportunity in zip(
                            opportunity_ids,
                            opportunities,
                        )
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "evaluate-json":
        opportunities, opportunity_ids, snapshot_path = evaluate_json_files(
            args.catalog_no,
            args.wameiji_json,
            args.xianyu_json,
            db_path,
            args.snapshot_dir or config.app.snapshot_dir,
            config.cost,
            config.evaluation,
        )
        print(
            json.dumps(
                {
                    "snapshot_path": str(snapshot_path),
                    "opportunities": [
                        _opportunity_dict_with_id(opportunity_id, opportunity)
                        for opportunity_id, opportunity in zip(
                            opportunity_ids,
                            opportunities,
                        )
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "discovery-worker":
        init_db(db_path)
        snapshot_root = Path(
            args.snapshot_dir or Path(config.app.snapshot_dir) / "discovery"
        )
        fetch_wameiji, fetch_xianyu = build_browser_fetchers(config, snapshot_root)
        worker = DiscoveryWorker(
            db_path=db_path,
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
            command_client=command_client_from_environment(),
        )

        async def run_worker() -> None:
            while True:
                result = await worker.run_once()
                print(json.dumps(asdict(result), ensure_ascii=False))
                if args.once:
                    return
                await asyncio.sleep(max(15, args.poll_seconds))

        asyncio.run(run_worker())
        return 0
    if args.command == "doctor":
        result = run_doctor(config, db_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.command == "web":
        run_web_server(db_path, args.host, args.port, args.static_dir)
        return 0
    if args.command == "task-generate":
        return _run_task_generate(args, db_path)
    return 2

def has_bound_account(items) -> bool:
    """True if any enabled watch has a non-empty per-task account_state_file.

    Mirrors the port from Usagi ai-goofish-monitor/spider_v2.py: a CLI run is
    only safe when at least one watch in the list already binds a usable
    login-state snapshot, otherwise every scraper call would loop into the
    "no cookies" failure mode.
    """
    for _w in items:
        if not getattr(_w, "enabled", True):
            continue
        _f = getattr(_w, "account_state_file", None)
        if isinstance(_f, str) and _f.strip():
            return True
    return False


def _run_task_generate(args, db_path: str) -> int:
    """CLI wrapper around TaskGenerationService.

    Mirrors the HTTP `POST /api/watchlist/generate` endpoint so users can
    AI-generate a task without running the web server. AI mode spawns the
    same 6-step background job; --watch polls until terminal.
    """
    import os
    import time
    from cd_monitor.core.models import WatchItem
    from cd_monitor.services.task_generate import TaskGenerationService
    from cd_monitor.storage.sqlite import add_watch, init_db

    task_name = (args.task_name or "").strip()
    keyword = (args.keyword or task_name).strip()
    description = (args.description or "").strip()
    decision_mode = args.decision_mode or "ai"
    if not task_name:
        print(json.dumps({"error": "task_name_required"}, ensure_ascii=False, indent=2))
        return 2
    if decision_mode == "ai" and not description:
        print(json.dumps({
            "error": "description_required",
            "detail": "AI 判断模式下，详细需求(description)不能为空。",
        }, ensure_ascii=False, indent=2))
        return 2

    reference_path = args.reference_file or "prompts/macbook_criteria.txt"
    if not os.path.exists(reference_path):
        print(json.dumps({"error": "reference_not_found", "path": reference_path},
                         ensure_ascii=False, indent=2))
        return 2

    prompts_dir = args.prompts_dir or "prompts"
    # P5.3 parity: if AI generation will trigger live scraping, every
    # enabled watch must have a usable login-state snapshot. Bare
    # assertion mirrors the scan-watchlist pre-flight (see
    # cd_monitor.cli.has_bound_account).
    if decision_mode == "ai":
        try:
            _pre_watches = list_watch(db_path)
        except Exception:
            _pre_watches = []
        if not has_bound_account(_pre_watches):
            _roots = {
                "xianyu": Path("data/xianyu_state.json"),
                "wameiji": Path("data/wameiji_state.json"),
            }
            if not any(pth.exists() for pth in _roots.values()):
                print(json.dumps({
                    "error": "no_login_state",
                    "detail": "AI 生成需要登录状态：请在 data/ 中绑定账号或提供 state file。",
                }, ensure_ascii=False, indent=2))
                return 2

    service = TaskGenerationService(prompts_dir=prompts_dir)
    job = service.create_job(task_name=task_name, catalog_no=keyword,
                             description=description, decision_mode=decision_mode)

    if decision_mode == "keyword":
        # keyword mode: no AI call, create the watch row directly.
        init_db(db_path)
        watch_id = add_watch(db_path, WatchItem(
            catalog_no=keyword, artist=task_name,
            decision_mode="keyword", description=description or "",
            required_keywords=[keyword] if keyword else [],
        ))
        result = {
            "mode": "keyword",
            "task_name": task_name,
            "keyword": keyword,
            "watch_id": watch_id,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # AI mode: kick background job, optionally watch it.
    service.track(
        service.run_ai_generation_job(
            job_id=job.job_id,
            reference_file_path=reference_path,
            db_path=db_path,
        ),
        job_id=job.job_id,
    )

    if not args.watch:
        # Fire-and-forget: return the snapshot immediately so callers can poll.
        print(json.dumps(job.to_dict(), ensure_ascii=False, indent=2))
        return 0

    # Watch mode: poll until terminal, mirror the 6 steps live.
    print("任务生成中，6 步实时进度：", flush=True)
    last_step = None
    while True:
        current = service.get_job(job.job_id)
        if current is None:
            print("任务已丢失。", flush=True)
            return 2
        if current.current_step and current.current_step != last_step:
            last_step = current.current_step
            step_label = next((s.label for s in current.steps if s.key == last_step), last_step)
            # Progress lines are prefixed with ::STEP:: so callers can grep / strip them.
            print("::STEP::" + json.dumps({"step": last_step, "label": step_label, "message": current.message or ""}, ensure_ascii=False), flush=True)
        if current.status in ("completed", "failed"):
            # Final machine-readable summary on a single line for reliable parsing.
            print(json.dumps(current.to_dict(), ensure_ascii=False))
            return 0 if current.status == "completed" else 1
        time.sleep(max(0.05, args.poll_interval or 0.6))




def _evaluate_mock(catalog_no: str):
    watch = WatchItem(catalog_no=catalog_no)
    item = MockWameijiAdapter(DEFAULT_WAMEIJI).search(watch)[0]
    samples = MockXianyuAdapter(DEFAULT_XIANYU).search_samples(watch)
    match = compute_match_confidence(item, watch)
    estimate = estimate_xianyu_price(samples, edition_confidence=max(0.7, match.confidence))
    cost = compute_landed_cost(item, expected_holding_days=watch.expected_holding_days)
    return evaluate_opportunity(watch, item, match, estimate, cost)


def _opportunity_dict(opportunity) -> dict:
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


def _opportunity_dict_with_id(opportunity_id: int, opportunity) -> dict:
    payload = _opportunity_dict(opportunity)
    payload["id"] = opportunity_id
    return payload


def _watch_dict(watch: WatchItem) -> dict:
    return {
        "catalog_no": watch.catalog_no,
        "jan": watch.jan,
        "artist": watch.artist,
        "title_jp": watch.title_jp,
        "title_cn": watch.title_cn,
        "edition": watch.edition,
        "required_keywords": watch.required_keywords,
        "excluded_keywords": watch.excluded_keywords,
        "priority": watch.priority,
        "expected_holding_days": watch.expected_holding_days,
    }


if __name__ == "__main__":
    raise SystemExit(main())
