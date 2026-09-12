from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import cd_monitor.web_server as web_server
from cd_monitor.core.discovery import DiscoveryCandidate
from cd_monitor.storage.sqlite import (
    init_db,
    list_discovery_pools,
    upsert_discovery_candidates_with_previous,
)
from cd_monitor.web_server import create_server


def _request(url: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    merged = dict(headers or {})
    if body is not None:
        merged.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=merged, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


def test_selection_board_and_remote_command_acknowledgement(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    monkeypatch.setenv("CD_SYNC_TOKEN", "collector-secret")
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        code, board = _request(f"{base_url}/api/discovery/board?access_token=viewer-secret")
        assert code == 200
        assert [pool["slug"] for pool in board["pools"]] == ["cd", "physical-game"]
        assert board["pools"][0]["keywords"]
        assert board["pools"][0]["keywords"][0]["keyword"] == "初回限定盤"
        assert board["opportunities"] == []
        assert board["summary"]["active_candidates"] == 0
        assert board["runs"] == []
        assert board["pools"][0]["capture_state"] == "active"

        code, command = _request(
            f"{base_url}/api/discovery/commands?access_token=viewer-secret",
            method="POST",
            payload={"command_type": "scan_now", "pool_id": pool_id},
        )
        assert code == 202
        assert command["status"] == "pending"
        assert isinstance(command["remote_command_id"], str)
        assert command["remote_command_id"]
        command_id = command["id"]

        code, unauthenticated = _request(f"{base_url}/api/discovery/collector/commands")
        assert code == 401
        assert unauthenticated["error"] == "sync_unauthorized"

        code, queued = _request(
            f"{base_url}/api/discovery/collector/commands",
            headers={"X-CD-Sync-Token": "collector-secret"},
        )
        assert code == 200
        assert [entry["id"] for entry in queued["items"]] == [command_id]
        assert queued["items"][0]["remote_command_id"] == command["remote_command_id"]

        code, complete = _request(
            f"{base_url}/api/discovery/collector/commands/{command_id}/complete",
            method="POST",
            payload={"result": {"runs": 2, "status": "ok"}},
            headers={"X-CD-Sync-Token": "collector-secret"},
        )
        assert code == 200
        assert complete["status"] == "completed"
        assert complete["result"]["runs"] == 2

        code, command_list = _request(
            f"{base_url}/api/discovery/commands?access_token=viewer-secret"
        )
        assert code == 200
        assert command_list["items"][0]["status"] == "completed"
    finally:
        server.shutdown()
        server.server_close()


def test_selection_board_prepares_an_absolute_wameiji_product_link(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        web_server,
        "list_discovery_opportunities",
        lambda _db_path, limit: [
            {
                "candidate_title": "商品详情页核验样本",
                "source_url": "/mall/market/detail/232665692319133696",
                "url": "/mall/market/detail/232665692319133696",
            }
        ],
    )

    views = web_server._discovery_opportunity_views(tmp_path)

    assert views[0]["url"] == "https://meruki.cn/mall/market/detail/232665692319133696"
    assert views[0]["source_url"] == views[0]["url"]


def test_selection_board_exposes_active_research_candidates_without_price_fields(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    upsert_discovery_candidates_with_previous(
        db_path,
        [
            DiscoveryCandidate(
                pool_id=pool_id,
                media_type="cd",
                identity_key="source:research-card",
                title="研究队列样本 CD 初回限定盤",
                source_url="/mall/market/detail/research-card",
                source_image_url="https://images.example.invalid/research-card.webp",
                source_price=1200,
                source_currency="JPY",
                availability="available",
                status="active",
            )
        ],
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        code, board = _request(f"{base_url}/api/discovery/board?access_token=viewer-secret")
        assert code == 200
        candidate = board["research_candidates"][0]
        assert candidate["candidate_title"] == "研究队列样本 CD 初回限定盤"
        assert candidate["research_stage"] == "source_detail_needed"
        assert candidate["source_url"] == "https://meruki.cn/mall/market/detail/research-card"
        assert not {
            "source_price",
            "source_currency",
            "expected_profit",
            "net_margin",
            "availability",
        } & set(candidate)
    finally:
        server.shutdown()
        server.server_close()
