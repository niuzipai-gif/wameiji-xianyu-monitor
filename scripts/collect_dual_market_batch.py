#!/usr/bin/env python3
"""Collect a bounded, quality-gated batch of exact dual-market pairs.

The script deliberately uses the normal rendered browser surfaces. It never
calls an undocumented marketplace API, and it writes a pair only after a
Wameiji detail page and matching Xianyu search-card evidence both pass.
"""

# ruff: noqa: E402 -- executable script bootstraps the local src/ directory.

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cd_monitor.core.dual_market import (
    DualMarketCostConfig,
    ListingObservation,
    is_eligible,
    observation_from_wameiji,
)
from cd_monitor.core.models import MarketItem, WatchItem, XianyuPriceSample
from cd_monitor.core.product_images import is_usable_product_image
from cd_monitor.core.title_query import clean_title_search_query
from cd_monitor.services.dual_market_batch import (
    build_reverse_discovery_candidate,
    choose_xianyu_pair,
    title_query_without_identifiers,
    wameiji_item_matches_seed,
)
from cd_monitor.services.dual_market_service import rebuild_current_comparison
from cd_monitor.services.live_browser_capture import (
    capture_page_html,
    capture_search_html,
)
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter
from cd_monitor.storage.sqlite import insert_listing_observation

SHANGHAI = ZoneInfo("Asia/Shanghai")
UNAVAILABLE = {"sold_out", "reserved", "unavailable"}


@dataclass(frozen=True, slots=True)
class Seed:
    id: int
    media_type: str
    title: str
    catalog_no: str | None
    jan: str | None
    source_item_id: str | None
    source_url: str
    source_price: float
    source_currency: str
    availability: str
    detail_verified: bool
    pipeline_stage: str
    last_seen_at: str | None
    lookup_query: str | None = None
    origin_xianyu_sample: XianyuPriceSample | None = None
    origin_snapshot_path: str | None = None
    origin_screenshot_path: str | None = None


@dataclass(slots=True)
class Counters:
    reverse_queries: int = 0
    reverse_cards_visible: int = 0
    reverse_cards_accepted: int = 0
    reverse_unique_candidates: int = 0
    seeds_attempted: int = 0
    source_search_ok: int = 0
    source_detail_attempts: int = 0
    source_detail_verified: int = 0
    xianyu_queries: int = 0
    xianyu_search_ok: int = 0
    exact_pairs: int = 0
    persisted_pairs: int = 0
    duplicate_products: int = 0
    human_required: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-db", required=True)
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--wameiji-profile", required=True)
    parser.add_argument("--xianyu-profile", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--target-total", type=int, default=50)
    parser.add_argument("--max-seeds", type=int, default=150)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--max-wameiji-details", type=int, default=3)
    parser.add_argument("--minimum-title-samples", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=25)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--reverse-query",
        action="append",
        default=[],
        help="Capture one broad Xianyu query and reverse-check its visible cards.",
    )
    parser.add_argument(
        "--reverse-source",
        action="append",
        default=[],
        metavar="QUERY=HTML",
        help="Use a saved broad Xianyu HTML snapshot instead of recapturing it.",
    )
    parser.add_argument("--reverse-max-candidates", type=int, default=30)
    parser.add_argument("--reverse-minimum-title-samples", type=int, default=1)
    parser.add_argument(
        "--reverse-scroll-rounds",
        type=int,
        default=4,
        help="Bounded result-expansion scrolls for each live broad Xianyu query.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args(argv)


def load_seeds(path: Path) -> list[Seed]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, media_type, title, catalog_no, jan, source_item_id,
                   source_url, source_price, source_currency, availability,
                   detail_verified, pipeline_stage, last_seen_at
            FROM discovery_candidates
            WHERE status = 'active'
              AND source_url IS NOT NULL AND trim(source_url) <> ''
              AND title IS NOT NULL AND trim(title) <> ''
              AND availability NOT IN ('sold_out', 'reserved', 'unavailable')
            ORDER BY
              detail_verified DESC,
              CASE WHEN catalog_no IS NOT NULL OR jan IS NOT NULL THEN 0 ELSE 1 END,
              CASE pipeline_stage
                WHEN 'resale_queued' THEN 0
                WHEN 'evaluated' THEN 1
                WHEN 'search_discovered' THEN 2
                ELSE 3
              END,
              source_price ASC,
              last_seen_at DESC,
              id DESC
            """
        ).fetchall()
    seeds: list[Seed] = []
    seen_source_urls: set[str] = set()
    for row in rows:
        source_url = str(row["source_url"] or "").strip()
        if source_url in seen_source_urls:
            continue
        seen_source_urls.add(source_url)
        seeds.append(
            Seed(
                id=int(row["id"]),
                media_type=str(row["media_type"] or ""),
                title=str(row["title"]),
                catalog_no=_optional_text(row["catalog_no"]),
                jan=_optional_text(row["jan"]),
                source_item_id=_optional_text(row["source_item_id"]),
                source_url=source_url,
                source_price=float(row["source_price"] or 0),
                source_currency=str(row["source_currency"] or "JPY"),
                availability=str(row["availability"] or "unknown_but_visible"),
                detail_verified=bool(row["detail_verified"]),
                pipeline_stage=str(row["pipeline_stage"] or ""),
                last_seen_at=_optional_text(row["last_seen_at"]),
            )
        )
    return seeds


def build_reverse_seeds(
    samples: list[XianyuPriceSample],
    *,
    max_candidates: int,
    evidence_by_url: dict[str, dict[str, str | None]] | None = None,
) -> tuple[list[Seed], list[dict[str, Any]]]:
    """Screen and deduplicate broad Xianyu cards into bounded lookup seeds."""

    screened_rows: list[dict[str, Any]] = []
    by_fingerprint: dict[str, Any] = {}
    for sample in samples:
        screened = build_reverse_discovery_candidate(sample)
        base_row = {
            "accepted": screened.candidate is not None,
            "reason": screened.rejection_reason,
            "title": sample.title,
            "price_cny": sample.price_cny,
            "url": sample.url,
        }
        if screened.candidate is not None:
            base_row.update(
                search_title=screened.candidate.search_title,
                catalog_no=screened.candidate.catalog_no,
                jan=screened.candidate.jan,
                product_fingerprint=screened.candidate.product_fingerprint,
            )
            current = by_fingerprint.get(screened.candidate.product_fingerprint)
            if current is None or (
                sample.price_cny,
                str(sample.url or ""),
            ) < (
                current.sample.price_cny,
                str(current.sample.url or ""),
            ):
                by_fingerprint[screened.candidate.product_fingerprint] = (
                    screened.candidate
                )
        screened_rows.append(base_row)

    candidates = sorted(
        by_fingerprint.values(),
        key=lambda value: (
            float(value.sample.price_cny),
            value.product_fingerprint,
            str(value.sample.url or ""),
        ),
    )[: max(0, int(max_candidates))]
    seeds: list[Seed] = []
    for candidate in candidates:
        source_url = str(candidate.sample.url or "").strip()
        evidence = (evidence_by_url or {}).get(source_url, {})
        digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:15]
        seeds.append(
            Seed(
                id=-int(digest, 16),
                media_type=candidate.media_type,
                title=candidate.seed_title,
                catalog_no=candidate.catalog_no,
                jan=candidate.jan,
                source_item_id=None,
                source_url=source_url,
                source_price=float(candidate.sample.price_cny),
                source_currency="CNY",
                availability="available",
                detail_verified=False,
                pipeline_stage="reverse_discovered",
                last_seen_at=_stamp(),
                lookup_query=candidate.search_title,
                origin_xianyu_sample=candidate.sample,
                origin_snapshot_path=evidence.get("snapshot_path"),
                origin_screenshot_path=evidence.get("screenshot_path"),
            )
        )
    return seeds, screened_rows


async def load_reverse_discovery(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    counters: Counters,
) -> tuple[list[Seed], dict[str, Any]]:
    """Load saved/live broad Xianyu pages and return screened reverse seeds."""

    all_samples: list[XianyuPriceSample] = []
    evidence_by_url: dict[str, dict[str, str | None]] = {}
    sources: list[dict[str, Any]] = []
    adapter = XianyuBrowserAdapter(enabled=True)

    for index, value in enumerate(args.reverse_source, start=1):
        if "=" not in value:
            raise ValueError("reverse-source must use QUERY=HTML")
        query, raw_path = value.split("=", 1)
        query = query.strip()
        source_path = Path(raw_path.strip()).resolve()
        if not query or not source_path.is_file():
            raise FileNotFoundError(source_path)
        counters.reverse_queries += 1
        status = adapter.parse_discovery_html(
            source_path.read_text(encoding="utf-8"),
            query_label=query,
        )
        if status.status != "ok":
            counters.human_required += 1
            return [], {
                "stage": "reverse_discovery",
                "status": "human_required",
                "reason": status.error_type,
                "sources": sources,
                "screenings": [],
            }
        samples = list(status.items)
        counters.reverse_cards_visible += len(samples)
        all_samples.extend(samples)
        for sample in samples:
            if sample.url:
                evidence_by_url[str(sample.url)] = {
                    "snapshot_path": str(source_path),
                    "screenshot_path": str(source_path.with_suffix(".png"))
                    if source_path.with_suffix(".png").is_file()
                    else None,
                }
        sources.append(
            {
                "query": query,
                "capture_mode": "saved_html",
                "snapshot_path": str(source_path),
                "screenshot_path": str(source_path.with_suffix(".png"))
                if source_path.with_suffix(".png").is_file()
                else None,
                "visible_cards": len(samples),
            }
        )

    for index, query in enumerate(args.reverse_query, start=1):
        query = str(query or "").strip()
        if not query:
            continue
        counters.reverse_queries += 1
        capture_paths = _capture_paths(
            run_dir / "reverse-discovery", f"broad-{index}"
        )
        capture = await capture_search_html(
            "xianyu",
            query,
            capture_paths["html"],
            xianyu_profile_dir=args.xianyu_profile,
            screenshot_path=capture_paths["screenshot"],
            timeout_seconds=args.timeout_seconds,
            headless=args.headless,
            xianyu_result_scroll_rounds=getattr(args, "reverse_scroll_rounds", 4),
        )
        capture_summary = _capture_public_summary(capture)
        if capture.get("status") != "ok":
            counters.human_required += 1
            sources.append(
                {
                    "query": query,
                    "capture_mode": "live_browser",
                    **capture_summary,
                }
            )
            return [], {
                "stage": "reverse_discovery",
                "status": "human_required",
                "reason": capture.get("error_type"),
                "sources": sources,
                "screenings": [],
            }
        status = adapter.parse_discovery_html(
            capture_paths["html"].read_text(encoding="utf-8"),
            query_label=query,
        )
        if status.status != "ok":
            counters.human_required += 1
            return [], {
                "stage": "reverse_discovery",
                "status": "human_required",
                "reason": status.error_type,
                "sources": sources,
                "screenings": [],
            }
        samples = list(status.items)
        counters.reverse_cards_visible += len(samples)
        all_samples.extend(samples)
        for sample in samples:
            if sample.url:
                evidence_by_url[str(sample.url)] = {
                    "snapshot_path": str(capture_paths["html"]),
                    "screenshot_path": str(capture_paths["screenshot"]),
                }
        sources.append(
            {
                "query": query,
                "capture_mode": "live_browser",
                "visible_cards": len(samples),
                **capture_summary,
            }
        )

    seeds, screenings = build_reverse_seeds(
        all_samples,
        max_candidates=args.reverse_max_candidates,
        evidence_by_url=evidence_by_url,
    )
    counters.reverse_cards_accepted = sum(
        1 for row in screenings if row.get("accepted")
    )
    counters.reverse_unique_candidates = len(seeds)
    return seeds, {
        "stage": "reverse_discovery",
        "status": "ok",
        "sources": sources,
        "screenings": screenings,
        "visible_cards": counters.reverse_cards_visible,
        "accepted_cards": counters.reverse_cards_accepted,
        "unique_candidates": counters.reverse_unique_candidates,
    }


def existing_product_identities(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with sqlite3.connect(path) as connection:
        try:
            rows = connection.execute(
                "SELECT DISTINCT canonical_product_key FROM price_comparisons"
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
    return {_product_identity(str(row[0])) for row in rows if row[0]}


def _product_identity(canonical_key: str) -> str:
    key = str(canonical_key or "").strip()
    if key.startswith(("catalog:", "jan:")):
        return key.split("|", 1)[0]
    return key


def _seed_query(seed: Seed) -> tuple[str, str]:
    jan = re.sub(r"\D", "", seed.jan or "")
    if len(jan) == 13:
        return jan, "structured_identifier"
    catalog = str(seed.catalog_no or "").strip()
    if catalog and not catalog.casefold().startswith("source:"):
        return catalog, "structured_identifier"
    lookup_query = str(seed.lookup_query or "").strip()
    if lookup_query:
        return lookup_query, "strict_title"
    return clean_title_search_query(seed.title), "strict_title"


def minimum_title_samples_for_seed(seed: Seed, args: argparse.Namespace) -> int:
    if seed.pipeline_stage == "reverse_discovered":
        return max(1, int(args.reverse_minimum_title_samples))
    return max(1, int(args.minimum_title_samples))


def choose_reverse_origin_pair(
    wameiji: ListingObservation,
    seed: Seed,
    *,
    captured_at: str,
):
    if seed.pipeline_stage != "reverse_discovered" or seed.origin_xianyu_sample is None:
        return None
    return choose_xianyu_pair(
        wameiji,
        [seed.origin_xianyu_sample],
        captured_at=captured_at,
        snapshot_path=seed.origin_snapshot_path,
        screenshot_path=seed.origin_screenshot_path,
        minimum_title_samples=1,
        required_variant_title=seed.title,
        required_media_type=seed.media_type,
    )


def _matching_wameiji_cards(
    items: list[MarketItem], seed: Seed, query: str, query_kind: str
) -> list[MarketItem]:
    unique: dict[str, MarketItem] = {}
    for item in items:
        url = str(item.url or "").strip()
        if not url:
            continue
        if not wameiji_item_matches_seed(
            item,
            seed_title=seed.title,
            query=query,
            query_kind=query_kind,
            seed_media_type=seed.media_type,
        ):
            continue
        unique.setdefault(url, item)
    return sorted(
        unique.values(),
        key=lambda item: (float(item.price), str(item.external_item_id or item.url or "")),
    )


def _detail_matches_seed(
    item: MarketItem, seed: Seed, query: str, query_kind: str
) -> bool:
    return wameiji_item_matches_seed(
        item,
        seed_title=seed.title,
        query=query,
        query_kind=query_kind,
        seed_media_type=seed.media_type,
    )


def _xianyu_queries(
    item: MarketItem, observation: ListingObservation, seed: Seed
) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    jan = re.sub(r"\D", "", item.jan or "")
    if len(jan) == 13:
        queries.append((jan, "structured_identifier"))
    else:
        catalog = str(item.catalog_no or "").strip()
        if catalog and not catalog.casefold().startswith("source:"):
            queries.append((catalog, "structured_identifier"))
    for title in (observation.title, seed.title):
        title_query = title_query_without_identifiers(title)
        if title_query and all(
            title_query.casefold() != query.casefold() for query, _ in queries
        ):
            queries.append((title_query, "strict_title"))
    return queries


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _stamp() -> str:
    return datetime.now(SHANGHAI).isoformat()


def _safe_name(seed: Seed, query: str) -> str:
    compact = re.sub(r"[^0-9A-Za-z_-]+", "-", query)[:46].strip("-")
    return f"{seed.id}-{compact or 'title'}"


def _capture_paths(root: Path, stem: str) -> dict[str, Path]:
    return {
        "html": root / f"{stem}.html",
        "screenshot": root / f"{stem}.png",
    }


def _report_path(run_dir: Path) -> Path:
    return run_dir / "batch-report.json"


def _write_report(
    run_dir: Path,
    *,
    status: str,
    stop_reason: str | None,
    counters: Counters,
    starting_total: int,
    current_total: int,
    outcomes: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    payload = {
        "schema_version": 1,
        "generated_at": _stamp(),
        "status": status,
        "stop_reason": stop_reason,
        "dry_run": bool(args.dry_run),
        "starting_total": starting_total,
        "current_total": current_total,
        "target_total": args.target_total,
        "counters": asdict(counters),
        "outcomes": outcomes,
    }
    target = _report_path(run_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _quality_stop(counters: Counters) -> str | None:
    if counters.human_required:
        return "human_required"
    if (
        counters.source_detail_attempts >= 6
        and counters.source_detail_verified * 2 < counters.source_detail_attempts
    ):
        return "detail_verification_rate_below_50_percent"
    if (
        counters.source_detail_verified >= 20
        and counters.exact_pairs * 10 < counters.source_detail_verified
    ):
        return "exact_pair_rate_below_10_percent"
    return None


async def collect_one_seed(
    seed: Seed,
    *,
    run_dir: Path,
    args: argparse.Namespace,
    counters: Counters,
    existing_identities: set[str],
) -> dict[str, Any]:
    query, query_kind = _seed_query(seed)
    outcome: dict[str, Any] = {
        "seed_id": seed.id,
        "seed_title": seed.title,
        "query": query,
        "query_kind": query_kind,
        "status": "started",
    }
    if not query:
        outcome.update(status="rejected", reason="no_safe_query")
        return outcome

    seed_dir = run_dir / _safe_name(seed, query)
    w_search_paths = _capture_paths(seed_dir, "wameiji-search")
    search_capture = await capture_search_html(
        "wameiji",
        query,
        w_search_paths["html"],
        profile_dir=args.wameiji_profile,
        screenshot_path=w_search_paths["screenshot"],
        timeout_seconds=args.timeout_seconds,
        headless=args.headless,
    )
    outcome["wameiji_search"] = _capture_public_summary(search_capture)
    if search_capture.get("status") != "ok":
        counters.human_required += 1
        outcome.update(status="stopped", reason="wameiji_search_human_required")
        return outcome
    counters.source_search_ok += 1
    search_html = w_search_paths["html"].read_text(encoding="utf-8")
    # A rendered result title can omit the identifier that Wameiji searched.
    # Parse the visible cards without the adapter's precise-ID prefilter, then
    # apply ``wameiji_item_matches_seed`` below to retain only exact products.
    parser_query = clean_title_search_query(seed.title) or query
    parsed = WameijiBrowserAdapter(enabled=True).parse_search_html(
        search_html, WatchItem(catalog_no=parser_query)
    )
    cards = _matching_wameiji_cards(list(parsed.items), seed, query, query_kind)
    outcome["wameiji_matching_cards"] = len(cards)
    if not cards:
        outcome.update(status="rejected", reason="no_matching_wameiji_search_card")
        return outcome

    xianyu_cache: dict[
        str, tuple[list[XianyuPriceSample], Path, Path, dict[str, Any]]
    ] = {}
    verified_details: list[tuple[ListingObservation, MarketItem]] = []
    for index, card in enumerate(cards[: max(1, args.max_wameiji_details)], start=1):
        counters.source_detail_attempts += 1
        detail_paths = _capture_paths(seed_dir, f"wameiji-detail-{index}")
        detail_capture = await capture_page_html(
            "wameiji",
            urljoin("https://meruki.cn/", str(card.url or "")),
            detail_paths["html"],
            profile_dir=args.wameiji_profile,
            screenshot_path=detail_paths["screenshot"],
            timeout_seconds=args.timeout_seconds,
            headless=args.headless,
        )
        if detail_capture.get("status") != "ok":
            counters.human_required += 1
            outcome.update(status="stopped", reason="wameiji_detail_human_required")
            return outcome
        detail_status = WameijiBrowserAdapter(enabled=True).parse_detail_html(
            detail_paths["html"].read_text(encoding="utf-8"), card
        )
        if detail_status.status != "ok" or not detail_status.items:
            continue
        detail_item = detail_status.items[0]
        if (
            not detail_item.detail_verified
            or detail_item.availability in UNAVAILABLE
            or not is_usable_product_image(detail_item.image_url)
            or not _detail_matches_seed(detail_item, seed, query, query_kind)
        ):
            continue
        try:
            observation = observation_from_wameiji(
                detail_item,
                captured_at=_stamp(),
                snapshot_path=str(detail_paths["html"]),
                screenshot_path=str(detail_paths["screenshot"]),
            )
        except ValueError:
            continue
        if not is_eligible(observation, source="wameiji"):
            continue
        counters.source_detail_verified += 1
        verified_details.append((observation, detail_item))

    if not verified_details:
        outcome.update(status="rejected", reason="no_eligible_wameiji_detail")
        return outcome

    verified_details.sort(key=lambda pair: (pair[0].price, pair[0].source_listing_id))
    for wameiji, detail_item in verified_details:
        product_identity = _product_identity(str(wameiji.canonical_product_key or ""))
        if product_identity and product_identity in existing_identities:
            counters.duplicate_products += 1
            outcome.update(status="duplicate", reason="product_already_present")
            return outcome

        resale_queries = _xianyu_queries(detail_item, wameiji, seed)
        if not resale_queries:
            continue
        selections: list[tuple[Any, str, str, dict[str, Any]]] = []
        search_summaries: list[dict[str, Any]] = []
        origin_selection = choose_reverse_origin_pair(
            wameiji,
            seed,
            captured_at=_stamp(),
        )
        if origin_selection is not None:
            origin_summary = {
                "status": "ok",
                "error_type": None,
                "error_message": None,
                "item_count": 1,
                "final_url": seed.source_url,
                "snapshot_path": seed.origin_snapshot_path,
                "screenshot_path": seed.origin_screenshot_path,
            }
            search_summaries.append(
                {
                    "query": seed.lookup_query,
                    "query_kind": "reverse_origin",
                    **origin_summary,
                }
            )
            selections.append(
                (
                    origin_selection,
                    str(seed.lookup_query or seed.title),
                    "reverse_origin",
                    origin_summary,
                )
            )
        for resale_query, resale_query_kind in resale_queries:
            if resale_query not in xianyu_cache:
                counters.xianyu_queries += 1
                capture_index = len(xianyu_cache) + 1
                xianyu_paths = _capture_paths(
                    seed_dir, f"xianyu-search-{capture_index}"
                )
                xianyu_capture = await capture_search_html(
                    "xianyu",
                    resale_query,
                    xianyu_paths["html"],
                    xianyu_profile_dir=args.xianyu_profile,
                    screenshot_path=xianyu_paths["screenshot"],
                    timeout_seconds=args.timeout_seconds,
                    headless=args.headless,
                )
                capture_summary = _capture_public_summary(xianyu_capture)
                if xianyu_capture.get("status") != "ok":
                    counters.human_required += 1
                    outcome.update(
                        status="stopped", reason="xianyu_search_human_required"
                    )
                    return outcome
                counters.xianyu_search_ok += 1
                xianyu_status = XianyuBrowserAdapter(enabled=True).parse_search_html(
                    xianyu_paths["html"].read_text(encoding="utf-8"),
                    WatchItem(catalog_no=resale_query),
                )
                xianyu_cache[resale_query] = (
                    list(xianyu_status.items),
                    xianyu_paths["html"],
                    xianyu_paths["screenshot"],
                    capture_summary,
                )
            samples, snapshot_path, screenshot_path, capture_summary = xianyu_cache[
                resale_query
            ]
            search_summaries.append(
                {
                    "query": resale_query,
                    "query_kind": resale_query_kind,
                    **capture_summary,
                }
            )
            selection = choose_xianyu_pair(
                wameiji,
                samples,
                captured_at=_stamp(),
                snapshot_path=str(snapshot_path),
                screenshot_path=str(screenshot_path),
                minimum_title_samples=minimum_title_samples_for_seed(seed, args),
                required_variant_title=seed.title,
                required_media_type=seed.media_type,
            )
            if selection is not None:
                selections.append(
                    (selection, resale_query, resale_query_kind, capture_summary)
                )
        outcome["xianyu_searches"] = search_summaries
        if not selections:
            continue
        selection, resale_query, resale_query_kind, selected_capture = min(
            selections,
            key=lambda entry: (
                entry[0].xianyu.price,
                entry[0].xianyu.source_listing_id,
            ),
        )
        outcome["xianyu_search"] = selected_capture
        outcome["xianyu_query"] = resale_query
        outcome["xianyu_query_kind"] = resale_query_kind
        identity = _product_identity(str(selection.wameiji.canonical_product_key or ""))
        if identity in existing_identities:
            counters.duplicate_products += 1
            outcome.update(status="duplicate", reason="product_already_present")
            return outcome

        counters.exact_pairs += 1
        outcome.update(
            status="verified" if args.dry_run else "persisted",
            match_kind=selection.match_kind,
            matched_xianyu_listings=selection.matched_listing_count,
            canonical_product_key=selection.wameiji.canonical_product_key,
            wameiji={
                "listing_id": selection.wameiji.source_listing_id,
                "title": selection.wameiji.title,
                "price_jpy": selection.wameiji.price,
                "url": selection.wameiji.url,
                "image_url": selection.wameiji.image_url,
                "condition_group": selection.wameiji.condition_group,
            },
            xianyu={
                "listing_id": selection.xianyu.source_listing_id,
                "title": selection.xianyu.title,
                "price_cny": selection.xianyu.price,
                "url": selection.xianyu.url,
                "image_url": selection.xianyu.image_url,
                "condition_group": selection.xianyu.condition_group,
            },
        )
        if not args.dry_run:
            wameiji_id = insert_listing_observation(args.target_db, selection.wameiji)
            xianyu_id = insert_listing_observation(args.target_db, selection.xianyu)
            comparison = rebuild_current_comparison(
                args.target_db,
                str(selection.wameiji.canonical_product_key),
                DualMarketCostConfig(),
            )
            if comparison.comparison is None:
                raise RuntimeError("persisted exact pair did not create a comparison")
            counters.persisted_pairs += 1
            outcome["wameiji_observation_id"] = wameiji_id
            outcome["xianyu_observation_id"] = xianyu_id
            outcome["comparison_id"] = comparison.comparison.id
            outcome["comparison_status"] = comparison.status
            existing_identities.add(identity)
        return outcome

    outcome.update(status="rejected", reason="no_exact_xianyu_pair")
    return outcome


def _capture_public_summary(capture: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": capture.get("status"),
        "error_type": capture.get("error_type"),
        "error_message": capture.get("error_message"),
        "item_count": capture.get("item_count"),
        "final_url": capture.get("final_url"),
        "snapshot_path": capture.get("snapshot_path"),
        "screenshot_path": capture.get("screenshot_path"),
    }


async def run(args: argparse.Namespace) -> int:
    seed_db = Path(args.seed_db).resolve()
    target_db = Path(args.target_db).resolve()
    run_dir = Path(args.run_root).resolve() / datetime.now(SHANGHAI).strftime(
        "%Y%m%d-%H%M%S-batch"
    )
    if not seed_db.is_file():
        raise FileNotFoundError(seed_db)
    if not Path(args.wameiji_profile).is_dir():
        raise FileNotFoundError(args.wameiji_profile)
    if not Path(args.xianyu_profile).is_dir():
        raise FileNotFoundError(args.xianyu_profile)
    if (
        args.target_total < 1
        or args.max_seeds < 1
        or args.reverse_max_candidates < 1
        or args.reverse_minimum_title_samples < 1
        or args.reverse_scroll_rounds < 0
    ):
        raise ValueError(
            "target-total, max-seeds, and reverse-max-candidates must be positive; "
            "reverse-scroll-rounds cannot be negative"
        )

    identities = existing_product_identities(target_db)
    starting_total = len(identities)
    current_total = starting_total
    counters = Counters()
    outcomes: list[dict[str, Any]] = []
    stop_reason: str | None = None
    status = "running"
    reverse_mode = bool(args.reverse_query or args.reverse_source)
    if reverse_mode:
        seeds, reverse_stage = await load_reverse_discovery(
            args,
            run_dir=run_dir,
            counters=counters,
        )
        outcomes.append(reverse_stage)
        if reverse_stage.get("status") != "ok":
            status = "paused_quality"
            stop_reason = str(reverse_stage.get("reason") or "reverse_discovery_failed")
    else:
        seeds = load_seeds(seed_db)
    seeds = seeds[max(0, args.seed_offset) : max(0, args.seed_offset) + args.max_seeds]
    _write_report(
        run_dir,
        status=status,
        stop_reason=None,
        counters=counters,
        starting_total=starting_total,
        current_total=current_total,
        outcomes=outcomes,
        args=args,
    )
    print(
        json.dumps(
            {
                "event": "batch_started",
                "run_dir": str(run_dir),
                "starting_total": starting_total,
                "target_total": args.target_total,
                "seed_count": len(seeds),
                "discovery_mode": "reverse" if reverse_mode else "forward",
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    for seed in seeds if status == "running" else []:
        if not args.dry_run and current_total >= args.target_total:
            status = "target_reached"
            break
        counters.seeds_attempted += 1
        outcome = await collect_one_seed(
            seed,
            run_dir=run_dir,
            args=args,
            counters=counters,
            existing_identities=identities,
        )
        outcomes.append(outcome)
        current_total = len(identities) if not args.dry_run else starting_total + counters.exact_pairs
        stop_reason = _quality_stop(counters)
        print(
            json.dumps(
                {
                    "event": "seed_finished",
                    "seed_id": seed.id,
                    "status": outcome.get("status"),
                    "reason": outcome.get("reason"),
                    "current_total": current_total,
                    "counters": asdict(counters),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        _write_report(
            run_dir,
            status="paused_quality" if stop_reason else "running",
            stop_reason=stop_reason,
            counters=counters,
            starting_total=starting_total,
            current_total=current_total,
            outcomes=outcomes,
            args=args,
        )
        if stop_reason:
            status = "paused_quality"
            break
        if args.delay_seconds > 0:
            await asyncio.sleep(args.delay_seconds)
    else:
        if status == "running":
            status = "dry_run_complete" if args.dry_run else "seed_batch_exhausted"

    if not args.dry_run and current_total >= args.target_total:
        status = "target_reached"
        stop_reason = None
    _write_report(
        run_dir,
        status=status,
        stop_reason=stop_reason,
        counters=counters,
        starting_total=starting_total,
        current_total=current_total,
        outcomes=outcomes,
        args=args,
    )
    print(
        json.dumps(
            {
                "event": "batch_finished",
                "status": status,
                "stop_reason": stop_reason,
                "run_dir": str(run_dir),
                "starting_total": starting_total,
                "current_total": current_total,
                "target_total": args.target_total,
                "counters": asdict(counters),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if status in {"target_reached", "dry_run_complete"} else 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
