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


def test_cd_discovery_skips_non_disc_results_before_xianyu_lookup(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    calls: list[tuple[str, str]] = []

    async def fetch_wameiji(keyword: str) -> list[MarketItem]:
        calls.append(("wameiji", keyword))
        return [
            MarketItem(
                source="wameiji",
                title="Anime figure 初回限定",
                price=900,
                currency="JPY",
                external_item_id="figure-1",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="Artist Album CD 初回限定盤",
                price=1200,
                currency="JPY",
                external_item_id="cd-1",
                availability="available",
            ),
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        calls.append(("xianyu", query))
        return []

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

    assert result.discovered_count == 2
    assert result.candidate_count == result.evaluated_count == 1
    assert calls == [("wameiji", "初回限定盤"), ("xianyu", "Artist")]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM discovery_candidates").fetchone()[0] == 1


def test_cached_front_card_does_not_spend_the_next_xianyu_budget(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []
    phase = 0

    first = MarketItem(
        source="wameiji",
        title="Artist One CD 初回限定盤",
        price=900,
        currency="JPY",
        catalog_no="SRCL-1001",
        external_item_id="cached-front",
        availability="available",
    )
    second = MarketItem(
        source="wameiji",
        title="Artist Two CD 初回限定盤",
        price=900,
        currency="JPY",
        catalog_no="SRCL-1002",
        external_item_id="new-behind-cache",
        availability="available",
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [first] if phase == 0 else [first, second]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return [
            XianyuPriceSample(catalog_no=query, title=f"{query} CD", price_cny=280),
            XianyuPriceSample(catalog_no=query, title=f"{query} CD", price_cny=300),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"candidate_budget": 1})
    first_run = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )
    phase = 1
    second_run = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert first_run.xianyu_query_count == 1
    assert second_run.candidate_count == 2
    assert second_run.evaluated_count == second_run.xianyu_query_count == 1
    assert xianyu_queries == ["SRCL-1001", "SRCL-1002"]


def test_discovery_does_not_turn_unrelated_numeric_text_into_a_xianyu_identifier(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []
    source_title = "Artist Album CD 初回限定 20235311"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title=source_title,
                price=1200,
                currency="JPY",
                external_item_id="source-with-numeric-listing-id",
                availability="available",
                raw_text="listing-id=20235311",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return []

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert xianyu_queries == ["Artist"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT jan FROM discovery_candidates").fetchone()[0] is None


def test_discovery_does_not_turn_a_price_adjacent_cd_token_into_a_catalog_number(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []
    source_title = "浜崎あゆみ UNITE! 帯付き CD"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title=source_title,
                price=300,
                currency="JPY",
                external_item_id="source-cd-300",
                availability="available",
                raw_text="浜崎あゆみ UNITE! 帯付き CD 300 円",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return []

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="帯付き",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert xianyu_queries == ["浜崎あゆみ UNITE"]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT catalog_no FROM discovery_candidates").fetchone()
    assert row[0] is None


def test_cd_discovery_rejects_album_bonus_card_without_a_disc_marker(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="CANDY TUNE BEST ALBUM 初回限定盤 トレカ",
                price=2000,
                currency="JPY",
                external_item_id="album-bonus-card",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="CANDY TUNE BEST ALBUM 初回限定盤 CD",
                price=3000,
                currency="JPY",
                external_item_id="album-with-cd",
                availability="available",
            ),
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return []

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

    assert result.candidate_count == result.evaluated_count == 1
    assert xianyu_queries == ["CANDY TUNE BEST"]


def test_game_discovery_rejects_console_hardware_but_keeps_game_software(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Nintendo Switch 本体 限定版",
                price=28000,
                currency="JPY",
                external_item_id="switch-console",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="薄桜鬼 Switch 限定版 ゲームソフト",
                price=5000,
                currency="JPY",
                external_item_id="switch-game",
                availability="available",
            ),
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return []

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="Switch 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == result.evaluated_count == 1
    assert xianyu_queries == ["薄桜鬼"]


def test_discovery_stops_the_remaining_xianyu_lookups_after_a_security_check(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist One Blue Skies CD",
                price=1000,
                currency="JPY",
                external_item_id="security-first",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="Artist Two Red Moon CD",
                price=1000,
                currency="JPY",
                external_item_id="security-second",
                availability="available",
            ),
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        raise RuntimeError("xianyu:security_check")

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

    assert result.candidate_count == 2
    assert result.xianyu_query_count == 1
    assert result.evaluated_count == 0
    assert xianyu_queries == ["Artist One Blue Skies"]


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
