from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cd_monitor.core.dual_market import (
    DualMarketCostConfig,
    ListingObservation,
    observation_from_wameiji,
    observation_from_xianyu,
)
from cd_monitor.core.models import MarketItem, XianyuPriceSample
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
    assert outcome.comparison.sale_price_cny == 298
    assert outcome.comparison.landed_cost_cny == 124
    assert outcome.comparison.expected_profit_cny == 172.21


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


@pytest.mark.parametrize(
    ("risk_raw_text", "expected_condition_group"),
    [
        ("盘面有轻微划痕", "minor_damage"),
        ("傷が多く大きな傷あり", "heavy_damage"),
        ("外盒有压痕", "box_damage"),
        ("ジャンク・動作不良", "defective"),
        ("功能未测试", "untested"),
    ],
)
def test_build_comparison_never_pairs_complete_used_with_a_risk_condition(
    tmp_path: Path,
    risk_raw_text: str,
    expected_condition_group: str,
) -> None:
    db_path = tmp_path / "monitor.db"
    wameiji = observation_from_wameiji(
        MarketItem(
            source="wameiji",
            title="Album SRCL-3520",
            price=1280,
            currency="JPY",
            external_item_id="m-complete-used",
            catalog_no="SRCL-3520",
            url="/mall/mercari/detail/m-complete-used",
            image_url="//images.example/wameiji/m-complete-used.jpg",
            availability="available",
            condition_text="中古・良品",
            detail_verified=True,
        ),
        captured_at="2026-09-08T00:00:00Z",
    )
    xianyu = observation_from_xianyu(
        XianyuPriceSample(
            catalog_no="SRCL-3520",
            title="Album SRCL-3520",
            price_cny=298,
            url="/item?id=x-risk-condition",
            image_url="//images.example/xianyu/x-risk-condition.jpg",
            raw_text=risk_raw_text,
        ),
        captured_at="2026-09-08T00:00:00Z",
    )

    assert wameiji.condition_group == "complete_used"
    assert xianyu.condition_group == expected_condition_group
    assert wameiji.canonical_product_key != xianyu.canonical_product_key
    insert_listing_observation(db_path, wameiji)
    insert_listing_observation(db_path, xianyu)

    outcome = rebuild_current_comparison(db_path, wameiji.canonical_product_key or "", COST_CONFIG)

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
