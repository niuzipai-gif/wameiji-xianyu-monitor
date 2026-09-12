from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.services.discovery_worker import DiscoveryWorker
from cd_monitor.storage.sqlite import (
    complete_collector_command,
    create_collector_command,
    init_db,
    list_collector_commands,
    list_discovery_pools,
    update_discovery_pool,
    upsert_remote_collector_command,
)


@pytest.fixture(autouse=True)
def enable_collection_for_mock_worker_behavior_tests(monkeypatch) -> None:
    """Existing injected-fetcher tests model an explicitly opted-in collector."""
    monkeypatch.setenv("DUAL_MARKET_COLLECTION_PAUSED", "0")


class _FakeCommandClient:
    def __init__(self, commands: list[dict]) -> None:
        self.commands = commands
        self.completions: list[tuple[int, str, dict]] = []

    def fetch_pending(self) -> list[dict]:
        return list(self.commands)

    def complete(self, command_id: int, *, status: str, result: dict) -> dict:
        self.completions.append((command_id, status, result))
        return {"id": command_id, "status": status, "result": result}


async def _verified_detail(item: MarketItem) -> MarketItem:
    return replace(item, detail_verified=True)


def test_worker_completes_local_scan_command_without_a_remote_client(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"keyword_budget": 1})
    command = create_collector_command(db_path, "scan_now", {"pool_id": pool_id})

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return []

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("an empty source scan must not query Xianyu")

    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=_verified_detail,
        fetch_xianyu=fetch_xianyu,
    )
    result = asyncio.run(worker.run_once())

    assert result.command_count == 1
    commands = list_collector_commands(db_path)
    assert commands[0]["id"] == command["id"]
    assert commands[0]["status"] == "completed"


def test_worker_executes_remote_scan_once_command_and_acknowledges(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"keyword_budget": 1})
    calls: list[tuple[str, str]] = []

    async def fetch_wameiji(keyword: str) -> list[MarketItem]:
        calls.append(("wameiji", keyword))
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 初回限定盤",
                price=1200,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="worker-1",
                availability="available",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        calls.append(("xianyu", query))
        return [
            XianyuPriceSample(catalog_no=query, title="Artist SRCL-3520 初回限定盤", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="Artist SRCL-3520 初回限定盤", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="Artist SRCL-3520 初回限定盤", price_cny=320),
        ]

    command_client = _FakeCommandClient(
        [{"id": 91, "command_type": "scan_now", "payload": {"pool_id": pool_id}}]
    )
    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=_verified_detail,
        fetch_xianyu=fetch_xianyu,
        command_client=command_client,
    )

    result = asyncio.run(worker.run_once())

    assert result.scan_count == 1
    assert calls[0] == ("wameiji", "初回限定盤")
    assert calls[1] == ("xianyu", "SRCL-3520")
    assert command_client.completions == [(91, "completed", {"runs": 1, "status": "ok"})]
    # Render's database is periodically overwritten by the local source DB.
    # Mirror the remote command locally so its visible completion survives
    # that next replica upload.
    mirrored = list_collector_commands(db_path)
    assert mirrored[0]["remote_command_id"] == "91"
    assert mirrored[0]["status"] == "completed"


def test_worker_uses_detail_fetcher_before_it_queries_xianyu(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"keyword_budget": 1})
    calls: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        calls.append("search")
        return [
            MarketItem(
                source="wameiji",
                title="Artist CD card",
                price=9999,
                currency="JPY",
                external_item_id="worker-detail-1",
                url="/mall/mercari/detail/worker-detail-1",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        calls.append("detail")
        return MarketItem(
            source="wameiji",
            title="Artist SRCL-3520 CD",
            price=1200,
            currency="JPY",
            catalog_no="SRCL-3520",
            external_item_id=item.external_item_id,
            url=item.url,
            availability="available",
            detail_verified=True,
        )

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        calls.append(f"xianyu:{query}")
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=320),
        ]

    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=fetch_wameiji_detail,
        fetch_xianyu=fetch_xianyu,
        command_client=_FakeCommandClient(
            [{"id": 93, "command_type": "scan_now", "payload": {"pool_id": pool_id}}]
        ),
    )

    result = asyncio.run(worker.run_once())

    assert result.scan_count == 1
    assert result.detail_query_count == 1
    assert result.xianyu_query_count == 1
    assert calls == ["search", "detail", "xianyu:SRCL-3520"]


def test_scheduled_worker_waits_for_pool_interval_before_rotating_due_keywords(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    cd_pool, game_pool = list_discovery_pools(db_path)
    assert cd_pool.id is not None
    assert game_pool.id is not None
    update_discovery_pool(db_path, cd_pool.id, {"keyword_budget": 1})
    update_discovery_pool(db_path, game_pool.id, {"enabled": False})
    searched_keywords: list[str] = []

    async def fetch_wameiji(keyword: str) -> list[MarketItem]:
        searched_keywords.append(keyword)
        return []

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("empty Wameiji results must not query Xianyu")

    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=_verified_detail,
        fetch_xianyu=fetch_xianyu,
    )

    result = asyncio.run(worker.run_once())
    second_result = asyncio.run(worker.run_once())

    assert result.scan_count == 1
    assert second_result.scan_count == 0
    assert len(searched_keywords) == 1


def test_worker_respects_disabled_pool_even_when_commanded(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"enabled": False})
    called = False

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        nonlocal called
        called = True
        return []

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("disabled pool must not query Xianyu")

    command_client = _FakeCommandClient(
        [{"id": 92, "command_type": "scan_now", "payload": {"pool_id": pool_id}}]
    )
    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=_verified_detail,
        fetch_xianyu=fetch_xianyu,
        command_client=command_client,
    )

    result = asyncio.run(worker.run_once())

    assert result.scan_count == 0
    assert called is False
    assert command_client.completions == [(92, "completed", {"runs": 0, "status": "ok"})]


def test_worker_skips_paused_quality_pool_before_browser_fetch(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pools = list_discovery_pools(db_path)
    pool_id = pools[0].id
    assert pool_id is not None
    assert pools[1].id is not None
    update_discovery_pool(db_path, pools[1].id, {"enabled": False})
    update_discovery_pool(
        db_path,
        pool_id,
        {
            "capture_state": "paused_quality",
            "pause_reason": "detail_verification_rate_below_50_percent",
        },
    )
    calls: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        calls.append("wameiji")
        return []

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("paused collection must not query Xianyu")

    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=_verified_detail,
        fetch_xianyu=fetch_xianyu,
    )

    result = asyncio.run(worker.run_once())

    assert result.scan_count == 0
    assert calls == []


def test_remote_command_identity_survives_render_id_reset(tmp_path: Path) -> None:
    """A Render restart can reuse numeric ids, so local mirroring needs its UUID."""
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    legacy = upsert_remote_collector_command(
        db_path,
        {"id": 1, "command_type": "scan_now", "payload": {}, "status": "pending"},
    )
    complete_collector_command(db_path, int(legacy["id"]), status="completed")

    restarted_render_command = upsert_remote_collector_command(
        db_path,
        {
            "id": 1,
            "remote_command_id": "d057d132e3f44d4d88493f2a3e8c01f2",
            "command_type": "scan_now",
            "payload": {},
            "status": "pending",
        },
    )

    assert restarted_render_command["id"] != legacy["id"]
    assert restarted_render_command["remote_command_id"] == "d057d132e3f44d4d88493f2a3e8c01f2"
    assert restarted_render_command["status"] == "pending"
