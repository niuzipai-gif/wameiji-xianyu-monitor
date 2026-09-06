from __future__ import annotations

import sqlite3
from pathlib import Path

from cd_monitor.core.discovery import DiscoveryCandidate, build_identity_key
from cd_monitor.core.models import MarketItem, Opportunity
from cd_monitor.storage.sqlite import (
    init_db,
    insert_discovery_opportunity,
    insert_market_items,
    list_discovery_keywords,
    list_discovery_opportunities,
    list_discovery_pools,
    replace_discovery_keywords,
    upsert_discovery_candidate,
)


def test_init_db_seeds_cd_and_physical_game_pools(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"

    init_db(db_path)

    pools = list_discovery_pools(db_path)
    assert [(pool.slug, pool.media_type) for pool in pools] == [
        ("cd", "cd"),
        ("physical-game", "physical_game"),
    ]
    assert all(pool.enabled for pool in pools)
    assert all(pool.scan_interval_minutes == 30 for pool in pools)


def test_identity_key_prefers_catalog_then_jan_then_source_then_title() -> None:
    assert build_identity_key(
        catalog_no=" SRCL-3520 ",
        jan="4988000000000",
        external_item_id="wameiji-7",
        title="ignored",
    ) == "catalog:SRCL3520"
    assert build_identity_key(
        catalog_no=None,
        jan="4988000000000",
        external_item_id="wameiji-7",
        title="ignored",
    ) == "jan:4988000000000"
    assert build_identity_key(
        catalog_no=None,
        jan=None,
        external_item_id=" wameiji-7 ",
        title="ignored",
    ) == "source:wameiji-7"
    title_key = build_identity_key(
        catalog_no=None,
        jan=None,
        external_item_id=None,
        title="  Artist   Album  ",
        artist="Artist",
        edition="初回限定盤",
    )
    assert title_key.startswith("title:")
    assert len(title_key) == len("title:") + 24


def test_candidate_upsert_keeps_identity_and_refreshes_market_observation(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    cd_pool = list_discovery_pools(db_path)[0]
    candidate = DiscoveryCandidate(
        pool_id=cd_pool.id,
        media_type="cd",
        identity_key="catalog:SRCL3520",
        catalog_no="SRCL-3520",
        title="Artist Album 初回限定盤",
        source_item_id="wameiji-1",
        source_url="https://meruki.cn/item/1",
        source_price=1200,
        source_currency="JPY",
        availability="available",
    )

    first_id = upsert_discovery_candidate(db_path, candidate)
    candidate.source_price = 980
    candidate.availability = "likely_available"
    second_id = upsert_discovery_candidate(db_path, candidate)

    assert second_id == first_id
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT source_price, availability, observation_count, status "
            "FROM discovery_candidates WHERE id = ?",
            (first_id,),
        ).fetchone()
    assert row == (980, "likely_available", 2, "active")


def test_replacing_pool_keywords_disables_stale_default_terms(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None

    replace_discovery_keywords(
        db_path,
        pool_id,
        [{"keyword": "动画原声带 初回限定", "weight": 3, "enabled": True}],
    )

    keywords = list_discovery_keywords(db_path, pool_id)
    enabled = [item for item in keywords if item.enabled]
    assert [(item.keyword, item.weight) for item in enabled] == [("动画原声带 初回限定", 3)]
    assert any(item.keyword == "初回限定盤" and not item.enabled for item in keywords)


def test_selection_board_orders_linked_opportunities_by_profit_descending(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    cd_pool = list_discovery_pools(db_path)[0]
    candidate_ids: list[int] = []
    for number, profit in (("A", 42.0), ("B", 96.0)):
        candidate_id = upsert_discovery_candidate(
            db_path,
            DiscoveryCandidate(
                pool_id=cd_pool.id,
                media_type="cd",
                identity_key=f"source:{number}",
                title=f"Album {number}",
                source_item_id=number,
                source_price=1200,
                source_currency="JPY",
                availability="available",
            ),
        )
        item_id = insert_market_items(
            db_path,
            [
                MarketItem(
                    source="wameiji",
                    title=f"Album {number}",
                    price=1200,
                    currency="JPY",
                    external_item_id=number,
                    availability="available",
                )
            ],
        )[0]
        opportunity = Opportunity(
            catalog_no=f"selection:{number}",
            item=MarketItem(source="wameiji", title=f"Album {number}", price=1200),
            xianyu_reference_price=200,
            expected_sale_price=180,
            landed_cost=60,
            expected_revenue=profit + 60,
            expected_profit=profit,
            net_margin=0.5,
            turnover_adjusted_roi=0.5,
            match_confidence=0.9,
            valid_xianyu_sample_count=3,
            liquidity_status="normal",
            decision="strong_alert",
            opportunity_hash=f"selection-{number}",
        )
        insert_discovery_opportunity(
            db_path,
            opportunity,
            wameiji_item_id=item_id,
            discovery_candidate_id=candidate_id,
            media_type="cd",
            identity_key=f"source:{number}",
        )
        candidate_ids.append(candidate_id)

    feed = list_discovery_opportunities(db_path, limit=20)

    assert [item["expected_profit"] for item in feed] == [96.0, 42.0]
    assert [item["candidate_id"] for item in feed] == [candidate_ids[1], candidate_ids[0]]
    assert all(item["media_type"] == "cd" for item in feed)
    assert feed[0]["purchase_price_jpy"] == 1200.0
    assert feed[0]["xianyu_price_cny"] == 200.0
