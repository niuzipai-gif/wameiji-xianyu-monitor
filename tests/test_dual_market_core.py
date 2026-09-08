from __future__ import annotations

from dataclasses import replace

import pytest

from cd_monitor.core.dual_market import ListingObservation, select_lowest_eligible


def make_wameiji_observation(**changes: object) -> ListingObservation:
    observation = ListingObservation(
        source="wameiji",
        source_listing_id="complete",
        canonical_product_key="jan:4547366123456",
        title="Album 初回限定盤 CD+DVD",
        price=1280,
        currency="JPY",
        url="https://meruki.cn/mall/mercari/detail/complete",
        image_url="https://images.example/complete.jpg",
        availability="available",
        condition_group="complete_used",
        completeness="complete",
        evidence_level="detail_verified",
        captured_at="2026-09-08T00:00:00Z",
    )
    return replace(observation, **changes)


def test_select_lowest_eligible_ignores_an_incomplete_cheaper_listing() -> None:
    incomplete = make_wameiji_observation(
        source_listing_id="box-only",
        title="Album 外箱のみ",
        price=100,
        completeness="incomplete",
    )
    complete = make_wameiji_observation()

    selected = select_lowest_eligible([incomplete, complete], source="wameiji")

    assert selected is not None
    assert selected.source_listing_id == "complete"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"availability": "sold_out"}, "已售"),
        ({"image_url": ""}, "无图"),
        ({"evidence_level": "search_card"}, "搜索卡未核验"),
    ],
)
def test_wameiji_ineligible_cheaper_records_never_win_lowest_price(
    changes: dict[str, object], reason: str
) -> None:
    invalid_cheaper = make_wameiji_observation(
        source_listing_id="invalid-cheaper",
        price=100,
        **changes,
    )
    complete = make_wameiji_observation()

    selected = select_lowest_eligible([invalid_cheaper, complete], source="wameiji")

    assert selected is not None, reason
    assert selected.source_listing_id == "complete", reason


def test_xianyu_search_card_can_be_the_current_lowest_asking_price() -> None:
    search_card = make_wameiji_observation(
        source="xianyu",
        source_listing_id="x-1",
        price=298,
        currency="CNY",
        url="https://www.goofish.com/item?id=x-1",
        evidence_level="search_card",
    )

    selected = select_lowest_eligible([search_card], source="xianyu")

    assert selected is search_card
