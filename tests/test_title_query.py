from __future__ import annotations

from cd_monitor.core.title_query import (
    build_alias_search_query,
    clean_title_search_query,
    matches_title_search_query,
    source_edition_required_terms,
    title_alias_lookup_query,
)


def test_clean_title_query_removes_store_code_and_bonus_but_keeps_product_name() -> None:
    assert clean_title_search_query(
        "TN-8-4-1）新品 未開封 浜田省吾「DUET」初回限定盤 メガジャケ付き CD"
    ) == "浜田省吾 DUET"


def test_clean_title_query_removes_standalone_unopened_condition() -> None:
    assert clean_title_search_query("新品 未開封 浜田省吾 DUET") == "浜田省吾 DUET"


def test_clean_title_query_normalizes_sw_edition_to_switch() -> None:
    assert clean_title_search_query("SW版 ラジルギ2 限定版 Switch") == "Switch ラジルギ2"


def test_title_alias_lookup_removes_condition_platform_and_edition_metadata() -> None:
    source_title = "【一部未使用】あくありうむ。 完全生産限定版 Switch ソフト"

    assert title_alias_lookup_query(source_title) == "あくありうむ"


def test_title_alias_lookup_removes_the_japanese_platform_suffix() -> None:
    source_title = "PS Vita用ソフト 「ソードアート・オンライン -ホロウ・フラグメント-」【限定版】"

    assert title_alias_lookup_query(source_title) == "ソードアート・オンライン ホロウ・フラグメント"


def test_clean_title_query_folds_latin_diacritics_without_changing_kana() -> None:
    assert clean_title_search_query("薄桜鬼 Hakuōki Switch 限定版") == "薄桜鬼 Hakuoki Switch"
    assert clean_title_search_query("ジャンヌダルク Switch 限定版") == "ジャンヌダルク Switch"


def test_alias_query_retains_platform_and_requires_the_same_complete_edition() -> None:
    source_title = "【一部未使用】あくありうむ。 完全生産限定版 Switch ソフト"
    variant = build_alias_search_query("AQUARIUM。", source_title)

    assert variant is not None
    assert variant.query == "AQUARIUM Switch 完全生产限定版"
    assert matches_title_search_query(
        "凑阿库娅 AQUARIUM 完全生产限定版 Switch 游戏卡带",
        variant.query,
        required_any_terms=variant.required_any_terms,
    ) is True
    assert matches_title_search_query(
        "凑阿库娅 AQUARIUM Switch 普通版 游戏卡带",
        variant.query,
        required_any_terms=variant.required_any_terms,
    ) is False


def test_source_edition_requirement_rejects_a_normal_edition_reference() -> None:
    terms = source_edition_required_terms("Switch Macross Shooting Insight 限定版")

    assert terms == ("限定", "限量", "limited")
    assert matches_title_search_query(
        "Switch Macross Shooting Insight 普通版 游戏卡带",
        "Switch Macross Shooting Insight",
        required_any_terms=terms,
    ) is False
    assert matches_title_search_query(
        "Switch Macross Shooting Insight 日版限定版 游戏卡带",
        "Switch Macross Shooting Insight",
        required_any_terms=terms,
    ) is True


def test_clean_title_query_uses_short_latin_and_han_product_anchor() -> None:
    assert clean_title_search_query(
        "新品同様 極上美品 X JAPAN 『ロンギング/Longing 〜切望の夜〜』CD "
        "マキシシングル 帯有り 国内正規品 廃盤 YOSHIKI hide"
    ) == "X JAPAN Longing 切望"


def test_title_query_requires_han_anchor_when_two_latin_terms_are_shared() -> None:
    query = "X JAPAN Longing 切望"

    assert matches_title_search_query(
        "X Japan Ballad Collection 日版CD 收录 Longing", query
    ) is False
    assert matches_title_search_query(
        "X JAPAN Longing～迹切れた melody～ 日版 CD", query
    ) is False
    assert matches_title_search_query(
        "X JAPAN Longing～切望的夜晚～ 日版 CD", query
    ) is True


def test_title_query_requires_a_contiguous_multiword_english_title() -> None:
    query = "ゲームCD ラストストーリー プレミアムサウンドトラック THE LAST STORY"

    assert matches_title_search_query(
        "THE LAST STORY THE PREMIUM SOUNDTRACK 日版 CD", query
    ) is True
    assert matches_title_search_query(
        "Taylor Swift CD 收录 The Story of Us 与 Last Kiss", query
    ) is False


def test_title_query_requires_a_distinctive_kana_variant_anchor_for_short_english_series() -> None:
    query = (
        "ゲームCD MONSTER HUNTER XX "
        "クリエイターズセレクション サウンドトラック"
    )

    assert matches_title_search_query(
        "怪物猎人 音乐 CD 非OST 日版 MONSTER HUNTER 2G 3G XX",
        query,
    ) is False
    assert matches_title_search_query(
        "MONSTER HUNTER XX クリエイターズセレクション サウンドトラック CD",
        query,
    ) is True


def test_title_query_rejects_a_different_game_platform_despite_series_name() -> None:
    query = "Riviera 約束の地リヴィエラ Switch"

    assert not matches_title_search_query(
        "GBA·约束之地：利维艾拉 Riviera 游戏卡带", query
    )


def test_title_query_accepts_shared_latin_title_when_platform_also_matches() -> None:
    query = "Riviera 約束の地リヴィエラ Switch"

    assert matches_title_search_query(
        "Nintendo Switch Riviera 约束之地 里维埃拉 特别版", query
    )


def test_title_query_rejects_a_shared_collector_edition_term() -> None:
    assert not matches_title_search_query(
        "Switch LA-MULANA 1&2 Collector's Box",
        "Switch Radiant Silvergun Collector's Box",
    )


def test_title_query_rejects_different_playstation_generation() -> None:
    assert not matches_title_search_query("PS4 Riviera 重制版", "PS5 Riviera")


def test_title_query_accepts_ns_as_a_switch_alias() -> None:
    assert matches_title_search_query("NS Riviera 约束之地 重制版", "Switch Riviera")


def test_title_query_accepts_japanese_game_when_platform_word_order_differs() -> None:
    assert matches_title_search_query("ラジルギ2 NS 限定版", "Switch ラジルギ2")


def test_title_query_rejects_3ds_when_source_is_ds() -> None:
    assert not matches_title_search_query("3DS Riviera 重制版", "DS Riviera")


def test_title_query_normalizes_vita_alias_and_rejects_psp() -> None:
    assert matches_title_search_query("Vita Riviera 重制版", "PS Vita Riviera")
    assert not matches_title_search_query("PSP Riviera 重制版", "Vita Riviera")


def test_title_query_rejects_a_multi_platform_result_for_single_platform_source() -> None:
    assert not matches_title_search_query("Switch PSP Riviera 合集", "Switch Riviera")
