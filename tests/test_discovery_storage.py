from __future__ import annotations

import sqlite3
from pathlib import Path

from cd_monitor.core.discovery import DiscoveryCandidate, build_identity_key
from cd_monitor.core.models import MarketItem, Opportunity
from cd_monitor.storage.sqlite import (
    discovery_summary,
    init_db,
    insert_discovery_opportunity,
    insert_market_items,
    list_discovery_detail_queue,
    list_discovery_keywords,
    list_discovery_opportunities,
    list_discovery_pools,
    list_discovery_resale_queue,
    mark_discovery_keyword_scanned,
    record_discovery_candidate_detail_attempt,
    record_discovery_title_alias_evidence,
    replace_discovery_keywords,
    update_discovery_pool,
    update_discovery_pool_last_scan,
    upsert_discovery_candidate,
    upsert_discovery_candidates_with_previous,
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
    assert all(pool.candidate_budget == 2 for pool in pools)


def test_detail_queue_keeps_an_older_unverified_listing_after_later_ingest(
    tmp_path: Path,
) -> None:
    """A later search page cannot make an earlier detail debt disappear."""

    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    first_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:older-detail-debt",
            title="Artist Album CD 初回限定盤",
            source_item_id="older-detail-debt",
            source_url="/mall/mercari/detail/older-detail-debt",
            source_price=1200,
            source_currency="JPY",
            availability="available",
        ),
    )
    upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:newer-detail-debt",
            title="Artist Album CD 初回限定盤",
            source_item_id="newer-detail-debt",
            source_url="/mall/mercari/detail/newer-detail-debt",
            source_price=1200,
            source_currency="JPY",
            availability="available",
        ),
    )

    queued = list_discovery_detail_queue(db_path, pool.id, limit=1)

    assert [candidate.id for candidate in queued] == [first_id]


def test_init_db_quarantines_pre_epoch_unverified_detail_debt_once(tmp_path: Path) -> None:
    """Old search cards must not starve the first real detail-first batch."""

    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    legacy_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:pre-epoch-card",
            title="Unverified legacy card CD",
            source_item_id="pre-epoch-card",
            source_url="/mall/mercari/detail/pre-epoch-card",
            source_price=1200,
            source_currency="JPY",
            availability="available",
        ),
    )
    verified_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:verified-before-epoch",
            title="Verified evidence CD",
            source_item_id="verified-before-epoch",
            source_url="/mall/mercari/detail/verified-before-epoch",
            source_price=1200,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )
    # Simulate an existing database being opened for the first detail-queue
    # recovery. The one-time epoch marker is absent in that old database.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM user_settings WHERE key = 'discovery_unverified_queue_epoch'"
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, status, pipeline_stage FROM discovery_candidates "
            "WHERE id IN (?, ?) ORDER BY id",
            (legacy_id, verified_id),
        ).fetchall()
    assert rows == [
        (legacy_id, "ignored", "quarantined"),
        (verified_id, "active", "resale_queued"),
    ]
    assert list_discovery_detail_queue(db_path, pool.id, limit=10) == []

    fresh_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:post-epoch-card",
            title="Fresh detail candidate CD",
            source_item_id="post-epoch-card",
            source_url="/mall/mercari/detail/post-epoch-card",
            source_price=900,
            source_currency="JPY",
            availability="available",
        ),
    )
    init_db(db_path)

    assert [candidate.id for candidate in list_discovery_detail_queue(db_path, pool.id, limit=10)] == [fresh_id]

    revived_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:pre-epoch-card",
            title="Re-observed source detail candidate CD",
            source_item_id="pre-epoch-card",
            source_url="/mall/mercari/detail/pre-epoch-card",
            source_price=1100,
            source_currency="JPY",
            availability="available",
        ),
    )

    assert revived_id == legacy_id
    assert [candidate.id for candidate in list_discovery_detail_queue(db_path, pool.id, limit=10)] == [
        legacy_id,
        fresh_id,
    ]


def test_init_db_retires_duplicate_source_urls_without_erasing_history(
    tmp_path: Path,
) -> None:
    """Legacy identity mistakes cannot leave two active rows for one listing."""

    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    duplicate_url = "/mall/mercari/detail/same-listing"
    legacy_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="catalog:LEGACY001",
            title="Legacy title",
            source_item_id="legacy-identity",
            source_url=duplicate_url,
            source_price=1500,
            source_currency="JPY",
            availability="available",
        ),
    )
    canonical_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:real-source-id",
            title="Verified title CD",
            source_item_id="real-source-id",
            source_url=duplicate_url,
            source_price=1200,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, status FROM discovery_candidates "
            "WHERE source_url = ? ORDER BY id",
            (duplicate_url,),
        ).fetchall()
    assert rows == [(legacy_id, "ignored"), (canonical_id, "active")]


def test_init_db_rebuilds_stale_pool_next_run_for_the_pool_rate_limit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_pools
            SET scan_interval_minutes = 25,
                last_scanned_at = '2026-09-07 00:00:00',
                next_run_at = '1999-01-01 00:00:00'
            WHERE id = ?
            """,
            (pool_id,),
        )

    init_db(db_path)

    assert list_discovery_pools(db_path)[0].next_run_at == "2026-09-07 00:25:00"


def test_init_db_invalidates_legacy_footer_based_reservations(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    candidate_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:legacy-footer-state",
            title="Legacy CD",
            source_item_id="legacy-footer-state",
            source_price=980,
            source_currency="JPY",
            availability="reserved",
            status="expired",
            raw_text="Product evidence Copyright All Rights Reserved",
            detail_verified=True,
        ),
    )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT availability, status, detail_verified, last_xianyu_checked_at "
            "FROM discovery_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    assert row == ("unknown_but_visible", "active", 0, None)


def test_init_db_rechecks_legacy_title_only_candidates_once(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    title_only_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:legacy-title-only",
            title="Artist Album CD",
            source_item_id="legacy-title-only",
            source_price=980,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )
    exact_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:legacy-catalog",
            catalog_no="SRCL-3520",
            title="Artist Album CD",
            source_item_id="legacy-catalog",
            source_price=980,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE discovery_candidates SET last_xianyu_checked_at = '2026-09-07 00:00:00'"
        )
        conn.execute(
            "UPDATE user_settings SET value = 'previous' "
            "WHERE key = 'discovery_title_query_evidence_version'"
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, last_xianyu_checked_at FROM discovery_candidates ORDER BY id"
        ).fetchall()
    assert rows == [(title_only_id, None), (exact_id, "2026-09-07 00:00:00")]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE discovery_candidates SET last_xianyu_checked_at = '2026-09-07 01:00:00' "
            "WHERE id = ?",
            (title_only_id,),
        )
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT last_xianyu_checked_at FROM discovery_candidates WHERE id = ?",
            (title_only_id,),
        ).fetchone()[0] == "2026-09-07 01:00:00"


def test_identity_key_prefers_source_listing_then_catalog_jan_then_title() -> None:
    assert build_identity_key(
        catalog_no=" SRCL-3520 ",
        jan="4988000000000",
        external_item_id="wameiji-7",
        title="ignored",
    ) == "source:wameiji-7"
    assert build_identity_key(
        catalog_no=None,
        jan="4988000000000",
        external_item_id="wameiji-7",
        title="ignored",
    ) == "source:wameiji-7"
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


def test_init_db_ignores_legacy_catalog_candidate_when_the_same_source_listing_exists(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    legacy_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="catalog:TKCA72506",
            catalog_no="TKCA-72506",
            title="Legacy Song File",
            source_item_id="same-listing",
            source_price=890,
            source_currency="JPY",
            availability="unknown_but_visible",
            detail_verified=False,
        ),
    )
    canonical_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:same-listing",
            catalog_no="TKCA-72506",
            title="Verified Song File",
            source_item_id="same-listing",
            source_price=890,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, status FROM discovery_candidates WHERE id IN (?, ?) ORDER BY id",
            (legacy_id, canonical_id),
        ).fetchall()
    assert rows == [(legacy_id, "ignored"), (canonical_id, "active")]


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


def test_title_alias_evidence_is_persisted_with_its_public_source(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[1]
    candidate_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="physical_game",
            identity_key="source:alias-proof",
            title="あくありうむ。 完全生産限定版 Switch",
            source_item_id="alias-proof",
            source_price=4899,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )

    record_discovery_title_alias_evidence(
        db_path,
        candidate_id=candidate_id,
        source_title="あくありうむ。 完全生産限定版 Switch",
        alias="Aquarium",
        query="Aquarium Switch 完全生产限定版",
        resolver="wikidata",
        source_url="https://www.wikidata.org/wiki/Q114964778",
        entity_id="Q114964778",
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT source_title, alias, query, resolver, source_url, entity_id "
            "FROM discovery_title_alias_evidence"
        ).fetchone()
    assert row == (
        "あくありうむ。 完全生産限定版 Switch",
        "Aquarium",
        "Aquarium Switch 完全生产限定版",
        "wikidata",
        "https://www.wikidata.org/wiki/Q114964778",
        "Q114964778",
    )


def test_batch_candidate_upsert_preserves_previous_values_in_input_order(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    cd_pool = list_discovery_pools(db_path)[0]
    assert cd_pool.id is not None
    existing = DiscoveryCandidate(
        pool_id=cd_pool.id,
        media_type="cd",
        identity_key="source:batch-existing",
        title="Existing Album CD",
        source_item_id="batch-existing",
        source_price=1200,
        source_currency="JPY",
        availability="available",
    )
    existing_id = upsert_discovery_candidate(db_path, existing)
    refreshed = DiscoveryCandidate(
        pool_id=cd_pool.id,
        media_type="cd",
        identity_key="source:batch-existing",
        title="Existing Album CD",
        source_item_id="batch-existing",
        source_price=950,
        source_currency="JPY",
        availability="available",
    )
    new = DiscoveryCandidate(
        pool_id=cd_pool.id,
        media_type="cd",
        identity_key="source:batch-new",
        title="New Album CD",
        source_item_id="batch-new",
        source_price=880,
        source_currency="JPY",
        availability="available",
    )

    result = upsert_discovery_candidates_with_previous(db_path, [refreshed, new])

    previous_existing, refreshed_id = result[0]
    previous_new, new_id = result[1]
    assert previous_existing is not None
    assert previous_existing.id == existing_id
    assert previous_existing.source_price == 1200
    assert refreshed_id == existing_id
    assert previous_new is None
    assert new_id != existing_id
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT identity_key, source_price FROM discovery_candidates ORDER BY identity_key"
        ).fetchall()
    assert rows == [("source:batch-existing", 950), ("source:batch-new", 880)]


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


def test_pool_next_run_tracks_its_last_scan_interval(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"scan_interval_minutes": 25})
    replace_discovery_keywords(
        db_path,
        pool_id,
        [
            {"keyword": "first", "weight": 2, "enabled": True},
            {"keyword": "second", "weight": 1, "enabled": True},
        ],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE discovery_keywords SET last_scanned_at = ? WHERE pool_id = ? AND keyword = ?",
            ("2026-09-07 00:00:00", pool_id, "first"),
        )
        conn.execute(
            "UPDATE discovery_keywords SET last_scanned_at = ? WHERE pool_id = ? AND keyword = ?",
            ("2026-09-07 00:20:00", pool_id, "second"),
        )

    update_discovery_pool_last_scan(db_path, pool_id)

    with sqlite3.connect(db_path) as conn:
        expected = conn.execute(
            """
            SELECT datetime(last_scanned_at, '+' || scan_interval_minutes || ' minutes')
            FROM discovery_pools
            WHERE id = ?
            """,
            (pool_id,),
        ).fetchone()[0]
    assert list_discovery_pools(db_path)[0].next_run_at == expected


def test_marking_a_keyword_scanned_preserves_the_pool_rate_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"scan_interval_minutes": 25})
    replace_discovery_keywords(
        db_path,
        pool_id,
        [
            {"keyword": "first", "weight": 2, "enabled": True},
            {"keyword": "second", "weight": 1, "enabled": True},
        ],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE discovery_keywords SET last_scanned_at = datetime('now', '-30 minutes') WHERE pool_id = ? AND keyword = ?",
            (pool_id, "first"),
        )
        conn.execute(
            "UPDATE discovery_keywords SET last_scanned_at = datetime('now', '-5 minutes') WHERE pool_id = ? AND keyword = ?",
            (pool_id, "second"),
        )
    update_discovery_pool_last_scan(db_path, pool_id)
    first_keyword = next(
        keyword
        for keyword in list_discovery_keywords(db_path, pool_id)
        if keyword.keyword == "first"
    )

    mark_discovery_keyword_scanned(db_path, first_keyword.id or 0)

    with sqlite3.connect(db_path) as conn:
        expected = conn.execute(
            """
            SELECT datetime(last_scanned_at, '+' || scan_interval_minutes || ' minutes')
            FROM discovery_pools
            WHERE id = ?
            """,
            (pool_id,),
        ).fetchone()[0]
    assert list_discovery_pools(db_path)[0].next_run_at == expected


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
                detail_verified=True,
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


def test_selection_board_hides_stale_xianyu_evaluation(tmp_path: Path) -> None:
    """A current board must never present an overdue price sample as profit."""

    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    candidate_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:stale-price",
            title="Stale Price Album CD",
            source_item_id="stale-price",
            source_price=1200,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )
    item = MarketItem(
        source="wameiji",
        title="Stale Price Album CD",
        price=1200,
        currency="JPY",
        external_item_id="stale-price",
        availability="available",
    )
    item_id = insert_market_items(db_path, [item])[0]
    opportunity_id = insert_discovery_opportunity(
        db_path,
        Opportunity(
            catalog_no="stale-price",
            item=item,
            xianyu_reference_price=300,
            expected_sale_price=250,
            landed_cost=70,
            expected_revenue=180,
            expected_profit=110,
            net_margin=0.61,
            turnover_adjusted_roi=0.61,
            match_confidence=0.9,
            valid_xianyu_sample_count=3,
            liquidity_status="normal",
            decision="strong_alert",
            opportunity_hash="stale-price",
        ),
        wameiji_item_id=item_id,
        discovery_candidate_id=candidate_id,
        media_type="cd",
        identity_key="source:stale-price",
    )

    assert len(list_discovery_opportunities(db_path)) == 1
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE opportunities
            SET last_seen_at = datetime(CURRENT_TIMESTAMP, '-181 minutes')
            WHERE id = ?
            """,
            (opportunity_id,),
        )

    assert list_discovery_opportunities(db_path) == []
    assert discovery_summary(db_path)["active_opportunities"] == 0


def test_stale_source_detail_requeues_before_any_xianyu_resale_lookup(
    tmp_path: Path,
) -> None:
    """A stale purchase price must be re-opened before it can use a resale price."""

    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    candidate_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:stale-detail",
            title="Stale Detail Album CD",
            source_item_id="stale-detail",
            source_url="/mall/market/detail/stale-detail",
            source_price=1200,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_candidates
            SET detail_verified_at = datetime(CURRENT_TIMESTAMP, '-181 minutes'),
                last_xianyu_checked_at = datetime(CURRENT_TIMESTAMP, '-181 minutes'),
                pipeline_stage = 'evaluated'
            WHERE id = ?
            """,
            (candidate_id,),
        )

    assert [candidate.id for candidate in list_discovery_detail_queue(db_path, pool.id, limit=10)] == [
        candidate_id
    ]
    assert list_discovery_resale_queue(db_path, pool.id, limit=10) == []

    record_discovery_candidate_detail_attempt(
        db_path,
        candidate_id,
        pipeline_stage="resale_queued",
        detail_verified=True,
        increment_attempt=False,
    )

    assert list_discovery_detail_queue(db_path, pool.id, limit=10) == []
    assert [candidate.id for candidate in list_discovery_resale_queue(db_path, pool.id, limit=10)] == [
        candidate_id
    ]


def test_selection_board_hides_stale_source_detail_even_with_fresh_xianyu_price(
    tmp_path: Path,
) -> None:
    """Both sides of a profit card must have fresh evidence."""

    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    candidate_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:stale-source-board",
            title="Stale Source Board Album CD",
            source_item_id="stale-source-board",
            source_price=1200,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )
    item = MarketItem(
        source="wameiji",
        title="Stale Source Board Album CD",
        price=1200,
        currency="JPY",
        external_item_id="stale-source-board",
        availability="available",
    )
    item_id = insert_market_items(db_path, [item])[0]
    insert_discovery_opportunity(
        db_path,
        Opportunity(
            catalog_no="stale-source-board",
            item=item,
            xianyu_reference_price=300,
            expected_sale_price=250,
            landed_cost=70,
            expected_revenue=180,
            expected_profit=110,
            net_margin=0.61,
            turnover_adjusted_roi=0.61,
            match_confidence=0.9,
            valid_xianyu_sample_count=3,
            liquidity_status="normal",
            decision="strong_alert",
            opportunity_hash="stale-source-board",
        ),
        wameiji_item_id=item_id,
        discovery_candidate_id=candidate_id,
        media_type="cd",
        identity_key="source:stale-source-board",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_candidates
            SET detail_verified_at = datetime(CURRENT_TIMESTAMP, '-181 minutes')
            WHERE id = ?
            """,
            (candidate_id,),
        )

    assert list_discovery_opportunities(db_path) == []
    assert discovery_summary(db_path)["active_opportunities"] == 0


def test_discovery_summary_exposes_detail_first_pipeline_state(tmp_path: Path) -> None:
    """The board must distinguish fresh purchase evidence from stale debt."""

    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    fresh_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:fresh-summary-detail",
            title="Fresh Summary Album CD",
            source_item_id="fresh-summary-detail",
            source_url="/mall/mercari/detail/fresh-summary-detail",
            source_price=1200,
            source_currency="JPY",
            availability="available",
        ),
    )
    stale_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:stale-summary-detail",
            title="Stale Summary Album CD",
            source_item_id="stale-summary-detail",
            source_url="/mall/mercari/detail/stale-summary-detail",
            source_price=1200,
            source_currency="JPY",
            availability="available",
        ),
    )
    for candidate_id in (fresh_id, stale_id):
        record_discovery_candidate_detail_attempt(
            db_path,
            candidate_id,
            pipeline_stage="resale_queued",
            detail_verified=True,
        )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE discovery_candidates
            SET detail_verified_at = datetime(CURRENT_TIMESTAMP, '-181 minutes')
            WHERE id = ?
            """,
            (stale_id,),
        )

    summary = discovery_summary(db_path)

    assert summary["active_candidates"] == 2
    assert summary["fresh_source_details"] == 1
    assert summary["stale_source_details"] == 1
    assert summary["resale_ready_candidates"] == 1


def test_selection_board_retires_a_previous_evaluation_for_the_same_candidate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    candidate_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:rechecked",
            title="Rechecked Album",
            source_item_id="rechecked",
            source_price=1000,
            source_currency="JPY",
            availability="available",
            detail_verified=True,
        ),
    )
    item = MarketItem(
        source="wameiji",
        title="Rechecked Album",
        price=1000,
        currency="JPY",
        external_item_id="rechecked",
        availability="available",
    )
    item_id = insert_market_items(db_path, [item])[0]

    positive_id = insert_discovery_opportunity(
        db_path,
        Opportunity(
            catalog_no="rechecked",
            item=item,
            xianyu_reference_price=300,
            expected_sale_price=250,
            landed_cost=70,
            expected_revenue=170,
            expected_profit=100,
            net_margin=1.0,
            turnover_adjusted_roi=1.0,
            match_confidence=0.9,
            valid_xianyu_sample_count=3,
            liquidity_status="normal",
            decision="strong_alert",
            opportunity_hash="rechecked-positive",
        ),
        wameiji_item_id=item_id,
        discovery_candidate_id=candidate_id,
        media_type="cd",
        identity_key="source:rechecked",
    )
    rejected_id = insert_discovery_opportunity(
        db_path,
        Opportunity(
            catalog_no="rechecked",
            item=item,
            xianyu_reference_price=0,
            expected_sale_price=0,
            landed_cost=70,
            expected_revenue=0,
            expected_profit=-70,
            net_margin=-1.0,
            turnover_adjusted_roi=-1.0,
            match_confidence=0.7,
            valid_xianyu_sample_count=0,
            liquidity_status="poor",
            decision="reject",
            opportunity_hash="rechecked-reject",
        ),
        wameiji_item_id=item_id,
        discovery_candidate_id=candidate_id,
        media_type="cd",
        identity_key="source:rechecked",
    )

    assert list_discovery_opportunities(db_path) == []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, status FROM opportunities WHERE id IN (?, ?) ORDER BY id",
            (positive_id, rejected_id),
        ).fetchall()
    assert rows == [(positive_id, "expired"), (rejected_id, "active")]
