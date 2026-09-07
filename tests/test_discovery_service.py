from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.services.discovery import scan_discovery_keyword
from cd_monitor.storage.sqlite import (
    discovery_summary,
    get_discovery_source_cooldown,
    list_discovery_opportunities,
    list_discovery_pools,
    update_discovery_pool,
)


async def _verified_detail(item: MarketItem) -> MarketItem:
    """Offline stand-in for a separately tested successful Wameiji detail read."""
    return replace(item, detail_verified=True)


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


def test_keyword_scan_keeps_distinct_source_listings_for_one_catalog(tmp_path: Path) -> None:
    """Different source listings for the same catalog are distinct purchase opportunities."""
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
    assert xianyu_queries == ["SRCL-3520", "SRCL-3520"]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT identity_key, source_price FROM discovery_candidates ORDER BY source_price"
        ).fetchall()
    assert rows == [("source:cheap-listing", 900.0), ("source:expensive-listing", 1600.0)]


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
            fetch_wameiji_detail=_verified_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == 1
    feed = list_discovery_opportunities(db_path)
    assert len(feed) == 1
    assert feed[0]["match_confidence"] == 0.8
    assert feed[0]["valid_xianyu_sample_count"] == 3
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
