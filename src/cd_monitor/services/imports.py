from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
import re
from typing import Any

from cd_monitor.core.models import AdapterStatus, WatchItem
from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import XianyuPriceEstimate, XianyuPriceSample
from cd_monitor.core.models import CostConfig, EvaluationConfig, Opportunity
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter
from cd_monitor.sources.mock import MockWameijiAdapter, MockXianyuAdapter
from cd_monitor.storage.snapshots import save_snapshot
from cd_monitor.storage.sqlite import init_db, insert_market_items, insert_opportunity, insert_xianyu_samples


def import_html_snapshot(
    source: str,
    catalog_no: str,
    html_path: str | Path,
    snapshot_dir: str | Path,
) -> tuple[AdapterStatus, Path]:
    html = Path(html_path).read_text(encoding="utf-8")
    watch = WatchItem(catalog_no=catalog_no)
    if source == "wameiji":
        status = WameijiBrowserAdapter(enabled=True).parse_search_html(html, watch)
    elif source == "xianyu":
        status = XianyuBrowserAdapter(enabled=True).parse_search_html(html, watch)
    else:
        raise ValueError(f"Unsupported html source: {source}")
    snapshot_path = save_snapshot(
        snapshot_dir,
        f"{source}-html",
        {
            "source": source,
            "catalog_no": catalog_no,
            "status": status.status,
            "error_type": status.error_type,
            "error_message": status.error_message,
            "items": [_item_to_dict(item) for item in status.items],
        },
    )
    return status, snapshot_path


def _item_to_dict(item: Any) -> dict[str, Any]:
    return asdict(item)


def import_xianyu_csv(
    catalog_no: str,
    csv_path: str | Path,
    snapshot_dir: str | Path,
) -> tuple[list[XianyuPriceSample], XianyuPriceEstimate, Path]:
    samples: list[XianyuPriceSample] = []
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            title = row.get("title") or row.get("标题") or ""
            price_text = row.get("price_cny") or row.get("price") or row.get("价格") or "0"
            samples.append(
                XianyuPriceSample(
                    catalog_no=catalog_no,
                    title=title,
                    price_cny=_parse_csv_price(price_text),
                    url=row.get("url") or row.get("链接") or None,
                    image_url=row.get("image_url") or row.get("图片") or None,
                    seller_text=row.get("seller_text") or row.get("卖家") or None,
                    raw_text=row.get("raw_text") or row.get("原文") or title,
                )
            )
    estimate = estimate_xianyu_price(samples)
    snapshot_path = save_snapshot(
        snapshot_dir,
        "xianyu-csv",
        {
            "source": "xianyu_csv",
            "catalog_no": catalog_no,
            "samples": [asdict(sample) for sample in samples],
            "estimate": asdict(estimate),
        },
    )
    return samples, estimate, snapshot_path


def _parse_csv_price(value: Any) -> float:
    text = str(value or "").strip()
    match = re.search(
        r"(?:[¥￥]|CNY|RMB)?\s*([\d][\d,]*(?:\.\d+)?)\s*(?:元)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return 0.0
    return float(match.group(1).replace(",", ""))


def evaluate_files(
    catalog_no: str,
    wameiji_html_path: str | Path,
    xianyu_csv_path: str | Path,
    db_path: str | Path,
    snapshot_dir: str | Path,
    cost_config: CostConfig | None = None,
    evaluation_config: EvaluationConfig | None = None,
) -> tuple[list[Opportunity], list[int], Path]:
    init_db(db_path)
    watch = WatchItem(catalog_no=catalog_no)
    html = Path(wameiji_html_path).read_text(encoding="utf-8")
    wameiji_status = WameijiBrowserAdapter(enabled=True).parse_search_html(html, watch)
    if wameiji_status.status != "ok":
        snapshot_path = save_snapshot(
            snapshot_dir,
            "evaluate-files",
            {
                "catalog_no": catalog_no,
                "status": wameiji_status.status,
                "error_type": wameiji_status.error_type,
                "error_message": wameiji_status.error_message,
            },
        )
        return [], [], snapshot_path
    samples, _, _ = import_xianyu_csv(catalog_no, xianyu_csv_path, snapshot_dir)
    item_ids = insert_market_items(db_path, wameiji_status.items)
    insert_xianyu_samples(db_path, samples)
    opportunities: list[Opportunity] = []
    opportunity_ids: list[int] = []
    for index, item in enumerate(wameiji_status.items):
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
    snapshot_path = save_snapshot(
        snapshot_dir,
        "evaluate-files",
        {
            "catalog_no": catalog_no,
            "wameiji_items": [asdict(item) for item in wameiji_status.items],
            "xianyu_samples": [asdict(sample) for sample in samples],
            "opportunities": [asdict(opportunity) for opportunity in opportunities],
        },
    )
    return opportunities, opportunity_ids, snapshot_path


def evaluate_html_files(
    catalog_no: str,
    wameiji_html_path: str | Path,
    xianyu_html_path: str | Path,
    db_path: str | Path,
    snapshot_dir: str | Path,
    cost_config: CostConfig | None = None,
    evaluation_config: EvaluationConfig | None = None,
) -> tuple[list[Opportunity], list[int], Path]:
    return evaluate_html_texts(
        catalog_no,
        Path(wameiji_html_path).read_text(encoding="utf-8"),
        Path(xianyu_html_path).read_text(encoding="utf-8"),
        db_path,
        snapshot_dir,
        cost_config,
        evaluation_config,
    )


def evaluate_html_texts(
    catalog_no: str,
    wameiji_html: str,
    xianyu_html: str,
    db_path: str | Path,
    snapshot_dir: str | Path,
    cost_config: CostConfig | None = None,
    evaluation_config: EvaluationConfig | None = None,
) -> tuple[list[Opportunity], list[int], Path]:
    init_db(db_path)
    watch = WatchItem(catalog_no=catalog_no)
    wameiji_status = WameijiBrowserAdapter(enabled=True).parse_search_html(wameiji_html, watch)
    xianyu_status = XianyuBrowserAdapter(enabled=True).parse_search_html(xianyu_html, watch)
    if wameiji_status.status != "ok" or xianyu_status.status != "ok":
        snapshot_path = save_snapshot(
            snapshot_dir,
            "evaluate-html",
            {
                "catalog_no": catalog_no,
                "wameiji_status": wameiji_status.status,
                "wameiji_error_type": wameiji_status.error_type,
                "wameiji_error_message": wameiji_status.error_message,
                "xianyu_status": xianyu_status.status,
                "xianyu_error_type": xianyu_status.error_type,
                "xianyu_error_message": xianyu_status.error_message,
            },
        )
        return [], [], snapshot_path
    samples = list(xianyu_status.items)
    item_ids = insert_market_items(db_path, wameiji_status.items)
    insert_xianyu_samples(db_path, samples)
    opportunities: list[Opportunity] = []
    opportunity_ids: list[int] = []
    for index, item in enumerate(wameiji_status.items):
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
    snapshot_path = save_snapshot(
        snapshot_dir,
        "evaluate-html",
        {
            "catalog_no": catalog_no,
            "wameiji_items": [asdict(item) for item in wameiji_status.items],
            "xianyu_samples": [asdict(sample) for sample in samples],
            "opportunities": [asdict(opportunity) for opportunity in opportunities],
        },
    )
    return opportunities, opportunity_ids, snapshot_path


def evaluate_json_files(
    catalog_no: str,
    wameiji_json_path: str | Path,
    xianyu_json_path: str | Path,
    db_path: str | Path,
    snapshot_dir: str | Path,
    cost_config: CostConfig | None = None,
    evaluation_config: EvaluationConfig | None = None,
) -> tuple[list[Opportunity], list[int], Path]:
    init_db(db_path)
    watch = WatchItem(catalog_no=catalog_no)
    items = MockWameijiAdapter(wameiji_json_path).search(watch)
    samples = MockXianyuAdapter(xianyu_json_path).search_samples(watch)
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
    snapshot_path = save_snapshot(
        snapshot_dir,
        "evaluate-json",
        {
            "catalog_no": catalog_no,
            "wameiji_items": [asdict(item) for item in items],
            "xianyu_samples": [asdict(sample) for sample in samples],
            "opportunities": [asdict(opportunity) for opportunity in opportunities],
        },
    )
    return opportunities, opportunity_ids, snapshot_path
