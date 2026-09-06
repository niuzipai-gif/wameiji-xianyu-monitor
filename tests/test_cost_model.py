from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.models import CostConfig, MarketItem


def test_page_cny_price_has_priority() -> None:
    item = MarketItem(source="wameiji", title="SRCL-3520", price=1000, price_cny_display=88)
    cost = compute_landed_cost(item, CostConfig())
    assert cost.first_payment_cny == 88
    assert cost.total_landed_cost_cny > 88


def test_jpy_exchange_calculation_when_no_page_cny() -> None:
    item = MarketItem(source="wameiji", title="SRCL-3520", price=1000)
    cost = compute_landed_cost(item, CostConfig(wameiji_exchange_rate=0.05, default_proxy_fee_jpy=200))
    assert cost.first_payment_cny == 60


def test_risk_reserve_minimum_and_capital_cost() -> None:
    item = MarketItem(source="wameiji", title="SRCL-3520", price=100)
    cost = compute_landed_cost(item, CostConfig(risk_reserve_min_cny=8, annual_capital_rate=0.08))
    assert cost.risk_reserve_cny == 8
    assert cost.capital_cost_cny > 0
