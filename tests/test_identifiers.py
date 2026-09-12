from cd_monitor.core.identifiers import (
    extract_catalog_candidates,
    extract_jan_candidates,
    normalize_catalog_no,
    normalize_catalog_no_compact,
    normalize_jan,
)


def test_catalog_normalization_accepts_hyphen_compact_and_space() -> None:
    assert normalize_catalog_no("SRCL-3520") == "SRCL-3520"
    assert normalize_catalog_no("srcl3520") == "SRCL-3520"
    assert normalize_catalog_no("SRCL 3520") == "SRCL-3520"


def test_catalog_normalization_accepts_typographic_hyphens() -> None:
    assert extract_catalog_candidates("品番 UPCJ‑1002") == ["UPCJ-1002"]
    assert extract_catalog_candidates("品番 AVCD–84975") == ["AVCD-84975"]


def test_idol_group_name_is_not_mistaken_for_a_catalog_number() -> None:
    assert extract_catalog_candidates(
        "SKE48 恋落ちフラグ 品番 AVCD-84975"
    ) == ["AVCD-84975"]
    assert normalize_catalog_no_compact("SRCL 3520") == "SRCL3520"


def test_extract_identifiers_from_text() -> None:
    text = "初回限定 SRCL 3520 JAN: 4988009123456 / barcode 498800123456"
    assert "SRCL-3520" in extract_catalog_candidates(text)
    assert extract_jan_candidates(text) == ["4988009123456", "498800123456"]
    assert normalize_jan("JAN 4988009123456") == "4988009123456"


def test_extract_catalog_number_when_joined_to_chinese_label() -> None:
    assert extract_catalog_candidates("初回限定盘，品番SRCL-8692~3") == ["SRCL-8692"]


def test_media_label_followed_by_release_year_is_not_a_catalog_number() -> None:
    assert extract_catalog_candidates("NEW DEAL PRESENTS STREET TEAM CD 2003年") == []
    assert extract_catalog_candidates("BoA Outgrow DVD 2006年") == []
