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
    missing_cost_fields = _missing_cost_fields(wameiji, cost_config)
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

    landed_cost = _calculate_landed_cost(wameiji, cost_config)
    sale_price = round(xianyu.price, 2)
    sales_fee = min(
        sale_price * _required(cost_config.sales_fee_rate),
        _required(cost_config.sales_fee_cap_cny),
    )
    profit = round(sale_price - sales_fee - landed_cost, 2)
    margin = round(profit / sale_price, 4) if sale_price else None
    status = "ready" if profit > 0 else "negative_profit"
    comparison = PriceComparison(
        canonical_product_key=key,
        wameiji_observation_id=wameiji.id,
        xianyu_observation_id=xianyu.id,
        cost_config_json=snapshot_json,
        landed_cost_cny=landed_cost,
        sale_price_cny=sale_price,
        expected_profit_cny=profit,
        net_margin=margin,
        status=status,
    )
    comparison_id = insert_price_comparison(db_path, comparison)
    return ComparisonOutcome(status=status, comparison=replace(comparison, id=comparison_id))


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
        "sales_fee_cap_cny": config.sales_fee_cap_cny,
    }
    if wameiji.source_detail_fee is None:
        required_fields["japan_domestic_shipping_jpy"] = config.japan_domestic_shipping_jpy
    return [name for name, value in required_fields.items() if value is None]


def _calculate_landed_cost(
    wameiji: ListingObservation, config: DualMarketCostConfig
) -> float:
    domestic_shipping = (
        wameiji.source_detail_fee
        if wameiji.source_detail_fee is not None
        else _required(config.japan_domestic_shipping_jpy)
    )
    jpy_component = (
        wameiji.price + domestic_shipping + _required(config.proxy_fee_jpy)
    ) * _required(config.exchange_rate_cny_per_jpy)
    cny_component = sum(
        (
            _required(config.international_shipping_per_item_cny),
            _required(config.china_reship_cny),
            _required(config.packaging_cny),
            _required(config.after_sale_reserve_cny),
            _required(config.risk_reserve_cny),
            _required(config.tax_cny),
        )
    )
    return round(jpy_component + cny_component, 2)


def _required(value: float | None) -> float:
    if value is None:  # pragma: no cover - guarded by _missing_cost_fields
        raise ValueError("cost configuration is incomplete")
    return float(value)
