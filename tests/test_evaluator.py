from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.models import (
    CostBreakdown,
    EvaluationConfig,
    MarketItem,
    MatchResult,
    WatchItem,
    XianyuPriceEstimate,
)


def cost(total: float) -> CostBreakdown:
    return CostBreakdown(
        first_payment_cny=total - 20,
        estimated_second_payment_cny=18,
        china_reship_cost_cny=12,
        risk_reserve_cny=8,
        capital_cost_cny=1,
        total_landed_cost_cny=total,
    )


def estimate(price: float, count: int, status: str = "normal") -> XianyuPriceEstimate:
    return XianyuPriceEstimate(
        reference_price_cny=price,
        valid_sample_count=count,
        liquidity_status=status,
        expected_sale_price_cny=price * 0.9,
    )


def test_strong_alert() -> None:
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520", price=1000),
        MatchResult(confidence=0.95),
        estimate(260, 6),
        cost(110),
        EvaluationConfig(),
    )
    assert opp.decision == "strong_alert"


def test_weak_alert() -> None:
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520", price=1000),
        MatchResult(confidence=0.8),
        estimate(260, 2, "thin"),
        cost(130),
        EvaluationConfig(),
    )
    assert opp.decision == "weak_alert"


def test_rejects_unavailable_items_even_when_margin_is_alertable() -> None:
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520", price=1000, availability="sold_out"),
        MatchResult(confidence=0.8),
        estimate(260, 2, "thin"),
        cost(130),
        EvaluationConfig(),
    )
    assert opp.decision == "reject"
    assert "availability_risk" in opp.risk_labels


def test_rejects_fatal_flag() -> None:
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520 特典のみ", price=1000),
        MatchResult(confidence=0.9, fatal_flags=["only_bonus"]),
        estimate(300, 6),
        cost(100),
        EvaluationConfig(),
    )
    assert opp.decision == "reject"


def test_rejects_insufficient_liquidity_with_low_profit() -> None:
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520", price=1000),
        MatchResult(confidence=0.9),
        estimate(180, 1, "poor"),
        cost(120),
        EvaluationConfig(),
    )
    assert opp.decision == "reject"



def test_rental_keyword_surfaces_in_risk_labels() -> None:
    """spec §4.3：rental 必须出现在 Opportunity.risk_labels。"""
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520 レンタル落ち", price=1000),
        MatchResult(confidence=0.85, negative_reasons=["rental"]),
        estimate(260, 6),
        cost(110),
        EvaluationConfig(),
    )
    assert "rental" in opp.risk_labels


def test_sample_keyword_surfaces_in_risk_labels() -> None:
    """spec §4.3：sample 必须出现在 Opportunity.risk_labels。"""
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520 sample 見本盤", price=1000),
        MatchResult(confidence=0.85, negative_reasons=["sample"]),
        estimate(260, 6),
        cost(110),
        EvaluationConfig(),
    )
    assert "sample" in opp.risk_labels


def test_poor_condition_keyword_surfaces_in_risk_labels() -> None:
    """spec §4.3：poor_condition 必须出现在 Opportunity.risk_labels。"""
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520 盤傷あり", price=1000),
        MatchResult(confidence=0.85, negative_reasons=["poor_condition"]),
        estimate(260, 6),
        cost(110),
        EvaluationConfig(),
    )
    assert "poor_condition" in opp.risk_labels


def test_fatal_flag_and_negative_reason_both_in_risk_labels_no_duplicates() -> None:
    """fatal_flag + negative_reasons 同时存在时不重复。"""
    opp = evaluate_opportunity(
        WatchItem(catalog_no="SRCL-3520"),
        MarketItem(source="wameiji", title="SRCL-3520 レンタル落ち", price=1000),
        MatchResult(
            confidence=0.85,
            fatal_flags=["only_bonus"],
            negative_reasons=["rental"],
        ),
        estimate(260, 6),
        cost(110),
        EvaluationConfig(),
    )
    assert "only_bonus" in opp.risk_labels
    assert "rental" in opp.risk_labels
    # No duplicates of the same label
    assert opp.risk_labels.count("rental") == 1
