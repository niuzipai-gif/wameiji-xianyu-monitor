"""Flat keyword rule engine.

Ported from Usagi `src/keyword_rule_engine.py` (108 lines, 1:1).
Behavior:

  - Pure-ASCII keywords use **token boundary** regex
    ``(?<![a-z0-9])keyword(?![a-z0-9])`` to avoid false hits like
    "Q1" matching "Q1R5".
  - CJK or non-ASCII keywords use **substring** matching
    (``keyword in normalized_text``).
  - Empty search text / empty keyword list both produce
    ``is_recommended=False`` with an explanatory reason.
  - Returns a dict whose schema matches Kuro''s AI analysis result:
    ``{analysis_source, is_recommended, reason, matched_keywords,
    keyword_hit_count}`` so downstream consumers (save / notify)
    don''t need to branch on decision_mode.

Kuro extension on top of Usagi:

  - A keyword starting with ``-`` is treated as an **exclusion**: when
    any exclusion matches the search text, the result is forced to
    ``is_recommended=False`` even if required keywords matched.
  - The flat list input maps cleanly onto Kuro''s historical
    ``required_keywords + excluded_keywords`` pair (which was kept for
    back-compat). Use ``to_legacy_pair(flat)`` to derive the two legacy
    lists for any call sites still on the old API.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

_ASCII_TOKEN_KEYWORD_PATTERN = re.compile(r"^[a-z0-9 ]+$")
_ASCII_TOKEN_BOUNDARY = r"[a-z0-9]"


def normalize_text(value: str) -> str:
    """Lowercase + collapse whitespace."""
    return " ".join((value or "").lower().split())


def _collect_text_fragments(value: Any, bucket: List[str]) -> None:
    """Recursively harvest every string scalar from a nested dict/list."""
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            bucket.append(text)
        return
    if isinstance(value, (int, float, bool)):
        bucket.append(str(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_text_fragments(item, bucket)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fragments(item, bucket)


def build_search_text(record: Dict[str, Any]) -> str:
    """Flatten a Usagi-style 商品信息/卖家信息 record into a single text blob.

    Kuro''s price samples carry title + raw_text rather than nested 商品信息.
    ``build_search_text_from_strings`` is the convenience entry point for
    those callers.
    """
    fragments: List[str] = []
    product_info = record.get("商品信息") if isinstance(record, dict) else None
    seller_info = record.get("卖家信息") if isinstance(record, dict) else None
    if product_info is not None:
        _collect_text_fragments(product_info.get("商品标题"), fragments)
        _collect_text_fragments(product_info, fragments)
    if seller_info is not None:
        _collect_text_fragments(seller_info, fragments)
    return normalize_text(" ".join(fragments))


def build_search_text_from_strings(*parts: str) -> str:
    """Convenience for Kuro callers: build search text from flat string parts."""
    fragments: List[str] = []
    for part in parts:
        if part:
            fragments.append(part)
    return normalize_text(" ".join(fragments))


def _normalize_keywords(values: Iterable[str]) -> List[str]:
    """Lowercase-dedupe-preserve-order. Whitespace-only entries dropped."""
    normalized: List[str] = []
    seen = set()
    for raw in values or []:
        text = normalize_text(str(raw).strip())
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def split_required_excluded(keywords: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Split a flat list into (required, excluded) by ``-`` prefix."""
    required: List[str] = []
    excluded: List[str] = []
    for raw in keywords or []:
        text = str(raw).strip()
        if not text:
            continue
        if text.startswith("-"):
            stripped = text[1:].strip()
            if stripped:
                excluded.append(stripped)
        else:
            required.append(text)
    return required, excluded


def to_legacy_pair(flat_keywords: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Back-compat shim: map flat -> (required_keywords, excluded_keywords)."""
    return split_required_excluded(flat_keywords)


def _uses_ascii_token_match(keyword: str) -> bool:
    """Pure ASCII alnum+space keywords get token boundary; everything else substring."""
    return bool(keyword) and _ASCII_TOKEN_KEYWORD_PATTERN.fullmatch(keyword) is not None


def _keyword_matches(keyword: str, normalized_text: str) -> bool:
    if not _uses_ascii_token_match(keyword):
        return keyword in normalized_text
    pattern = rf"(?<!{_ASCII_TOKEN_BOUNDARY}){re.escape(keyword)}(?!{_ASCII_TOKEN_BOUNDARY})"
    return re.search(pattern, normalized_text) is not None


def evaluate_keyword_rules(
    keywords: List[str],
    search_text: str,
) -> Dict[str, Any]:
    """Apply a flat keyword list (with optional ``-`` exclusions) to a text blob.

    Returns a dict matching the AI analysis schema:

    .. code-block:: python

       {
         "analysis_source": "keyword",
         "is_recommended": bool,
         "reason": str,
         "matched_keywords": List[str],
         "keyword_hit_count": int,
       }
    """
    normalized_text = normalize_text(search_text)
    required, excluded = split_required_excluded(keywords)
    required = _normalize_keywords(required)
    excluded = _normalize_keywords(excluded)

    if not normalized_text:
        return {
            "analysis_source": "keyword",
            "is_recommended": False,
            "reason": "可匹配文本为空，关键词规则无法执行。",
            "matched_keywords": [],
            "keyword_hit_count": 0,
        }

    if not required and not excluded:
        return {
            "analysis_source": "keyword",
            "is_recommended": False,
            "reason": "未配置关键词规则。",
            "matched_keywords": [],
            "keyword_hit_count": 0,
        }

    matched_required = [kw for kw in required if _keyword_matches(kw, normalized_text)]
    matched_excluded = [kw for kw in excluded if _keyword_matches(kw, normalized_text)]
    hit_count = len(matched_required)
    is_recommended = bool(matched_required) and not matched_excluded

    if matched_excluded and not matched_required:
        reason = f"命中 {len(matched_excluded)} 个排除关键词：{', '.join(matched_excluded)}"
    elif matched_excluded and matched_required:
        reason = (
            f"命中 {hit_count} 个关键词 {', '.join(matched_required)}，"
            f"但排除关键词 {', '.join(matched_excluded)} 命中，整体不推荐。"
        )
    elif is_recommended:
        reason = f"命中 {hit_count} 个关键词：{', '.join(matched_required)}"
    else:
        reason = "未命中任何关键词。"

    return {
        "analysis_source": "keyword",
        "is_recommended": is_recommended,
        "reason": reason,
        "matched_keywords": matched_required,
        "keyword_hit_count": hit_count,
    }


__all__ = [
    "build_search_text",
    "build_search_text_from_strings",
    "evaluate_keyword_rules",
    "normalize_text",
    "split_required_excluded",
    "to_legacy_pair",
]
