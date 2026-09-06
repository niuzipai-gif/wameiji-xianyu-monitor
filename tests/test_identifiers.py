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
    assert normalize_catalog_no_compact("SRCL 3520") == "SRCL3520"


def test_extract_identifiers_from_text() -> None:
    text = "初回限定 SRCL 3520 JAN: 4988009123456 / barcode 498800123456"
    assert "SRCL-3520" in extract_catalog_candidates(text)
    assert extract_jan_candidates(text) == ["4988009123456", "498800123456"]
    assert normalize_jan("JAN 4988009123456") == "4988009123456"
