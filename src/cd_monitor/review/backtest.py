from __future__ import annotations

import json
from pathlib import Path

from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import (
    CostConfig,
    EvaluationConfig,
    MarketItem,
    Opportunity,
    WatchItem,
    XianyuPriceSample,
)
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.storage.sqlite import list_market_items, list_xianyu_samples


def backtest_snapshots(
    snapshot_dir: str | Path,
    cost_config: CostConfig | None = None,
    evaluation_config: EvaluationConfig | None = None,
) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    for path in sorted(Path(snapshot_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        catalog_no = payload.get("catalog_no")
        if not catalog_no:
            continue
        watch = WatchItem(catalog_no=catalog_no)
        samples = [
            XianyuPriceSample(**sample)
            for sample in payload.get("xianyu_samples", [])
            if isinstance(sample, dict)
        ]
        for raw_item in payload.get("wameiji_items", []):
            if not isinstance(raw_item, dict):
                continue
            item = MarketItem(**raw_item)
            match = compute_match_confidence(item, watch)
            estimate = _estimate(samples, match.confidence, watch, evaluation_config)
            cost = compute_landed_cost(
                item,
                config=cost_config,
                expected_holding_days=watch.expected_holding_days,
            )
            opportunities.append(evaluate_opportunity(watch, item, match, estimate, cost, evaluation_config))
    return opportunities


def backtest_database_history(
    db_path: str | Path,
    catalog_no: str,
    cost_config: CostConfig | None = None,
    evaluation_config: EvaluationConfig | None = None,
) -> list[Opportunity]:
    watch = WatchItem(catalog_no=catalog_no)
    samples = list_xianyu_samples(db_path, catalog_no)
    opportunities: list[Opportunity] = []
    for item in list_market_items(db_path, catalog_no):
        match = compute_match_confidence(item, watch)
        estimate = _estimate(samples, match.confidence, watch, evaluation_config)
        cost = compute_landed_cost(
            item,
            config=cost_config,
            expected_holding_days=watch.expected_holding_days,
        )
        opportunities.append(evaluate_opportunity(watch, item, match, estimate, cost, evaluation_config))
    return opportunities


def _estimate(
    samples: list[XianyuPriceSample],
    match_confidence: float,
    watch: WatchItem,
    evaluation_config: EvaluationConfig | None,
):
    return estimate_xianyu_price(
        samples,
        min_valid_price_cny=(
            evaluation_config.min_valid_price_cny if evaluation_config else 10
        ),
        max_valid_price_cny=(
            evaluation_config.max_valid_price_cny if evaluation_config else 2000
        ),
        sample_limit=evaluation_config.sample_limit if evaluation_config else 15,
        negotiation_discount=(
            evaluation_config.negotiation_discount if evaluation_config else 0.92
        ),
        liquidity_discount_default=(
            evaluation_config.liquidity_discount_default if evaluation_config else 0.90
        ),
        edition_confidence=max(0.7, match_confidence),
        edition=watch.edition,
        required_keywords=watch.required_keywords,
        excluded_keywords=watch.excluded_keywords,
    )
