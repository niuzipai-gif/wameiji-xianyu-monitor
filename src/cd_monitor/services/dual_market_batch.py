"""Conservative pair selection for staged dual-market collection runs."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace

from cd_monitor.core.dual_market import (
    ListingObservation,
    classify_completeness,
    is_eligible,
    observation_from_xianyu,
)
from cd_monitor.core.identifiers import (
    extract_catalog_candidates,
    extract_jan_candidates,
    normalize_catalog_no,
    normalize_catalog_no_compact,
)
from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.core.product_images import is_usable_product_image
from cd_monitor.core.title_query import (
    clean_title_search_query,
    matches_title_search_query,
    source_edition_required_terms,
)


@dataclass(frozen=True, slots=True)
class PairSelection:
    """One lowest eligible listing per source plus the evidence gate used."""

    wameiji: ListingObservation
    xianyu: ListingObservation
    match_kind: str
    matched_listing_count: int


@dataclass(frozen=True, slots=True)
class ReverseDiscoveryCandidate:
    """One safe Xianyu card converted into a Wameiji lookup seed."""

    sample: XianyuPriceSample
    search_title: str
    seed_title: str
    catalog_no: str | None
    jan: str | None
    media_type: str
    product_fingerprint: str


@dataclass(frozen=True, slots=True)
class ReverseDiscoveryScreen:
    """Explicit accept/reject result so batch reports expose every stop."""

    candidate: ReverseDiscoveryCandidate | None
    rejection_reason: str | None = None


def build_reverse_discovery_candidate(
    sample: XianyuPriceSample,
) -> ReverseDiscoveryScreen:
    """Screen a broad Xianyu card before it can trigger a Wameiji lookup."""

    if sample.price_cny <= 0 or not str(sample.url or "").strip():
        return ReverseDiscoveryScreen(None, "missing_price_or_listing_url")
    if not is_usable_product_image(sample.image_url):
        return ReverseDiscoveryScreen(None, "missing_product_image")
    if _is_deferred_cross_border_quote(sample):
        return ReverseDiscoveryScreen(None, "cross_border_proxy_quote")
    if _is_ambiguous_multi_item_quote(sample):
        return ReverseDiscoveryScreen(None, "ambiguous_multi_item_price")
    if classify_completeness(sample.title, sample.raw_text) != "complete":
        return ReverseDiscoveryScreen(None, "incomplete_product")

    media_type = _reverse_media_type(sample.title, sample.raw_text)
    if media_type is None:
        return ReverseDiscoveryScreen(None, "not_supported_physical_media")
    if _is_digital_only(sample.title, sample.raw_text):
        return ReverseDiscoveryScreen(None, "digital_or_account_listing")

    catalog_no = _explicit_catalog_number(sample.title)
    jan = _explicit_jan(sample.title)
    search_title = _reverse_search_title(sample.title)
    if not search_title or (
        catalog_no is None and jan is None and not _is_distinct_reverse_title(search_title)
    ):
        return ReverseDiscoveryScreen(None, "no_distinct_product_title")

    fingerprint_source = jan or catalog_no or search_title
    fingerprint = _compact(fingerprint_source)
    return ReverseDiscoveryScreen(
        ReverseDiscoveryCandidate(
            sample=sample,
            search_title=search_title,
            seed_title=_reverse_seed_title(search_title, sample.title),
            catalog_no=catalog_no,
            jan=jan,
            media_type=media_type,
            product_fingerprint=fingerprint,
        )
    )


def wameiji_item_matches_seed(
    item: MarketItem,
    *,
    seed_title: str,
    query: str,
    query_kind: str,
    seed_media_type: str | None = None,
) -> bool:
    """Accept a rendered Wameiji card/detail only with exact product evidence.

    Wameiji often finds a product by JAN/catalog number but omits that number
    from the visible result title. In that case a strict title and edition
    match may retain the card. An unrelated title can never pass merely
    because it appeared on the same search page.
    """

    item_evidence = f"{item.title} {item.raw_text or ''}"
    if seed_media_type and _media_type_explicitly_conflicts(
        item_evidence, seed_media_type
    ):
        return False
    if not _numbered_release_labels_compatible(seed_title, item.title):
        return False
    if not _edition_family_required_match(seed_title, item_evidence):
        return False
    if not _edition_subvariants_match(seed_title, item_evidence):
        return False

    if query_kind == "strict_title":
        title_query = query
    else:
        if _has_exact_identifier(item, query):
            # JAN is globally assigned to a product, while short catalogue
            # codes can collide across unrelated categories (CC-1079 is both
            # a classical CD and a pastry brush).  A catalogue hit therefore
            # still needs an independent physical-media or title fingerprint.
            if extract_jan_candidates(query):
                return True
            if seed_media_type and _supports_seed_media(item, seed_media_type):
                return True
            title_query = title_query_without_identifiers(seed_title)
            return bool(
                title_query
                and matches_title_search_query(
                    item.title,
                    title_query,
                    required_any_terms=source_edition_required_terms(seed_title),
                )
            )
        title_query = clean_title_search_query(seed_title)
    return bool(
        title_query
        and matches_title_search_query(
            item.title,
            title_query,
            required_any_terms=source_edition_required_terms(seed_title),
        )
    )


def choose_xianyu_pair(
    wameiji: ListingObservation,
    samples: Iterable[XianyuPriceSample],
    *,
    captured_at: str,
    snapshot_path: str | None = None,
    screenshot_path: str | None = None,
    minimum_title_samples: int = 2,
    required_variant_title: str | None = None,
    required_media_type: str | None = None,
) -> PairSelection | None:
    """Choose the lowest same-product Xianyu listing without loose matching.

    Catalog/JAN observations use their structured base identity and retain the
    Wameiji edition, platform, and condition requirements. Title-only source
    records require multiple distinct strict title matches before they can be
    assigned a shared opaque product key.
    """

    if not is_eligible(wameiji, source="wameiji"):
        return None
    structured = _structured_base(wameiji.canonical_product_key)
    structured_candidates: list[ListingObservation] = []
    title_candidates: list[ListingObservation] = []
    seen_listing_ids: set[str] = set()
    query = title_query_without_identifiers(wameiji.title)
    edition_terms = source_edition_required_terms(wameiji.title)
    # ``required_variant_title`` comes from the exact Xianyu discovery card.
    # Keep its complete product fingerprint for validation. Reusing the short
    # recall query here can collapse a franchise release to a generic prefix
    # (for example THE IDOLM@STER) and admit a different album in that series.
    variant_query = (
        unicodedata.normalize("NFKC", required_variant_title).strip()
        if required_variant_title
        else ""
    )
    variant_edition_terms = (
        source_edition_required_terms(required_variant_title)
        if required_variant_title
        else ()
    )

    for sample in samples:
        if _is_deferred_cross_border_quote(sample) or _is_ambiguous_multi_item_quote(
            sample
        ):
            continue
        if _is_digital_only(sample.title, sample.raw_text):
            continue
        if required_media_type and _reverse_media_type(
            sample.title, sample.raw_text
        ) != required_media_type:
            continue
        sample_evidence = f"{sample.title} {sample.raw_text or ''}"
        source_region_evidence = " ".join(
            value
            for value in (wameiji.title, required_variant_title)
            if value
        )
        if not _market_regions_compatible(source_region_evidence, sample_evidence):
            continue
        if required_variant_title and not _edition_family_required_match(
            required_variant_title, sample_evidence
        ):
            continue
        if required_variant_title and not _edition_subvariants_match(
            required_variant_title, sample_evidence
        ):
            continue
        if required_variant_title and not _numbered_release_labels_compatible(
            required_variant_title, sample.title
        ):
            continue
        if not _edition_families_compatible(wameiji.title, sample_evidence):
            continue
        if not _edition_subvariants_match(wameiji.title, sample_evidence):
            continue
        if not _numbered_release_labels_compatible(wameiji.title, sample.title):
            continue
        try:
            observation = observation_from_xianyu(
                sample,
                captured_at=captured_at,
                snapshot_path=snapshot_path,
                screenshot_path=screenshot_path,
            )
        except ValueError:
            continue
        if observation.source_listing_id in seen_listing_ids:
            continue
        if observation.condition_group != wameiji.condition_group:
            continue
        if not is_eligible(observation, source="xianyu"):
            continue

        observation_base = _structured_base(observation.canonical_product_key)
        if structured and observation_base:
            if observation_base != structured:
                continue
            if not _structured_variants_match(wameiji, observation, sample.raw_text):
                continue
            structured_candidates.append(observation)
        else:
            source_title_match = bool(
                query
                and matches_title_search_query(
                    observation.title,
                    query,
                    required_any_terms=edition_terms,
                )
            )
            reverse_seed_match = bool(
                variant_query
                and matches_title_search_query(
                    observation.title,
                    variant_query,
                    required_any_terms=variant_edition_terms,
                )
            )
            if not (source_title_match or reverse_seed_match):
                continue
            title_candidates.append(observation)

        seen_listing_ids.add(observation.source_listing_id)
    minimum_titles = max(1, int(minimum_title_samples))
    if structured:
        eligible_titles = title_candidates if len(title_candidates) >= minimum_titles else []
        # When an exact JAN/catalog listing is available, do not let a cheaper
        # title-only card outrank it. The latter may be another pressing or
        # physical format with the same album name (for example an 8 cm disc).
        candidates = structured_candidates or eligible_titles
        if not candidates:
            return None
        canonical_key = str(wameiji.canonical_product_key)
    elif len(title_candidates) >= minimum_titles:
        candidates = title_candidates
        canonical_key = _title_pair_key(
            query,
            edition_terms=edition_terms,
            condition_group=wameiji.condition_group,
        )
    else:
        return None

    selected = min(candidates, key=lambda item: (item.price, item.source_listing_id))
    if selected in structured_candidates:
        match_kind = "structured_identifier"
        matched_listing_count = len(structured_candidates)
    elif structured:
        match_kind = (
            "strict_title_single"
            if len(title_candidates) == 1
            else "strict_title_corroborated"
        )
        matched_listing_count = len(title_candidates)
    else:
        match_kind = (
            "strict_title_single" if len(title_candidates) == 1 else "strict_title"
        )
        matched_listing_count = len(title_candidates)
    return PairSelection(
        wameiji=replace(wameiji, canonical_product_key=canonical_key),
        xianyu=replace(selected, canonical_product_key=canonical_key),
        match_kind=match_kind,
        matched_listing_count=matched_listing_count,
    )


def _structured_base(key: str | None) -> str | None:
    base = str(key or "").split("|", 1)[0]
    return base if base.startswith(("catalog:", "jan:")) else None


def title_query_without_identifiers(value: str) -> str:
    """Build a title fingerprint without reusing the structured lookup key."""

    title = unicodedata.normalize("NFKC", str(value or ""))
    title = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", title)
    title = re.split(r"(?:定価|参考価格|メーカー希望小売価格)", title, maxsplit=1)[0]
    title = re.sub(
        r"(?:DVD)?スリーブケース(?:付(?:き)?)?|"
        r"ミニフォトブック(?:付(?:き)?)?|セル版",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"(?<!\d)\d{8,13}(?!\d)", " ", title)
    title = re.sub(
        r"(?<![A-Za-z0-9])[A-Za-z]{2,6}[-\s]?\d{2,6}(?![A-Za-z0-9])",
        " ",
        title,
    )
    return clean_title_search_query(title)


def _has_exact_identifier(item: MarketItem, query: str) -> bool:
    query_jans = extract_jan_candidates(query)
    evidence = " ".join(
        str(value or "")
        for value in (item.catalog_no, item.jan, item.title, item.raw_text)
    )
    if query_jans:
        return query_jans[0] in extract_jan_candidates(evidence)

    query_catalog = normalize_catalog_no_compact(query).casefold()
    evidence_catalogs = {
        normalize_catalog_no_compact(value).casefold()
        for value in extract_catalog_candidates(evidence)
    }
    return bool(query_catalog and query_catalog in evidence_catalogs)


def _structured_variants_match(
    wameiji: ListingObservation,
    xianyu: ListingObservation,
    xianyu_raw_text: str | None,
) -> bool:
    source_parts = set(str(wameiji.canonical_product_key or "").split("|")[1:])
    resale_parts = set(str(xianyu.canonical_product_key or "").split("|")[1:])
    source_platform = {part for part in source_parts if part.startswith("platform:")}
    resale_platform = {part for part in resale_parts if part.startswith("platform:")}
    if source_platform and source_platform != resale_platform:
        return False

    source_editions = {part for part in source_parts if part.startswith("edition:")}
    resale_editions = {part for part in resale_parts if part.startswith("edition:")}
    if source_editions and resale_editions and source_editions != resale_editions:
        return False
    edition_terms = source_edition_required_terms(wameiji.title)
    return not edition_terms or _contains_any_term(
        f"{xianyu.title} {xianyu_raw_text or ''}", edition_terms
    )


def _contains_any_term(value: str, terms: tuple[str, ...]) -> bool:
    compact = _compact(value)
    return any(_compact(term) in compact for term in terms)


_MARKET_REGION_MARKERS: dict[str, tuple[str, ...]] = {
    "japan": (
        "日版",
        "日本版",
        "日压",
        "日壓",
        "日盤",
        "日本原版",
        "日文版",
        "日首",
        "日版首版",
        "日本首版",
        "日版初版",
        "日本初版",
    ),
    "mainland": ("内地引进", "內地引進", "大陆版", "大陸版", "国行版", "國行版", "国内引进", "國內引進"),
    "taiwan": ("台版", "臺版", "台湾版", "臺灣版"),
    "hong_kong": ("港版", "香港版"),
    "korea": ("韩版", "韓版", "韩国版", "韓國版"),
    "north_america": (
        "美版",
        "美国版",
        "美國版",
        "美国首版",
        "美國首版",
        "美首版",
        "北美版",
        "米国盤",
        "アメリカ盤",
        "US版",
        "US盤",
    ),
    "europe": ("欧版", "歐版", "欧洲版", "歐洲版", "英版", "德版"),
    "foreign_import": ("輸入盤", "輸入版", "进口盘", "進口盤", "进口版", "進口版"),
}


def _market_regions_compatible(source_text: str, resale_text: str) -> bool:
    """Reject a same-name resale card that explicitly names another pressing.

    Wameiji is a Japan-facing source, so an otherwise-unqualified Japanese
    resale label is compatible. A mainland, Taiwan, Hong Kong, Korean, US, or
    European pressing needs the same explicit family on the source evidence;
    title similarity alone cannot bridge that product identity difference.
    """

    def families(value: str) -> set[str]:
        lowered = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return {
            family
            for family, markers in _MARKET_REGION_MARKERS.items()
            if any(marker.casefold() in lowered for marker in markers)
        }

    source_families = families(source_text)
    resale_families = families(resale_text)
    if not resale_families:
        # A Japanese shop's explicit ``輸入盤`` is not evidence for any one
        # domestic pressing. A resale card must independently retain that
        # imported-edition fact before it can price the same product.
        return "foreign_import" not in source_families
    if source_families:
        return bool(source_families & resale_families)
    return resale_families == {"japan"}


def _is_deferred_cross_border_quote(sample: XianyuPriceSample) -> bool:
    """Reject proxy-service teaser prices that are not a domestic sale price."""

    value = _compact(f"{sample.title} {sample.raw_text or ''}")
    proxy_markers = ("日本代购", "日本直邮", "代购商品")
    return any(_compact(marker) in value for marker in proxy_markers)


def _is_ambiguous_multi_item_quote(sample: XianyuPriceSample) -> bool:
    """Reject a card price when one listing advertises several priced items."""

    title = unicodedata.normalize("NFKC", str(sample.title or ""))
    evidence = unicodedata.normalize(
        "NFKC", f"{sample.title or ''} {sample.raw_text or ''}"
    )
    compact = _compact(title)
    explicit_markers = (
        "标价非实际价",
        "价格见详情",
        "价格见图",
        "多款可选",
        "可单出",
        "分开出",
        "拍下不发",
        "其他盘",
        "随机发",
        "从左至右",
        "从上至下",
        "私聊选编号",
        "按编号选",
        "多张专辑",
    )
    if any(_compact(marker) in compact for marker in explicit_markers):
        return True
    if re.search(
        r"(?:合售|打包出售|多(?:张|碟|盘|款)(?:一起)?打包|"
        r"(?:两|二|2|俩)\s*(?:张|碟|盘|枚|个|款).{0,8}(?:打包|一起出)|"
        r"(?:两|二|三|四|五|六|七|八|九|[2-9])\s*"
        r"(?:张|碟|盘|枚|个|款)\s*(?:共|合计|合計)?\s*(?:¥|￥)?\d{1,4})",
        evidence,
        re.IGNORECASE,
    ):
        return True
    if len(re.findall(r"(?:^|\s)[1-9][.、．]", title)) >= 3:
        return True

    without_identifiers = re.sub(
        r"(?<![A-Za-z0-9])[A-Za-z]{2,6}[-\s]?\d{2,6}(?![A-Za-z0-9])",
        " ",
        title,
    )
    without_identifiers = re.sub(r"(?<!\d)\d{8,13}(?!\d)", " ", without_identifiers)
    amounts = [
        int(value)
        for value in re.findall(r"(?<![A-Za-z0-9])([1-9]\d{1,3})(?![A-Za-z0-9])", without_identifiers)
        if 20 <= int(value) <= 9999 and not 1900 <= int(value) <= 2099
    ]
    if len(amounts) >= 2:
        return True
    for match in re.finditer(
        r"(?:单张价格|每张|每个|单个|一张|单出)\s*(?:为|是|:|：)?\s*"
        r"(?:¥|￥)?\s*([1-9]\d{0,4}(?:\.\d+)?)\s*(?:元|块)?",
        title,
        re.IGNORECASE,
    ):
        if abs(float(match.group(1)) - float(sample.price_cny)) > 0.01:
            return True
    return False


_LEADING_REVERSE_NOISE_RE = re.compile(
    r"^(?:(?:全新(?:未拆封?|未拆)?|未拆封?|正版|正品|原版|"
    r"日版(?:正版|原装|行货)?|现货|包邮|已拆|二手)\s*)+",
    re.IGNORECASE,
)
_REVERSE_CORE_STOP_RE = re.compile(
    r"(?:\s|^)(?:日版(?:正版|原装|行货)?|初回|限定(?:版|盘)?|"
    r"(?:CD|DVD|BD)(?![A-Za-z])|Blu\s*-?\s*ray|蓝光|实体卡带|"
    r"实体\b|全新|未拆|已拆|二手|收录|盘面|碟面|外壳|"
    r"品相|成色|附件|配件|播放|包邮|盒说|编号)",
    re.IGNORECASE,
)
_PLATFORM_AT_START_RE = re.compile(
    r"^(Switch|PS\s*Vita|PSP|3DS|NDS|DS|GBA|PS[345])\s+",
    re.IGNORECASE,
)
_LABELED_CATALOG_RE = re.compile(
    r"(?:品番|品号|品號|编号|編號|型番|カタログ(?:番号|No\.?)?)\s*[:：]?\s*"
    r"([A-Za-z]{2,6})[-‐‑‒–—−\s]?(\d{2,6})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_EXPLICIT_JAN_RE = re.compile(
    r"(?:JAN|条码|條碼|商品码|商品碼)\s*(?:[:：号號为為是])?\s*(\d{13})(?!\d)",
    re.IGNORECASE,
)
_DIGITAL_ONLY_RE = re.compile(
    r"(?:下载版|下載版|数字版|數字版|兑换码|兌換碼|激活码|啟用碼|"
    r"账号|帳號|仅代码|僅代碼|自动发货|自動發貨|网盘(?:下载)?|"
    r"網盤(?:下載)?|免安装|免安裝|解压即玩|解壓即玩|"
    r"download\s*code|steam\s*key)",
    re.IGNORECASE,
)
_GAME_MEDIA_RE = re.compile(
    r"(?:\bSwitch\b|\bPS\s*Vita\b|\bPSP\b|\b3DS\b|\bNDS\b|"
    r"\bPS[345]\b|\bGBA\b|GalGame|游戏|遊戲|卡带|卡帶)",
    re.IGNORECASE,
)
_DISC_MEDIA_RE = re.compile(
    r"(?:(?<![A-Za-z])CD(?![A-Za-z])|"
    r"(?<![A-Za-z])OST(?![A-Za-z])|"
    r"(?<![A-Za-z])album(?![A-Za-z])|(?<![A-Za-z])single(?![A-Za-z])|"
    r"专辑|專輯|单曲|單曲|唱片|音像|光盘|光盤)",
    re.IGNORECASE,
)
_CASSETTE_MEDIA_RE = re.compile(
    r"(?:磁带|磁帶|カセット(?:テープ)?|audio\s*tape|cassette)",
    re.IGNORECASE,
)
_NUMBERED_RELEASE_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,})\s*"
    r"(?:[-:#]\s*)?([1-9]\d?|[IVX]{2,5})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_NUMBERED_RELEASE_IGNORED_LABELS = {"cd", "dvd", "bd", "ost", "no"}
_PRINT_MEDIA_RE = re.compile(
    r"(?:P\s*ミニアルバム|楽譜|譜面|バンド\s*スコア|スコア\s*ブック|"
    r"ピアノ(?:ソロ|弾き語り|譜)|ギター(?:弾き語り|譜)|"
    r"sheet\s*music|music\s*score|出版社|"
    r"乐谱|樂譜|曲谱|曲譜|五线谱|五線譜|钢琴谱|鋼琴譜|"
    r"吉他谱|吉他譜|琴谱|琴譜|谱集|譜集|"
    r"(?:JAN|ISBN)\s*[:：]?\s*97[89]\d{10})",
    re.IGNORECASE,
)


def _explicit_catalog_number(title: str | None) -> str | None:
    normalized_title = unicodedata.normalize("NFKC", str(title or ""))
    labeled = _LABELED_CATALOG_RE.search(normalized_title)
    if labeled is not None:
        return normalize_catalog_no(f"{labeled.group(1)}-{labeled.group(2)}")
    candidates = extract_catalog_candidates(normalized_title)
    return candidates[0] if candidates else None


def _explicit_jan(title: str | None) -> str | None:
    match = _EXPLICIT_JAN_RE.search(unicodedata.normalize("NFKC", str(title or "")))
    return match.group(1) if match else None


def _reverse_media_type(title: str | None, raw_text: str | None) -> str | None:
    normalized_title = unicodedata.normalize("NFKC", str(title or ""))
    if _CASSETTE_MEDIA_RE.search(normalized_title):
        return None
    value = unicodedata.normalize("NFKC", f"{normalized_title} {raw_text or ''}")
    if _GAME_MEDIA_RE.search(value):
        return "physical_game"
    if _DISC_MEDIA_RE.search(value):
        return "cd"
    return None


def _supports_seed_media(item: MarketItem, seed_media_type: str) -> bool:
    value = unicodedata.normalize(
        "NFKC", f"{item.title or ''} {item.raw_text or ''}"
    )
    if seed_media_type == "physical_game":
        return bool(_GAME_MEDIA_RE.search(value))
    if seed_media_type == "cd":
        if _PRINT_MEDIA_RE.search(value):
            return False
        return bool(_DISC_MEDIA_RE.search(value))
    return False


def _media_type_explicitly_conflicts(value: str, seed_media_type: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    if seed_media_type == "cd":
        return bool(_PRINT_MEDIA_RE.search(normalized))
    return False


def _numbered_release_labels_compatible(left: str, right: str) -> bool:
    """Keep numbered releases aligned when both titles reuse the same label.

    Marketplace titles frequently mix Arabic and Roman numerals (``2`` versus
    ``II``). A generic series token such as ``Concert`` must not make
    ``Concert 4`` price a card that only names another/unspecified volume.
    """

    def roman_to_int(value: str) -> int:
        values = {"I": 1, "V": 5, "X": 10}
        total = 0
        previous = 0
        for char in reversed(value.upper()):
            current = values[char]
            total += -current if current < previous else current
            previous = max(previous, current)
        return total

    def pairs(value: str) -> set[tuple[str, int]]:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        normalized = normalized.translate(
            str.maketrans({"Ø": "0", "ø": "0", "Φ": "0", "φ": "0"})
        )
        found: set[tuple[str, int]] = set()
        for label, raw_number in _NUMBERED_RELEASE_RE.findall(normalized):
            key = label.casefold()
            if key in _NUMBERED_RELEASE_IGNORED_LABELS:
                continue
            number = int(raw_number) if raw_number.isdigit() else roman_to_int(raw_number)
            found.add((key, number))
        return found

    def has_label(value: str, label: str) -> bool:
        normalized = unicodedata.normalize("NFKC", str(value or "")).translate(
            str.maketrans({"Ø": "0", "ø": "0", "Φ": "0", "φ": "0"})
        )
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])",
                normalized,
                re.IGNORECASE,
            )
        )

    left_pairs = pairs(left)
    right_pairs = pairs(right)
    for label, number in left_pairs:
        if has_label(right, label) and (label, number) not in right_pairs:
            return False
    for label, number in right_pairs:
        if has_label(left, label) and (label, number) not in left_pairs:
            return False
    return True


def _is_digital_only(title: str | None, raw_text: str | None) -> bool:
    return bool(_DIGITAL_ONLY_RE.search(f"{title or ''} {raw_text or ''}"))


def _reverse_search_title(title: str | None) -> str:
    value = unicodedata.normalize("NFKC", str(title or ""))
    value = re.sub(r"\bD\s+VD\b", "DVD", value, flags=re.IGNORECASE)
    value = re.sub(r"\bC\s+D\b", "CD", value, flags=re.IGNORECASE)
    value = re.sub(r"\bBlu\s*-\s*ray\b", "Blu-ray", value, flags=re.IGNORECASE)
    # In ``2CD+DVD`` the leading number is a disc count, not part of the album
    # name. Remove it before the media marker becomes the query stop point.
    value = re.sub(
        r"(?<![A-Za-z0-9])\d+\s*(?=(?:CD|DVD|BD)\s*\+)",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^\s*(?:【[^】]*】|\[[^\]]*\])\s*", "", value)
    value = _LEADING_REVERSE_NOISE_RE.sub("", value).strip(" ，,。;；:-")

    quoted = re.search(r"《([^》]{2,100})》", value)
    tracklist = re.search(
        r"(?:收录|收錄|曲目|歌曲目录|歌曲目錄|track\s*list)",
        value,
        re.IGNORECASE,
    )
    if quoted and tracklist and quoted.start() >= tracklist.start():
        quoted = None
    if quoted:
        prefix = re.split(r"[,，。；;]", value[: quoted.start()])[-1]
        prefix = _LEADING_REVERSE_NOISE_RE.sub("", prefix).strip(" ，,。;；:-")
        prefix = re.sub(r"(?:精选辑|精選輯|专辑|專輯|单曲|單曲)\s*$", "", prefix)
        core = f"{prefix} {quoted.group(1)}"
    else:
        platform_match = _PLATFORM_AT_START_RE.match(value)
        if platform_match:
            platform = platform_match.group(1)
            remainder = _LEADING_REVERSE_NOISE_RE.sub(
                "", value[platform_match.end() :]
            ).strip()
            stop = _REVERSE_CORE_STOP_RE.search(remainder)
            product = remainder[: stop.start()] if stop else remainder
            core = f"{platform} {product}"
        else:
            stop = _REVERSE_CORE_STOP_RE.search(value)
            core = value[: stop.start()] if stop else value

    core = re.sub(r"\b(?:19|20)\d{2}\s*年?\s*$", "", core).strip()
    return _clean_reverse_core(core)[:60].strip()


def _clean_reverse_core(value: str) -> str:
    cleaned = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = cleaned.translate(
        str.maketrans({"Ø": "0", "ø": "0", "Φ": "0", "φ": "0"})
    )
    cleaned = re.sub(
        r"(?:全新(?:未拆封?|未拆)?|未拆封?|正版|正品|原版|"
        r"日版(?:正版|原装|行货)?|初回(?:限定)?(?:版|盘|盤)?[A-D]?|"
        r"通常(?:版|盘|盤)|限定(?:版|盘|盤)[A-D]?|"
        r"(?<![A-Za-z])(?:CD|DVD|BD)(?![A-Za-z])|Blu\s*-?\s*ray|"
        r"实体卡带|實體卡帶|实体|實體)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+", " ", cleaned)
    words: list[str] = []
    seen: set[str] = set()
    for word in cleaned.split():
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        words.append(word)
    return " ".join(words)


def _is_distinct_reverse_title(query: str) -> bool:
    normalized = unicodedata.normalize("NFKC", str(query or ""))
    normalized = re.sub(
        r"(?<![A-Za-z0-9])(?:Switch|PS\s*Vita|PSP|3DS|NDS|DS|GBA|PS[345])"
        r"(?![A-Za-z0-9])",
        " ",
        normalized,
        flags=re.IGNORECASE,
    )
    latin = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", normalized)
        if token.casefold() not in {"cd", "dvd", "blu", "ray", "ost"}
    }
    cjk_or_kana = re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", normalized)
    return len(latin) >= 2 or len(cjk_or_kana) >= 3


def _reverse_seed_title(search_title: str, source_title: str | None) -> str:
    source = unicodedata.normalize("NFKC", str(source_title or ""))
    subvariant = _edition_subvariant(source) or ""
    if re.search(r"初回(?:限定)?(?:版|盘|盤)?", source, re.IGNORECASE):
        suffix = "初回限定版" + subvariant
    elif re.search(r"(?:限定|limited)(?:版|盘|盤|edition)?", source, re.IGNORECASE):
        suffix = "限定版" + subvariant
    elif re.search(r"(?:通常|普通|standard)(?:版|盘|盤|edition)?", source, re.IGNORECASE):
        suffix = "通常版"
    else:
        suffix = ""
    return " ".join(part for part in (search_title.strip(), suffix) if part)


_EDITION_SUBVARIANT_PATTERNS = (
    re.compile(
        r"(?:初回(?:生産|生产)?(?:限定)?(?:版|盤|盘)?|"
        r"限定(?:版|盤|盘))\s*(?:第\s*)?([1-9一二三]|[A-D])"
        r"(?![0-9A-Za-z\u3040-\u30ff\u3400-\u9fff])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:TYPE|タイプ)\s*[-:：]?\s*([A-D])"
        r"(?![0-9A-Za-z\u3040-\u30ff\u3400-\u9fff])",
        re.IGNORECASE,
    ),
)


def _edition_subvariant(value: str | None) -> str | None:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    for pattern in _EDITION_SUBVARIANT_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return {"一": "1", "二": "2", "三": "3"}.get(
                match.group(1), match.group(1).upper()
            )
    return None


def _edition_subvariants_match(left: str | None, right: str | None) -> bool:
    left_variant = _edition_subvariant(left)
    right_variant = _edition_subvariant(right)
    if left_variant is None:
        return True
    return right_variant == left_variant


def _edition_family(value: str | None) -> str | None:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    if re.search(r"完全(?:生産|生产)?限定(?:版|盤|盘)?", normalized, re.IGNORECASE):
        return "complete_limited"
    if re.search(
        r"(?:期間生産限定|期間限定|动画(?:限定)?(?:版|盤|盘)|"
        r"動畫(?:限定)?(?:版|盤|盘)|アニメ(?:限定)?(?:版|盤)?)",
        normalized,
        re.IGNORECASE,
    ):
        return "anime_limited"
    if re.search(
        r"(?:初回(?:生産|生产)?(?:限定)?(?:版|盤|盘)?|"
        r"首[发發](?:限定)?(?:版|盤|盘)?|first\s*press)",
        normalized,
        re.IGNORECASE,
    ):
        return "initial_limited"
    if re.search(
        r"(?:通常|普通|标准|標準|standard)(?:版|盤|盘|edition)?",
        normalized,
        re.IGNORECASE,
    ):
        return "standard"
    if re.search(
        r"(?:限定(?:版|盤|盘)|限量版|limited\s*edition)",
        normalized,
        re.IGNORECASE,
    ):
        return "limited"
    return None


def _edition_family_required_match(source: str | None, target: str | None) -> bool:
    source_family = _edition_family(source)
    return source_family is None or _edition_family(target) == source_family


def _edition_families_compatible(left: str | None, right: str | None) -> bool:
    left_family = _edition_family(left)
    right_family = _edition_family(right)
    return not (left_family and right_family) or left_family == right_family


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", normalized)


def _title_pair_key(
    query: str,
    *,
    edition_terms: tuple[str, ...],
    condition_group: str,
) -> str:
    material = "|".join((_compact(query), ",".join(edition_terms), condition_group))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"title:v1:{digest}"
