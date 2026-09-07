from __future__ import annotations

import re
from statistics import median

from cd_monitor.core.models import XianyuPriceEstimate, XianyuPriceSample
from cd_monitor.core.keyword_rule_engine import (
    evaluate_keyword_rules,
    normalize_text,
)


NOISE_KEYWORDS = ["蹲", "代拍", "代抢", "代抽", "代充", "代下", "代订", "预定", "仅展示", "不出", "无盘", "空盒", "空箱", "特典单出", "特典のみ", "BJD", "bjd", "娃衣", "娃包", "假发", "手办", "景品", "谷子", "吧唧", "亚克力", "挂件", "立牌", "色纸", "流麻", "拍立得", "明信片", "透卡", "小卡", "海报", "T恤", "短袖", "卫衣", "外套", "袜子", "鞋子", "包包", "手表", "耳机", "手机", "平板", "电脑", "相机", "镜头", "电动车", "电瓶车", "自行车", "滑板", "账号", "代练", "陪玩", "出租", "改装", "二手书", "教材", "辅导", "家教", "健身", "瑜伽", "美甲", "美睫", "宠物", "猫", "狗", "租房", "招聘", "兼职", "门票", "演唱会门票", "签名", "签售", "预售票"]
_WANTED_PREFIX_RE = re.compile(r"^\s*(?:求(?:收|购)?|收(?![录藏纳]))(?:\s|[A-Za-z0-9\u3040-\u30ff\u3400-\u9fff])")
_EXCHANGE_PREFIX_RE = re.compile(
    r"^\s*(?:求\s*)?(?:换物|换卡|换碟|换游戏|交换)(?:\s|[A-Za-z0-9\u3040-\u30ff\u3400-\u9fff])"
)
_CROSSBORDER_PROXY_MARKERS = (
    "日本代购",
    "海外代购",
    "代购商品性质特殊",
    "商品价格不含国际运费",
    "商品价格不含国际运费及税费",
    "到手价详询客服",
    "拍下后自动改价",
    "等待改价成功后再付款",
)
_BUNDLE_ACCESSORY_NOISE_KEYWORDS = {
    "谷子",
    "吧唧",
    "亚克力",
    "挂件",
    "立牌",
    "色纸",
    "流麻",
    "拍立得",
    "明信片",
    "透卡",
    "小卡",
    "海报",
}
_GAME_CORE_REFERENCE_MARKERS = (
    "switch",
    "ns",
    "游戏",
    "卡带",
    "游戏卡",
    "nintendo",
    "ps vita",
    "psp",
    "ps4",
    "ps5",
    "3ds",
    "ソフト",
)
_GAME_COMPONENT_MISSING_MARKERS = (
    "特典单出",
    "特典のみ",
    "仅特典",
    "仅出特典",
    "无卡带",
    "无盘",
    "不含游戏",
    "游戏不含",
)
_EXPLICIT_USED_CONDITION_MARKERS = (
    "二手",
    "中古",
    "已拆",
    "已开封",
    "开封品",
    "開封済",
    "使用过",
    "已使用",
    "使用済",
    "玩过",
    "卡带状态好",
    "有使用痕迹",
)

CD_MUST_HAVE = ["CD", "cd", "专辑", "磛", "盤", "盘", "唱片", "album", "Album", "ALBUM", "单曲", "ep", "EP", "原版", "正版", "日版", "台版", "韩版", "初回", "限定", "通常盤", "完全生産", "完全版"]



def clean_xianyu_samples(
    samples: list[XianyuPriceSample],
    min_valid_price_cny: float = 10,
    max_valid_price_cny: float = 2000,
    edition: str | None = None,
    required_keywords: list[str] | None = None,
    excluded_keywords: list[str] | None = None,
    allow_complete_game_bundles: bool = False,
    require_new_condition: bool = False,
) -> list[XianyuPriceSample]:
    cleaned: list[XianyuPriceSample] = []
    for sample in samples:
        reason = None
        text = f"{sample.title} {sample.raw_text or ''}"
        if sample.price_cny < min_valid_price_cny:
            reason = "invalid_extreme_low"
        elif sample.price_cny > max_valid_price_cny:
            reason = "invalid_extreme_high"
        elif _is_noise_listing(
            text, allow_complete_game_bundles=allow_complete_game_bundles
        ):
            reason = "invalid_noise"
        elif _is_crossborder_proxy_listing(text):
            reason = "invalid_proxy_listing"
        elif require_new_condition and _is_explicitly_used_listing(text):
            # A factory-new source cannot use an explicitly used local copy
            # to dilute the resale reference.  Cards that omit condition stay
            # eligible for later detail-page verification.
            reason = "invalid_condition_mismatch"
        elif _edition_mismatch(text, edition, required_keywords, excluded_keywords):
            reason = "invalid_edition_mismatch"
        cleaned.append(
            XianyuPriceSample(
                catalog_no=sample.catalog_no,
                title=sample.title,
                price_cny=sample.price_cny,
                url=sample.url,
                image_url=sample.image_url,
                image_phash=getattr(sample, "image_phash", None),
                image_dhash=getattr(sample, "image_dhash", None),
                cover_text=getattr(sample, "cover_text", None),
                seller_text=sample.seller_text,
                raw_text=sample.raw_text,
                is_valid=reason is None,
                invalid_reason=reason,
                market_item_id=sample.market_item_id,
            )
        )
    return cleaned


def _is_noise_listing(text: str, *, allow_complete_game_bundles: bool = False) -> bool:
    if _WANTED_PREFIX_RE.search(text) or _EXCHANGE_PREFIX_RE.search(text):
        return True
    lower_text = text.casefold()
    keeps_game_bundle = (
        allow_complete_game_bundles
        and any(marker in lower_text for marker in _GAME_CORE_REFERENCE_MARKERS)
        and not any(marker in text for marker in _GAME_COMPONENT_MISSING_MARKERS)
    )
    for keyword in NOISE_KEYWORDS:
        if keyword not in text:
            continue
        if keeps_game_bundle and keyword in _BUNDLE_ACCESSORY_NOISE_KEYWORDS:
            continue
        return True
    return False


def _is_crossborder_proxy_listing(text: str) -> bool:
    """Exclude another Japan-buying service from the local resale baseline."""
    return any(marker in text for marker in _CROSSBORDER_PROXY_MARKERS)


def _is_explicitly_used_listing(text: str) -> bool:
    return any(marker in text.casefold() for marker in _EXPLICIT_USED_CONDITION_MARKERS)


def estimate_xianyu_price(
    samples: list[XianyuPriceSample],
    min_valid_price_cny: float = 10,
    max_valid_price_cny: float = 2000,
    sample_limit: int | None = 15,
    min_reference_samples: int = 3,
    negotiation_discount: float = 0.92,
    liquidity_discount_default: float = 0.90,
    edition_confidence: float = 1.0,
    edition: str | None = None,
    required_keywords: list[str] | None = None,
    excluded_keywords: list[str] | None = None,
    allow_complete_game_bundles: bool = False,
    require_new_condition: bool = False,
) -> XianyuPriceEstimate:
    limited_samples = samples[:sample_limit] if sample_limit and sample_limit > 0 else samples
    cleaned = clean_xianyu_samples(
        limited_samples,
        min_valid_price_cny=min_valid_price_cny,
        max_valid_price_cny=max_valid_price_cny,
        edition=edition,
        required_keywords=required_keywords,
        excluded_keywords=excluded_keywords,
        allow_complete_game_bundles=allow_complete_game_bundles,
        require_new_condition=require_new_condition,
    )
    valid = [sample for sample in cleaned if sample.is_valid]
    invalid = [sample for sample in cleaned if not sample.is_valid]
    prices = sorted(sample.price_cny for sample in valid)
    count = len(prices)
    reference_minimum = max(1, int(min_reference_samples))
    if count >= 6:
        reference = sum(prices[1:-1]) / (count - 2)
        liquidity = "normal"
    elif count >= reference_minimum:
        reference = float(median(prices))
        liquidity = "thin"
    elif count >= 1:
        # The evaluator's price signal requires at least three clean market
        # samples.  A one- or two-item result remains visible for review, but
        # must not be promoted with a synthetic reference price.
        reference = 0.0
        liquidity = "poor"
    else:
        reference = 0.0
        liquidity = "poor"
    expected_sale = reference * negotiation_discount * liquidity_discount_default * edition_confidence
    return XianyuPriceEstimate(
        reference_price_cny=round(reference, 2),
        valid_sample_count=count,
        liquidity_status=liquidity,
        expected_sale_price_cny=round(expected_sale, 2),
        valid_samples=valid,
        invalid_samples=invalid,
    )


def _edition_mismatch(
    text: str,
    edition: str | None,
    required_keywords: list[str] | None,
    excluded_keywords: list[str] | None,
) -> bool:
    """Return True if the sample text does not match the edition/keyword rules.

    P6.x upgrade: delegates to keyword_rule_engine so ASCII keywords get token
    boundary protection (e.g. "Q1" no longer falsely matches "Q1R5"). The legacy
    ``required_keywords`` + ``excluded_keywords`` pair is mapped onto the new flat
    engine via ``to_legacy_pair`` semantics: required goes in as positives,
    excluded goes in as ``-foo`` exclusions.

    Edition is treated as a SOFT hint: only flag mismatch when the sample title
    explicitly declares a different edition (限定/初回/豪華/完全生産/期間生産/etc.).
    xianyu sellers usually omit edition markers -> assume 通常盤 compatible.
    """
    if edition:
        edition_indicators = [
            "限定", "初回", "豪華", "完全生産", "期間生産", "アナログ",
            "通常盤", "普通盤", "standard edition", "complete limited",
            "limited edition", "初回限定", "完全限定",
        ]
        conflicting = [kw for kw in edition_indicators if kw.lower() in text.lower() and kw not in edition]
        if conflicting:
            return True
    flat: list[str] = []
    for kw in required_keywords or []:
        if kw:
            flat.append(kw)
    for kw in excluded_keywords or []:
        if kw:
            flat.append("-" + kw)
    if not flat:
        return False
    decision = evaluate_keyword_rules(flat, text)
    return not decision["is_recommended"]
