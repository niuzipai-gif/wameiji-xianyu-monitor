from __future__ import annotations

from cd_monitor.core.models import CostBreakdown, CostConfig, MarketItem


def compute_landed_cost(
    item: MarketItem,
    config: CostConfig | None = None,
    expected_holding_days: int = 30,
) -> CostBreakdown:
    config = config or CostConfig()
    japan_domestic_shipping_jpy = (
        item.japan_domestic_shipping_jpy
        if item.japan_domestic_shipping_jpy is not None
        else config.default_japan_domestic_shipping_jpy
    )
    proxy_fee_jpy = (
        item.proxy_fee_jpy
        if item.proxy_fee_jpy is not None
        else config.default_proxy_fee_jpy
    )
    if item.price_cny_display is not None:
        first_payment = item.price_cny_display
    else:
        first_payment = (
            item.price
            + japan_domestic_shipping_jpy
            + proxy_fee_jpy
            + config.add_on_fee_jpy
            + config.merge_fee_jpy
        ) * config.wameiji_exchange_rate
    second_payment = (
        config.international_shipping_per_cd_cny
        + config.duty_cny
        + config.optional_inspection_cny
        + config.optional_reinforcement_cny
        + config.other_related_fee_cny
    )
    before_risk = first_payment + second_payment + config.china_reship_cost_cny
    risk = max(config.risk_reserve_min_cny, before_risk * config.risk_reserve_rate)
    before_capital = before_risk + risk
    capital = before_capital * config.annual_capital_rate * expected_holding_days / 365
    total = before_capital + capital
    return CostBreakdown(
        first_payment_cny=round(first_payment, 2),
        estimated_second_payment_cny=round(second_payment, 2),
        china_reship_cost_cny=round(config.china_reship_cost_cny, 2),
        risk_reserve_cny=round(risk, 2),
        capital_cost_cny=round(capital, 2),
        total_landed_cost_cny=round(total, 2),
    )
