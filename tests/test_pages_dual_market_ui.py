from __future__ import annotations

from pathlib import Path


def extract_dual_market_renderer(javascript: str) -> str:
    start = javascript.index("function readyComparisonCard")
    end = javascript.index("function setSelectionBoardQuery")
    return javascript[start:end]


def test_dual_market_ui_renders_source_specific_images_and_waiting_state() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    renderer = extract_dual_market_renderer(javascript)

    assert "item.xianyu.image_url" in renderer
    assert "item.wameiji.image_url" in renderer
    assert "waiting_wameiji" in renderer
    assert "waiting_xianyu" in renderer
    assert "/api/dual-market/board" in javascript
    assert "xianyu_reference_price" not in renderer


def test_homepage_names_cd_and_galgame_physical_media() -> None:
    homepage = Path("web/index.html").read_text(encoding="utf-8")

    assert "CD 与 GalGame 实体" in homepage
