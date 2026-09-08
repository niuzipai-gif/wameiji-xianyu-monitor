from __future__ import annotations

from cd_monitor.core.dual_market import observation_from_wameiji, observation_from_xianyu
from cd_monitor.core.models import MarketItem, XianyuPriceSample


def test_wameiji_detail_normalizes_to_a_detail_verified_observation() -> None:
    item = MarketItem(
        source="wameiji",
        title="Album SRCL-3520 初回限定盤",
        price=1280,
        currency="JPY",
        external_item_id="m-1",
        catalog_no="SRCL-3520",
        url="/mall/mercari/detail/m-1",
        image_url="//images.example/wameiji/a.jpg",
        availability="available",
        condition_text="中古・付属品あり",
        detail_verified=True,
    )

    observation = observation_from_wameiji(item, captured_at="2026-09-08T00:00:00Z")

    assert observation.canonical_product_key == "catalog:srcl3520"
    assert observation.evidence_level == "detail_verified"
    assert observation.source_listing_id == "m-1"
    assert observation.url == "https://meruki.cn/mall/mercari/detail/m-1"
    assert observation.image_url == "https://images.example/wameiji/a.jpg"


def test_xianyu_search_card_normalizes_without_borrowing_wameiji_identity() -> None:
    sample = XianyuPriceSample(
        catalog_no="SRCL-3520",
        title="Artist Album SRCL-3520 初回限定盤",
        price_cny=298,
        url="/item?id=x-1",
        image_url="//images.example/xianyu/x-1.jpg",
    )

    observation = observation_from_xianyu(sample, captured_at="2026-09-08T00:00:00Z")

    assert observation.source == "xianyu"
    assert observation.source_listing_id == "x-1"
    assert observation.canonical_product_key == "catalog:srcl3520"
    assert observation.evidence_level == "search_card"
    assert observation.url == "https://www.goofish.com/item?id=x-1"
    assert observation.image_url == "https://images.example/xianyu/x-1.jpg"


def test_title_without_a_confirmed_catalog_or_jan_stays_unpaired() -> None:
    sample = XianyuPriceSample(
        catalog_no="Artist Album Limited Edition",
        title="Artist Album Limited Edition",
        price_cny=298,
        url="https://www.goofish.com/item?id=x-2",
        image_url="https://images.example/xianyu/x-2.jpg",
    )

    observation = observation_from_xianyu(sample, captured_at="2026-09-08T00:00:00Z")

    assert observation.canonical_product_key is None
