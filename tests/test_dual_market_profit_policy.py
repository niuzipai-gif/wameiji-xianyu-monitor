from __future__ import annotations

import pytest

from cd_monitor.services.dual_market_profit_policy import (
    STRICT_PROFIT_POLICY_VERSION,
    WameijiCostEvidence,
    parse_wameiji_cost_evidence,
    strict_profit_cost_config,
)


def test_saved_detail_extracts_displayed_rate_proxy_and_seller_paid_shipping() -> None:
    evidence = parse_wameiji_cost_evidence(
        """
        <header><p class="exchange">挖煤姬汇率：1 日元 ≈ 0.0455 人民币</p></header>
        <main class="goods-detail">
          <div class="sku-item">日本国内运费 <span>卖家承担</span></div>
          <div class="sku-item">代购手续费 <span>200日元</span></div>
        </main>
        """
    )

    assert evidence.exchange_rate_cny_per_jpy == pytest.approx(0.0455)
    assert evidence.proxy_fee_jpy == pytest.approx(200)
    assert evidence.japan_domestic_shipping_jpy == 0


def test_buyer_paid_shipping_without_amount_remains_unknown() -> None:
    evidence = parse_wameiji_cost_evidence(
        """
        <p>挖煤姬汇率：1 日元 ≈ 0.0455 人民币</p>
        <div>日本国内运费 买家承担</div>
        <div>代购手续费 200日元</div>
        """
    )

    assert evidence.japan_domestic_shipping_jpy is None


def test_numeric_domestic_shipping_is_extracted_from_its_own_row() -> None:
    evidence = parse_wameiji_cost_evidence(
        """
        <p>挖煤姬汇率：1 日元 ≈ 0.0455 人民币</p>
        <div>日本国内运费 230日元</div>
        <div>代购手续费 200日元</div>
        """
    )

    assert evidence.japan_domestic_shipping_jpy == pytest.approx(230)
    assert evidence.proxy_fee_jpy == pytest.approx(200)


def test_missing_labels_never_fall_back_to_current_observed_values() -> None:
    evidence = parse_wameiji_cost_evidence("<main>商品价格 1,500日元</main>")

    assert evidence == WameijiCostEvidence(
        exchange_rate_cny_per_jpy=None,
        proxy_fee_jpy=None,
        japan_domestic_shipping_jpy=None,
    )


def test_strict_policy_uses_confirmed_costs_and_uncapped_seller_fee() -> None:
    config = strict_profit_cost_config(
        WameijiCostEvidence(
            exchange_rate_cny_per_jpy=0.0455,
            proxy_fee_jpy=200,
            japan_domestic_shipping_jpy=230,
        )
    )

    assert config.exchange_rate_cny_per_jpy == pytest.approx(0.0455)
    assert config.japan_domestic_shipping_jpy == pytest.approx(230)
    assert config.proxy_fee_jpy == pytest.approx(200)
    assert config.international_shipping_per_item_cny == 15
    assert config.china_reship_cny == 5
    assert config.packaging_cny == 2
    assert config.after_sale_reserve_cny == 0
    assert config.risk_reserve_cny == 0
    assert config.tax_cny == 0
    assert config.sales_fee_rate == pytest.approx(0.016)
    assert config.sales_fee_cap_cny is None
    assert config.sales_fee_uncapped is True
    assert config.minimum_net_margin == pytest.approx(0.25)
    assert config.policy_version == STRICT_PROFIT_POLICY_VERSION
