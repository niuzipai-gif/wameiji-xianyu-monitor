"""Pure, explainable matching for approved reference-product samples.

This module deliberately has no filesystem, database, browser, or price
dependencies.  A missing reference match means only that the approved positive
sample set has no identity evidence for the candidate; it is never a rejection.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from cd_monitor.core.identifiers import extract_jan_candidates, normalize_jan

_TOKEN_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9]*|[\u3040-\u30ff]+|[\u3400-\u9fff]+")
_GENERIC_MEDIA_TOKENS: Final = frozenset(
    {
        "album",
        "blu",
        "bluray",
        "box",
        "cd",
        "disc",
        "dvd",
        "edition",
        "first",
        "limited",
        "lp",
        "media",
        "new",
        "original",
        "used",
        "version",
        "with",
        # Repeated listing-shell and taxonomy labels from the approved Xianyu
        # screenshots.  They are useful for display, but never distinguish a
        # concrete product identity.
        "明星",
        "角色",
        "存储",
        "介质",
        "时代",
        "少年团",
        "闲鱼",
        "交易",
        "须知",
        "购买",
        "退货",
        "规则",
        "保障",
        "权益",
        "搜索",
        "宝贝",
        "想要",
        "浏览",
        "能量",
        "兑换",
        "好礼",
        "邮费",
        "自理",
        "不包",
        "包邮",
        "手续费",
        "成色",
        "如图",
        "全新",
        "未拆",
        "东西",
        "齐全",
        "标题",
        "详情",
        "价格",
        "平台",
        "类型",
        "包装",
        "日版",
        "初回",
        "限定",
        "限定版",
        "特典",
        "同人",
        "音乐",
        "专辑",
        "周边",
        "画集",
        "游戏",
        "视觉",
        "小说",
        "原声",
        "正版",
        "中古",
        "商品",
        "出售",
        "卖掉",
        "私聊",
        "拍下",
    }
)
_MIN_SHARED_DISTINCTIVE_TOKENS: Final = 2


@dataclass(frozen=True)
class CandidateEvidence:
    """Identity-bearing candidate fields normalized without market values."""

    barcodes: frozenset[str]
    tokens: frozenset[str]


@dataclass(frozen=True)
class ReferenceProductEvidence:
    """A persisted positive reference product distilled for matching."""

    product_id: int
    stable_key: str
    barcode: str | None
    tokens: frozenset[str]

    def __post_init__(self) -> None:
        if type(self.product_id) is not int or self.product_id <= 0:
            raise ValueError("product_id must be a positive integer")
        if not isinstance(self.stable_key, str) or not self.stable_key.strip():
            raise ValueError("stable_key must be a non-empty string")
        if self.barcode is not None:
            normalized = _normalize_barcode(self.barcode)
            if normalized is None:
                raise ValueError("barcode must be an 8, 12, or 13 digit EAN/JAN value")
            object.__setattr__(self, "barcode", normalized)
        object.__setattr__(self, "tokens", normalize_reference_tokens(self.tokens))


@dataclass(frozen=True)
class ReferenceMatch:
    """The explainable best result for one candidate/product comparison."""

    product_id: int | None
    score: float
    match_kind: str
    evidence: dict[str, object]


def normalize_reference_tokens(value: str | frozenset[str] | set[str]) -> frozenset[str]:
    """Return Unicode-normalized, non-numeric identity tokens.

    Pure numeric strings are intentionally excluded: price values must not be
    able to create identity evidence.  Valid JAN/EAN values are represented in
    ``barcodes`` instead.
    """

    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (frozenset, set)) and all(isinstance(item, str) for item in value):
        values = tuple(value)
    else:
        raise TypeError("reference tokens must be text or a set of text values")

    tokens: set[str] = set()
    for raw_value in values:
        normalized_text = unicodedata.normalize("NFKC", raw_value).casefold()
        for token in _TOKEN_RE.findall(normalized_text):
            cleaned = token.strip("-_./")
            if len(cleaned) < 2 or cleaned.isdecimal():
                continue
            tokens.add(cleaned)
    return frozenset(tokens)


def build_candidate_evidence(
    *,
    title: str | None = None,
    artist: str | None = None,
    edition: str | None = None,
    catalog_no: str | None = None,
    jan: str | None = None,
    raw_text: str | None = None,
) -> CandidateEvidence:
    """Build candidate identity evidence from product fields only.

    Callers intentionally do not pass price, sales, or availability fields.
    ``raw_text`` is allowed for OCR/listing identity text, but standalone
    numeric values remain excluded from token evidence.
    """

    text_fields = (title, artist, edition, catalog_no, jan, raw_text)
    normalized_fields = tuple(_optional_text(value, field="candidate field") for value in text_fields)
    barcodes = _extract_barcodes(normalized_fields)
    return CandidateEvidence(
        barcodes=frozenset(barcodes),
        tokens=normalize_reference_tokens(" ".join(normalized_fields)),
    )


def score_candidate_against_product(
    candidate: CandidateEvidence, product: ReferenceProductEvidence
) -> ReferenceMatch:
    """Score one candidate against a positive reference product.

    Exact EAN/JAN is the only full-strength match.  Fallback matching requires
    at least two non-generic shared identity terms.  The score is capped below
    one so token overlap cannot be confused with exact product identity.
    """

    if not isinstance(candidate, CandidateEvidence):
        raise TypeError("candidate must be CandidateEvidence")
    if not isinstance(product, ReferenceProductEvidence):
        raise TypeError("product must be ReferenceProductEvidence")

    if product.barcode is not None and product.barcode in candidate.barcodes:
        return ReferenceMatch(
            product_id=product.product_id,
            score=1.0,
            match_kind="exact_barcode",
            evidence={"matched_barcode": product.barcode},
        )

    product_tokens = product.tokens - _GENERIC_MEDIA_TOKENS
    candidate_tokens = candidate.tokens - _GENERIC_MEDIA_TOKENS
    shared_tokens = sorted(product_tokens & candidate_tokens)
    if len(shared_tokens) < _MIN_SHARED_DISTINCTIVE_TOKENS:
        return ReferenceMatch(
            product_id=None,
            score=0.0,
            match_kind="no_match",
            evidence={"shared_tokens": shared_tokens},
        )

    product_coverage = len(shared_tokens) / len(product_tokens)
    score = round(min(0.95, 0.55 + (0.4 * product_coverage)), 4)
    return ReferenceMatch(
        product_id=product.product_id,
        score=score,
        match_kind="token_overlap",
        evidence={
            "shared_tokens": shared_tokens,
            "reference_token_coverage": round(product_coverage, 4),
        },
    )


def _optional_text(value: str | None, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text or None")
    return value


def _extract_barcodes(values: tuple[str, ...]) -> set[str]:
    barcodes: set[str] = set()
    for value in values:
        direct = _normalize_barcode(value)
        if direct is not None:
            barcodes.add(direct)
        for candidate in extract_jan_candidates(value):
            normalized = _normalize_barcode(candidate)
            if normalized is not None:
                barcodes.add(normalized)
    return barcodes


def _normalize_barcode(value: str) -> str | None:
    normalized = normalize_jan(value)
    if normalized is None or len(normalized) not in {8, 12, 13}:
        return None
    return normalized
