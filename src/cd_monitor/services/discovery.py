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

from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.discovery import DiscoveryCandidate, DiscoveryPool, build_identity_key
from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.identifiers import extract_catalog_candidates, extract_jan_candidates
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.models import MarketItem, WatchItem, XianyuPriceSample
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
    mark_discovery_candidate_xianyu_checked,
    record_discovery_run,
    record_discovery_title_alias_evidence,
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
    "音楽cd",
    "サウンドトラック",
    "サントラ",
)
_CD_SOFT_MEDIA_MARKERS = ("album", "single", "アルバム", "シングル")
_CD_BONUS_ONLY_MARKERS = ("トレカ", "フォトカード", "生写真", "アクリル", "缶バッジ")
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
    evaluated_count = 0
    xianyu_query_count = 0
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
    seen_identity_keys: set[str] = set()
    # Wameiji can return over one hundred visible cards for a broad keyword.
    # Ingest enough of them for cache hits at the front not to hide later
    # listings, while keeping the local SQLite growth bounded.
    listing_budget = max(pool.candidate_budget * 10, 100)
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
        candidate = _candidate_from_market_item(pool_id, pool.media_type, item)
        if candidate.identity_key in seen_identity_keys:
            continue
        seen_identity_keys.add(candidate.identity_key)
        candidate_items.append((item, candidate))

    # Marketplace relevance ordering is not a profit ordering.  Spend the
    # bounded detail-page budget on verifiable, special/rare, lower-cost cards
    # first; source details still remain the sole authority for availability,
    # fee and completeness before any resale lookup is made.
    candidate_items.sort(
        key=lambda pair: _detail_priority(pair[0]),
        reverse=True,
    )
    candidate_count = len(candidate_items)
    persisted = upsert_discovery_candidates_with_previous(
        db_path, [candidate for _, candidate in candidate_items]
    )
    for (item, candidate), (previous, candidate_id) in zip(
        candidate_items, persisted, strict=True
    ):
        if candidate.availability in _NOT_PURCHASABLE_AVAILABILITY:
            continue
        needs_detail = (
            previous is None
            or not previous.detail_verified
            or _needs_xianyu_refresh(previous, candidate)
        )
        if not needs_detail:
            continue
        if detail_query_count >= pool.candidate_budget:
            continue
        detail_query_count += 1
        try:
            detail_item = await fetch_wameiji_detail(item)
        except Exception as exc:  # noqa: BLE001 - a blocked detail is not comparable
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
            continue
        if detail_item is None or not detail_item.detail_verified:
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
        candidate = _candidate_from_market_item(pool_id, pool.media_type, item)
        if not _is_media_relevant(item, pool.media_type):
            ignored_candidate = replace(candidate, status="ignored")
            upsert_discovery_candidates_with_previous(db_path, [ignored_candidate])
            continue
        detail_persisted = upsert_discovery_candidates_with_previous(db_path, [candidate])
        _, candidate_id = detail_persisted[0]
        if candidate.availability in _NOT_PURCHASABLE_AVAILABILITY:
            continue

        query = _xianyu_query(candidate)
        if not query:
            continue
        watch = _watch_for_candidate(candidate, query)
        preflight_match = compute_match_confidence(item, watch)
        if preflight_match.fatal_flags:
            # A detail page can reveal that a collector box is only packaging
            # or that its game disc / soundtrack is missing.  No Xianyu price
            # can make an incomplete core product a valid purchase candidate.
            upsert_discovery_candidates_with_previous(
                db_path, [replace(candidate, status="ignored")]
            )
            continue
        if xianyu_blocked:
            continue

        source_edition_terms = source_edition_required_terms(candidate.title)
        initial_required_terms = (
            source_edition_terms if _is_title_only_candidate(candidate) else ()
        )
        query_variants: list[
            tuple[str, tuple[str, ...], ResolvedTitleAlias | None, bool]
        ] = [
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
        xianyu_lookup_failed = False
        while query_variants and xianyu_query_count < pool.candidate_budget:
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
            xianyu_query_count += 1
            try:
                raw_samples = await fetch_xianyu(current_query)
            except Exception as exc:  # noqa: BLE001 - browser adapters expose heterogeneous failures
                record_discovery_run(
                    db_path,
                    pool_id=pool_id,
                    source="xianyu",
                    status="human_required",
                    keyword=current_query,
                    candidate_count=1,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                if _is_xianyu_security_check(exc):
                    set_discovery_source_cooldown(
                        db_path,
                        "xianyu",
                        cooldown_seconds=_XIANYU_SECURITY_COOLDOWN_SECONDS,
                        reason="security_check",
                    )
                    xianyu_blocked = True
                xianyu_lookup_failed = True
                break

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

        if xianyu_lookup_failed:
            continue
        samples = _deduplicate_xianyu_samples(samples)
        item_id = insert_market_items(db_path, [item])[0]
        match = preflight_match
        estimate = estimate_xianyu_price(
            samples,
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
            if used_title_alias:
                positive_reasons.append("exact_public_title_alias")
            match = replace(
                match,
                confidence=max(match.confidence, _STRICT_TITLE_SAMPLE_CONFIDENCE),
                positive_reasons=positive_reasons,
            )
            estimate = estimate_xianyu_price(
                samples,
                min_reference_samples=pool.min_valid_xianyu_samples,
                edition_confidence=max(0.7, match.confidence),
                edition=watch.edition,
                required_keywords=watch.required_keywords,
                excluded_keywords=watch.excluded_keywords,
                allow_complete_game_bundles=pool.media_type == "physical_game",
                require_new_condition=_source_is_factory_new(item),
            )
        # Persist the cleaner's verdict rather than the unclassified search
        # card. The board must show why a proxy listing or a bonus-only item
        # was excluded from the reference price.
        insert_xianyu_samples(
            db_path,
            [
                *estimate.valid_samples,
                *estimate.invalid_samples,
                *rejected_title_samples,
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
        detail_query_count=detail_query_count,
        evaluated_count=evaluated_count,
        xianyu_query_count=xianyu_query_count,
        opportunity_ids=opportunity_ids,
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


def _is_media_relevant(item: MarketItem, media_type: str) -> bool:
    """Keep broad Wameiji searches from spending Xianyu reads on other goods."""

    title = str(item.title or "").casefold()
    if media_type == "physical_game" and any(
        marker in title for marker in _GAME_HARDWARE_MARKERS
    ):
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
