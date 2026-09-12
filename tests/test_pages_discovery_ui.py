from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image


def test_strict_dual_market_feed_keeps_the_nonprice_selectable_pool() -> None:
    script = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    pool_start = script.index("function appendSelectableCandidatePool")
    pool_end = script.index("function opportunityCard", pool_start)
    pool_renderer = script[pool_start:pool_end]

    assert "function appendSelectableCandidatePool" in script
    assert "appendSelectableCandidatePool(target)" in script
    assert "暂缺价格不降级" in pool_renderer
    assert "candidate.source_price" not in pool_renderer
    assert "candidate.expected_profit" not in pool_renderer


def test_selectable_candidate_pool_uses_compact_cards_not_opportunity_columns() -> None:
    script = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    stylesheet = Path("web/styles/kuro.css").read_text(encoding="utf-8")
    card_start = script.index("function selectableCandidateCard")
    card_end = script.index("function selectableCandidates", card_start)
    card_renderer = script[card_start:card_end]

    assert 'class="research-candidate-card"' in card_renderer
    assert 'class="op-card research-candidate-card"' not in card_renderer
    assert "research-candidate-grid" in script
    assert "暂缺价格不降级" not in card_renderer
    assert "body.kuro .research-candidate-grid {" in stylesheet
    assert "grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));" in stylesheet
    assert "body.kuro .research-candidate-card {" in stylesheet
    assert "display: flex;" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr) minmax(260px, 0.8fr) auto;" not in stylesheet


def test_home_feed_promotes_verified_positive_candidates_when_live_comparisons_are_empty() -> None:
    script = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    renderer_start = script.index("function renderDualMarketFeed")
    renderer_end = script.index("function itemSearchText", renderer_start)
    renderer = script[renderer_start:renderer_end]

    assert "let verifiedPositiveCandidates" in renderer
    assert "verifiedPositiveCandidates.map(opportunityCard).join(\"\")" in renderer
    assert "appendSelectableCandidatePool(target)" in renderer


def test_pages_build_includes_the_automatic_selection_board(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    destination = tmp_path / "site"

    subprocess.run(
        [
            sys.executable,
            "scripts/build_pages.py",
            "--api-base",
            "https://collector.example",
            "--dest",
            str(destination),
        ],
        cwd=root,
        check=True,
    )

    index = (destination / "index.html").read_text(encoding="utf-8")
    script = (destination / "discovery-ui.js").read_text(encoding="utf-8")
    assert "discovery-ui.js" in index
    assert "AI 闲鱼猎手" in index
    assert "首页机会流" in index
    assert 'id="sideScanDiscoveryBtn"' in index
    assert "浏览器扩展" not in index
    assert "/api/discovery/board" in script
    assert "/api/discovery/commands" in script
    assert "闲鱼 · 销售侧" in script
    assert "挖煤姬 · 进货侧" in script
    assert 'class="op-card discovery-op-card"' in script
    assert 'sideMarkup("xianyu"' in script
    assert 'sideMarkup("market"' in script
    assert "comparison-rail" in script
    assert "capture_state" in script
    assert "selectable_candidates" in script
    assert "可选品池 · 等待利润核验" in script
    assert "selectableCandidateCard" in script
    assert "profitReadinessLabel" in script
    assert "暂缺价格不降级" in script
    assert "candidate.source_price" not in script
    assert "candidate.source_currency" not in script
    assert "candidate.expected_profit" not in script
    assert "已自动暂停" in script
    assert "fresh_source_details" in script
    assert "resale_ready_candidates" in script
    assert "xianyu_login_state" in script
    assert "闲鱼需要扫码登录" in script
    assert "等待闲鱼价格样本" in script
    assert "每轮详情页上限" in script
    assert "每轮闲鱼商品上限" in script
    assert 'url.startsWith("//") ? "https:" + url : url' in script
    assert "sigmerchantimg" in script


def test_pages_build_copies_verified_snapshot_and_images(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    destination = tmp_path / "site"

    subprocess.run(
        [sys.executable, "scripts/build_pages.py", "--dest", str(destination)],
        cwd=root,
        check=True,
    )

    payload = json.loads(
        (destination / "data" / "dual-market-snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["schema_version"] == 2
    assert payload["strategy"] == {
        "policy_version": "wameiji-xianyu-net-v1",
        "trade_direction": "wameiji_jpy_to_xianyu_cny",
        "minimum_net_margin": 0.25,
        "margin_denominator": "xianyu_sale_price_cny",
    }
    assert payload["summary"] == {
        "evaluated_count": 50,
        "eligible_count": 4,
        "below_margin_count": 38,
        "cost_pending_count": 8,
        "waiting_wameiji_count": 0,
        "waiting_xianyu_count": 0,
    }
    assert payload["below_margin"] == []
    assert payload["cost_pending"] == []
    assert payload["waiting_wameiji"] == []
    assert payload["waiting_xianyu"] == []
    cards = payload["eligible"]
    assert len(cards) == 4
    for card in cards:
        assert card["calculation"]["status"] == "eligible"
        assert card["calculation"]["expected_profit_cny"] > 0
        assert card["calculation"]["net_margin"] >= 0.25
        assert card["xianyu"]["currency"] == "CNY"
        assert card["wameiji"]["currency"] == "JPY"
        assert card["xianyu"]["condition_group"] == card["wameiji"]["condition_group"]
        assert card["xianyu"]["image_url"] != card["wameiji"]["image_url"]

        costs = card["calculation"]["cost_breakdown"]
        assert costs["missing_fields"] == []
        assert costs["policy_version"] == "wameiji-xianyu-net-v1"
        assert costs["international_shipping_cny"] == 15
        assert costs["china_postage_cny"] == 5
        assert costs["packaging_cny"] == 2
        expected_purchase = (
            costs["wameiji_item_cny"]
            + costs["wameiji_domestic_shipping_cny"]
            + costs["wameiji_proxy_fee_cny"]
        )
        assert math.isclose(costs["wameiji_purchase_cny"], expected_purchase)
        expected_landed = (
            expected_purchase
            + costs["international_shipping_cny"]
            + costs["china_postage_cny"]
            + costs["packaging_cny"]
            + costs["after_sale_reserve_cny"]
            + costs["risk_reserve_cny"]
            + costs["tax_cny"]
        )
        assert math.isclose(costs["landed_cost_cny"], expected_landed)
        expected_profit = (
            costs["xianyu_sale_cny"]
            - costs["xianyu_seller_fee_cny"]
            - costs["landed_cost_cny"]
        )
        assert math.isclose(costs["net_profit_cny"], expected_profit)
        assert math.isclose(
            costs["net_margin"], expected_profit / costs["xianyu_sale_cny"]
        )

        for source in ("xianyu", "wameiji"):
            relative_image = card[source]["image_url"]
            assert relative_image.startswith("assets/dual-market/")
            image_path = destination / relative_image
            assert image_path.is_file()
            with Image.open(image_path) as image:
                image.verify()

    manifest = json.loads(
        (destination / "data" / "dual-market-snapshot.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["published_comparisons"] == 4
    assert manifest["dropped_comparison_ids"] == []
    assert len(manifest["assets"]) == 8
    for asset in manifest["assets"]:
        asset_path = destination / asset["path"]
        assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == asset["sha256"]


def test_public_snapshot_contains_no_private_runtime_material() -> None:
    snapshot = Path("web/data/dual-market-snapshot.json").read_text(encoding="utf-8")
    lowered = snapshot.lower()

    for forbidden in (
        "access_token",
        "authorization",
        "cookie",
        "raw_snapshot_path",
        "screenshot_path",
        "file://",
        "f:\\",
        "c:\\users\\",
        "<html",
    ):
        assert forbidden not in lowered
