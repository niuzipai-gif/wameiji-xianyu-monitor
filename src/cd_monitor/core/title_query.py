from __future__ import annotations

import math
import re
import unicodedata

# These words describe condition, packaging, fulfilment, or a media format.
# They are useful discovery terms but cannot identify one specific CD/game in a
# Xianyu result list. Remove them before using a marketplace title as a query
# or accepting a title-only match.
_TITLE_NOISE_PATTERNS = (
    re.compile(
        r"初回(?:限定)?盤[Ａ-ＤA-D]?|初回限定版[Ａ-ＤA-D]?|初回限定|通常盤|限定盤|限定版",
        re.IGNORECASE,
    ),
    re.compile(r"新品(?:未開封)?|中古|帯付き|外付|先着特典|封入特典|特典付き?"),
    re.compile(r"トレカ(?:[Ａ-ＺA-Z]?タイプ?\d*種?)?|フォトカード(?:付)?|生写真"),
    re.compile(r"送料無料|メール便|最短翌日配達対応|楽天ブックス|オリジナルステッカー"),
    re.compile(
        r"\b(?:blu[\s-]?ray|bd|dvd|cd|disc|album|single|edition|limited|bonus)\b",
        re.IGNORECASE,
    ),
    re.compile(r"ブルーレイ|ディスク|アルバム|シングル"),
    re.compile(r"ゲームソフト|ソフト|ニンテンドー|プレイステーション"),
    re.compile(
        r"\b(?:nintendo|switch|playstation|ps\s?vita|psp|ps[345]|3ds|wii|xbox|game)\b",
        re.IGNORECASE,
    ),
    # Eight-digit values in a rendered listing are usually source/listing IDs,
    # never a Japanese JAN (which is validated separately as 13 digits).
    re.compile(r"\b\d{8,}\b"),
)

_SPACE_RE = re.compile(r"\s+")
_SEPARATOR_RE = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+")
_CJK_OR_KANA_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def clean_title_search_query(value: str | None, *, max_length: int = 80) -> str:
    """Return a short, product-bearing query from a marketplace listing title.

    Listings often prepend edition, bonus-card, and shipping text. Passing
    that whole string to Xianyu both weakens result quality and makes the
    later exact-title filter reject valid listings that phrase conditions
    differently. An empty result means the source title held no product
    fingerprint strong enough for a safe title-only lookup.
    """

    cleaned = _without_title_noise(value)
    if not _has_distinctive_fingerprint(_compact(cleaned)):
        return ""
    return cleaned[:max_length].strip()


def matches_title_search_query(text: str | None, query: str | None) -> bool:
    """Conservatively match a title-only Xianyu result to a source query.

    Exact catalog/JAN lookups are handled separately. For a title query,
    accept a full product fingerprint or a long shared product fragment after
    removing edition/format noise. Generic terms alone never qualify.
    """

    compact_query = _compact(_without_title_noise(query))
    compact_text = _compact(_without_title_noise(text))
    if not _has_distinctive_fingerprint(compact_query) or not compact_text:
        return False
    if compact_query in compact_text:
        return True

    common = _longest_common_substring(compact_query, compact_text)
    if not common:
        return False
    if _CJK_OR_KANA_RE.search(common):
        return len(common) >= 4 and len(common) >= math.ceil(len(compact_query) * 0.5)
    return len(common) >= 8 and len(common) >= math.ceil(len(compact_query) * 0.55)


def _without_title_noise(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    for pattern in _TITLE_NOISE_PATTERNS:
        normalized = pattern.sub(" ", normalized)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    words = [word for word in normalized.split() if word.casefold() not in {"a", "b", "c", "d"}]
    return _SPACE_RE.sub(" ", " ".join(words)).strip()


def _compact(value: str) -> str:
    return _SEPARATOR_RE.sub("", value).casefold()


def _has_distinctive_fingerprint(compact: str) -> bool:
    return len(compact) >= 6 or len(_CJK_OR_KANA_RE.findall(compact)) >= 3


def _longest_common_substring(left: str, right: str) -> str:
    """Return one longest contiguous shared fragment with bounded memory."""

    if len(left) > len(right):
        left, right = right, left
    previous = [0] * (len(left) + 1)
    best_length = 0
    best_end = 0
    for right_index, right_char in enumerate(right, start=1):
        current = [0] * (len(left) + 1)
        for left_index, left_char in enumerate(left, start=1):
            if left_char != right_char:
                continue
            current[left_index] = previous[left_index - 1] + 1
            if current[left_index] > best_length:
                best_length = current[left_index]
                best_end = right_index
        previous = current
    return right[best_end - best_length : best_end]
