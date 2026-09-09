"""Source-neutral facts and eligibility rules for the dual-market board.

An observation is one rendered listing seen at one time.  It deliberately does
not contain a matched price from the other marketplace: comparisons are built
later from two independently eligible observations.
"""

from __future__ import annotations

import re
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


# Risk markers deliberately take precedence over new/sealed labels. A listing
# can be unopened while its box is damaged, and that is not interchangeable
# with an ordinary new listing.
_RISK_CONDITION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "defective",
        (
            "ジャンク",
            "動作不良",
            "読み込み不良",
            "再生不良",
            "起動不可",
            "故障",
            "不良品",
            "defective",
            "faulty",
            "not working",
            "for parts",
            "故障品",
            "功能异常",
            "功能異常",
            "无法播放",
            "無法播放",
            "无法读取",
            "無法讀取",
            "无法使用",
            "無法使用",
            "坏了",
            "壞了",
        ),
    ),
    (
        "untested",
        (
            "動作未確認",
            "動作確認未",
            "未動作確認",
            "未チェック",
            "未検品",
            "未検証",
            "untested",
            "not tested",
            "未测试",
            "未測試",
            "功能未测",
            "功能未測",
            "未检测",
            "未檢測",
            "不保证正常播放",
            "不保證正常播放",
            "不保证播放",
            "不保證播放",
            "不保读取",
            "不保讀取",
            "未试听",
            "未試聽",
        ),
    ),
    (
        "box_damage",
        (
            "箱潰れ",
            "箱つぶれ",
            "箱破れ",
            "外箱潰",
            "外箱に潰",
            "外箱傷",
            "外箱に傷",
            "ケース割れ",
            "ケース傷",
            "ケースに傷",
            "box damage",
            "damaged box",
            "case cracked",
            "盒损",
            "盒損",
            "外盒有压",
            "外盒有壓",
            "外盒破",
            "包装破损",
            "包裝破損",
            "盒子破",
            "盒子压",
            "盒子壓",
        ),
    ),
    (
        "heavy_damage",
        (
            "傷が多",
            "大きな傷",
            "深い傷",
            "目立つ傷",
            "ひどい傷",
            "破損",
            "割れ",
            "heavily scratched",
            "major scratch",
            "serious damage",
            "严重划痕",
            "嚴重劃痕",
            "重度划痕",
            "重度劃痕",
            "多处划痕",
            "多處劃痕",
            "明显划痕",
            "明顯劃痕",
            "严重瑕疵",
            "嚴重瑕疵",
            "破损",
            "品相不佳",
            "品相差",
            "整体状态不佳",
            "整體狀態不佳",
        ),
    ),
    (
        "minor_damage",
        (
            "盤傷",
            "傷あり",
            "キズ",
            "擦り傷",
            "スレ",
            "汚れ",
            "使用感",
            "minor scratch",
            "scratched",
            "scuff",
            "stain",
            "轻微划痕",
            "輕微劃痕",
            "划痕",
            "劃痕",
            "轻微瑕疵",
            "輕微瑕疵",
            "小瑕疵",
            "磨损",
            "磨損",
            "污渍",
            "污漬",
            "使用痕迹",
            "使用痕跡",
            "瑕疵",
        ),
    ),
)

_NEGATED_RISK_MARKERS = (
    "映像不良等でない限り",
    "動作不良等でない限り",
    "目立った傷や汚れなし",
    "目立つ傷や汚れなし",
    "傷や汚れなし",
    "傷・汚れなし",
    "傷なし",
    "キズなし",
    "汚れなし",
    "没有明显的损伤或污渍",
    "没有明显损伤或污渍",
    "无明显损伤或污渍",
)


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
    id: int | None = None


@dataclass(frozen=True, slots=True)
class DualMarketCostConfig:
    """Explicit user-supplied inputs for a conservative resale calculation.

    All fields intentionally default to ``None``.  A zero is valid only when
    the user explicitly supplies it (for example a legally applicable tax
    exemption), which lets the service distinguish zero from unknown.
    """

    exchange_rate_cny_per_jpy: float | None = None
    japan_domestic_shipping_jpy: float | None = None
    proxy_fee_jpy: float | None = None
    international_shipping_per_item_cny: float | None = None
    china_reship_cny: float | None = None
    packaging_cny: float | None = None
    after_sale_reserve_cny: float | None = None
    risk_reserve_cny: float | None = None
    tax_cny: float | None = None
    sales_fee_rate: float | None = None
    sales_fee_cap_cny: float | None = None


@dataclass(frozen=True, slots=True)
class PriceComparison:
    """A frozen calculation tied to exactly one listing from each market."""

    canonical_product_key: str
    wameiji_observation_id: int
    xianyu_observation_id: int
    cost_config_json: str
    landed_cost_cny: float | None
    sale_price_cny: float
    expected_profit_cny: float | None
    net_margin: float | None
    status: Literal["ready", "negative_profit", "cost_pending"]
    id: int | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonOutcome:
    """A board-safe result: waiting states never invent a profit card."""

    status: Literal[
        "ready",
        "negative_profit",
        "cost_pending",
        "waiting_wameiji",
        "waiting_xianyu",
    ]
    comparison: PriceComparison | None = None


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
    # A marketplace's labeled condition is the authoritative product scope.
    # Full detail text can contain unrelated recommendation titles (including
    # words such as ``新品``), so appending it would silently turn an explicit
    # used listing into a new one. Fall back to the page text only when the
    # detail parser could not isolate a condition field.
    condition_evidence = item.condition_text or item.raw_text or item.title
    condition_group = normalize_condition_group(condition_evidence)
    if item.condition_text and item.raw_text:
        raw_group = normalize_condition_group(item.raw_text)
        if raw_group in {
            "defective",
            "untested",
            "box_damage",
            "heavy_damage",
            "minor_damage",
        }:
            # Explicit defects in the product description still outrank a
            # benign selector label; only generic new/sealed words from
            # recommendation copy are ignored.
            condition_group = raw_group
    return ListingObservation(
        source="wameiji",
        source_listing_id=_source_listing_id(item.external_item_id, url),
        canonical_product_key=canonical_product_key(
            item.catalog_no,
            item.jan,
            item.title,
            condition_group=condition_group,
        ),
        title=_require_text(item.title, field="title"),
        price=float(item.price),
        currency=_require_text(item.currency, field="currency").upper(),
        url=url,
        image_url=_require_http_url(item.image_url, base_url=url),
        availability=item.availability,
        condition_group=condition_group,
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
    condition_group = normalize_condition_group(sample.raw_text)
    return ListingObservation(
        source="xianyu",
        source_listing_id=_source_listing_id(None, url),
        canonical_product_key=canonical_product_key(
            sample.catalog_no,
            None,
            sample.title,
            condition_group=condition_group,
        ),
        title=_require_text(sample.title, field="title"),
        price=float(sample.price_cny),
        currency="CNY",
        url=url,
        image_url=_require_http_url(sample.image_url, base_url=url),
        availability="available" if sample.is_valid else "unavailable",
        condition_group=condition_group,
        completeness=classify_completeness(sample.title, sample.raw_text),
        evidence_level=evidence_level,
        captured_at=_require_text(captured_at, field="captured_at"),
        raw_snapshot_path=snapshot_path,
        screenshot_path=screenshot_path,
    )


def canonical_product_key(
    catalog_no: str | None,
    jan: str | None,
    title: str | None,
    *,
    condition_group: str | None = None,
) -> str | None:
    """Return an identifier-backed, saleable-variant key.

    A title alone remains insufficient.  When a title explicitly names an
    edition or platform, that fact becomes part of the key; missing metadata
    therefore creates a waiting state instead of a loose cross-variant match.
    Non-used condition groups are also distinct saleable variants.
    """

    normalized_jan = normalize_jan(jan)
    if normalized_jan:
        base = f"jan:{normalized_jan}"
    else:
        base = None
    for text in (catalog_no, title) if base is None else ():
        jan_candidates = extract_jan_candidates(text)
        if jan_candidates:
            base = f"jan:{jan_candidates[0]}"
            break
    for text in (catalog_no, title) if base is None else ():
        catalog_candidates = extract_catalog_candidates(text)
        if catalog_candidates:
            base = f"catalog:{normalize_catalog_no_compact(catalog_candidates[0]).lower()}"
            break
    if base is None:
        return None
    return "|".join((base, *_variant_discriminators(title, condition_group)))


def _variant_discriminators(title: str | None, condition_group: str | None) -> list[str]:
    value = (title or "").casefold()
    discriminators: list[str] = []
    for marker, label in (
        ("完全生産限定", "complete_limited"),
        ("初回限定", "initial_limited"),
        ("限定版", "limited"),
        ("限定盤", "limited"),
        ("通常版", "standard"),
        ("通常盤", "standard"),
    ):
        if marker in value:
            discriminators.append(f"edition:{label}")
            break
    for marker, label in (
        ("nintendo switch", "switch"),
        ("switch", "switch"),
        ("playstation 5", "ps5"),
        ("ps5", "ps5"),
        ("playstation 4", "ps4"),
        ("ps4", "ps4"),
        ("playstation vita", "psvita"),
        ("psvita", "psvita"),
        ("ps vita", "psvita"),
        ("psp", "psp"),
        ("windows", "windows"),
        (" pc", "windows"),
    ):
        if marker in value:
            discriminators.append(f"platform:{label}")
            break
    if condition_group and condition_group != "complete_used":
        discriminators.append(f"condition:{condition_group}")
    return discriminators


def normalize_condition_group(condition_text: str | None) -> str:
    """Use conservative condition buckets until detail evidence says otherwise."""

    value = (condition_text or "").casefold()
    risk_value = value
    for marker in _NEGATED_RISK_MARKERS:
        risk_value = risk_value.replace(marker, "")
    for group, markers in _RISK_CONDITION_GROUPS:
        if any(marker in risk_value for marker in markers):
            return group
    if any(marker in value for marker in ("新品", "new", "brand new")):
        return "new"
    if any(marker in value for marker in ("未開封", "未拆", "sealed")):
        return "sealed"
    return "complete_used"


def classify_completeness(title: str | None, raw_text: str | None) -> str:
    """Exclude explicit shell/bonus-only or missing-core-media records."""

    normalized_title = str(title or "")
    if re.search(
        r"(?:^|】)\s*(?:コレクションカード|トレーディングカード|"
        r"生写真|ブロマイド)",
        normalized_title,
    ):
        return "incomplete"
    if re.search(
        r"(?:トレカ|フォトカード|photocard|写真卡|小卡|卡片)",
        normalized_title,
        re.IGNORECASE,
    ) and not re.search(
        r"(?:(?<![A-Za-z])(?:CD|DVD|BD)(?![A-Za-z])|ゲームソフト|"
        r"游戏卡|遊戲卡|卡带|卡帶|ソフト)",
        normalized_title,
        re.IGNORECASE,
    ):
        return "incomplete"
    value = f"{title or ''} {raw_text or ''}".casefold()
    incomplete_markers = (
        "外箱のみ",
        "箱のみ",
        "ケースのみ",
        "特典のみ",
        "カードのみ",
        "帯のみ",
        "帯だけ",
        "ジャケットのみ",
        "ジャケットだけ",
        "ブックレットのみ",
        "ブックレットだけ",
        "歌詞カードのみ",
        "歌詞カードだけ",
        "ディスクなし",
        "本体なし",
        "空盒",
        "仅外盒",
        "仅特典",
        "仅卡",
        "缺盘",
        "缺少本体",
        "没有cd",
        "无cd",
        "不含cd",
        "不带cd",
        "没有游戏卡",
        "无游戏卡",
        "トレカのみ",
        "フォトカードのみ",
        "フォトカード1枚のみ",
        "不含游戏卡",
        "只出特典",
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
