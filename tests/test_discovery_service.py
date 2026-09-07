from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.services.discovery import scan_discovery_keyword
from cd_monitor.sources.wikidata_aliases import ResolvedTitleAlias
from cd_monitor.storage.sqlite import (
    discovery_summary,
    get_discovery_candidate_by_identity,
    get_discovery_pool,
    get_discovery_source_cooldown,
    list_discovery_opportunities,
    list_discovery_pools,
    list_discovery_runs,
    update_discovery_pool,
)


async def _verified_detail(item: MarketItem) -> MarketItem:
    """Offline stand-in for a separately tested successful Wameiji detail read."""
    return replace(item, detail_verified=True)


def test_four_bad_detail_pages_pause_the_pool_without_xianyu_calls(
    tmp_path: Path,
) -> None:
    """A bad detail batch stops before broadening the collection further."""

    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title=f"Artist {index} CD 初回限定盤",
                price=1000 + index,
                currency="JPY",
                external_item_id=f"bad-detail-{index}",
                url=f"/mall/mercari/detail/bad-detail-{index}",
                availability="available",
            )
            for index in range(4)
        ]

    async def fetch_wameiji_detail(_item: MarketItem) -> None:
        return None

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("unverified detail pages must never query Xianyu")

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {
            "detail_budget": 4,
            "xianyu_query_budget": 3,
        },
    )

    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    pool = get_discovery_pool(db_path, pool_id)
    assert result.status == "paused_quality"
    assert result.detail_query_count == 4
    assert pool.capture_state == "paused_quality"
    assert pool.pause_reason == "detail_verification_rate_below_50_percent"


def test_two_detail_access_blocks_pause_the_pool_before_resale_lookup(
    tmp_path: Path,
) -> None:
    """Two access challenges mean the browser session needs a human reset."""

    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title=f"Artist {index} CD 初回限定盤",
                price=1000 + index,
                currency="JPY",
                external_item_id=f"redirected-detail-{index}",
                url=f"/mall/mercari/detail/redirected-detail-{index}",
                availability="available",
            )
            for index in range(2)
        ]

    async def fetch_wameiji_detail(_item: MarketItem) -> None:
        raise RuntimeError("wameiji_detail:redirected_away_from_listing")

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("a blocked detail browser must not enter resale lookup")

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {"detail_budget": 2, "xianyu_query_budget": 3},
    )

    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    pool = get_discovery_pool(db_path, pool_id)
    assert result.status == "paused_quality"
    assert result.detail_query_count == 2
    assert pool.capture_state == "paused_quality"
    assert pool.pause_reason == "consecutive_detail_access_blocks"


def test_detail_backlog_at_high_watermark_skips_new_search_cards(tmp_path: Path) -> None:
    """Existing source-detail debt is drained before another broad search."""

    db_path = tmp_path / "selection.db"
    phase = "seed"
    search_calls: list[str] = []
    detail_calls: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        search_calls.append(phase)
        if phase != "seed":
            raise AssertionError("high detail backlog must suppress a new search page")
        return [
            MarketItem(
                source="wameiji",
                title=f"Artist {index} CD 初回限定盤",
                price=1000 + index,
                currency="JPY",
                external_item_id=f"backlog-high-water-{index}",
                url=f"/mall/mercari/detail/backlog-high-water-{index}",
                availability="available",
            )
            for index in range(2)
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_calls.append(str(item.external_item_id))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("resale work is intentionally disabled for this queue test")

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {"detail_budget": 0, "xianyu_query_budget": 0, "queue_high_watermark": 2},
    )
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    phase = "drain"
    update_discovery_pool(db_path, pool_id, {"detail_budget": 1})
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.discovered_count == result.candidate_count == 0
    assert result.detail_query_count == 1
    assert search_calls == ["seed"]
    assert detail_calls == ["backlog-high-water-0"]


def test_duplicate_source_urls_in_one_search_batch_pause_before_detail_reads(
    tmp_path: Path,
) -> None:
    """A parser that repeats listing URLs must not consume source-detail budget."""

    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        unique = [
            MarketItem(
                source="wameiji",
                title=f"Unique Artist {index} CD 初回限定盤",
                price=1000 + index,
                currency="JPY",
                external_item_id=f"unique-url-{index}",
                url=f"/mall/mercari/detail/unique-url-{index}",
                availability="available",
            )
            for index in range(17)
        ]
        repeated = [
            MarketItem(
                source="wameiji",
                title="Repeated Artist CD 初回限定盤",
                price=1200,
                currency="JPY",
                external_item_id=f"repeated-url-{index}",
                url="/mall/mercari/detail/repeated-source-url",
                availability="available",
            )
            for index in range(3)
        ]
        return [*unique, *repeated]

    async def fetch_wameiji_detail(_item: MarketItem) -> MarketItem:
        raise AssertionError("duplicate source URLs must stop before detail reads")

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("duplicate source URLs must stop before resale lookup")

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {"search_card_budget": 20, "detail_budget": 4, "xianyu_query_budget": 3},
    )

    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    pool = get_discovery_pool(db_path, pool_id)
    assert result.status == "paused_quality"
    assert result.detail_query_count == result.xianyu_query_count == 0
    assert pool.capture_state == "paused_quality"
    assert pool.pause_reason == "source_url_duplicate_rate_above_5_percent"


def test_completed_run_exposes_detail_and_resale_stage_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-4040 CD",
                price=900,
                currency="JPY",
                catalog_no="SRCL-4040",
                external_item_id="stage-metrics",
                url="/mall/mercari/detail/stage-metrics",
                availability="available",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-4040 CD", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="SRCL-4040 CD", price_cny=300),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    run = list_discovery_runs(db_path, limit=1)[0]
    assert run["detail_query_count"] == 1
    assert run["detail_verified_count"] == 1
    assert run["detail_rejected_count"] == 0
    assert run["xianyu_query_count"] == 1
    assert run["resale_sampled_count"] == 1
    candidate = get_discovery_candidate_by_identity(
        db_path, pool_id, "source:stage-metrics"
    )
    assert candidate is not None
    assert candidate.pipeline_stage == "evaluated"
    assert candidate.product_key == "catalog:srcl-4040"


def test_failed_detail_attempt_is_persisted_as_blocked_queue_state(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist CD 初回限定盤",
                price=900,
                currency="JPY",
                external_item_id="blocked-detail",
                url="/mall/mercari/detail/blocked-detail",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(_item: MarketItem) -> None:
        return None

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("an unverified detail is not a resale candidate")

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"detail_budget": 1})
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    candidate = get_discovery_candidate_by_identity(
        db_path, pool_id, "source:blocked-detail"
    )
    assert candidate is not None
    assert candidate.detail_attempt_count == 1
    assert candidate.pipeline_stage == "blocked"
    assert candidate.last_detail_error == "detail_parse_failed"


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
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )
    second = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
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
    assert feed[0]["identity_key"] == "source:wameiji-3520"
    assert feed[0]["expected_profit"] > 0
    with sqlite3.connect(db_path) as conn:
        # First run saves the search-card discovery and its verified detail;
        # the unchanged second run only refreshes the search-card observation.
        assert conn.execute("SELECT observation_count FROM discovery_candidates").fetchone()[0] == 3


def test_keyword_scan_uses_detail_page_item_before_xianyu_lookup(tmp_path: Path) -> None:
    """The Xianyu query must use the verified detail record, never a search-card guess."""
    db_path = tmp_path / "selection.db"
    calls: list[tuple[str, str]] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Card title CD without a reliable identifier",
                price=9999,
                currency="JPY",
                external_item_id="listing-3520",
                url="/mall/mercari/detail/listing-3520",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        calls.append(("detail", str(item.url)))
        return MarketItem(
            source="wameiji",
            title="Artist SRCL-3520 初回限定盤 CD",
            price=1200,
            currency="JPY",
            catalog_no="SRCL-3520",
            external_item_id=item.external_item_id,
            url=item.url,
            availability="likely_available",
            detail_verified=True,
        )

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        calls.append(("xianyu", query))
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=320),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.evaluated_count == 1
    assert calls == [
        ("detail", "/mall/mercari/detail/listing-3520"),
        ("xianyu", "SRCL-3520"),
    ]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT source_price, catalog_no, detail_verified FROM discovery_candidates"
        ).fetchone()
    assert row == (1200.0, "SRCL-3520", 1)


def test_detail_fetches_are_bounded_by_the_candidate_budget(tmp_path: Path) -> None:
    """A broad search must not fan out into an unbounded number of detail pages."""
    db_path = tmp_path / "selection.db"
    detail_urls: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title=f"Artist SRCL-{3520 + index} CD",
                price=1000 + index,
                currency="JPY",
                catalog_no=f"SRCL-{3520 + index}",
                external_item_id=f"listing-{index}",
                url=f"/mall/mercari/detail/listing-{index}",
                availability="available",
            )
            for index in range(3)
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_urls.append(str(item.url))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        return [
            XianyuPriceSample(catalog_no=query, title=f"{query} CD", price_cny=300),
            XianyuPriceSample(catalog_no=query, title=f"{query} CD", price_cny=320),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"candidate_budget": 1})
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert detail_urls == ["/mall/mercari/detail/listing-0"]
    assert result.xianyu_query_count == result.evaluated_count == 1


def test_second_search_drains_previous_detail_backlog_before_its_new_card(
    tmp_path: Path,
) -> None:
    """The next page cannot starve a source URL already queued for detail."""

    db_path = tmp_path / "selection.db"
    phase = 0
    detail_calls: list[str] = []

    def card(identifier: str) -> MarketItem:
        return MarketItem(
            source="wameiji",
            title=f"Artist {identifier} CD 初回限定盤",
            price=1200,
            currency="JPY",
            external_item_id=identifier,
            url=f"/mall/mercari/detail/{identifier}",
            availability="available",
        )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [card("first"), card("backlog")] if phase == 0 else [card("new-page")]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_calls.append(str(item.external_item_id))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        return []

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {
            "candidate_budget": 1,
            "detail_budget": 1,
            "xianyu_query_budget": 0,
        },
    )

    first = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )
    phase = 1
    second = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert first.detail_query_count == second.detail_query_count == 1
    assert detail_calls == ["first", "backlog"]


def test_detail_budget_skips_a_search_card_that_explicitly_lacks_the_game(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    detail_ids: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="PS Vita 相州戦神館學園 八命陣 天之刻 初回限定版 ソフトなし",
                price=1280,
                currency="JPY",
                external_item_id="missing-game-card",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="Switch Macross Shooting Insight 限定版",
                price=3300,
                currency="JPY",
                external_item_id="complete-game-card",
                availability="available",
            ),
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_ids.append(str(item.external_item_id))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        return []

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"candidate_budget": 1})
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="Switch 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.detail_query_count == 1
    assert detail_ids == ["complete-game-card"]


def test_detail_budget_prioritizes_the_cheapest_complete_limited_game(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    detail_ids: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Switch Macross Shooting Insight 限定版",
                price=4000,
                currency="JPY",
                external_item_id="higher-price",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="Switch Macross Shooting Insight 限定版",
                price=3300,
                currency="JPY",
                external_item_id="lower-price",
                availability="available",
            ),
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_ids.append(str(item.external_item_id))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        return []

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"candidate_budget": 1})
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="Switch 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert detail_ids == ["lower-price"]


def test_sold_detail_page_is_saved_but_never_sent_to_xianyu(tmp_path: Path) -> None:
    """A current detail-page stock state outranks the stale search-card state."""
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 CD",
                price=1000,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="sold-detail",
                url="/mall/mercari/detail/sold-detail",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        return replace(item, availability="sold_out", detail_verified=True)

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
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.detail_query_count == 1
    assert result.xianyu_query_count == result.evaluated_count == 0
    assert xianyu_queries == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status FROM discovery_candidates WHERE identity_key = ?",
            ("source:sold-detail",),
        ).fetchone()[0] == "expired"


def test_unchanged_search_card_does_not_reopen_a_verified_reserved_detail(tmp_path: Path) -> None:
    """A vague search card cannot reactivate a detail-confirmed reservation."""
    db_path = tmp_path / "selection.db"
    detail_calls = 0

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 CD",
                price=1000,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="reserved-detail",
                url="/mall/mercari/detail/reserved-detail",
                availability="unknown_but_visible",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        nonlocal detail_calls
        detail_calls += 1
        return replace(item, availability="reserved", detail_verified=True)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("reserved listings must never query Xianyu")

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    first = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )
    second = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert first.detail_query_count == 1
    assert second.detail_query_count == 0
    assert detail_calls == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT availability, status, detail_verified FROM discovery_candidates WHERE identity_key = ?",
            ("source:reserved-detail",),
        ).fetchone() == ("reserved", "expired", 1)


def test_detail_page_can_reject_a_misclassified_search_card_before_xianyu(
    tmp_path: Path,
) -> None:
    """Only the detail page can establish that a discovered link is a disc."""
    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist CD 初回限定盤",
                price=1000,
                currency="JPY",
                external_item_id="misclassified-card",
                url="/mall/mercari/detail/misclassified-card",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        return replace(
            item,
            title="Artist 生写真 フォトカード 特典",
            catalog_no=None,
            jan=None,
            detail_verified=True,
        )

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("a non-disc detail page must not query Xianyu")

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.detail_query_count == 1
    assert result.xianyu_query_count == result.evaluated_count == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status, detail_verified FROM discovery_candidates WHERE identity_key = ?",
            ("source:misclassified-card",),
        ).fetchone() == ("ignored", 1)


def test_two_verified_source_listings_for_one_catalog_share_one_xianyu_query(
    tmp_path: Path,
) -> None:
    """Different purchase links reuse one market lookup after detail verification."""
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 初回限定盤 CD",
                price=900,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="cheap-listing",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 初回限定盤 CD",
                price=1600,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="expensive-listing",
                availability="available",
            ),
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=320),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.evaluated_count == 2
    assert xianyu_queries == ["SRCL-3520"]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT identity_key, source_price FROM discovery_candidates ORDER BY source_price"
        ).fetchall()
    assert rows == [("source:cheap-listing", 900.0), ("source:expensive-listing", 1600.0)]


def test_verified_detail_waits_in_persistent_resale_queue_for_a_later_budget(
    tmp_path: Path,
) -> None:
    """A detail read remains useful even when its resale budget is exhausted."""

    db_path = tmp_path / "selection.db"
    phase = 0
    detail_calls: list[str] = []
    xianyu_queries: list[str] = []
    source = MarketItem(
        source="wameiji",
        title="Artist SRCL-9090 初回限定盤 CD",
        price=900,
        currency="JPY",
        catalog_no="SRCL-9090",
        external_item_id="persistent-resale-queue",
        url="/mall/mercari/detail/persistent-resale-queue",
        availability="available",
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [source] if phase == 0 else []

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_calls.append(str(item.external_item_id))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-9090 CD", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="SRCL-9090 CD", price_cny=300),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {
            "candidate_budget": 1,
            "detail_budget": 1,
            "xianyu_query_budget": 0,
        },
    )
    first = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    phase = 1
    update_discovery_pool(db_path, pool_id, {"xianyu_query_budget": 1})
    second = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert first.detail_query_count == 1
    assert first.xianyu_query_count == first.evaluated_count == 0
    assert second.detail_query_count == 0
    assert second.evaluated_count == 1
    assert second.resale_sampled_count == 1
    assert detail_calls == ["persistent-resale-queue"]
    assert xianyu_queries == ["SRCL-9090"]


def test_stale_resale_samples_are_rechecked_without_reopening_wameiji_detail(
    tmp_path: Path,
) -> None:
    """A stable source listing still needs a low-frequency Xianyu price refresh."""

    db_path = tmp_path / "selection.db"
    phase = 0
    detail_calls: list[str] = []
    xianyu_queries: list[str] = []
    source = MarketItem(
        source="wameiji",
        title="Artist SRCL-8181 初回限定盤 CD",
        price=900,
        currency="JPY",
        catalog_no="SRCL-8181",
        external_item_id="stale-resale-sample",
        url="/mall/mercari/detail/stale-resale-sample",
        availability="available",
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [source] if phase == 0 else []

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_calls.append(str(item.external_item_id))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        resale_price = 280 if phase == 0 else 380
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-8181 CD", price_cny=resale_price),
            XianyuPriceSample(
                catalog_no=query,
                title="SRCL-8181 CD",
                price_cny=resale_price + 20,
            ),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {"detail_budget": 1, "xianyu_query_budget": 1},
    )
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_candidates
            SET last_xianyu_checked_at = datetime(CURRENT_TIMESTAMP, '-181 minutes')
            WHERE identity_key = 'source:stale-resale-sample'
            """
        )
        conn.commit()

    phase = 1
    second = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert second.detail_query_count == 0
    assert second.xianyu_query_count == second.evaluated_count == 1
    assert detail_calls == ["stale-resale-sample"]
    assert xianyu_queries == ["SRCL-8181", "SRCL-8181"]


def test_changed_search_price_reopens_the_wameiji_detail_before_repricing(
    tmp_path: Path,
) -> None:
    """A search-card price change cannot overwrite a detail-verified purchase."""

    db_path = tmp_path / "selection.db"
    phase = 0
    detail_prices: list[float] = []
    xianyu_queries: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        price = 900 if phase == 0 else 600
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-6060 CD",
                price=price,
                currency="JPY",
                catalog_no="SRCL-6060",
                external_item_id="changed-detail-price",
                url="/mall/mercari/detail/changed-detail-price",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_prices.append(item.price)
        return replace(item, detail_verified=True)

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-6060 CD", price_cny=280),
            XianyuPriceSample(catalog_no=query, title="SRCL-6060 CD", price_cny=300),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(
        db_path,
        pool_id,
        {
            "candidate_budget": 1,
            "detail_budget": 1,
            "xianyu_query_budget": 1,
        },
    )
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )
    phase = 1
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert detail_prices == [900, 600]
    assert xianyu_queries == ["SRCL-6060", "SRCL-6060"]


def test_unverified_search_card_cannot_enter_the_profit_board(tmp_path: Path) -> None:
    """Legacy/search-only records are evidence, never purchasable recommendations."""
    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 初回限定盤 CD",
                price=500,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="unverified-card",
                availability="available",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=320),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=340),
        ]

    async def fetch_wameiji_detail(_item: MarketItem) -> None:
        return None

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.evaluated_count == 0
    assert list_discovery_opportunities(db_path) == []
    summary = discovery_summary(db_path)
    assert summary["active_candidates"] == 0
    assert summary["active_opportunities"] == 0
    assert summary["total_expected_profit"] == 0
    assert summary["highest_expected_profit"] == 0


def test_legacy_unverified_candidate_is_excluded_from_the_profit_board(tmp_path: Path) -> None:
    """A pre-migration search-only opportunity can never reappear in the board."""
    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist SRCL-3520 初回限定盤 CD",
                price=500,
                currency="JPY",
                catalog_no="SRCL-3520",
                external_item_id="legacy-card",
                availability="available",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        return [
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=320),
            XianyuPriceSample(catalog_no=query, title="SRCL-3520 CD", price_cny=340),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )
    assert result.evaluated_count == 1
    assert len(list_discovery_opportunities(db_path)) == 1

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE discovery_candidates SET detail_verified = 0 WHERE identity_key = ?",
            ("source:legacy-card",),
        )
        conn.commit()

    assert list_discovery_opportunities(db_path) == []


def test_title_only_candidate_with_strict_samples_can_enter_profit_board(tmp_path: Path) -> None:
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
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == 1
    feed = list_discovery_opportunities(db_path)
    assert len(feed) == 1
    assert feed[0]["match_confidence"] == 0.8
    assert feed[0]["valid_xianyu_sample_count"] == 2
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT identity_key FROM discovery_candidates").fetchone()[0] == "source:title-only-1"


def test_title_only_candidate_discards_unmatched_xianyu_samples(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Artist Album 初回限定盤 CD",
                price=1200,
                currency="JPY",
                external_item_id="title-only-unmatched",
                availability="available",
            )
        ]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        return [
            XianyuPriceSample(catalog_no=query, title="Other Album CD", price_cny=300),
            XianyuPriceSample(catalog_no=query, title="Other Album CD", price_cny=320),
            XianyuPriceSample(catalog_no=query, title="Other Album CD", price_cny=340),
        ]

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT valid_xianyu_sample_count FROM opportunities"
        ).fetchone()[0] == 0
    assert list_discovery_opportunities(db_path) == []


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
            fetch_wameiji_detail=_verified_detail,
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
            fetch_wameiji_detail=_verified_detail,
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
            fetch_wameiji_detail=_verified_detail,
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
            fetch_wameiji_detail=_verified_detail,
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
            fetch_wameiji_detail=_verified_detail,
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
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == result.evaluated_count == 1
    assert xianyu_queries == ["CANDY TUNE BEST"]


def test_cd_discovery_rejects_phone_case_with_a_weak_band_marker(tmp_path: Path) -> None:
    """"帯付き" modifies a title; it never proves the item is a CD."""

    db_path = tmp_path / "selection.db"
    opened_details: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="OPPO A3 5G CPH2639スマホケース 手首帯付き リンク付き 無地ビジネス革ケース",
                price=2236,
                currency="JPY",
                external_item_id="oppo-case-with-band",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="Artist Best Album 初回限定盤 CD 帯付き",
                price=1200,
                currency="JPY",
                external_item_id="actual-cd-with-band",
                availability="available",
            ),
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        opened_details.append(item.title)
        return await _verified_detail(item)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        return []

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="帯付き",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == result.detail_query_count == 1
    assert opened_details == ["Artist Best Album 初回限定盤 CD 帯付き"]


def test_cd_discovery_keeps_an_import_disc_without_an_english_cd_marker(tmp_path: Path) -> None:
    """輸入盤 is a valid CD-source signal even when the title omits literal CD."""

    db_path = tmp_path / "selection.db"
    opened_details: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Worrisome Heart [帯付き輸入盤]",
                price=1296,
                currency="JPY",
                external_item_id="import-disc",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        opened_details.append(item.title)
        return await _verified_detail(item)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        return []

    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="輸入盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == result.detail_query_count == 1
    assert opened_details == ["Worrisome Heart [帯付き輸入盤]"]


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
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == result.evaluated_count == 1
    assert xianyu_queries == ["薄桜鬼 Switch"]


def test_game_discovery_rejects_switch_protective_case(tmp_path: Path) -> None:
    """A platform name alone must not enqueue a protective accessory."""

    db_path = tmp_path / "selection.db"
    opened_details: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Nintendo Switch 有機ELモデル用 保護ケース",
                price=1800,
                currency="JPY",
                external_item_id="switch-protective-case",
                availability="available",
            ),
            MarketItem(
                source="wameiji",
                title="薄桜鬼 Switch 限定版 ゲームソフト",
                price=5000,
                currency="JPY",
                external_item_id="switch-game-software",
                availability="available",
            ),
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        opened_details.append(item.title)
        return await _verified_detail(item)

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        return []

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="Switch 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == result.detail_query_count == 1
    assert opened_details == ["薄桜鬼 Switch 限定版 ゲームソフト"]


def test_game_discovery_skips_xianyu_when_detail_is_missing_the_core_media(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Nintendo Switch レイディアント シルバーガン COLLECTOR'S BOX",
                price=1799,
                currency="JPY",
                external_item_id="incomplete-collector-box",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        return replace(
            item,
            detail_verified=True,
            raw_text="※ソフト+サントラ欠品です。",
        )

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
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.detail_query_count == 1
    assert result.xianyu_query_count == result.evaluated_count == 0
    assert xianyu_queries == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status, detail_attempt_count FROM discovery_candidates WHERE source_item_id = ?",
            ("incomplete-collector-box",),
        ).fetchone() == ("ignored", 1)


def test_title_only_discovery_retries_with_an_exact_public_title_alias(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []

    source = MarketItem(
        source="wameiji",
        title="【一部未使用】あくありうむ。 完全生産限定版 Switch ソフト",
        price=4899,
        currency="JPY",
        external_item_id="aquarium-alias",
        availability="available",
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [source]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        if query == "あくありうむ Switch":
            return []
        assert query == "Aquarium Switch 完全生产限定版"
        return [
            XianyuPriceSample(
                catalog_no=query,
                title="凑阿库娅 AQUARIUM 完全生产限定版 Switch 游戏卡带",
                price_cny=400,
                url="https://www.goofish.com/item?id=400",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="凑阿库娅 AQUARIUM 完全生产限定版 Switch 游戏卡带",
                price_cny=450,
                url="https://www.goofish.com/item?id=450",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="凑阿库娅 AQUARIUM 完全生产限定版 Switch 游戏卡带",
                price_cny=500,
                url="https://www.goofish.com/item?id=500",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="AQUARIUM 完全生产限定版 Switch 日本代购",
                price_cny=456,
                url="https://www.goofish.com/item?id=456",
            ),
        ]

    async def resolve_aliases(_candidate):
        return [
            ResolvedTitleAlias(
                value="Aquarium",
                source="wikidata",
                source_url="https://www.wikidata.org/wiki/Q114964778",
                entity_id="Q114964778",
            )
        ]

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="Switch 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
            resolve_title_aliases=resolve_aliases,
        )
    )

    assert result.xianyu_query_count == 2
    assert result.evaluated_count == 1
    assert xianyu_queries == ["あくありうむ Switch", "Aquarium Switch 完全生产限定版"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT query, source_url FROM discovery_title_alias_evidence"
        ).fetchone() == (
            "Aquarium Switch 完全生产限定版",
            "https://www.wikidata.org/wiki/Q114964778",
        )
        assert conn.execute(
            "SELECT valid_xianyu_sample_count FROM opportunities"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT is_valid, invalid_reason FROM xianyu_price_samples WHERE price_cny = 456"
        ).fetchone() == (0, "invalid_proxy_listing")


def test_title_only_limited_source_excludes_normal_edition_resale_cards(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    source = MarketItem(
        source="wameiji",
        title="Switch Macross Shooting Insight 限定版",
        price=4000,
        currency="JPY",
        external_item_id="macross-limited-edition",
        availability="available",
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [source]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        assert query == "Switch Macross Shooting Insight"
        return [
            XianyuPriceSample(
                catalog_no=query,
                title="Switch Macross Shooting Insight 普通版 游戏卡带",
                price_cny=95,
                url="https://www.goofish.com/item?id=normal",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="Switch Macross Shooting Insight 日版限定版 游戏卡带",
                price_cny=265,
                url="https://www.goofish.com/item?id=limited-265",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="Switch Macross Shooting Insight 日版限定版 游戏卡带",
                price_cny=418,
                url="https://www.goofish.com/item?id=limited-418",
            ),
        ]

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="Switch 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.evaluated_count == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT valid_xianyu_sample_count FROM opportunities"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT is_valid, invalid_reason FROM xianyu_price_samples WHERE price_cny = 95"
        ).fetchone() == (0, "title_mismatch")


def test_detail_verified_new_source_excludes_explicitly_used_resale_cards(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    source = MarketItem(
        source="wameiji",
        title="Switch Macross Shooting Insight 限定版",
        price=3300,
        currency="JPY",
        external_item_id="new-macross-limited",
        availability="available",
        raw_text=(
            "Switch Macross Shooting Insight 限定版 新品 价格 3300 日元 "
            "中古精选站点活动"
        ),
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [source]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        assert query == "Switch Macross Shooting Insight"
        return [
            XianyuPriceSample(
                catalog_no=query,
                title="Switch Macross Shooting Insight 限定版 日版二手",
                price_cny=245,
                url="https://www.goofish.com/item?id=used",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="Switch Macross Shooting Insight 限定版 全新未拆封",
                price_cny=340,
                url="https://www.goofish.com/item?id=new-340",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="Switch Macross Shooting Insight 限定版 全新未拆封",
                price_cny=418,
                url="https://www.goofish.com/item?id=new-418",
            ),
        ]

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="Switch 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.evaluated_count == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT valid_xianyu_sample_count FROM opportunities"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT is_valid, invalid_reason FROM xianyu_price_samples WHERE price_cny = 245"
        ).fetchone() == (0, "invalid_condition_mismatch")


def test_catalog_lookup_falls_back_to_a_matched_title_when_xianyu_omits_the_code(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []
    source = MarketItem(
        source="wameiji",
        title="Horizon Zero Dawn 初回限定版 PS4",
        price=297,
        currency="JPY",
        catalog_no="PCJS-73501",
        external_item_id="catalog-title-fallback",
        availability="available",
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [source]

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        if query == "PCJS-73501":
            return []
        assert query == "Horizon Zero Dawn PS4"
        return [
            XianyuPriceSample(
                catalog_no=query,
                title="Horizon Zero Dawn PS4 限定版 游戏光盘",
                price_cny=120,
                url="https://www.goofish.com/item?id=120",
            ),
            XianyuPriceSample(
                catalog_no=query,
                title="Horizon Zero Dawn PS4 初回限定版",
                price_cny=140,
                url="https://www.goofish.com/item?id=140",
            ),
        ]

    pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="PS4 限定版",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.xianyu_query_count == 2
    assert result.evaluated_count == 1
    assert xianyu_queries == ["PCJS-73501", "Horizon Zero Dawn PS4"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT valid_xianyu_sample_count FROM opportunities"
        ).fetchone()[0] == 2


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
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == 2
    assert result.xianyu_query_count == 1
    assert result.evaluated_count == 0
    assert xianyu_queries == ["Artist One Blue Skies"]


def test_security_check_cools_down_xianyu_but_still_verifies_wameiji_details(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    xianyu_queries: list[str] = []
    detail_urls: list[str] = []
    phase = 0

    first = MarketItem(
        source="wameiji",
        title="Artist One Blue Skies CD",
        price=1000,
        currency="JPY",
        external_item_id="security-cooldown-first",
        availability="available",
    )
    second = MarketItem(
        source="wameiji",
        title="Game Two Nintendo Switch 限定版",
        price=5000,
        currency="JPY",
        external_item_id="security-cooldown-second",
        availability="available",
    )

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [first] if phase == 0 else [second]

    async def fetch_wameiji_detail(item: MarketItem) -> MarketItem:
        detail_urls.append(str(item.external_item_id))
        return replace(item, detail_verified=True)

    async def fetch_xianyu(query: str) -> list[XianyuPriceSample]:
        xianyu_queries.append(query)
        raise RuntimeError("xianyu:security_check")

    pool_id = list_discovery_pools(db_path)[0].id
    game_pool_id = list_discovery_pools(db_path)[1].id
    assert pool_id is not None
    assert game_pool_id is not None
    first_result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool_id,
            keyword="初回限定盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )
    phase = 1
    second_result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=game_pool_id,
            keyword="廃盤",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert first_result.xianyu_query_count == 1
    assert get_discovery_source_cooldown(db_path, "xianyu") is not None
    assert second_result.status == "ok"
    assert second_result.candidate_count == 1
    assert second_result.detail_query_count == 1
    assert second_result.xianyu_query_count == second_result.evaluated_count == 0
    assert xianyu_queries == ["Artist One Blue Skies"]
    assert detail_urls == ["security-cooldown-first", "security-cooldown-second"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM discovery_candidates").fetchone()[0] == 2
        assert conn.execute(
            "SELECT count(*) FROM discovery_candidates WHERE detail_verified = 1"
        ).fetchone()[0] == 2


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
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.evaluated_count == 1
    assert list_discovery_opportunities(db_path) == []
    update_discovery_pool(db_path, pool_id, {"min_profit_cny": 0})
    assert len(list_discovery_opportunities(db_path)) == 1
