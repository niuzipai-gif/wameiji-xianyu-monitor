from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import Opportunity, WatchItem
from cd_monitor.core.models import CostConfig, EvaluationConfig
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.sources.mock import MockWameijiAdapter, MockXianyuAdapter
from cd_monitor.storage.snapshots import save_snapshot
from cd_monitor.storage.sqlite import (
    finish_search_run,
    init_db,
    insert_market_items,
    insert_opportunity,
    insert_search_run,
    insert_xianyu_samples,
)


@dataclass(slots=True)
class ScanResult:
    search_run_id: int
    opportunities: list[Opportunity]
    opportunity_ids: list[int]
    snapshot_path: Path


def scan_once_mock(
    catalog_no: str,
    db_path: str | Path,
    wameiji_path: str | Path,
    xianyu_path: str | Path,
    snapshot_dir: str | Path,
    cost_config: CostConfig | None = None,
    evaluation_config: EvaluationConfig | None = None,
    watch_item: WatchItem | None = None,
) -> ScanResult:
    init_db(db_path)
    watch = watch_item or WatchItem(catalog_no=catalog_no)
    catalog_no = watch.catalog_no
    run_id = insert_search_run(db_path, source="mock", keyword=catalog_no, status="running")
    wameiji = MockWameijiAdapter(wameiji_path)
    xianyu = MockXianyuAdapter(xianyu_path)
    items = wameiji.search(watch)
    samples = xianyu.search_samples(watch)
    snapshot_path = save_snapshot(
        snapshot_dir,
        "mock-scan",
        {
            "catalog_no": catalog_no,
            "wameiji_items": [asdict(item) for item in items],
            "xianyu_samples": [asdict(sample) for sample in samples],
        },
    )
    item_ids = insert_market_items(db_path, items)
    insert_xianyu_samples(db_path, samples)
    opportunities: list[Opportunity] = []
    opportunity_ids: list[int] = []
    for index, item in enumerate(items):
        match = compute_match_confidence(item, watch)
        estimate = estimate_xianyu_price(
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
            edition_confidence=max(0.7, match.confidence),
            edition=watch.edition,
            required_keywords=watch.required_keywords,
            excluded_keywords=watch.excluded_keywords,
        )
        cost = compute_landed_cost(
            item,
            config=cost_config,
            expected_holding_days=watch.expected_holding_days,
        )
        opportunity = evaluate_opportunity(watch, item, match, estimate, cost, evaluation_config)
        opportunity_id = insert_opportunity(
            db_path,
            opportunity,
            item_ids[index] if index < len(item_ids) else None,
        )
        opportunity_ids.append(opportunity_id)
        opportunities.append(opportunity)
    finish_search_run(db_path, run_id, status="ok", raw_snapshot_path=str(snapshot_path))
    return ScanResult(
        search_run_id=run_id,
        opportunities=opportunities,
        opportunity_ids=opportunity_ids,
        snapshot_path=snapshot_path,
    )
