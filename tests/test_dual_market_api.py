from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cd_monitor.core.dual_market import DualMarketCostConfig, ListingObservation
from cd_monitor.services.dual_market_service import rebuild_current_comparison
from cd_monitor.storage.sqlite import insert_listing_observation
from cd_monitor.web_server import create_server

COST_CONFIG = DualMarketCostConfig(
    exchange_rate_cny_per_jpy=0.05,
    japan_domestic_shipping_jpy=100,
    proxy_fee_jpy=200,
    international_shipping_per_item_cny=18,
    china_reship_cny=12,
    packaging_cny=2,
    after_sale_reserve_cny=5,
    risk_reserve_cny=8,
    tax_cny=0,
    sales_fee_rate=0.006,
    sales_fee_cap_cny=60,
)


def make_observation(**changes: object) -> ListingObservation:
    observation = ListingObservation(
        source="wameiji",
        source_listing_id="m-1",
        canonical_product_key="catalog:srcl3520",
        title="Album SRCL-3520 初回限定盤",
        price=1280,
        currency="JPY",
        url="https://meruki.cn/mall/mercari/detail/m-1",
        image_url="https://images.example/wameiji/m-1.jpg",
        availability="available",
        condition_group="complete_used",
        completeness="complete",
        evidence_level="detail_verified",
        captured_at=datetime.now(UTC).isoformat(),
    )
    return replace(observation, **changes)


def request_json(url: str) -> tuple[int, dict[str, object]]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_dual_market_board_returns_exact_ready_and_waiting_streams(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    monkeypatch.setenv("CD_JPY_TO_CNY", "0.047")
    db_path = tmp_path / "monitor.db"
    wameiji_id = insert_listing_observation(db_path, make_observation())
    xianyu_id = insert_listing_observation(
        db_path,
        make_observation(
            source="xianyu",
            source_listing_id="x-1",
            price=298,
            currency="CNY",
            url="https://www.goofish.com/item?id=x-1",
            image_url="https://images.example/xianyu/x-1.jpg",
            evidence_level="search_card",
        ),
    )
    insert_listing_observation(
        db_path,
        make_observation(
            source_listing_id="m-waiting",
            canonical_product_key="catalog:waiting",
            url="https://meruki.cn/mall/mercari/detail/m-waiting",
            image_url="https://images.example/wameiji/m-waiting.jpg",
        ),
    )
    rebuild_current_comparison(db_path, "catalog:srcl3520", COST_CONFIG)
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        code, payload = request_json(
            f"{base_url}/api/dual-market/board?access_token=viewer-secret"
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 200
    assert payload["display_exchange_rate_cny_per_jpy"] == 0.047
    assert payload["summary"] == {
        "ready_count": 1,
        "negative_profit_count": 0,
        "cost_pending_count": 0,
        "waiting_wameiji_count": 0,
        "waiting_xianyu_count": 1,
    }
    [ready] = payload["ready"]
    assert ready["xianyu"]["listing_id"] == xianyu_id
    assert ready["wameiji"]["listing_id"] == wameiji_id
    assert ready["xianyu"]["image_url"] == "https://images.example/xianyu/x-1.jpg"
    assert ready["wameiji"]["image_url"] == "https://images.example/wameiji/m-1.jpg"
    assert ready["calculation"]["status"] == "ready"
    assert payload["waiting_xianyu"][0]["wameiji"]["listing_id"] is not None


def test_dual_market_board_hides_stale_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    db_path = tmp_path / "monitor.db"
    insert_listing_observation(
        db_path,
        make_observation(captured_at="2000-01-01T00:00:00Z"),
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        code, payload = request_json(
            f"{base_url}/api/dual-market/board?access_token=viewer-secret"
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 200
    assert payload["summary"]["waiting_xianyu_count"] == 0


def test_dual_market_board_keeps_same_day_collection_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    db_path = tmp_path / "monitor.db"
    insert_listing_observation(
        db_path,
        make_observation(captured_at=(datetime.now(UTC) - timedelta(hours=6)).isoformat()),
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        code, payload = request_json(
            f"{base_url}/api/dual-market/board?access_token=viewer-secret"
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 200
    assert payload["summary"]["waiting_xianyu_count"] == 1


def test_dual_market_pause_rejects_scan_commands(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    monkeypatch.delenv("DUAL_MARKET_COLLECTION_PAUSED", raising=False)
    db_path = tmp_path / "monitor.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        code, payload = post_json(
            f"{base_url}/api/discovery/commands?access_token=viewer-secret",
            {"command_type": "scan_now"},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert code == 409
    assert payload == {"error": "collector_paused"}
