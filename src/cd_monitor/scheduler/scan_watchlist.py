from __future__ import annotations

from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import Opportunity, WatchItem
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.sources.base import WameijiSourceAdapter, XianyuSourceAdapter


def scan_watch_item(
    watch_item: WatchItem,
    wameiji: WameijiSourceAdapter,
    xianyu: XianyuSourceAdapter,
) -> list[Opportunity]:
    samples = xianyu.search_samples(watch_item)
    opportunities: list[Opportunity] = []
    for item in wameiji.search(watch_item):
        match = compute_match_confidence(item, watch_item)
        estimate = estimate_xianyu_price(
            samples,
            edition_confidence=max(0.7, match.confidence),
            edition=watch_item.edition,
            required_keywords=watch_item.required_keywords,
            excluded_keywords=watch_item.excluded_keywords,
        )
        cost = compute_landed_cost(item, expected_holding_days=watch_item.expected_holding_days)
        opportunities.append(evaluate_opportunity(watch_item, item, match, estimate, cost))
    return opportunities
