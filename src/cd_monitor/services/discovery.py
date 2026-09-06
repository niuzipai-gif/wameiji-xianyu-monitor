"""Offline-safe candidate discovery and opportunity evaluation.

The module intentionally accepts injected fetch functions.  The local worker
will later bind them to the visible-browser capture path, while unit tests can
exercise the full candidate, matching and profit flow without network access.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.discovery import DiscoveryCandidate, build_identity_key
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.identifiers import extract_catalog_candidates, extract_jan_candidates
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import MarketItem, WatchItem, XianyuPriceSample
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.storage.sqlite import (
    get_discovery_candidate_by_identity,
    get_discovery_pool,
    insert_discovery_opportunity,
    insert_market_items,
    insert_xianyu_samples,
    mark_discovery_candidate_xianyu_checked,
    record_discovery_run,
    update_discovery_pool_last_scan,
    upsert_discovery_candidate,
)

FetchWameiji = Callable[[str], Awaitable[list[MarketItem]]]
FetchXianyu = Callable[[str], Awaitable[list[XianyuPriceSample]]]


@dataclass(slots=True)
class DiscoveryScanResult:
    status: str
    pool_id: int
    keyword: str
    run_id: int
    discovered_count: int = 0
    candidate_count: int = 0
    evaluated_count: int = 0
    xianyu_query_count: int = 0
    opportunity_ids: list[int] | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.opportunity_ids is None:
            self.opportunity_ids = []


async def scan_discovery_keyword(
    *,
    db_path: str | Path,
    pool_id: int,
    keyword: str,
    fetch_wameiji: FetchWameiji,
    fetch_xianyu: FetchXianyu,
) -> DiscoveryScanResult:
    """Discover a bounded set of purchase listings for one pool keyword.

    A candidate is only estimated on its first observation or after a visible
    price/availability change.  This keeps the source-side discovery cadence
    separate from the more expensive Xianyu lookup cadence.
    """

    pool = get_discovery_pool(db_path, pool_id)
    if not pool.enabled:
        run_id = record_discovery_run(
            db_path,
            pool_id=pool_id,
            source="wameiji",
            status="disabled",
            keyword=keyword,
        )
        return DiscoveryScanResult("disabled", pool_id, keyword, run_id)

    try:
        purchase_items = await fetch_wameiji(keyword)
    except Exception as exc:  # noqa: BLE001 - browser adapters expose heterogeneous failures
        run_id = record_discovery_run(
            db_path,
            pool_id=pool_id,
            source="wameiji",
            status="human_required",
            keyword=keyword,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return DiscoveryScanResult(
            "human_required",
            pool_id,
            keyword,
            run_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    discovered_count = len(purchase_items)
    candidate_count = 0
    evaluated_count = 0
    opportunity_ids: list[int] = []
    seen_identity_keys: set[str] = set()
    for item in purchase_items:
        if candidate_count >= pool.candidate_budget:
            break
        candidate = _candidate_from_market_item(pool_id, pool.media_type, item)
        if candidate.identity_key in seen_identity_keys:
            continue
        seen_identity_keys.add(candidate.identity_key)
        previous = get_discovery_candidate_by_identity(
            db_path, pool_id, candidate.identity_key
        )
        candidate_id = upsert_discovery_candidate(db_path, candidate)
        candidate_count += 1
        if candidate.availability in {"sold_out", "unavailable"}:
            continue
        if not _needs_xianyu_refresh(previous, candidate):
            continue

        query = _xianyu_query(candidate)
        try:
            raw_samples = await fetch_xianyu(query)
        except Exception as exc:  # noqa: BLE001 - browser adapters expose heterogeneous failures
            record_discovery_run(
                db_path,
                pool_id=pool_id,
                source="xianyu",
                status="human_required",
                keyword=query,
                candidate_count=1,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            continue

        samples = [replace(sample, catalog_no=query) for sample in raw_samples]
        item_id = insert_market_items(db_path, [item])[0]
        insert_xianyu_samples(db_path, samples)
        watch = _watch_for_candidate(candidate, query)
        match = compute_match_confidence(item, watch)
        estimate = estimate_xianyu_price(
            samples,
            edition_confidence=max(0.7, match.confidence),
            edition=watch.edition,
            required_keywords=watch.required_keywords,
            excluded_keywords=watch.excluded_keywords,
        )
        cost = compute_landed_cost(item, expected_holding_days=watch.expected_holding_days)
        opportunity = evaluate_opportunity(watch, item, match, estimate, cost)
        opportunity_id = insert_discovery_opportunity(
            db_path,
            opportunity,
            wameiji_item_id=item_id,
            discovery_candidate_id=candidate_id,
            media_type=pool.media_type,
            identity_key=candidate.identity_key,
        )
        mark_discovery_candidate_xianyu_checked(db_path, candidate_id)
        evaluated_count += 1
        opportunity_ids.append(opportunity_id)

    run_id = record_discovery_run(
        db_path,
        pool_id=pool_id,
        source="wameiji",
        status="ok",
        keyword=keyword,
        discovered_count=discovered_count,
        candidate_count=candidate_count,
        evaluated_count=evaluated_count,
    )
    update_discovery_pool_last_scan(db_path, pool_id)
    return DiscoveryScanResult(
        "ok",
        pool_id,
        keyword,
        run_id,
        discovered_count=discovered_count,
        candidate_count=candidate_count,
        evaluated_count=evaluated_count,
        xianyu_query_count=evaluated_count,
        opportunity_ids=opportunity_ids,
    )


def _candidate_from_market_item(
    pool_id: int, media_type: str, item: MarketItem
) -> DiscoveryCandidate:
    text = " ".join(
        value for value in (item.title, item.raw_text, item.condition_text) if value
    )
    catalog_no = item.catalog_no or next(iter(extract_catalog_candidates(text)), None)
    jan = item.jan or next(iter(extract_jan_candidates(text)), None)
    return DiscoveryCandidate(
        pool_id=pool_id,
        media_type=media_type,
        identity_key=build_identity_key(
            catalog_no=catalog_no,
            jan=jan,
            external_item_id=item.external_item_id,
            title=item.title,
        ),
        catalog_no=catalog_no,
        jan=jan,
        title=item.title,
        source_item_id=item.external_item_id,
        source_url=item.url,
        source_price=item.price,
        source_currency=item.currency,
        availability=item.availability,
        status="expired" if item.availability in {"sold_out", "unavailable"} else "active",
        raw_text=item.raw_text,
    )


def _needs_xianyu_refresh(
    previous: DiscoveryCandidate | None, current: DiscoveryCandidate
) -> bool:
    if previous is None or previous.last_xianyu_checked_at is None:
        return True
    if previous.status != "active":
        return True
    if previous.availability != current.availability:
        return True
    return abs(previous.source_price - current.source_price) > 0.001


def _xianyu_query(candidate: DiscoveryCandidate) -> str:
    return candidate.catalog_no or candidate.jan or candidate.title


def _watch_for_candidate(candidate: DiscoveryCandidate, query: str) -> WatchItem:
    return WatchItem(
        # A title is a search query, not a catalog number.  Passing it through
        # catalog_no would make the legacy matcher report a false
        # catalog_no_fuzzy hit when the source title echoes the query.
        catalog_no=candidate.catalog_no or candidate.jan or candidate.identity_key,
        jan=candidate.jan,
        artist=candidate.artist,
        title_jp=candidate.title,
        edition=candidate.edition,
        decision_mode="keyword",
    )
