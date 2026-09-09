from __future__ import annotations

import subprocess
from pathlib import Path


def extract_dual_market_renderer(javascript: str) -> str:
    start = javascript.index("function readyComparisonCard")
    end = javascript.index("function setSelectionBoardQuery")
    return javascript[start:end]


def test_dual_market_ui_renders_source_specific_images_and_waiting_state() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    loader = Path("web/dual-market-data.js").read_text(encoding="utf-8")
    renderer = extract_dual_market_renderer(javascript)

    assert "item.xianyu.image_url" in renderer
    assert "item.wameiji.image_url" in renderer
    assert "waiting_wameiji" in renderer
    assert "waiting_xianyu" in renderer
    assert "/api/dual-market/board" in loader
    assert "xianyu_reference_price" not in renderer


def test_static_snapshot_product_images_resolve_from_the_pages_base_url() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    start = javascript.index("function safeHttpUrl")
    end = javascript.index("function liquidityChip")
    image_helpers = javascript[start:end]
    harness = f"""
const document = {{ baseURI: "https://example.github.io/project/" }};
{image_helpers}
const result = usableProductImage(
  "assets/dual-market/snapshot/4-xianyu.webp"
);
if (result !== "https://example.github.io/project/assets/dual-market/snapshot/4-xianyu.webp") {{
  throw new Error("unexpected image URL: " + result);
}}
if (usableProductImage("../private.png") !== "") {{
  throw new Error("relative paths outside the published asset tree must be rejected");
}}
"""

    result = subprocess.run(
        ["node", "-e", harness],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_homepage_names_cd_and_galgame_physical_media() -> None:
    homepage = Path("web/index.html").read_text(encoding="utf-8")

    assert "CD 与 GalGame 实体" in homepage
    assert "已核验双边" in homepage


def test_homepage_distinguishes_xianyu_search_evidence_from_wameiji_detail() -> None:
    homepage = Path("web/index.html").read_text(encoding="utf-8")

    assert "闲鱼以淡黄色显示搜索挂牌价样本" in homepage
    assert "挖煤姬以淡粉白显示详情已核验进货价" in homepage
    assert "只有详情已核验的来源才会进入机会流" not in homepage


def test_dual_market_ui_fails_closed_when_its_board_is_unavailable() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    loader = Path("web/dual-market-data.js").read_text(encoding="utf-8")

    assert 'apiGet("/api/dual-market/board").catch(() => null)' not in javascript
    assert 'mode: "unavailable"' in loader
    assert "unavailable: true" in loader
    assert "双边证据流暂不可用" in javascript
    assert "不展示旧机会卡" in javascript


def test_dual_market_ui_does_not_submit_scan_or_resume_while_collection_is_paused() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")

    assert "采集已暂停，未提交扫描命令" in javascript
    assert "采集已暂停，未提交恢复命令" in javascript


def test_dual_market_ui_does_not_coerce_missing_landed_cost_to_zero() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    start = javascript.index("function cny")
    end = javascript.index("function jpy")
    currency_formatter = javascript[start:end]

    assert 'value === null || value === undefined || value === ""' in currency_formatter


def test_market_prices_use_explicit_cny_and_jpy_units_with_conversion() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    start = javascript.index("function cny")
    end = javascript.index("function percent")
    currency_formatter = javascript[start:end]
    harness = f"""
{currency_formatter}
if (cny(30) !== "30 CNY") throw new Error("unexpected CNY: " + cny(30));
if (jpy(110, 0.046) !== "110 JPY（约 5.06 CNY）") {{
  throw new Error("unexpected JPY conversion: " + jpy(110, 0.046));
}}
if (cny(null) !== "--" || jpy(null, 0.046) !== "--") {{
  throw new Error("missing prices must remain unknown");
}}
"""

    result = subprocess.run(
        ["node", "-e", harness],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_dual_market_ui_states_the_china_resale_profit_direction() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    renderer = extract_dual_market_renderer(javascript)
    homepage = Path("web/index.html").read_text(encoding="utf-8")

    assert "挖煤姬进货（JPY） → 闲鱼国内销售（CNY）" in renderer
    assert "闲鱼预计净利（CNY）" in renderer
    assert "闲鱼销售利润率" in renderer
    assert "利润 = 闲鱼销售价（CNY）" in renderer
    assert "闲鱼预计净利" in homepage
    assert "闲鱼最高净利" in homepage


def test_dual_market_kpis_label_cost_pending_pairs_without_zero_profit() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    start = javascript.index("function renderKpis")
    end = javascript.index("function safeHttpUrl")
    renderer = javascript[start:end]

    assert "verifiedPairCount" in renderer
    assert "costPendingCount" in renderer
    assert '"待算"' in renderer


def test_homepage_loads_snapshot_loader_before_the_board_renderer() -> None:
    homepage = Path("web/index.html").read_text(encoding="utf-8")

    assert "dual-market-data.js" in homepage
    assert homepage.index("dual-market-data.js") < homepage.index("discovery-ui.js")


def test_homepage_busts_cached_renderer_after_currency_and_resale_fix() -> None:
    homepage = Path("web/index.html").read_text(encoding="utf-8")

    assert "discovery-ui.js?v=20260909-public-snapshot-no-prompt" in homepage


def test_board_refresh_decouples_legacy_api_failures_from_dual_market_data() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")

    assert 'apiGet("/api/discovery/board").catch(() => emptyBoard)' in javascript
    assert 'apiGet("/api/discovery/commands").catch(() => ({ items: [] }))' in javascript
    assert "window.DualMarketData.load({ apiGet })" in javascript


def test_static_snapshot_status_is_explicit_about_freshness() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")

    assert "Pages 已核验快照" in javascript
    assert "Pages 历史快照" in javascript
    assert "不代表当前可买" in javascript
    assert "采集仅在本机运行" in javascript


def test_home_feed_is_two_columns_with_compact_three_part_cards() -> None:
    css = Path("web/styles/kuro.css").read_text(encoding="utf-8")
    homepage = Path("web/index.html").read_text(encoding="utf-8")

    assert "body.kuro #homeFeed" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert (
        "grid-template-columns: minmax(0, 1fr) minmax(130px, .7fr) "
        "minmax(0, 1fr)" in css
    )
    assert "max-height: 245px" in css
    assert "@media (max-width: 1180px)" in css
    assert "@media (max-width: 760px)" in css
    assert "一行两条机会" in homepage
    assert "左侧闲鱼" in homepage
    assert "右侧挖煤姬" in homepage
