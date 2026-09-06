from __future__ import annotations

import asyncio
from pathlib import Path

from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.services.discovery_worker import DiscoveryWorker
from cd_monitor.storage.sqlite import (
    init_db,
    list_collector_commands,
    list_discovery_pools,
    update_discovery_pool,
)


class _FakeCommandClient:
    def __init__(self, commands: list[dict]) -> None:
        self.commands = commands
        self.completions: list[tuple[int, str, dict]] = []

    def fetch_pending(self) -> list[dict]:
        return list(self.commands)

    def complete(self, command_id: int, *, status: str, result: dict) -> dict:
        self.completions.append((command_id, status, result))
        return {"id": command_id, "status": status, "result": result}


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
        fetch_xianyu=fetch_xianyu,
        command_client=command_client,
    )

    result = asyncio.run(worker.run_once())

    assert result.scan_count == 0
    assert called is False
    assert command_client.completions == [(92, "completed", {"runs": 0, "status": "ok"})]
