from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.services.discovery import scan_discovery_keyword
from cd_monitor.storage.sqlite import (
    list_discovery_opportunities,
    list_discovery_pools,
    update_discovery_pool,
)


def test_keyword_scan_creates_a_ranked_opportunity_and_skips_unchanged_requery(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
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
                external_item_id="wameiji-3520",
                url="https://meruki.cn/item/3520",
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

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    first = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )
    second = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert first.status == "ok"
    assert first.candidate_count == first.evaluated_count == first.xianyu_query_count == 1
    assert second.status == "ok"
    assert second.candidate_count == 1
    assert second.xianyu_query_count == second.evaluated_count == 0
    assert calls == [
        ("wameiji", "初回限定盤"),
        ("xianyu", "SRCL-3520"),
        ("wameiji", "初回限定盤"),
    ]
    feed = list_discovery_opportunities(db_path)
    assert len(feed) == 1
    assert feed[0]["identity_key"] == "catalog:SRCL3520"
    assert feed[0]["expected_profit"] > 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT observation_count FROM discovery_candidates").fetchone()[0] == 2


def test_title_only_candidate_is_saved_but_stays_out_of_default_profit_board(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist Album 初回限定盤",
                price=1200,
                currency="JPY",
                external_item_id="title-only-1",
                availability="available",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        return [
            XianyuPriceSample(catalog_no=query, title="Artist Album 初回限定盤", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="Artist Album 初回限定盤", price_cny=320),
            XianyuPriceSample(catalog_no=query, title="Artist Album 初回限定盤", price_cny=340),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == 1
    assert list_discovery_opportunities(db_path) == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT identity_key FROM discovery_candidates").fetchone()[0] == "source:title-only-1"


def test_pool_profit_threshold_filters_existing_evaluations_without_a_new_source_lookup(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"min_profit_cny": 9_999})

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 初回限定盤",
                price=1200,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="threshold-1",
                availability="available",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        return [
            XianyuPriceSample(catalog_no=query, title="Artist SRCL-3520 初回限定盤", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="Artist SRCL-3520 初回限定盤", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="Artist SRCL-3520 初回限定盤", price_cny=320),
        ]

    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.evaluated_count == 1
    assert list_discovery_opportunities(db_path) == []
    update_discovery_pool(db_path, pool_id, {"min_profit_cny": 0})
    assert len(list_discovery_opportunities(db_path)) == 1
