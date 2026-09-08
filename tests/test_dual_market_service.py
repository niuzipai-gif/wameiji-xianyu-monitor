from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cd_monitor.core.dual_market import DualMarketCostConfig, ListingObservation
from cd_monitor.services.dual_market_service import rebuild_current_comparison
from cd_monitor.storage.sqlite import insert_listing_observation

COST_CONFIG = DualMarketCostConfig(
    exchange_rate_cny_per_jpy=0.05,
    japan_domestic_shipping_jpy=100,
    proxy_fee_jpy=200,
    international_shipping_per_item_cny=18,
    china_reship_cny=12,
    packaging_cny=2,
    after_sale_reserve_cny=5,
    risk_reserve_cny=8,
    tax_cny=0,
    sales_fee_rate=0.006,
    sales_fee_cap_cny=60,
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
    )
    return replace(observation, **changes)


def test_build_comparison_references_each_side_lowest_eligible_observation(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.db"
    wameiji_id = insert_listing_observation(db_path, make_observation())
    xianyu_id = insert_listing_observation(
        db_path,
        make_observation(
            source="xianyu",
            source_listing_id="x-1",
            price=298,
            currency="CNY",
            url="https://www.goofish.com/item?id=x-1",
            image_url="https://images.example/xianyu/x-1.jpg",
            evidence_level="search_card",
        ),
    )

    outcome = rebuild_current_comparison(db_path, "catalog:srcl3520", COST_CONFIG)

    assert outcome.status == "ready"
    assert outcome.comparison is not None
    assert outcome.comparison.wameiji_observation_id == wameiji_id
    assert outcome.comparison.xianyu_observation_id == xianyu_id
    assert outcome.comparison.expected_profit_cny is not None
    assert outcome.comparison.expected_profit_cny > 0


def test_build_comparison_returns_waiting_when_only_one_source_has_an_eligible_listing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitor.db"
    insert_listing_observation(db_path, make_observation())

    outcome = rebuild_current_comparison(db_path, "catalog:srcl3520", COST_CONFIG)

    assert outcome.status == "waiting_xianyu"
    assert outcome.comparison is None


def test_build_comparison_refuses_a_different_condition_even_with_the_same_product_key(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitor.db"
    insert_listing_observation(db_path, make_observation())
    insert_listing_observation(
        db_path,
        make_observation(
            source="xianyu",
            source_listing_id="x-sealed",
            price=298,
            currency="CNY",
            url="https://www.goofish.com/item?id=x-sealed",
            image_url="https://images.example/xianyu/x-sealed.jpg",
            evidence_level="search_card",
            condition_group="sealed",
        ),
    )

    outcome = rebuild_current_comparison(db_path, "catalog:srcl3520", COST_CONFIG)

    assert outcome.status == "waiting_xianyu"
    assert outcome.comparison is None


def test_lower_xianyu_price_creates_negative_profit_history_not_buy_recommendation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitor.db"
    insert_listing_observation(db_path, make_observation())
    insert_listing_observation(
        db_path,
        make_observation(
            source="xianyu",
            source_listing_id="x-low",
            price=100,
            currency="CNY",
            url="https://www.goofish.com/item?id=x-low",
            image_url="https://images.example/xianyu/x-low.jpg",
            evidence_level="search_card",
        ),
    )

    outcome = rebuild_current_comparison(db_path, "catalog:srcl3520", COST_CONFIG)

    assert outcome.status == "negative_profit"
    assert outcome.comparison is not None
    assert outcome.comparison.expected_profit_cny is not None
    assert outcome.comparison.expected_profit_cny < 0


def test_missing_tax_configuration_creates_cost_pending_without_inventing_profit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitor.db"
    insert_listing_observation(db_path, make_observation())
    insert_listing_observation(
        db_path,
        make_observation(
            source="xianyu",
            source_listing_id="x-1",
            price=298,
            currency="CNY",
            url="https://www.goofish.com/item?id=x-1",
            image_url="https://images.example/xianyu/x-1.jpg",
            evidence_level="search_card",
        ),
    )

    outcome = rebuild_current_comparison(
        db_path,
        "catalog:srcl3520",
        replace(COST_CONFIG, tax_cny=None),
    )

    assert outcome.status == "cost_pending"
    assert outcome.comparison is not None
    assert outcome.comparison.expected_profit_cny is None
