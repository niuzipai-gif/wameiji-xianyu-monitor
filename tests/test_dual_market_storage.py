from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from cd_monitor.core.dual_market import ListingObservation
from cd_monitor.core.models import MarketItem
from cd_monitor.storage.sqlite import (
    get_listing_observation,
    init_db,
    insert_listing_observation,
    insert_market_items,
    list_current_observations,
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
        captured_at="2026-09-08T00:00:00Z",
        raw_snapshot_path="snapshots/wameiji/m-1.html",
        screenshot_path="screenshots/wameiji/m-1.png",
        source_detail_fee=300,
    )
    return replace(observation, **changes)


def test_observation_round_trip_keeps_source_image_and_capture_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.db"

    observation_id = insert_listing_observation(db_path, make_observation())
    restored = get_listing_observation(db_path, observation_id)

    assert restored is not None
    assert restored.source == "wameiji"
    assert restored.image_url == "https://images.example/wameiji/m-1.jpg"
    assert restored.raw_snapshot_path == "snapshots/wameiji/m-1.html"
    assert restored.screenshot_path == "screenshots/wameiji/m-1.png"


def test_current_observations_keep_history_but_return_each_listing_latest_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.db"
    first_id = insert_listing_observation(db_path, make_observation())
    latest_id = insert_listing_observation(
        db_path,
        make_observation(price=1080, captured_at="2026-09-08T01:00:00Z"),
    )

    current = list_current_observations(db_path, canonical_product_key="catalog:srcl3520")

    assert first_id != latest_id
    assert [observation.price for observation in current] == [1080]


def test_current_observations_exclude_stale_evidence_when_a_freshness_boundary_is_given(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitor.db"
    insert_listing_observation(db_path, make_observation(captured_at="2026-09-08T00:00:00Z"))

    current = list_current_observations(
        db_path,
        canonical_product_key="catalog:srcl3520",
        captured_since="2026-09-08T03:00:00Z",
    )

    assert current == []


def test_initialize_database_preserves_legacy_market_items_when_adding_observations(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    [legacy_id] = insert_market_items(
        db_path,
        [
            MarketItem(
                source="wameiji",
                title="Legacy listing",
                price=1000,
                currency="JPY",
            )
        ],
    )

    init_db(db_path)
    observation_id = insert_listing_observation(db_path, make_observation())

    assert legacy_id > 0
    assert get_listing_observation(db_path, observation_id) is not None


def test_initialize_database_migrates_legacy_comparison_statuses(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE price_comparisons (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              canonical_product_key TEXT NOT NULL,
              wameiji_observation_id INTEGER NOT NULL,
              xianyu_observation_id INTEGER NOT NULL,
              cost_config_json TEXT NOT NULL,
              landed_cost_cny REAL,
              sale_price_cny REAL NOT NULL,
              expected_profit_cny REAL,
              net_margin REAL,
              status TEXT NOT NULL CHECK(status IN ('ready', 'negative_profit', 'cost_pending')),
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO price_comparisons (
              canonical_product_key, wameiji_observation_id, xianyu_observation_id,
              cost_config_json, sale_price_cny, status
            ) VALUES (?, 1, 2, '{}', 100, ?)
            """,
            (("catalog:ready", "ready"), ("catalog:negative", "negative_profit")),
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='price_comparisons'"
        ).fetchone()[0]
        statuses = [
            row[0]
            for row in conn.execute("SELECT status FROM price_comparisons ORDER BY id").fetchall()
        ]
        conn.execute(
            """
            INSERT INTO price_comparisons (
              canonical_product_key, wameiji_observation_id, xianyu_observation_id,
              cost_config_json, sale_price_cny, status
            ) VALUES ('catalog:new', 1, 2, '{}', 100, 'eligible')
            """
        )

    assert "'eligible'" in table_sql
    assert "'below_margin'" in table_sql
    assert statuses == ["eligible", "below_margin"]
