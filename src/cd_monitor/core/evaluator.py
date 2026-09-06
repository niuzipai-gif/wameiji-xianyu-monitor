from __future__ import annotations

from cd_monitor.core.hashing import build_alert_hash
from cd_monitor.core.models import (
    CostBreakdown,
    EvaluationConfig,
    MarketItem,
    MatchResult,
    Opportunity,
    WatchItem,
    XianyuPriceEstimate,
)


def evaluate_opportunity(
    watch_item: WatchItem,
    item: MarketItem,
    match: MatchResult,
    xianyu: XianyuPriceEstimate,
    cost: CostBreakdown,
    config: EvaluationConfig | None = None,
) -> Opportunity:
    config = config or EvaluationConfig()
    sale_price = xianyu.expected_sale_price_cny or (
        xianyu.reference_price_cny
        * config.negotiation_discount
        * config.liquidity_discount_default
        * max(0.7, match.confidence)
    )
    fee = min(sale_price * config.xianyu_fee_rate, config.xianyu_fee_cap_cny)
    revenue = (
        sale_price
        - fee
        - config.domestic_outbound_shipping_cny
        - config.packaging_cost_cny
        - config.after_sale_reserve_cny
    )
    profit = revenue - cost.total_landed_cost_cny
    margin = profit / cost.total_landed_cost_cny if cost.total_landed_cost_cny else 0.0
    turnover_roi = margin * 30 / max(1, watch_item.expected_holding_days)
    risks: list[str] = []
    risks.extend(match.fatal_flags)
    # spec §4.3 negative_signals 也要出现在 risk_labels（rental / sample / poor_condition）
    for spec_negative in ("rental", "sample", "poor_condition"):
        if spec_negative in match.negative_reasons and spec_negative not in risks:
            risks.append(spec_negative)
    if xianyu.liquidity_status == "poor":
        risks.append("liquidity_poor")
    if match.confidence < config.min_match_confidence_weak:
        risks.append("match_confidence_low")
    if item.availability not in {"available", "likely_available", "unknown_but_visible"}:
        risks.append("availability_risk")
    availability_blocked = item.availability not in {
        "available",
        "likely_available",
        "unknown_but_visible",
    }

    if (
        match.fatal_flags
        or availability_blocked
        or match.confidence < config.min_match_confidence_weak
        or (xianyu.liquidity_status == "poor" and profit < 80)
        or profit <= 0
        or margin < 0.20
    ):
        decision = "reject"
    elif (
        profit >= config.strong_profit_min_cny
        and margin >= config.strong_margin_min
        and xianyu.valid_sample_count >= config.min_xianyu_samples_strong
        and match.confidence >= config.min_match_confidence_strong
        and xianyu.liquidity_status != "poor"
        and item.availability in {"available", "likely_available", "unknown_but_visible"}
    ):
        decision = "strong_alert"
    elif (
        profit >= config.weak_profit_min_cny
        and margin >= config.weak_margin_min
        and xianyu.valid_sample_count >= config.min_xianyu_samples_weak
        and not match.fatal_flags
    ):
        decision = "weak_alert"
    else:
        decision = "review_only"

    opportunity_hash = build_alert_hash(
        item.source,
        item.external_item_id,
        item.price,
        xianyu.reference_price_cny,
        cost.total_landed_cost_cny,
        decision,
    )
    return Opportunity(
        catalog_no=watch_item.catalog_no,
        item=item,
        xianyu_reference_price=xianyu.reference_price_cny,
        expected_sale_price=round(sale_price, 2),
        landed_cost=cost.total_landed_cost_cny,
        expected_revenue=round(revenue, 2),
        expected_profit=round(profit, 2),
        net_margin=round(margin, 4),
        turnover_adjusted_roi=round(turnover_roi, 4),
        match_confidence=match.confidence,
        valid_xianyu_sample_count=xianyu.valid_sample_count,
        liquidity_status=xianyu.liquidity_status,
        decision=decision,
        risk_labels=risks,
        opportunity_hash=opportunity_hash,
    )
