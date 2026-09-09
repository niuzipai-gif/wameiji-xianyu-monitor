"""Build auditable price comparisons from exact dual-market observations."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

from cd_monitor.core.dual_market import (
    ComparisonOutcome,
    DualMarketCostConfig,
    ListingObservation,
    PriceComparison,
    select_lowest_eligible,
)
from cd_monitor.storage.sqlite import (
    insert_price_comparison,
    list_current_observations,
)


CostBreakdown = dict[str, float | str | list[str] | None]


def rebuild_current_comparison(
    db_path: str | Path,
    canonical_product_key: str,
    cost_config: DualMarketCostConfig,
) -> ComparisonOutcome:
    """Select each side independently and persist an immutable comparison snapshot."""

    key = canonical_product_key.strip()
    if not key:
        raise ValueError("canonical_product_key is required")
    observations = list_current_observations(db_path, canonical_product_key=key)
    wameiji = select_lowest_eligible(observations, source="wameiji")
    if wameiji is None:
        return ComparisonOutcome(status="waiting_wameiji")
    xianyu = select_lowest_eligible(
        (
            observation
            for observation in observations
            if observation.condition_group == wameiji.condition_group
        ),
        source="xianyu",
    )
    if xianyu is None:
        return ComparisonOutcome(status="waiting_xianyu")
    if wameiji.canonical_product_key != key or xianyu.canonical_product_key != key:
        raise ValueError("comparison observations must share the requested product key")
    if wameiji.condition_group != xianyu.condition_group:
        raise ValueError("comparison observations must share the same condition group")
    if wameiji.id is None or xianyu.id is None:
        raise ValueError("persisted observations require database ids")
    if wameiji.currency != "JPY" or xianyu.currency != "CNY":
        raise ValueError("dual-market comparison requires JPY purchase and CNY resale prices")

    snapshot_json = json.dumps(asdict(cost_config), ensure_ascii=False, sort_keys=True)
    breakdown = comparison_cost_breakdown(wameiji, xianyu, cost_config)
    missing_cost_fields = list(breakdown["missing_fields"] or [])
    if missing_cost_fields:
        comparison = PriceComparison(
            canonical_product_key=key,
            wameiji_observation_id=wameiji.id,
            xianyu_observation_id=xianyu.id,
            cost_config_json=snapshot_json,
            landed_cost_cny=None,
            sale_price_cny=round(xianyu.price, 2),
            expected_profit_cny=None,
            net_margin=None,
            status="cost_pending",
        )
        comparison_id = insert_price_comparison(db_path, comparison)
        return ComparisonOutcome(
            status="cost_pending", comparison=replace(comparison, id=comparison_id)
        )

    raw_landed_cost = float(breakdown["landed_cost_cny"])
    raw_profit = float(breakdown["net_profit_cny"])
    raw_margin = float(breakdown["net_margin"])
    sale_price = round(xianyu.price, 2)
    threshold = float(cost_config.minimum_net_margin or 0.0)
    status = (
        "eligible"
        if raw_profit > 0 and raw_margin >= threshold
        else "below_margin"
    )
    comparison = PriceComparison(
        canonical_product_key=key,
        wameiji_observation_id=wameiji.id,
        xianyu_observation_id=xianyu.id,
        cost_config_json=snapshot_json,
        landed_cost_cny=round(raw_landed_cost, 2),
        sale_price_cny=sale_price,
        expected_profit_cny=round(raw_profit, 2),
        net_margin=raw_margin,
        status=status,
    )
    comparison_id = insert_price_comparison(db_path, comparison)
    return ComparisonOutcome(status=status, comparison=replace(comparison, id=comparison_id))


def comparison_cost_breakdown(
    wameiji: ListingObservation,
    xianyu: ListingObservation,
    config: DualMarketCostConfig,
) -> CostBreakdown:
    """Return every auditable input and raw result without early rounding."""

    if wameiji.currency != "JPY" or xianyu.currency != "CNY":
        raise ValueError("dual-market comparison requires JPY purchase and CNY resale prices")

    missing_fields = _missing_cost_fields(wameiji, config)
    domestic_shipping = (
        wameiji.source_detail_fee
        if wameiji.source_detail_fee is not None
        else config.japan_domestic_shipping_jpy
    )
    result: CostBreakdown = {
        "policy_version": config.policy_version,
        "minimum_net_margin": config.minimum_net_margin,
        "missing_fields": missing_fields,
        "xianyu_sale_cny": float(xianyu.price),
        "xianyu_seller_fee_cny": None,
        "wameiji_exchange_rate_cny_per_jpy": config.exchange_rate_cny_per_jpy,
        "wameiji_item_jpy": float(wameiji.price),
        "wameiji_item_cny": None,
        "wameiji_domestic_shipping_jpy": domestic_shipping,
        "wameiji_domestic_shipping_cny": None,
        "wameiji_proxy_fee_jpy": config.proxy_fee_jpy,
        "wameiji_proxy_fee_cny": None,
        "wameiji_purchase_cny": None,
        "international_shipping_cny": config.international_shipping_per_item_cny,
        "china_postage_cny": config.china_reship_cny,
        "packaging_cny": config.packaging_cny,
        "after_sale_reserve_cny": config.after_sale_reserve_cny,
        "risk_reserve_cny": config.risk_reserve_cny,
        "tax_cny": config.tax_cny,
        "landed_cost_cny": None,
        "net_profit_cny": None,
        "net_margin": None,
    }
    if missing_fields:
        return result

    exchange_rate = _required(config.exchange_rate_cny_per_jpy)
    item_cny = float(wameiji.price) * exchange_rate
    domestic_shipping_cny = _required(domestic_shipping) * exchange_rate
    proxy_fee_cny = _required(config.proxy_fee_jpy) * exchange_rate
    wameiji_purchase_cny = item_cny + domestic_shipping_cny + proxy_fee_cny
    landed_cost_cny = wameiji_purchase_cny + sum(
        (
            _required(config.international_shipping_per_item_cny),
            _required(config.china_reship_cny),
            _required(config.packaging_cny),
            _required(config.after_sale_reserve_cny),
            _required(config.risk_reserve_cny),
            _required(config.tax_cny),
        )
    )
    sales_fee_cny = _calculate_sales_fee(float(xianyu.price), config)
    net_profit_cny = float(xianyu.price) - sales_fee_cny - landed_cost_cny
    net_margin = net_profit_cny / float(xianyu.price)
    result.update(
        {
            "xianyu_seller_fee_cny": sales_fee_cny,
            "wameiji_item_cny": item_cny,
            "wameiji_domestic_shipping_cny": domestic_shipping_cny,
            "wameiji_proxy_fee_cny": proxy_fee_cny,
            "wameiji_purchase_cny": wameiji_purchase_cny,
            "landed_cost_cny": landed_cost_cny,
            "net_profit_cny": net_profit_cny,
            "net_margin": net_margin,
        }
    )
    return result


def _missing_cost_fields(
    wameiji: ListingObservation, config: DualMarketCostConfig
) -> list[str]:
    required_fields: dict[str, float | None] = {
        "exchange_rate_cny_per_jpy": config.exchange_rate_cny_per_jpy,
        "proxy_fee_jpy": config.proxy_fee_jpy,
        "international_shipping_per_item_cny": config.international_shipping_per_item_cny,
        "china_reship_cny": config.china_reship_cny,
        "packaging_cny": config.packaging_cny,
        "after_sale_reserve_cny": config.after_sale_reserve_cny,
        "risk_reserve_cny": config.risk_reserve_cny,
        "tax_cny": config.tax_cny,
        "sales_fee_rate": config.sales_fee_rate,
    }
    if config.sales_fee_uncapped is not True:
        required_fields["sales_fee_cap_cny"] = config.sales_fee_cap_cny
    if wameiji.source_detail_fee is None:
        required_fields["japan_domestic_shipping_jpy"] = config.japan_domestic_shipping_jpy
    return [name for name, value in required_fields.items() if value is None]


def _calculate_sales_fee(sale_price_cny: float, config: DualMarketCostConfig) -> float:
    fee = sale_price_cny * _required(config.sales_fee_rate)
    if config.sales_fee_uncapped is True:
        return fee
    return min(fee, _required(config.sales_fee_cap_cny))


def _required(value: float | None) -> float:
    if value is None:  # pragma: no cover - guarded by _missing_cost_fields
        raise ValueError("cost configuration is incomplete")
    return float(value)
