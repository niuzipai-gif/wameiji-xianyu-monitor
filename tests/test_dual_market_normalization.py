from __future__ import annotations

import pytest

from cd_monitor.core.dual_market import (
    classify_completeness,
    normalize_condition_group,
    observation_from_wameiji,
    observation_from_xianyu,
)
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

    assert observation.canonical_product_key == "catalog:srcl3520|edition:initial_limited"
    assert observation.evidence_level == "detail_verified"
    assert observation.source_listing_id == "m-1"
    assert observation.url == "https://meruki.cn/mall/mercari/detail/m-1"
    assert observation.image_url == "https://images.example/wameiji/a.jpg"


def test_wameiji_labeled_condition_beats_unrelated_recommendation_text() -> None:
    item = MarketItem(
        source="wameiji",
        title="【中古】ロックの逆襲 初回限定盤A CD+DVD",
        price=274,
        currency="JPY",
        external_item_id="m-used",
        catalog_no="UPCH-9162",
        url="/mall/rakuten/detail/m-used",
        image_url="//images.example/wameiji/m-used.jpg",
        availability="available",
        condition_text="中古品-非常に良い 中古品-良い 中古品-可",
        raw_text="商品紹介 新品 CD unrelated recommendation",
        detail_verified=True,
    )

    observation = observation_from_wameiji(
        item, captured_at="2026-09-09T04:15:00+08:00"
    )

    assert observation.condition_group == "complete_used"
    assert "condition:new" not in str(observation.canonical_product_key)


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
    assert observation.canonical_product_key == "catalog:srcl3520|edition:initial_limited"
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


def test_same_catalog_with_different_edition_platform_or_condition_stays_unpaired() -> None:
    wameiji = observation_from_wameiji(
        MarketItem(
            source="wameiji",
            title="Game SRCL-3520 初回限定版 Nintendo Switch",
            price=1280,
            currency="JPY",
            external_item_id="m-edition",
            catalog_no="SRCL-3520",
            url="/mall/mercari/detail/m-edition",
            image_url="//images.example/wameiji/edition.jpg",
            availability="available",
            condition_text="中古",
            detail_verified=True,
        ),
        captured_at="2026-09-08T00:00:00Z",
    )
    xianyu = observation_from_xianyu(
        XianyuPriceSample(
            catalog_no="SRCL-3520",
            title="Game SRCL-3520 通常版 PlayStation 4",
            price_cny=298,
            url="/item?id=x-edition",
            image_url="//images.example/xianyu/edition.jpg",
            raw_text="新品",
        ),
        captured_at="2026-09-08T00:00:00Z",
    )

    assert wameiji.canonical_product_key != xianyu.canonical_product_key


@pytest.mark.parametrize(
    ("condition_text", "expected_group"),
    [
        ("盤に薄い傷あり", "minor_damage"),
        ("盘面有轻微划痕", "minor_damage"),
        ("傷が多く大きな傷あり", "heavy_damage"),
        ("盘面严重划痕", "heavy_damage"),
        ("外箱に潰れあり", "box_damage"),
        ("外盒有压痕", "box_damage"),
        ("ジャンク・動作不良", "defective"),
        ("故障品，无法播放", "defective"),
        ("動作未確認", "untested"),
        ("功能未测试", "untested"),
        ("年代久远，不保证正常播放，纯收藏", "untested"),
        ("未试听，不保读取", "untested"),
    ],
)
def test_normalize_condition_group_keeps_japanese_and_chinese_risk_conditions_distinct(
    condition_text: str,
    expected_group: str,
) -> None:
    assert normalize_condition_group(condition_text) == expected_group


def test_normalize_condition_group_does_not_treat_japanese_no_damage_as_minor_damage() -> None:
    assert normalize_condition_group("【商品の状態】目立った傷や汚れなし") == "complete_used"


@pytest.mark.parametrize(
    "disclaimer",
    [
        "★映像不良等でない限り、発送後の返品は受け付けません。",
        "動作不良等でない限り返品不可です。",
    ],
)
def test_normalize_condition_group_ignores_negated_return_policy_disclaimer(
    disclaimer: str,
) -> None:
    assert normalize_condition_group(disclaimer) == "complete_used"


def test_box_damage_does_not_make_an_otherwise_complete_listing_incomplete() -> None:
    assert classify_completeness("Album SRCL-3520", "外箱に潰れあり") == "complete"
    assert classify_completeness("Album SRCL-3520 外箱のみ", None) == "incomplete"


@pytest.mark.parametrize(
    "title",
    [
        "北斗の拳 オリジナルソングス ORIGINAL SONGS CDの帯のみ",
        "宇多田ヒカル アルバム 帯だけ",
        "ゲームソフト ジャケットのみ",
        "限定盤 CD ブックレットのみ",
    ],
)
def test_accessory_only_japanese_listing_is_incomplete(title: str) -> None:
    assert classify_completeness(title, None) == "incomplete"


def test_collection_card_named_after_a_cd_is_not_the_cd_itself() -> None:
    title = (
        "【中古】コレクションカード(男性)/CD「The STAR」"
        "【初回限定盤Red】封入特典 JO1"
    )

    assert classify_completeness(title, None) == "incomplete"


def test_trading_card_only_title_is_not_the_album() -> None:
    assert (
        classify_completeness(
            "Kep1er Kep1going 通常盤 トレカ ヒカル",
            "フォトカード1枚のみ",
        )
        == "incomplete"
    )


@pytest.mark.parametrize(
    "title",
    [
        "YUI 日版初回限定 注意没有CD碟 只卖歌词本盒子和DVD",
        "专辑空盒 不含CD，仅外壳和侧标",
        "日版游戏限定版 无游戏卡带，只出特典",
    ],
)
def test_missing_core_disc_or_game_card_is_incomplete(title: str) -> None:
    assert classify_completeness(title, None) == "incomplete"


@pytest.mark.parametrize(
    ("raw_risk_text", "expected_group"),
    [
        ("動作未確認", "untested"),
        ("箱潰れあり", "box_damage"),
        ("スレあり", "minor_damage"),
        ("使用感あり", "minor_damage"),
    ],
)
def test_wameiji_observation_keeps_raw_risk_text_when_condition_text_is_benign(
    raw_risk_text: str,
    expected_group: str,
) -> None:
    observation = observation_from_wameiji(
        MarketItem(
            source="wameiji",
            title="Album SRCL-3520 通常盤",
            price=1280,
            currency="JPY",
            external_item_id="m-risk",
            catalog_no="SRCL-3520",
            url="/mall/mercari/detail/m-risk",
            image_url="//images.example/m-risk.jpg",
            availability="available",
            condition_text="中古・良品",
            raw_text=f"Album SRCL-3520 通常盤 中古・良品 {raw_risk_text}",
            detail_verified=True,
        ),
        captured_at="2026-09-08T00:00:00Z",
    )

    assert observation.condition_group == expected_group
