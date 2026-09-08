from __future__ import annotations

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
