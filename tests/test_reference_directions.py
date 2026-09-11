from __future__ import annotations

from cd_monitor.core.reference_directions import (
    build_candidate_direction_text,
    extract_reference_directions,
)


def test_extract_reference_directions_supports_composite_product_text() -> None:
    assert extract_reference_directions("初回限定 CD 同人 音楽") == (
        "physical_music",
        "doujin_or_anime",
        "limited_or_first_edition",
    )


def test_extract_reference_directions_supports_game_and_book_text() -> None:
    assert extract_reference_directions("Nintendo Switch 設定集") == (
        "console_game",
        "art_or_book",
    )


def test_page_shell_cannot_create_a_direction() -> None:
    assert extract_reference_directions("闲鱼 交易 角色 搜索 邮费 自理") == ()


def test_candidate_direction_text_prefers_structured_fields_to_raw_page_text() -> None:
    assert build_candidate_direction_text(
        title="STEINS;GATE Switch 限定版",
        raw_text="CD 专辑 店铺规则 运费",
    ) == "STEINS;GATE Switch 限定版"


def test_candidate_direction_text_uses_raw_text_only_without_identity_fields() -> None:
    assert build_candidate_direction_text(raw_text="初回限定 CD") == "初回限定 CD"
