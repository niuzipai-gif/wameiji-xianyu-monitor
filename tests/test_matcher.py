from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import MarketItem, WatchItem


def test_exact_catalog_match_scores_alertable_confidence() -> None:
    item = MarketItem(source="wameiji", title="Artist Album SRCL-3520 初回限定", price=1000)
    watch = WatchItem(catalog_no="SRCL-3520", artist="Artist", edition="初回限定")
    result = compute_match_confidence(item, watch)
    assert result.confidence >= 0.85
    assert result.fatal_flags == []
    assert "catalog_no_exact" in result.matched_identifiers


def test_fatal_keywords_for_bonus_empty_case_and_no_disc() -> None:
    item = MarketItem(source="wameiji", title="SRCL-3520 特典のみ 空盒 無盤", price=1000)
    result = compute_match_confidence(item, WatchItem(catalog_no="SRCL-3520"))
    assert {"only_bonus", "empty_case", "no_disc"}.issubset(set(result.fatal_flags))
    assert result.confidence < 0.75


def test_missing_core_game_content_is_fatal_even_when_the_collector_box_exists() -> None:
    item = MarketItem(
        source="wameiji",
        title="Nintendo Switch レイディアント シルバーガン COLLECTOR'S BOX",
        price=1799,
        raw_text="Nintendo Switch用ソフトです。※ソフト+サントラ欠品です。",
    )

    result = compute_match_confidence(item, WatchItem(catalog_no="RADIANT-SILVERGUN"))

    assert "core_media_missing" in result.fatal_flags


def test_core_media_missing_guard_keeps_an_explicit_no_missing_parts_claim() -> None:
    item = MarketItem(
        source="wameiji",
        title="Nintendo Switch レイディアント シルバーガン COLLECTOR'S BOX",
        price=1799,
        raw_text="ソフト欠品なし、付属品はすべて揃っています。",
    )

    result = compute_match_confidence(item, WatchItem(catalog_no="RADIANT-SILVERGUN"))

    assert "core_media_missing" not in result.fatal_flags


def test_rental_and_sample_reduce_confidence() -> None:
    item = MarketItem(source="wameiji", title="SRCL-3520 レンタル落ち sample", price=1000)
    result = compute_match_confidence(item, WatchItem(catalog_no="SRCL-3520"))
    assert "rental" in result.negative_reasons
    assert "sample" in result.negative_reasons
    assert result.confidence < 0.85


def test_rental_denial_is_not_treated_as_rental_history() -> None:
    item = MarketItem(
        source="wameiji",
        title="X JAPAN Longing",
        price=400,
        raw_text="新品購入。レンタルアップ品ではありません。",
    )

    result = compute_match_confidence(item, WatchItem(catalog_no="X JAPAN Longing"))

    assert "rental" not in result.negative_reasons


def test_chinese_overall_poor_condition_is_a_risk_signal() -> None:
    item = MarketItem(
        source="wameiji",
        title="SRCL-3520",
        price=1000,
        condition_text="整体状态不佳",
    )

    result = compute_match_confidence(item, WatchItem(catalog_no="SRCL-3520"))

    assert "poor_condition" in result.negative_reasons


def test_missing_required_keyword_reduces_confidence() -> None:
    item = MarketItem(source="wameiji", title="Artist SRCL-3520 初回限定", price=1000)
    result = compute_match_confidence(
        item,
        WatchItem(catalog_no="SRCL-3520", required_keywords=["帯付き"]),
    )

    assert "missing_required_keyword:帯付き" in result.negative_reasons
    assert result.confidence < 0.75
