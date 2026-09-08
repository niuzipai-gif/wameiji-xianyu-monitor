"""Source-neutral facts and eligibility rules for the dual-market board.

An observation is one rendered listing seen at one time.  It deliberately does
not contain a matched price from the other marketplace: comparisons are built
later from two independently eligible observations.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlparse

from cd_monitor.core.identifiers import (
    extract_catalog_candidates,
    extract_jan_candidates,
    normalize_catalog_no_compact,
    normalize_jan,
)
from cd_monitor.core.models import MarketItem, XianyuPriceSample

MarketSource = Literal["wameiji", "xianyu"]
EvidenceLevel = Literal["search_card", "detail_verified"]


@dataclass(frozen=True, slots=True)
class ListingObservation:
    """Immutable evidence collected from one visible marketplace listing."""

    source: MarketSource
    source_listing_id: str
    canonical_product_key: str | None
    title: str
    price: float
    currency: str
    url: str
    image_url: str
    availability: str
    condition_group: str
    completeness: str
    evidence_level: EvidenceLevel
    captured_at: str
    raw_snapshot_path: str | None = None
    screenshot_path: str | None = None
    source_detail_fee: float | None = None


def is_eligible(observation: ListingObservation, *, source: MarketSource) -> bool:
    """Return whether an observation may participate in that side's lowest price.

    Wameiji's search cards are discovery hints only.  Xianyu search cards are
    valid asking-price evidence but remain labelled as such in later views.
    """

    if observation.source != source:
        return False
    if observation.availability != "available":
        return False
    if observation.completeness != "complete":
        return False
    if observation.price <= 0:
        return False
    if not all((observation.title.strip(), observation.url.strip(), observation.image_url.strip())):
        return False
    return source != "wameiji" or observation.evidence_level == "detail_verified"


def select_lowest_eligible(
    observations: Iterable[ListingObservation], *, source: MarketSource
) -> ListingObservation | None:
    """Choose the lowest valid current listing for one marketplace only."""

    candidates = [item for item in observations if is_eligible(item, source=source)]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.price, item.source_listing_id))


def observation_from_wameiji(
    item: MarketItem,
    *,
    captured_at: str,
    snapshot_path: str | None = None,
    screenshot_path: str | None = None,
) -> ListingObservation:
    """Normalize one Wameiji parser result without inventing cross-site facts."""

    if item.source != "wameiji":
        raise ValueError("Wameiji observations require a wameiji MarketItem")
    url = _require_http_url(item.url, base_url="https://meruki.cn")
    return ListingObservation(
        source="wameiji",
        source_listing_id=_source_listing_id(item.external_item_id, url),
        canonical_product_key=canonical_product_key(item.catalog_no, item.jan, item.title),
        title=_require_text(item.title, field="title"),
        price=float(item.price),
        currency=_require_text(item.currency, field="currency").upper(),
        url=url,
        image_url=_require_http_url(item.image_url, base_url=url),
        availability=item.availability,
        condition_group=normalize_condition_group(item.condition_text),
        completeness=classify_completeness(item.title, item.raw_text),
        evidence_level="detail_verified" if item.detail_verified else "search_card",
        captured_at=_require_text(captured_at, field="captured_at"),
        raw_snapshot_path=snapshot_path,
        screenshot_path=screenshot_path or item.screenshot_path,
        source_detail_fee=item.japan_domestic_shipping_jpy,
    )


def observation_from_xianyu(
    sample: XianyuPriceSample,
    *,
    captured_at: str,
    snapshot_path: str | None = None,
    screenshot_path: str | None = None,
    evidence_level: EvidenceLevel = "search_card",
) -> ListingObservation:
    """Normalize one Xianyu listing card as its own asking-price observation."""

    url = _require_http_url(sample.url, base_url="https://www.goofish.com")
    return ListingObservation(
        source="xianyu",
        source_listing_id=_source_listing_id(None, url),
        canonical_product_key=canonical_product_key(sample.catalog_no, None, sample.title),
        title=_require_text(sample.title, field="title"),
        price=float(sample.price_cny),
        currency="CNY",
        url=url,
        image_url=_require_http_url(sample.image_url, base_url=url),
        availability="available" if sample.is_valid else "unavailable",
        condition_group=normalize_condition_group(sample.raw_text),
        completeness=classify_completeness(sample.title, sample.raw_text),
        evidence_level=evidence_level,
        captured_at=_require_text(captured_at, field="captured_at"),
        raw_snapshot_path=snapshot_path,
        screenshot_path=screenshot_path,
    )


def canonical_product_key(
    catalog_no: str | None, jan: str | None, title: str | None
) -> str | None:
    """Return only an identifier-backed key; titles alone stay unpaired."""

    normalized_jan = normalize_jan(jan)
    if normalized_jan:
        return f"jan:{normalized_jan}"
    for text in (catalog_no, title):
        jan_candidates = extract_jan_candidates(text)
        if jan_candidates:
            return f"jan:{jan_candidates[0]}"
    for text in (catalog_no, title):
        catalog_candidates = extract_catalog_candidates(text)
        if catalog_candidates:
            return f"catalog:{normalize_catalog_no_compact(catalog_candidates[0]).lower()}"
    return None


def normalize_condition_group(condition_text: str | None) -> str:
    """Use conservative condition buckets until detail evidence says otherwise."""

    value = (condition_text or "").casefold()
    if any(marker in value for marker in ("新品", "new", "brand new")):
        return "new"
    if any(marker in value for marker in ("未開封", "未拆", "sealed")):
        return "sealed"
    return "complete_used"


def classify_completeness(title: str | None, raw_text: str | None) -> str:
    """Exclude explicit shell/bonus-only or missing-core-media records."""

    value = f"{title or ''} {raw_text or ''}".casefold()
    incomplete_markers = (
        "外箱のみ",
        "箱のみ",
        "ケースのみ",
        "特典のみ",
        "カードのみ",
        "ディスクなし",
        "本体なし",
        "空盒",
        "仅外盒",
        "仅特典",
        "仅卡",
        "缺盘",
        "缺少本体",
    )
    return "incomplete" if any(marker in value for marker in incomplete_markers) else "complete"


def _require_text(value: str | None, *, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field} is required for a listing observation")
    return text


def _require_http_url(value: str | None, *, base_url: str) -> str:
    raw = _require_text(value, field="url")
    if raw.startswith("//"):
        raw = "https:" + raw
    normalized = urljoin(base_url, raw)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("listing observation requires an HTTP(S) URL")
    return normalized


def _source_listing_id(explicit_id: str | None, url: str) -> str:
    if explicit_id and explicit_id.strip():
        return explicit_id.strip()
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for field in ("id", "itemId", "item_id", "goodsId", "goods_id"):
        values = query.get(field)
        if values and values[0].strip():
            return values[0].strip()
    last_path_part = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if last_path_part and last_path_part not in {"item", "detail"}:
        return last_path_part
    raise ValueError("listing observation requires a source listing id or identifiable URL")
