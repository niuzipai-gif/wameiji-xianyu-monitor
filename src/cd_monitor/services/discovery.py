"""Offline-safe candidate discovery and opportunity evaluation.

The module intentionally accepts injected fetch functions.  The local worker
will later bind them to the visible-browser capture path, while unit tests can
exercise the full candidate, matching and profit flow without network access.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.discovery import DiscoveryCandidate, DiscoveryPool, build_identity_key
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.identifiers import extract_catalog_candidates, extract_jan_candidates
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import MarketItem, MatchResult, WatchItem, XianyuPriceSample
from cd_monitor.core.title_query import (
    build_alias_search_query,
    clean_title_search_query,
    matches_title_search_query,
    source_edition_required_terms,
)
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.sources.wikidata_aliases import ResolvedTitleAlias
from cd_monitor.storage.sqlite import (
    get_discovery_pool,
    get_discovery_source_cooldown,
    insert_discovery_opportunity,
    insert_market_items,
    insert_xianyu_samples,
    list_discovery_detail_queue,
    list_discovery_resale_queue,
    mark_discovery_candidate_xianyu_checked,
    record_discovery_candidate_detail_attempt,
    record_discovery_run,
    record_discovery_title_alias_evidence,
    set_discovery_pool_capture_state,
    set_discovery_source_cooldown,
    update_discovery_pool_last_scan,
    upsert_discovery_candidates_with_previous,
)

FetchWameiji = Callable[[str], Awaitable[list[MarketItem]]]
FetchWameijiDetail = Callable[[MarketItem], Awaitable[MarketItem | None]]
FetchXianyu = Callable[[str], Awaitable[list[XianyuPriceSample]]]
ResolveTitleAliases = Callable[[DiscoveryCandidate], Awaitable[list[ResolvedTitleAlias]]]


_CD_HARD_MEDIA_MARKERS = (
    "cd",
    "dvd",
    "blu-ray",
    "bluray",
    "sacd",
    "輸入盤",
    "レコード",
    "vinyl",
    "音楽cd",
    "サウンドトラック",
    "サントラ",
)
_CD_SOFT_MEDIA_MARKERS = ("album", "single", "アルバム", "シングル")
_CD_BONUS_ONLY_MARKERS = ("トレカ", "フォトカード", "生写真", "アクリル", "缶バッジ")
_CD_FASHION_LOGO_MARKERS = ("cdロゴ", "cd logo", "cd-logo")
_CD_FASHION_CONTEXT_MARKERS = (
    "dior",
    "ディオール",
    "christian dior",
    "クリスチャンディオール",
    "バッグ",
    "カバン",
    "ハンドバッグ",
    "ショルダーバッグ",
    "財布",
    "ウォレット",
    "チャーム",
    "アクセサリー",
    "ネックレス",
    "ピアス",
    "リング",
    "ベルト",
    "レザー",
)
_GAME_MEDIA_MARKERS = (
    "switch",
    "nintendo",
    "ニンテンドー",
    "ゲーム",
    "game",
    "playstation",
    "ps vita",
    "psp",
    "ps4",
    "ps5",
    "3ds",
    "wii",
    "xbox",
    "ソフト",
)
_GAME_HARDWARE_MARKERS = (
    "本体",
    "コントローラー",
    "ジョイコン",
    "joy-con",
    "充電器",
    "充電スタンド",
    "ドック",
    "保護フィルム",
    "保護ケース",
    "保護カバー",
    "キャリングケース",
    "収納ケース",
    "ポーチ",
    "ケースのみ",
)
_INCOMPLETE_GAME_SEARCH_CARD_MARKERS = (
    "ソフトなし",
    "ゲームなし",
    "カセットなし",
    "ディスクなし",
    "本なし",
    "ケースのみ",
    "外箱のみ",
    "パッケージのみ",
    "特典のみ",
    "特典単品",
    "特典だけ",
    "予約特典のみ",
    "コードのみ",
    "ダウンロードコードのみ",
)
_INCOMPLETE_CD_SEARCH_CARD_MARKERS = (
    "盤なし",
    "ディスクなし",
    "cdなし",
    "cd無し",
    "ケースのみ",
    "外箱のみ",
    "パッケージのみ",
    "特典のみ",
    "特典単品",
    "特典だけ",
)
_SPECIAL_EDITION_MARKERS = (
    "完全生産限定",
    "完全生产限定",
    "初回限定",
    "限定版",
    "限定盤",
    "collector",
    "collectors",
    "limited edition",
)
_CONDITION_PRIORITY_MARKERS = ("新品", "未開封", "未使用", "帯付き")
_RARITY_PRIORITY_MARKERS = ("廃盤", "レア", "サウンドトラック", "サントラ", "ost")
_XIANYU_SECURITY_COOLDOWN_SECONDS = 30 * 60
_XIANYU_RESAMPLE_MINUTES = 180
_NOT_PURCHASABLE_AVAILABILITY = {"sold_out", "unavailable", "reserved"}
_STRICT_TITLE_SAMPLE_CONFIDENCE = 0.80


@dataclass(slots=True)
class DiscoveryScanResult:
    status: str
    pool_id: int
    keyword: str
    run_id: int
    discovered_count: int = 0
    candidate_count: int = 0
    detail_query_count: int = 0
    detail_verified_count: int = 0
    detail_rejected_count: int = 0
    evaluated_count: int = 0
    xianyu_query_count: int = 0
    resale_sampled_count: int = 0
    opportunity_ids: list[int] | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.opportunity_ids is None:
            self.opportunity_ids = []


@dataclass(slots=True)
class _XianyuEvidence:
    samples: list[XianyuPriceSample]
    rejected_title_samples: list[XianyuPriceSample]
    query_count: int
    used_title_alias: bool
    lookup_failed: bool = False
    security_blocked: bool = False


async def scan_discovery_keyword(
    *,
    db_path: str | Path,
    pool_id: int,
    keyword: str,
    fetch_wameiji: FetchWameiji,
    fetch_wameiji_detail: FetchWameijiDetail,
    fetch_xianyu: FetchXianyu,
    resolve_title_aliases: ResolveTitleAliases | None = None,
) -> DiscoveryScanResult:
    """Discover a bounded set of purchase listings for one pool keyword.

    Search cards only discover candidate URLs. Each candidate must first pass
    its Wameiji detail page before it can use the bounded Xianyu lookup budget.
    Later scans revisit details only for unverified, new, or visibly changed
    listings.
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
    if pool.capture_state != "active":
        run_id = record_discovery_run(
            db_path,
            pool_id=pool_id,
            source="wameiji",
            status=pool.capture_state,
            keyword=keyword,
            error_type="capture_paused",
            error_message=pool.pause_reason,
        )
        return DiscoveryScanResult(pool.capture_state, pool_id, keyword, run_id)

    # Once detail debt reaches the configured high-water mark, do not open a
    # new broad result page. A source search is a browser action too, and more
    # cards would only hide the fact that older detail URLs remain unverified.
    queue_high_watermark = max(1, pool.queue_high_watermark)
    outstanding_details = list_discovery_detail_queue(
        db_path,
        pool_id,
        limit=queue_high_watermark,
    )
    search_suppressed_for_backlog = len(outstanding_details) >= queue_high_watermark
    if search_suppressed_for_backlog:
        purchase_items: list[MarketItem] = []
    else:
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
    detail_query_count = 0
    detail_verified_count = 0
    detail_rejected_count = 0
    evaluated_count = 0
    xianyu_query_count = 0
    resale_sampled_count = 0
    consecutive_detail_access_blocks = 0
    cooldown_until = get_discovery_source_cooldown(db_path, "xianyu")
    xianyu_blocked = cooldown_until is not None
    if xianyu_blocked:
        # Wameiji detail verification remains useful during a Xianyu challenge;
        # only the Xianyu lookup is paused until its cooldown has elapsed.
        record_discovery_run(
            db_path,
            pool_id=pool_id,
            source="xianyu",
            status="cooldown",
            keyword=keyword,
            error_type="security_cooldown",
            error_message=f"Xianyu lookup paused until {cooldown_until} after a security check.",
        )
    opportunity_ids: list[int] = []
    resale_evidence_cache: dict[str, _XianyuEvidence] = {}
    seen_identity_keys: set[str] = set()
    seen_source_urls: set[str] = set()
    eligible_source_card_count = 0
    duplicate_source_url_count = 0
    # Wameiji can return over one hundred visible cards for a broad keyword.
    # Ingest enough of them for cache hits at the front not to hide later
    # listings, while keeping the local SQLite growth bounded.
    listing_budget = max(1, pool.search_card_budget)
    candidate_items: list[tuple[MarketItem, DiscoveryCandidate]] = []
    for item in purchase_items:
        if len(candidate_items) >= listing_budget:
            break
        if not _is_media_relevant(item, pool.media_type):
            continue
        if _is_obviously_incomplete_search_card(item, pool.media_type):
            # A source result card can already say that it contains only a
            # box, booklet, bonus, or other non-core component.  It cannot
            # become a purchase candidate, so do not spend a scarce detail
            # browser read merely to learn the same thing again.
            continue
        eligible_source_card_count += 1
        candidate = _candidate_from_market_item(pool_id, pool.media_type, item)
        source_url_key = _source_url_key(item.url)
        if source_url_key and source_url_key in seen_source_urls:
            duplicate_source_url_count += 1
            continue
        if source_url_key:
            seen_source_urls.add(source_url_key)
        if candidate.identity_key in seen_identity_keys:
            continue
        seen_identity_keys.add(candidate.identity_key)
        candidate_items.append((item, candidate))

    candidate_count = len(candidate_items)
    if _duplicate_source_url_rate_exceeded(
        eligible_source_card_count, duplicate_source_url_count
    ):
        pause_reason = "source_url_duplicate_rate_above_5_percent"
        set_discovery_pool_capture_state(
            db_path,
            pool_id,
            capture_state="paused_quality",
            pause_reason=pause_reason,
        )
        run_id = record_discovery_run(
            db_path,
            pool_id=pool_id,
            source="wameiji",
            status="paused_quality",
            keyword=keyword,
            discovered_count=discovered_count,
            candidate_count=candidate_count,
            error_type="source_url_duplicate_rate",
            error_message=(
                f"{duplicate_source_url_count} duplicate source URLs among "
                f"{eligible_source_card_count} eligible cards."
            ),
        )
        update_discovery_pool_last_scan(db_path, pool_id)
        return DiscoveryScanResult(
            "paused_quality",
            pool_id,
            keyword,
            run_id,
            discovered_count=discovered_count,
            candidate_count=candidate_count,
        )
    upsert_discovery_candidates_with_previous(
        db_path, [candidate for _, candidate in candidate_items]
    )

    # A search page only adds durable source links. The bounded browser work
    # always drains the existing queue, so a new page cannot leapfrog older
    # detail debt.
    detail_queue = list_discovery_detail_queue(
        db_path,
        pool_id,
        limit=max(pool.detail_budget, pool.queue_high_watermark),
    )
    detail_queue.sort(
        key=lambda candidate: _detail_priority(_market_item_from_candidate(candidate)),
        reverse=True,
    )
    detail_queue = detail_queue[: max(0, pool.detail_budget)]
    for candidate in detail_queue:
        assert candidate.id is not None
        candidate_id = candidate.id
        item = _market_item_from_candidate(candidate)
        if candidate.availability in _NOT_PURCHASABLE_AVAILABILITY:
            continue
        detail_query_count += 1
        try:
            detail_item = await fetch_wameiji_detail(item)
        except Exception as exc:  # noqa: BLE001 - a blocked detail is not comparable
            detail_rejected_count += 1
            if _is_wameiji_detail_access_block(exc):
                consecutive_detail_access_blocks += 1
            else:
                consecutive_detail_access_blocks = 0
            record_discovery_candidate_detail_attempt(
                db_path,
                candidate_id,
                pipeline_stage="blocked",
                error_message=type(exc).__name__,
            )
            record_discovery_run(
                db_path,
                pool_id=pool_id,
                source="wameiji_detail",
                status="human_required",
                keyword=item.url or item.title,
                candidate_count=1,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            if consecutive_detail_access_blocks >= 2:
                # A second access challenge means the visible browser session
                # is no longer trustworthy. Stop opening more source pages and
                # leave the outstanding detail queue intact for human review.
                break
            continue
        if detail_item is None or not detail_item.detail_verified:
            detail_rejected_count += 1
            consecutive_detail_access_blocks = 0
            record_discovery_candidate_detail_attempt(
                db_path,
                candidate_id,
                pipeline_stage="blocked",
                error_message="detail_parse_failed",
            )
            record_discovery_run(
                db_path,
                pool_id=pool_id,
                source="wameiji_detail",
                status="human_required",
                keyword=item.url or item.title,
                candidate_count=1,
                error_type="detail_parse_failed",
                error_message="Detail page did not yield a verified purchasable item.",
            )
            continue
        item = detail_item
        consecutive_detail_access_blocks = 0
        detail_verified_count += 1
        candidate = _candidate_from_market_item(pool_id, pool.media_type, item)
        if not _is_media_relevant(item, pool.media_type):
            detail_rejected_count += 1
            ignored_candidate = replace(candidate, status="ignored")
            ignored_persisted = upsert_discovery_candidates_with_previous(
                db_path, [ignored_candidate]
            )
            _, ignored_candidate_id = ignored_persisted[0]
            record_discovery_candidate_detail_attempt(
                db_path,
                ignored_candidate_id,
                pipeline_stage="rejected",
                detail_verified=True,
                error_message="media_type_mismatch",
            )
            continue
        detail_persisted = upsert_discovery_candidates_with_previous(db_path, [candidate])
        _, candidate_id = detail_persisted[0]
        record_discovery_candidate_detail_attempt(
            db_path,
            candidate_id,
            pipeline_stage="resale_queued",
            detail_verified=True,
        )
        if candidate.availability in _NOT_PURCHASABLE_AVAILABILITY:
            continue

        query = _xianyu_query(candidate)
        if not query:
            continue
        group_key = _resale_group_key(candidate, query)
        record_discovery_candidate_detail_attempt(
            db_path,
            candidate_id,
            pipeline_stage="resale_queued",
            detail_verified=True,
            product_key=group_key,
            increment_attempt=False,
        )
        watch = _watch_for_candidate(candidate, query)
        preflight_match = compute_match_confidence(item, watch)
        if preflight_match.fatal_flags:
            # A detail page can reveal that a collector box is only packaging
            # or that its game disc / soundtrack is missing.  No Xianyu price
            # can make an incomplete core product a valid purchase candidate.
            upsert_discovery_candidates_with_previous(
                db_path, [replace(candidate, status="ignored")]
            )
            detail_rejected_count += 1
            record_discovery_candidate_detail_attempt(
                db_path,
                candidate_id,
                pipeline_stage="rejected",
                detail_verified=True,
                error_message="incomplete_core_media",
                increment_attempt=False,
            )
            continue
        evidence = resale_evidence_cache.get(group_key)
        cache_hit = evidence is not None
        if evidence is None:
            if xianyu_blocked or xianyu_query_count >= pool.xianyu_query_budget:
                continue
            evidence = await _collect_xianyu_evidence(
                db_path=db_path,
                pool=pool,
                candidate=candidate,
                candidate_id=candidate_id,
                item=item,
                watch=watch,
                preflight_match=preflight_match,
                query=query,
                fetch_xianyu=fetch_xianyu,
                resolve_title_aliases=resolve_title_aliases,
                query_budget=pool.xianyu_query_budget - xianyu_query_count,
            )
            xianyu_query_count += evidence.query_count
            if evidence.security_blocked:
                xianyu_blocked = True
            if evidence.lookup_failed:
                continue
            resale_evidence_cache[group_key] = evidence

        opportunity_id = _persist_discovery_evaluation(
            db_path=db_path,
            pool=pool,
            candidate=candidate,
            candidate_id=candidate_id,
            item=item,
            watch=watch,
            preflight_match=preflight_match,
            evidence=evidence,
            persist_samples=not cache_hit,
        )
        if not cache_hit and evidence.samples:
            resale_sampled_count += 1
        evaluated_count += 1
        opportunity_ids.append(opportunity_id)

    quality_pause_reason = (
        "consecutive_detail_access_blocks"
        if consecutive_detail_access_blocks >= 2
        else (
            "detail_verification_rate_below_50_percent"
            if detail_query_count >= 4 and detail_verified_count * 2 < detail_query_count
            else None
        )
    )
    if quality_pause_reason is not None:
        set_discovery_pool_capture_state(
            db_path,
            pool_id,
            capture_state="paused_quality",
            pause_reason=quality_pause_reason,
        )
        queued_query_count = 0
        queued_evaluated_count = 0
        queued_opportunity_ids: list[int] = []
        queued_blocked = False
    else:
        (
            queued_query_count,
            queued_evaluated_count,
            queued_resale_sampled_count,
            queued_opportunity_ids,
            queued_blocked,
        ) = await _drain_persistent_resale_queue(
            db_path=db_path,
            pool=pool,
            fetch_xianyu=fetch_xianyu,
            resolve_title_aliases=resolve_title_aliases,
            query_budget=(
                0
                if xianyu_blocked
                else pool.xianyu_query_budget - xianyu_query_count
            ),
            evidence_cache=resale_evidence_cache,
        )
    if quality_pause_reason is not None:
        queued_resale_sampled_count = 0
    xianyu_query_count += queued_query_count
    evaluated_count += queued_evaluated_count
    resale_sampled_count += queued_resale_sampled_count
    opportunity_ids.extend(queued_opportunity_ids)
    xianyu_blocked = xianyu_blocked or queued_blocked

    status = "paused_quality" if quality_pause_reason is not None else "ok"
    run_id = record_discovery_run(
        db_path,
        pool_id=pool_id,
        source="wameiji",
        status=status,
        keyword=keyword,
        discovered_count=discovered_count,
        candidate_count=candidate_count,
        detail_query_count=detail_query_count,
        detail_verified_count=detail_verified_count,
        detail_rejected_count=detail_rejected_count,
        evaluated_count=evaluated_count,
        xianyu_query_count=xianyu_query_count,
        resale_sampled_count=resale_sampled_count,
    )
    update_discovery_pool_last_scan(db_path, pool_id)
    return DiscoveryScanResult(
        status,
        pool_id,
        keyword,
        run_id,
        discovered_count=discovered_count,
        candidate_count=candidate_count,
        detail_query_count=detail_query_count,
        detail_verified_count=detail_verified_count,
        detail_rejected_count=detail_rejected_count,
        evaluated_count=evaluated_count,
        xianyu_query_count=xianyu_query_count,
        resale_sampled_count=resale_sampled_count,
        opportunity_ids=opportunity_ids,
    )


async def _collect_xianyu_evidence(
    *,
    db_path: str | Path,
    pool: DiscoveryPool,
    candidate: DiscoveryCandidate,
    candidate_id: int,
    item: MarketItem,
    watch: WatchItem,
    preflight_match: MatchResult,
    query: str,
    fetch_xianyu: FetchXianyu,
    resolve_title_aliases: ResolveTitleAliases | None,
    query_budget: int,
) -> _XianyuEvidence:
    """Read one product's market evidence under the remaining source budget."""

    source_edition_terms = source_edition_required_terms(candidate.title)
    initial_required_terms = (
        source_edition_terms if _is_title_only_candidate(candidate) else ()
    )
    query_variants: list[tuple[str, tuple[str, ...], ResolvedTitleAlias | None, bool]] = [
        (
            query,
            initial_required_terms,
            None,
            _is_title_only_candidate(candidate),
        )
    ]
    samples: list[XianyuPriceSample] = []
    rejected_title_samples: list[XianyuPriceSample] = []
    seen_query_keys: set[str] = set()
    used_title_alias = False
    query_count = 0
    security_blocked = False

    while query_variants and query_count < max(0, query_budget):
        (
            current_query,
            required_any_terms,
            alias_evidence,
            requires_title_match,
        ) = query_variants.pop(0)
        query_key = " ".join(current_query.casefold().split())
        if not query_key or query_key in seen_query_keys:
            continue
        seen_query_keys.add(query_key)
        query_count += 1
        try:
            raw_samples = await fetch_xianyu(current_query)
        except Exception as exc:  # noqa: BLE001 - browser adapters expose heterogeneous failures
            record_discovery_run(
                db_path,
                pool_id=pool.id or 0,
                source="xianyu",
                status="human_required",
                keyword=current_query,
                candidate_count=1,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            security_blocked = _is_xianyu_security_check(exc)
            if security_blocked:
                set_discovery_source_cooldown(
                    db_path,
                    "xianyu",
                    cooldown_seconds=_XIANYU_SECURITY_COOLDOWN_SECONDS,
                    reason="security_check",
                )
            return _XianyuEvidence(
                samples=_deduplicate_xianyu_samples(samples),
                rejected_title_samples=rejected_title_samples,
                query_count=query_count,
                used_title_alias=used_title_alias,
                lookup_failed=True,
                security_blocked=security_blocked,
            )

        current_samples = [
            replace(sample, catalog_no=current_query) for sample in raw_samples
        ]
        if requires_title_match:
            matched_samples, rejected_samples = _filter_title_only_samples(
                current_samples,
                current_query,
                required_any_terms=required_any_terms,
            )
            samples.extend(matched_samples)
            rejected_title_samples.extend(rejected_samples)
        else:
            samples.extend(current_samples)

        if alias_evidence is not None:
            used_title_alias = True
        preview = estimate_xianyu_price(
            _deduplicate_xianyu_samples(samples),
            min_reference_samples=pool.min_valid_xianyu_samples,
            edition_confidence=max(0.7, preflight_match.confidence),
            edition=watch.edition,
            required_keywords=watch.required_keywords,
            excluded_keywords=watch.excluded_keywords,
            allow_complete_game_bundles=pool.media_type == "physical_game",
            require_new_condition=_source_is_factory_new(item),
        )
        if preview.valid_sample_count >= pool.min_valid_xianyu_samples:
            break
        aliases: list[ResolvedTitleAlias] = []
        if alias_evidence is None and resolve_title_aliases is not None:
            try:
                aliases = await resolve_title_aliases(candidate)
            except Exception:  # noqa: BLE001 - a free resolver cannot block collection.
                aliases = []
        for resolved_alias in aliases:
            alias_variant = build_alias_search_query(resolved_alias.value, candidate.title)
            if alias_variant is None:
                continue
            alias_key = " ".join(alias_variant.query.casefold().split())
            if alias_key in seen_query_keys or any(
                alias_key == " ".join(queued[0].casefold().split())
                for queued in query_variants
            ):
                continue
            record_discovery_title_alias_evidence(
                db_path,
                candidate_id=candidate_id,
                source_title=candidate.title,
                alias=resolved_alias.value,
                query=alias_variant.query,
                resolver=resolved_alias.source,
                source_url=resolved_alias.source_url,
                entity_id=resolved_alias.entity_id,
            )
            query_variants.append(
                (
                    alias_variant.query,
                    alias_variant.required_any_terms,
                    resolved_alias,
                    True,
                )
            )
        direct_title_query = clean_title_search_query(candidate.title)
        direct_title_key = " ".join(direct_title_query.casefold().split())
        if direct_title_key and direct_title_key not in seen_query_keys and not any(
            direct_title_key == " ".join(queued[0].casefold().split())
            for queued in query_variants
        ):
            query_variants.append(
                (direct_title_query, source_edition_terms, None, True)
            )

    return _XianyuEvidence(
        samples=_deduplicate_xianyu_samples(samples),
        rejected_title_samples=rejected_title_samples,
        query_count=query_count,
        used_title_alias=used_title_alias,
    )


def _persist_discovery_evaluation(
    *,
    db_path: str | Path,
    pool: DiscoveryPool,
    candidate: DiscoveryCandidate,
    candidate_id: int,
    item: MarketItem,
    watch: WatchItem,
    preflight_match: MatchResult,
    evidence: _XianyuEvidence,
    persist_samples: bool,
) -> int:
    """Evaluate one source-detail price against already collected market evidence."""

    item_id = insert_market_items(db_path, [item])[0]
    match = preflight_match
    estimate = estimate_xianyu_price(
        evidence.samples,
        min_reference_samples=pool.min_valid_xianyu_samples,
        edition_confidence=max(0.7, match.confidence),
        edition=watch.edition,
        required_keywords=watch.required_keywords,
        excluded_keywords=watch.excluded_keywords,
        allow_complete_game_bundles=pool.media_type == "physical_game",
        require_new_condition=_source_is_factory_new(item),
    )
    if _has_strict_title_sample_evidence(candidate, estimate.valid_samples, pool):
        positive_reasons = [*match.positive_reasons, "strict_title_sample_match"]
        if evidence.used_title_alias:
            positive_reasons.append("exact_public_title_alias")
        match = replace(
            match,
            confidence=max(match.confidence, _STRICT_TITLE_SAMPLE_CONFIDENCE),
            positive_reasons=positive_reasons,
        )
        estimate = estimate_xianyu_price(
            evidence.samples,
            min_reference_samples=pool.min_valid_xianyu_samples,
            edition_confidence=max(0.7, match.confidence),
            edition=watch.edition,
            required_keywords=watch.required_keywords,
            excluded_keywords=watch.excluded_keywords,
            allow_complete_game_bundles=pool.media_type == "physical_game",
            require_new_condition=_source_is_factory_new(item),
        )
    if persist_samples:
        insert_xianyu_samples(
            db_path,
            [
                *estimate.valid_samples,
                *estimate.invalid_samples,
                *evidence.rejected_title_samples,
            ],
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
    return opportunity_id


async def _drain_persistent_resale_queue(
    *,
    db_path: str | Path,
    pool: DiscoveryPool,
    fetch_xianyu: FetchXianyu,
    resolve_title_aliases: ResolveTitleAliases | None,
    query_budget: int,
    evidence_cache: dict[str, _XianyuEvidence],
) -> tuple[int, int, int, list[int], bool]:
    """Spend remaining resale budget on details verified in earlier scans."""

    remaining_budget = max(0, query_budget)
    if remaining_budget == 0:
        return 0, 0, 0, [], False
    queue = list_discovery_resale_queue(
        db_path,
        pool.id or 0,
        limit=max(pool.queue_high_watermark, remaining_budget * 5),
        refresh_after_minutes=_XIANYU_RESAMPLE_MINUTES,
    )
    groups: dict[
        str,
        list[tuple[DiscoveryCandidate, int, MarketItem, str, WatchItem, MatchResult]],
    ] = {}
    for candidate in queue:
        assert candidate.id is not None
        item = _market_item_from_candidate(candidate)
        query = _xianyu_query(candidate)
        if not query:
            continue
        group_key = _resale_group_key(candidate, query)
        record_discovery_candidate_detail_attempt(
            db_path,
            candidate.id,
            pipeline_stage="resale_queued",
            detail_verified=True,
            product_key=group_key,
            increment_attempt=False,
        )
        watch = _watch_for_candidate(candidate, query)
        preflight_match = compute_match_confidence(item, watch)
        if preflight_match.fatal_flags:
            upsert_discovery_candidates_with_previous(
                db_path, [replace(candidate, status="ignored")]
            )
            record_discovery_candidate_detail_attempt(
                db_path,
                candidate.id,
                pipeline_stage="rejected",
                detail_verified=True,
                error_message="incomplete_core_media",
                increment_attempt=False,
            )
            continue
        groups.setdefault(_resale_group_key(candidate, query), []).append(
            (candidate, candidate.id, item, query, watch, preflight_match)
        )

    query_count = 0
    evaluated_count = 0
    resale_sampled_count = 0
    opportunity_ids: list[int] = []
    for group in groups.values():
        candidate, candidate_id, item, query, watch, preflight_match = group[0]
        group_key = _resale_group_key(candidate, query)
        evidence = evidence_cache.get(group_key)
        cache_hit = evidence is not None
        if evidence is None:
            if query_count >= remaining_budget:
                break
            evidence = await _collect_xianyu_evidence(
                db_path=db_path,
                pool=pool,
                candidate=candidate,
                candidate_id=candidate_id,
                item=item,
                watch=watch,
                preflight_match=preflight_match,
                query=query,
                fetch_xianyu=fetch_xianyu,
                resolve_title_aliases=resolve_title_aliases,
                query_budget=remaining_budget - query_count,
            )
            query_count += evidence.query_count
            if evidence.lookup_failed:
                if evidence.security_blocked:
                    return (
                        query_count,
                        evaluated_count,
                        resale_sampled_count,
                        opportunity_ids,
                        True,
                    )
                continue
            evidence_cache[group_key] = evidence

        if not cache_hit and evidence.samples:
            resale_sampled_count += 1

        for index, (
            queued_candidate,
            queued_candidate_id,
            queued_item,
            _queued_query,
            queued_watch,
            queued_match,
        ) in enumerate(group):
            opportunity_id = _persist_discovery_evaluation(
                db_path=db_path,
                pool=pool,
                candidate=queued_candidate,
                candidate_id=queued_candidate_id,
                item=queued_item,
                watch=queued_watch,
                preflight_match=queued_match,
                evidence=evidence,
                persist_samples=not cache_hit and index == 0,
            )
            evaluated_count += 1
            opportunity_ids.append(opportunity_id)
    return query_count, evaluated_count, resale_sampled_count, opportunity_ids, False


def _resale_group_key(candidate: DiscoveryCandidate, query: str) -> str:
    """A verified catalog/JAN is stronger than a title-only query key."""

    kind = "catalog" if candidate.catalog_no else "jan" if candidate.jan else "title"
    normalized = " ".join(query.casefold().split())
    return f"{kind}:{normalized}"


def _source_url_key(value: str | None) -> str | None:
    """Normalize one listing URL enough to catch repeated result cards."""

    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), path, parsed.query, "")
    )


def _duplicate_source_url_rate_exceeded(
    eligible_source_card_count: int, duplicate_source_url_count: int
) -> bool:
    """Avoid pausing on one repeated sticky card, but stop parser-wide repeats."""

    return (
        eligible_source_card_count >= 10
        and duplicate_source_url_count * 20 > eligible_source_card_count
    )


def _candidate_from_market_item(
    pool_id: int, media_type: str, item: MarketItem
) -> DiscoveryCandidate:
    # Raw card payloads include listing URLs and the visible price. Do not mine
    # them for identifiers: for example, ``CD 300`` is not catalog ``CD-300``.
    catalog_no = _first_plausible_catalog_no(item.catalog_no) or _first_plausible_catalog_no(
        item.title
    )
    jan = _first_japanese_jan(item.jan) or _first_japanese_jan(item.title)
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
        status="expired" if item.availability in _NOT_PURCHASABLE_AVAILABILITY else "active",
        raw_text=item.raw_text,
        detail_verified=item.detail_verified,
    )


def _market_item_from_candidate(candidate: DiscoveryCandidate) -> MarketItem:
    """Rebuild the lightweight source card needed to open one queued detail."""

    return MarketItem(
        source="wameiji",
        title=candidate.title,
        price=candidate.source_price,
        currency=candidate.source_currency,
        catalog_no=candidate.catalog_no,
        jan=candidate.jan,
        external_item_id=candidate.source_item_id,
        url=candidate.source_url,
        availability=candidate.availability,
        raw_text=candidate.raw_text,
        detail_verified=candidate.detail_verified,
    )


def _is_media_relevant(item: MarketItem, media_type: str) -> bool:
    """Keep broad Wameiji searches from spending Xianyu reads on other goods."""

    title = str(item.title or "").casefold()
    if media_type == "physical_game" and any(
        marker in title for marker in _GAME_HARDWARE_MARKERS
    ):
        return False
    if media_type == "cd" and _is_obvious_cd_logo_fashion_item(title):
        # A fashion-brand ``CDロゴ`` describes a logo.  It must not inherit the
        # generic ``cd`` disc marker even if a noisy source card also contains
        # an identifier-looking string.
        return False
    if _first_plausible_catalog_no(item.catalog_no) or _first_japanese_jan(item.jan):
        return True
    if media_type == "cd":
        has_hard_media_marker = any(marker in title for marker in _CD_HARD_MEDIA_MARKERS)
        has_soft_media_marker = any(marker in title for marker in _CD_SOFT_MEDIA_MARKERS)
        is_bonus_only = any(marker in title for marker in _CD_BONUS_ONLY_MARKERS)
        return has_hard_media_marker or (has_soft_media_marker and not is_bonus_only)
    if media_type == "physical_game":
        return any(marker in title for marker in _GAME_MEDIA_MARKERS)
    return bool(title.strip())


def _is_obvious_cd_logo_fashion_item(title: str) -> bool:
    """Recognize fashion-brand ``CDロゴ`` as a logo instead of a disc signal."""

    return any(marker in title for marker in _CD_FASHION_LOGO_MARKERS) and any(
        marker in title for marker in _CD_FASHION_CONTEXT_MARKERS
    )


def _is_obviously_incomplete_search_card(item: MarketItem, media_type: str) -> bool:
    title = str(item.title or "").casefold()
    if media_type == "physical_game":
        return any(marker in title for marker in _INCOMPLETE_GAME_SEARCH_CARD_MARKERS)
    if media_type == "cd":
        return any(marker in title for marker in _INCOMPLETE_CD_SEARCH_CARD_MARKERS)
    return False


def _source_is_factory_new(item: MarketItem) -> bool:
    """Read the condition from an already-verified source detail page."""

    explicit_condition = str(item.condition_text or "").casefold()
    raw_text = str(item.raw_text or "")
    title = str(item.title or "")
    title_index = raw_text.find(title) if title else -1
    # The Wameiji detail header carries the seller's product state before the
    # long product description and platform campaign text.  Later campaign
    # text can say “中古精选” even for a product whose actual state is 新品.
    header_start = title_index + len(title) if title_index >= 0 else 0
    header_condition = raw_text[header_start : header_start + 240]
    # The first price field ends the condition block.  Do not let later site
    # promotions such as “中古精选” overwrite a preceding product-state word.
    for boundary in ("价格", "価格", "price"):
        index = header_condition.casefold().find(boundary.casefold())
        if index >= 0:
            header_condition = header_condition[:index]
            break
    header_condition = header_condition.casefold()
    text = " ".join((explicit_condition, header_condition))
    if any(
        marker in text
        for marker in (
            "二手",
            "中古",
            "使用済",
            "開封済",
            "已拆",
            "一部未使用",
            "未使用に近い",
        )
    ):
        return False
    return any(marker in text for marker in ("新品", "未開封", "未使用", "全新", "未拆"))


def _detail_priority(item: MarketItem) -> tuple[int, int, int, float]:
    """Rank search cards for a limited source-detail inspection budget."""

    title = str(item.title or "").casefold()
    has_exact_identifier = int(
        bool(_first_plausible_catalog_no(item.catalog_no))
        or bool(_first_japanese_jan(item.jan))
    )
    special_edition = int(any(marker in title for marker in _SPECIAL_EDITION_MARKERS))
    condition_or_rarity = int(
        any(marker in title for marker in _CONDITION_PRIORITY_MARKERS)
        or any(marker in title for marker in _RARITY_PRIORITY_MARKERS)
    )
    price = float(item.price)
    affordable_price = -price if isfinite(price) and price > 0 else float("-inf")
    return has_exact_identifier, special_edition, condition_or_rarity, affordable_price


def _first_japanese_jan(text: str | None) -> str | None:
    """Return only a plausible Japanese retail JAN, never an incidental ID."""

    for candidate in extract_jan_candidates(text):
        if len(candidate) == 13 and candidate.startswith(("45", "49")):
            return candidate
    return None


def _first_plausible_catalog_no(text: str | None) -> str | None:
    """Return a catalog code, excluding price-adjacent text such as CD-300."""

    for candidate in extract_catalog_candidates(text):
        prefix, _, suffix = candidate.partition("-")
        if len(prefix) >= 3 and len(suffix) >= 3:
            return candidate
    return None


def _is_xianyu_security_check(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in ("security_check", "captcha", "安全验证"))


def _is_wameiji_detail_access_block(exc: Exception) -> bool:
    """Recognize browser blocks that make another detail retry unsafe."""

    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "redirected_away_from_listing",
            "security_check",
            "captcha",
            "安全验证",
            "login_required",
        )
    )


def _needs_xianyu_refresh(
    previous: DiscoveryCandidate | None, current: DiscoveryCandidate
) -> bool:
    if previous is None:
        return True
    if previous.detail_verified and previous.status == "ignored":
        return False
    if previous.detail_verified and previous.status != "active":
        # A generic search card often says only that a link is visible. It is
        # not evidence that a detail-confirmed reservation or sold listing is
        # purchasable again. Revisit only when the card explicitly becomes
        # available or its visible price changes.
        return current.availability == "available" or abs(
            previous.source_price - current.source_price
        ) > 0.001
    if previous.last_xianyu_checked_at is None:
        return True
    if previous.availability != current.availability:
        return True
    return abs(previous.source_price - current.source_price) > 0.001


def _xianyu_query(candidate: DiscoveryCandidate) -> str | None:
    return candidate.catalog_no or candidate.jan or clean_title_search_query(candidate.title)


def _is_title_only_candidate(candidate: DiscoveryCandidate) -> bool:
    return not candidate.catalog_no and not candidate.jan


def _has_strict_title_sample_evidence(
    candidate: DiscoveryCandidate,
    valid_samples: list[XianyuPriceSample],
    pool: DiscoveryPool,
) -> bool:
    if not _is_title_only_candidate(candidate):
        return False
    minimum = max(2, int(pool.min_valid_xianyu_samples))
    distinct_samples = {
        sample.url or f"{sample.title.casefold()}|{sample.price_cny:.2f}"
        for sample in valid_samples
    }
    return len(distinct_samples) >= minimum


def _filter_title_only_samples(
    samples: list[XianyuPriceSample],
    query: str,
    *,
    required_any_terms: tuple[str, ...] = (),
) -> tuple[list[XianyuPriceSample], list[XianyuPriceSample]]:
    """Keep only title evidence that retains the variant's product facts."""

    matched = [
        sample
        for sample in samples
        if matches_title_search_query(
            sample.title,
            query,
            required_any_terms=required_any_terms,
        )
    ]
    matched_ids = {id(sample) for sample in matched}
    rejected = [
        replace(sample, is_valid=False, invalid_reason="title_mismatch")
        for sample in samples
        if id(sample) not in matched_ids
    ]
    return matched, rejected


def _deduplicate_xianyu_samples(
    samples: list[XianyuPriceSample],
) -> list[XianyuPriceSample]:
    """A listing returned by both Japanese and alias queries counts once."""

    unique: list[XianyuPriceSample] = []
    seen: set[str] = set()
    for sample in samples:
        key = sample.url or f"{sample.title.casefold()}|{sample.price_cny:.2f}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(sample)
    return unique


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
