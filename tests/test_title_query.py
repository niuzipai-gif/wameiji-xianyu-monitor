from __future__ import annotations

from cd_monitor.core.title_query import (
    clean_title_search_query,
    matches_title_search_query,
)


def test_clean_title_query_removes_store_code_and_bonus_but_keeps_product_name() -> None:
    assert clean_title_search_query(
        "TN-8-4-1）新品 未開封 浜田省吾「DUET」初回限定盤 メガジャケ付き CD"
    ) == "浜田省吾 DUET"


def test_clean_title_query_removes_standalone_unopened_condition() -> None:
    assert clean_title_search_query("新品 未開封 浜田省吾 DUET") == "浜田省吾 DUET"


def test_clean_title_query_normalizes_sw_edition_to_switch() -> None:
    assert clean_title_search_query("SW版 ラジルギ2 限定版 Switch") == "Switch ラジルギ2"


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
