"""Cost model walkthrough — spec §6 端到端算账。

不走浏览器，只走：MarketItem → compute_landed_cost → evaluate_opportunity → Opportunity。
验证手算数字与代码输出一致（误差 < 0.01 CNY）。
"""
from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.models import (
    CostConfig,
    EvaluationConfig,
    MarketItem,
    MatchResult,
    WatchItem,
    XianyuPriceEstimate,
)


# 走默认 CostConfig（与 spec §6.3 一致）
def test_default_cost_config_walkthrough_matches_hand_calc() -> None:
    item = MarketItem(source="wameiji", title="SRCL-3520 初回限定 帯付き", price=1200)
    cost = compute_landed_cost(item, CostConfig(), expected_holding_days=30)

    # 手算：
    # first  = (1200 + 0 + 200 + 0 + 0) * 0.046 = 64.40
    # second = 18
    # before_risk = 64.40 + 18 + 12 = 94.40
    # risk        = max(8, 94.40 * 0.03) = 8.00
    # before_cap  = 94.40 + 8.00 = 102.40
    # capital     = 102.40 * 0.08 * 30 / 365 ≈ 0.6733
    # total       ≈ 103.07
    assert abs(cost.first_payment_cny - 64.40) < 0.01
    assert abs(cost.estimated_second_payment_cny - 18.0) < 0.01
    assert abs(cost.china_reship_cost_cny - 12.0) < 0.01
    assert abs(cost.risk_reserve_cny - 8.0) < 0.01
    assert cost.capital_cost_cny > 0
    assert cost.capital_cost_cny < 1.0
    assert abs(cost.total_landed_cost_cny - 103.07) < 0.05


def test_price_cny_display_overrides_jpy_path() -> None:
    """页面直接展示人民币价时，first_payment 直接用 CNY，跳过 JPY→CNY 换算。"""
    item = MarketItem(source="wameiji", title="SRCL-3520", price=10000, price_cny_display=88)
    cost = compute_landed_cost(item, CostConfig())
    assert cost.first_payment_cny == 88
    # second/other still apply
    assert cost.total_landed_cost_cny > 88


def test_zero_proxy_fee_path() -> None:
    """当 proxy_fee=0 且 ItemJPY 极小时，risk_reserve 走 min 8 CNY。"""
    item = MarketItem(source="wameiji", title="SRCL-3520", price=10)
    cost = compute_landed_cost(item, CostConfig(default_proxy_fee_jpy=0))
    # before_risk = (10 + 0 + 0) * 0.046 + 18 + 12 = 0.46 + 30 = 30.46
    # risk = max(8, 30.46 * 0.03) = max(8, 0.91) = 8
    assert cost.risk_reserve_cny == 8


def test_holding_days_changes_capital_cost() -> None:
    """持有天数越长，资金成本越高（验证 capital_cost CNY 单调递增）。"""
    item = MarketItem(source="wameiji", title="SRCL-3520", price=1000)
    c30 = compute_landed_cost(item, CostConfig(), expected_holding_days=30)
    c90 = compute_landed_cost(item, CostConfig(), expected_holding_days=90)
    assert c90.capital_cost_cny > c30.capital_cost_cny
    # 大致 3 倍关系
    assert abs(c90.capital_cost_cny - 3 * c30.capital_cost_cny) < 0.1


def test_cost_model_feeds_evaluator_to_opportunity() -> None:
    """端到端：cost_model → evaluator → Opportunity。"""
    item = MarketItem(source="wameiji", title="SRCL-3520 初回限定 帯付き", price=1200)
    watch = WatchItem(catalog_no="SRCL-3520", expected_holding_days=30)
    cost = compute_landed_cost(item, CostConfig(), expected_holding_days=30)
    match = MatchResult(confidence=0.95, matched_identifiers=["catalog_no_exact"])
    xianyu = XianyuPriceEstimate(
        reference_price_cny=260,
        valid_sample_count=6,
        liquidity_status="normal",
        expected_sale_price_cny=260 * 0.92 * 0.90 * 0.95,
    )
    opp = evaluate_opportunity(watch, item, match, xianyu, cost, EvaluationConfig())
    assert opp.landed_cost == cost.total_landed_cost_cny
    assert opp.expected_profit is not None
    # 260 RMB 闲鱼参考价 vs ~103 CNY 落地成本 → 强提醒（如果所有阈值都过）
    # 这里只验证链路通畅、决策在 4 选 1 中
    assert opp.decision in {"strong_alert", "weak_alert", "review_only", "reject"}