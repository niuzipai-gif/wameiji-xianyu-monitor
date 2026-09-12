import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from cd_monitor.services.scan import scan_once_mock
from cd_monitor.storage.sqlite import add_watch, init_db
from cd_monitor.core.models import WatchItem
from cd_monitor.web_server import create_server


def test_web_server_serves_dashboard_api_and_static_app(tmp_path) -> None:
    db_path = tmp_path / "web.db"
    init_db(db_path)
    add_watch(db_path, WatchItem(catalog_no="SRCL-3520", artist="L'Arc-en-Ciel", priority=3))
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        health = _get_json(f"{base_url}/api/health")
        assert health["ok"] is True
        assert health["database"].endswith("web.db")

        summary = _get_json(f"{base_url}/api/summary")
        assert summary["watch_count"] == 1
        assert summary["opportunity_count"] == 1
        assert summary["strong_alert_count"] >= 0

        opportunities = _get_json(f"{base_url}/api/opportunities")
        assert opportunities["items"][0]["catalog_no"] == "SRCL-3520"
        assert opportunities["items"][0]["expected_profit"] > 0
        assert opportunities["items"][0]["item_title"]

        html = _get_text(f"{base_url}/")
        # Kuro Atelier Edition v8 frontend
        assert "WAMEIJI-XIANYU" in html
        assert "Kuro Atelier" in html
        assert "app.js" in html
        assert "kuro.css" in html
        assert "kuro_chibi_avatar.png" in html
        assert "kuro_mascot.png" in html
        # 6 pages wired in sidebar nav
        assert "AI 闲鱼猎手" in html
        assert "首页机会流" in html
        assert "每日推荐榜" in html
        assert "任务管理" in html
        assert "号池管理" in html
        assert "运行日志" in html
        assert "策略设置" in html
        assert "左侧闲鱼以淡黄色显示搜索挂牌价样本" in html
        assert "右侧挖煤姬以淡粉白显示详情已核验进货价" in html
        # new task form + accounts + logs + settings sections
        assert "newTaskForm" in html
        assert "登录资料只保存在采集电脑" in html
        assert "浏览器扩展" not in html
        assert "logList" in html
        assert "doctorOutput" in html
        # modal scaffolding
        assert "detailModal" in html
        assert "filterModal" in html
        app_js = _get_text(f"{base_url}/app.js")
        # 6 page switcher + render functions
        assert "switchPage" in app_js
        assert "renderHome" in app_js
        assert "renderRanking" in app_js
        assert "renderTasks" in app_js
        assert "renderAccounts" in app_js
        assert "renderLogs" in app_js
        assert "renderSettings" in app_js
        # new endpoints wired
        assert "showOpportunityDetail" in app_js
        assert "/api/opportunities/" in app_js
        assert "/api/user-settings" in app_js
        assert "/api/search" in app_js
        assert "/api/watchlist/" in app_js and "hard-delete" in app_js
        assert "Kuro Atelier" in app_js or "kuro" in app_js
        # frontend labels
        assert "强提醒" in app_js
        assert "弱提醒" in app_js
        assert "净利润" in app_js or "预计利润" in app_js
        assert "登录态" in app_js or "登录" in app_js
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_trigger_mock_scan(tmp_path) -> None:
    db_path = tmp_path / "web-scan.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        payload = _post_json(f"{base_url}/api/scan/mock", {"catalog_no": "SRCL-3520"})

        assert payload["catalog_no"] == "SRCL-3520"
        assert payload["opportunity_count"] == 1
        assert payload["opportunities"][0]["id"] > 0
        assert payload["opportunities"][0]["catalog_no"] == "SRCL-3520"
        assert payload["opportunities"][0]["xianyu_reference_price"] > 0
        assert payload["opportunities"][0]["landed_cost"] > 0
        assert payload["opportunities"][0]["turnover_adjusted_roi"] > 0
        assert payload["opportunities"][0]["valid_xianyu_sample_count"] >= 3
        assert payload["opportunities"][0]["liquidity_status"]
        assert payload["opportunities"][0]["item_url"]
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_scan_live_reports_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_ENABLED", raising=False)
    db_path = tmp_path / "web-live.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        payload = _post_json(f"{base_url}/api/scan/live", {"catalog_no": "SRCL-3520"})

        assert payload["status"] == "disabled"
        assert payload["opportunity_count"] == 0
        assert payload["wameiji"]["status"] == "disabled"
        assert payload["xianyu"]["status"] == "disabled"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_exposes_opportunity_detail_and_search_runs(tmp_path) -> None:
    db_path = tmp_path / "web-detail.db"
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        opportunities = _get_json(f"{base_url}/api/opportunities")
        opportunity_id = opportunities["items"][0]["id"]

        detail = _get_json(f"{base_url}/api/opportunities/{opportunity_id}")
        assert detail["opportunity"]["catalog_no"] == "SRCL-3520"
        assert detail["market_items"][0]["title"].startswith("Artist Album")
        assert len(detail["xianyu_samples"]) == 7
        assert detail["xianyu_samples"][0]["catalog_no"] == "SRCL-3520"

        runs = _get_json(f"{base_url}/api/search-runs")
        assert runs["items"][0]["keyword"] == "SRCL-3520"
        assert runs["items"][0]["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_exposes_review_options(tmp_path) -> None:
    server = create_server("127.0.0.1", 0, tmp_path / "web-review-options.db", static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        options = _get_json(f"{base_url}/api/review/options")

        values = [item["value"] for item in options["results"]]
        assert "accepted_for_personal_collection" in values
        assert "rejected_version_mismatch" in values
        assert any("确认仍可购买" in item["label"] for item in options["checklist"])
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_opportunity_detail_includes_review_and_alert_history(tmp_path) -> None:
    db_path = tmp_path / "web-detail-history.db"
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        opportunities = _get_json(f"{base_url}/api/opportunities")
        opportunity_id = opportunities["items"][0]["id"]
        _post_json(
            f"{base_url}/api/review",
            {
                "opportunity_id": opportunity_id,
                "result": "accepted_for_personal_collection",
                "note": "detail history review",
            },
        )
        _post_json(
            f"{base_url}/api/notify",
            {"opportunity_id": opportunity_id, "channel": "feishu", "dry_run": True},
        )

        detail = _get_json(f"{base_url}/api/opportunities/{opportunity_id}")

        assert detail["review_decisions"][0]["opportunity_id"] == opportunity_id
        assert detail["review_decisions"][0]["note"] == "detail history review"
        assert detail["sent_alerts"][0]["opportunity_id"] == opportunity_id
        assert detail["sent_alerts"][0]["channel"] == "feishu"
        assert detail["sent_alerts"][0]["status"] == "dry_run"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_scan_watchlist(tmp_path) -> None:
    db_path = tmp_path / "web-watchlist-scan.db"
    init_db(db_path)
    add_watch(db_path, WatchItem(catalog_no="SRCL-3520"))
    add_watch(db_path, WatchItem(catalog_no="NO-SUCH-ITEM"))
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        payload = _post_json(f"{base_url}/api/scan/watchlist", {})

        assert payload["scanned_count"] == 2
        assert payload["opportunity_count"] == 1
        assert payload["missed_catalogs"] == ["NO-SUCH-ITEM"]
        assert payload["errors"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_evaluate_json_files_backtest_and_write_report(tmp_path) -> None:
    db_path = tmp_path / "web-import.db"
    snapshot_dir = tmp_path / "snapshots"
    report_path = tmp_path / "review-report.md"
    wameiji_path = tmp_path / "wameiji.json"
    xianyu_path = tmp_path / "xianyu.json"
    wameiji_path.write_text(
        json.dumps(
            [
                {
                    "source": "wameiji",
                    "source_site": "mercari",
                    "external_item_id": "w-json-1",
                    "title": "Artist SRCL-3520 初回限定 帯付き",
                    "price": 1200,
                    "currency": "JPY",
                    "availability": "available",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    xianyu_path.write_text(
        json.dumps(
            [
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 260},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 280},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 300},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        imported = _post_json(
            f"{base_url}/api/import/evaluate-json",
            {
                "catalog_no": "SRCL-3520",
                "wameiji_json": str(wameiji_path),
                "xianyu_json": str(xianyu_path),
                "snapshot_dir": str(snapshot_dir),
            },
        )
        assert imported["opportunity_count"] == 1
        assert imported["snapshot_path"].endswith(".json")
        assert imported["opportunities"][0]["id"] > 0

        backtest = _post_json(
            f"{base_url}/api/backtest",
            {"catalog_no": "SRCL-3520", "snapshot_dir": str(snapshot_dir)},
        )
        assert backtest["opportunity_count"] == 1
        assert backtest["opportunities"][0]["catalog_no"] == "SRCL-3520"

        report = _post_json(
            f"{base_url}/api/report",
            {"output": str(report_path), "limit": 20},
        )
        assert report["opportunity_count"] == 1
        assert report_path.exists()
        assert "CD 价差人工复核报告" in report_path.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_evaluate_html_and_csv_files(tmp_path) -> None:
    db_path = tmp_path / "web-import-files.db"
    snapshot_dir = tmp_path / "snapshots"
    html_path = tmp_path / "wameiji.html"
    csv_path = tmp_path / "xianyu.csv"
    html_path.write_text(
        """
        <div data-item-card>
          <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定 帯付き</a>
          <span data-price>¥1,200</span>
          <span data-source-site>mercari</span>
        </div>
        """,
        encoding="utf-8",
    )
    csv_path.write_text(
        "title,price_cny\n"
        "Artist SRCL-3520,260\n"
        "Artist SRCL-3520,280\n"
        "Artist SRCL-3520,300\n",
        encoding="utf-8",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        imported = _post_json(
            f"{base_url}/api/import/evaluate-files",
            {
                "catalog_no": "SRCL-3520",
                "wameiji_html": str(html_path),
                "xianyu_csv": str(csv_path),
                "snapshot_dir": str(snapshot_dir),
            },
        )

        assert imported["opportunity_count"] == 1
        assert imported["snapshot_path"].endswith(".json")
        assert imported["opportunities"][0]["id"] > 0
        assert imported["opportunities"][0]["catalog_no"] == "SRCL-3520"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_evaluate_wameiji_and_xianyu_html_files(tmp_path) -> None:
    db_path = tmp_path / "web-import-html.db"
    snapshot_dir = tmp_path / "snapshots"
    wameiji_path = tmp_path / "wameiji.html"
    xianyu_path = tmp_path / "xianyu.html"
    wameiji_path.write_text(
        """
        <div data-item-card>
          <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定 帯付き</a>
          <span data-price>¥1,200</span>
          <span data-source-site>mercari</span>
        </div>
        """,
        encoding="utf-8",
    )
    xianyu_path.write_text(
        """
        <div data-xianyu-card>
          <a href="https://example.invalid/x/1">Artist SRCL-3520 初回限定</a>
          <span data-price>￥260</span>
        </div>
        <div data-xianyu-card>
          <a href="https://example.invalid/x/2">Artist SRCL-3520 通常盤</a>
          <span data-price>￥280</span>
        </div>
        <div data-xianyu-card>
          <a href="https://example.invalid/x/3">Artist SRCL-3520 帯付き</a>
          <span data-price>￥300</span>
        </div>
        """,
        encoding="utf-8",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        imported = _post_json(
            f"{base_url}/api/import/evaluate-html",
            {
                "catalog_no": "SRCL-3520",
                "wameiji_html": str(wameiji_path),
                "xianyu_html": str(xianyu_path),
                "snapshot_dir": str(snapshot_dir),
            },
        )

        assert imported["opportunity_count"] == 1
        assert imported["opportunities"][0]["id"] > 0
        assert imported["opportunities"][0]["catalog_no"] == "SRCL-3520"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_evaluate_pasted_wameiji_and_xianyu_html(tmp_path) -> None:
    db_path = tmp_path / "web-import-html-text.db"
    snapshot_dir = tmp_path / "snapshots"
    wameiji_html = """
    <section>
      <article>
        <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定 帯付き</a>
        <span>価格 1,200円</span>
      </article>
    </section>
    """
    xianyu_html = """
    <main>
      <article>
        <a href="https://example.invalid/x/1">Artist SRCL-3520 初回限定</a>
        <span>260元</span>
      </article>
      <article>
        <a href="https://example.invalid/x/2">Artist SRCL-3520 通常盤</a>
        <span>CNY 280</span>
      </article>
      <article>
        <a href="https://example.invalid/x/3">Artist SRCL-3520 帯付き</a>
        <span>RMB 300</span>
      </article>
    </main>
    """
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        imported = _post_json(
            f"{base_url}/api/import/evaluate-html-text",
            {
                "catalog_no": "SRCL-3520",
                "wameiji_html_text": wameiji_html,
                "xianyu_html_text": xianyu_html,
                "snapshot_dir": str(snapshot_dir),
            },
        )

        assert imported["opportunity_count"] == 1
        assert imported["opportunities"][0]["id"] > 0
        assert imported["opportunities"][0]["catalog_no"] == "SRCL-3520"
        assert imported["snapshot_path"].endswith(".json")
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_schedule_candidate_recheck(tmp_path) -> None:
    db_path = tmp_path / "web-rechecks.db"
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        opportunity_id = _get_json(f"{base_url}/api/opportunities")["items"][0]["id"]

        scheduled = _post_json(
            f"{base_url}/api/rechecks",
            {
                "opportunity_id": opportunity_id,
                "delay_seconds": 1,
                "reason": "web candidate recheck",
            },
        )
        rechecks = _get_json(f"{base_url}/api/rechecks")

        assert scheduled["opportunity_id"] == opportunity_id
        assert scheduled["status"] == "pending"
        assert scheduled["reason"] == "web candidate recheck"
        assert scheduled["scheduled_at"]
        assert rechecks["items"][0]["opportunity_id"] == opportunity_id
        assert rechecks["items"][0]["status"] == "pending"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_returns_400_for_invalid_integer_fields(tmp_path) -> None:
    db_path = tmp_path / "web-invalid-int.db"
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        opportunity_id = _get_json(f"{base_url}/api/opportunities")["items"][0]["id"]
        request = urllib.request.Request(
            f"{base_url}/api/rechecks",
            data=json.dumps(
                {"opportunity_id": opportunity_id, "delay_seconds": "soon"},
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode("utf-8"))
            assert body["error"] == "invalid_integer"
            assert body["field"] == "delay_seconds"
        else:
            raise AssertionError("Expected invalid integer field to return 400")
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_resolve_candidate_recheck(tmp_path) -> None:
    db_path = tmp_path / "web-recheck-resolve.db"
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        opportunity_id = _get_json(f"{base_url}/api/opportunities")["items"][0]["id"]
        scheduled = _post_json(
            f"{base_url}/api/rechecks",
            {"opportunity_id": opportunity_id, "delay_seconds": 60, "reason": "resolve me"},
        )

        resolved = _post_json(
            f"{base_url}/api/rechecks/{scheduled['id']}/resolve",
            {"status": "confirmed", "reason": "still available"},
        )
        rechecks = _get_json(f"{base_url}/api/rechecks")
        detail = _get_json(f"{base_url}/api/opportunities/{opportunity_id}")

        assert resolved["id"] == scheduled["id"]
        assert resolved["status"] == "confirmed"
        assert resolved["reason"] == "still available"
        assert rechecks["items"] == []
        assert detail["candidate_rechecks"][0]["status"] == "confirmed"
        assert detail["candidate_rechecks"][0]["reason"] == "still available"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_notify_dry_run_and_update_alert_log(tmp_path) -> None:
    db_path = tmp_path / "web-notify.db"
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        opportunities = _get_json(f"{base_url}/api/opportunities")
        opportunity_id = opportunities["items"][0]["id"]

        first = _post_json(
            f"{base_url}/api/notify",
            {"opportunity_id": opportunity_id, "channel": "feishu", "dry_run": True},
        )
        second = _post_json(
            f"{base_url}/api/notify",
            {"opportunity_id": opportunity_id, "channel": "feishu", "dry_run": True},
        )

        assert first["status"] == "dry_run"
        assert first["recorded"] is True
        assert second["recorded"] is False
        alerts = _get_json(f"{base_url}/api/alerts")
        assert alerts["items"][0]["opportunity_id"] == opportunity_id
        assert alerts["items"][0]["channel"] == "feishu"
        assert alerts["items"][0]["status"] == "dry_run"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_add_watch_and_record_review(tmp_path) -> None:
    db_path = tmp_path / "web-actions.db"
    scan_once_mock(
        catalog_no="SRCL-3520",
        db_path=db_path,
        wameiji_path="data/mock/wameiji_items.sample.json",
        xianyu_path="data/mock/xianyu_samples.sample.json",
        snapshot_dir=tmp_path / "snapshots",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        watch = _post_json(
            f"{base_url}/api/watchlist",
            {
                "decision_mode": "keyword",
                "catalog_no": "ESCL-1234",
                "jan": "4988010000000",
                "artist": "Test Artist",
                "title_jp": "日本語タイトル",
                "title_cn": "中文标题",
                "edition": "初回限定",
                "required_keywords": ["帯付き"],
                "excluded_keywords": ["レンタル"],
                "priority": 2,
                "expected_holding_days": 45,
            },
        )
        assert watch["id"] > 0

        watchlist = _get_json(f"{base_url}/api/watchlist")
        added = next(item for item in watchlist["items"] if item["catalog_no"] == "ESCL-1234")
        assert added["jan"] == "4988010000000"
        assert added["title_jp"] == "日本語タイトル"
        assert added["title_cn"] == "中文标题"
        assert added["edition"] == "初回限定"
        assert added["required_keywords"] == ["帯付き"]
        assert added["excluded_keywords"] == ["レンタル"]
        assert added["priority"] == 2
        assert added["expected_holding_days"] == 45

        opportunities = _get_json(f"{base_url}/api/opportunities")
        review = _post_json(
            f"{base_url}/api/review",
            {
                "opportunity_id": opportunities["items"][0]["id"],
                "result": "rejected_low_profit",
                "note": "web review",
            },
        )
        assert review["id"] > 0
        assert review["result"] == "rejected_low_profit"

        reviews = _get_json(f"{base_url}/api/reviews")
        assert reviews["items"][0]["note"] == "web review"
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_can_update_and_disable_watch(tmp_path) -> None:
    db_path = tmp_path / "web-watch-manage.db"
    init_db(db_path)
    watch_id = add_watch(db_path, WatchItem(catalog_no="SRCL-3520"))
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        updated = _post_json(
            f"{base_url}/api/watchlist/{watch_id}",
            {
                "artist": "L'Arc-en-Ciel",
                "jan": "4988000000001",
                "title_jp": "更新タイトル",
                "title_cn": "更新中文",
                "edition": "通常盤",
                "priority": 4,
                "expected_holding_days": 60,
                "required_keywords": ["初回限定"],
                "excluded_keywords": ["レンタル"],
            },
        )
        assert updated["updated"] is True

        watchlist = _get_json(f"{base_url}/api/watchlist")
        assert watchlist["items"][0]["artist"] == "L'Arc-en-Ciel"
        assert watchlist["items"][0]["jan"] == "4988000000001"
        assert watchlist["items"][0]["title_jp"] == "更新タイトル"
        assert watchlist["items"][0]["title_cn"] == "更新中文"
        assert watchlist["items"][0]["edition"] == "通常盤"
        assert watchlist["items"][0]["priority"] == 4
        assert watchlist["items"][0]["expected_holding_days"] == 60
        assert watchlist["items"][0]["required_keywords"] == ["初回限定"]
        assert watchlist["items"][0]["excluded_keywords"] == ["レンタル"]

        disabled = _post_json(f"{base_url}/api/watchlist/{watch_id}/disable", {})
        assert disabled["disabled"] is True
        assert _get_json(f"{base_url}/api/watchlist")["items"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_exposes_safe_config_and_browser_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/secret-token")
    monkeypatch.delenv("BROWSER_ENABLED", raising=False)
    server = create_server("127.0.0.1", 0, tmp_path / "web-config.db", static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        config = _get_json(f"{base_url}/api/config")
        assert config["app"]["database"].endswith("web-config.db")
        assert config["notify"]["feishu_configured"] is True
        assert "secret-token" not in json.dumps(config)
        assert config["browser"]["enabled"] is False
        assert config["cost"]["wameiji_exchange_rate"] > 0
        assert config["evaluation"]["weak_profit_min_cny"] > 0
        assert config["evaluation"]["weak_margin_min"] > 0
        assert config["evaluation"]["min_match_confidence_weak"] > 0
        assert config["evaluation"]["min_valid_price_cny"] > 0
        assert config["evaluation"]["max_valid_price_cny"] > config["evaluation"]["min_valid_price_cny"]
        assert config["evaluation"]["sample_limit"] > 0
        assert config["data_sources"]["mock"]["ok"] is True
        assert config["data_sources"]["manual_snapshots"]["ok"] is True
        assert "evaluate-html" in config["data_sources"]["manual_snapshots"]["commands"]
        assert "Evaluate Pasted HTML" in config["data_sources"]["manual_snapshots"]["web_actions"]
        assert config["data_sources"]["wameiji_live_browser"]["status"] == "disabled"
        assert config["data_sources"]["xianyu_live_browser"]["status"] == "disabled"

        status = _get_json(f"{base_url}/api/browser/status")
        assert status["wameiji"]["status"] == "disabled"
        assert status["xianyu"]["status"] == "disabled"
        assert "add_to_cart" in status["wameiji"]["prohibited_actions"]
        assert "message_seller" in status["xianyu"]["prohibited_actions"]
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_browser_status_exposes_manual_entry_urls_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    server = create_server("127.0.0.1", 0, tmp_path / "web-browser-entries.db", static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status = _get_json(f"{base_url}/api/browser/status")

        assert status["enabled"] is True
        assert status["wameiji"]["status"] == "human_required"
        assert "meruki.cn" in status["wameiji"]["search_entry_url"]
        assert "visible search result HTML" in status["wameiji"]["capture_instruction"]
        assert status["xianyu"]["status"] == "human_required"
        assert "goofish.com/search" in status["xianyu"]["search_entry_url"]
        assert "visible search result HTML" in status["xianyu"]["capture_instruction"]
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_returns_404_for_unknown_api(tmp_path) -> None:
    server = create_server("127.0.0.1", 0, tmp_path / "web.db", static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        try:
            _get_text(f"{base_url}/api/missing")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            body = json.loads(exc.read().decode("utf-8"))
            assert body["error"] == "not_found"
        else:
            raise AssertionError("Expected /api/missing to return 404")
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_optional_access_token_sets_session_cookie(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "test-token")
    server = create_server("127.0.0.1", 0, tmp_path / "web-token.db", static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        try:
            _get_text(f"{base_url}/")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("Expected missing access token to return 401")

        request = urllib.request.Request(f"{base_url}/?access_token=test-token")
        with urllib.request.urlopen(request, timeout=5) as response:
            html = response.read().decode("utf-8")
            cookie = response.headers["Set-Cookie"]

        assert "WAMEIJI-XIANYU" in html
        assert "cd_monitor_access=test-token" in cookie

        request = urllib.request.Request(
            f"{base_url}/api/summary",
            headers={"Cookie": "cd_monitor_access=test-token"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert "watch_count" in payload

        request = urllib.request.Request(
            f"{base_url}/api/config",
            headers={"Cookie": "cd_monitor_access=test-token"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            config = json.loads(response.read().decode("utf-8"))
        assert config["security"]["access_token_required"] is True
        assert "test-token" not in json.dumps(config)
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_rejects_cross_origin_post_requests(tmp_path) -> None:
    server = create_server("127.0.0.1", 0, tmp_path / "web-origin.db", static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        request = urllib.request.Request(
            f"{base_url}/api/scan/live",
            data=json.dumps({"catalog_no": "SRCL-3520"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": "https://evil.example.invalid",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            body = json.loads(exc.read().decode("utf-8"))
            assert body["error"] == "cross_origin_forbidden"
        else:
            raise AssertionError("Expected cross-origin POST to return 403")
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_returns_400_for_malformed_json_posts(tmp_path) -> None:
    server = create_server("127.0.0.1", 0, tmp_path / "web-bad-json.db", static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        request = urllib.request.Request(
            f"{base_url}/api/scan/live",
            data=b"{bad-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode("utf-8"))
            assert body["error"] == "invalid_json"
        else:
            raise AssertionError("Expected malformed JSON POST to return 400")
    finally:
        server.shutdown()
        server.server_close()


def _get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def _get_json(url: str) -> dict:
    return json.loads(_get_text(url))


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_web_server_persists_user_overrides(tmp_path) -> None:
    """The user-overrides form on the settings page saves kuro_theme,
    min_margin, and min_diff to the user-settings endpoint and the values
    round-trip via GET."""
    db_path = tmp_path / "user-overrides.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        payload = {"kuro_theme": "sage", "min_margin": "0.40", "min_diff": "2000"}
        resp = _post_json(f"{base_url}/api/user-settings", payload)
        assert resp["saved"] >= 3

        # round-trip via GET
        current = _get_json(f"{base_url}/api/user-settings")
        items = current["items"]
        assert items["kuro_theme"] == "sage"
        assert items["min_margin"] == "0.40"
        assert items["min_diff"] == "2000"
    finally:
        server.shutdown()
        server.server_close()


def test_frontend_exposes_user_overrides_form() -> None:
    """The settings page must include an editable user-overrides form with
    kuro_theme / min_margin / min_diff inputs and the JS handler must
    persist them via /api/user-settings."""
    index_html = (Path("web") / "index.html").read_text(encoding="utf-8")
    app_js = (Path("web") / "app.js").read_text(encoding="utf-8")
    # form scaffolding
    assert "userOverridesForm" in index_html
    assert 'name="kuro_theme"' in index_html
    assert 'name="min_margin"' in index_html
    assert 'name="min_diff"' in index_html
    # JS handlers
    assert "bindUserOverridesForm" in app_js
    assert "applyKuroTheme" in app_js
    assert "data-kuro-theme" in app_js
    # CSS theme variants
    css_path = Path("web/styles/kuro.css")
    if css_path.exists():
        kuro_css = css_path.read_text(encoding="utf-8")
        for theme in ("rose", "sage", "ink", "sand"):
            assert theme in kuro_css, f"missing theme variant: {theme}"


def test_web_server_can_evaluate_files_from_inline_content(tmp_path) -> None:
    """The /api/import/evaluate-files endpoint must accept raw pasted content
    (not just file paths) and route it through the same evaluator as the CLI.
    Regression: previously the handler forwarded the strings straight to
    ``evaluate_files()`` which tries to ``Path(value).read_text`` and crashes
    when ``value`` is not a path."""
    db_path = tmp_path / "files-inline.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        payload = {
            "catalog_no": "SRCL-3520",
            "wameiji_html": "<html><body><span>Artist SRCL-3520 \xe5\x88\x9d\xe5\x9b\x9e\xe9\x99\x90\xe5\xae\x9a</span><span>1200</span></body></html>",
            "xianyu_csv": "title,price_cny\nSRCL-3520,200\nSRCL-3520 alt,250",
        }
        resp = _post_json(f"{base_url}/api/import/evaluate-files", payload)
        assert resp["catalog_no"] == "SRCL-3520"
        assert "snapshot_path" in resp
        assert isinstance(resp["opportunity_count"], int)
    finally:
        server.shutdown()
        server.server_close()
