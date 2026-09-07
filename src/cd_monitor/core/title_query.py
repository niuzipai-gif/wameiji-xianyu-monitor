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
    re.compile(r"新品|未開封|中古|帯付き|外付|先着特典|封入特典|特典付き?"),
    re.compile(r"メガジャケ(?:付(?:き)?)?"),
    re.compile(r"トレカ(?:[Ａ-ＺA-Z]?タイプ?\d*種?)?|フォトカード(?:付)?|生写真"),
    re.compile(r"送料無料|メール便|最短翌日配達対応|楽天ブックス|オリジナルステッカー"),
    re.compile(
        r"\b(?:blu[\s-]?ray|bd|dvd|cd|disc|album|single|edition|limited|bonus)\b",
        re.IGNORECASE,
    ),
    re.compile(r"ブルーレイ|ディスク|アルバム|シングル"),
    re.compile(r"ゲームソフト|ソフト|ニンテンドー|プレイステーション"),
    re.compile(r"\b(?:nintendo|playstation|game)\b", re.IGNORECASE),
    # Eight-digit values in a rendered listing are usually source/listing IDs,
    # never a Japanese JAN (which is validated separately as 13 digits).
    re.compile(r"\b\d{8,}\b"),
)

_SPACE_RE = re.compile(r"\s+")
_SEPARATOR_RE = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+")
_CJK_OR_KANA_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LATIN_TOKEN_RE = re.compile(r"[a-z]{6,}", re.IGNORECASE)
_STORE_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{1,4}(?:-\d{1,5}){2,}(?![A-Za-z0-9])"
)
_PLATFORM_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?<![A-Za-z0-9])(?:nintendo\s*)?switch\s*(?:版)?(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
        "Switch",
    ),
    (re.compile(r"(?:ニンテンドー\s*)?スイッチ(?:版)?"), "Switch"),
    (re.compile(r"(?<![A-Za-z0-9])sw\s*版(?![A-Za-z0-9])", re.IGNORECASE), "Switch"),
    (re.compile(r"(?<![A-Za-z0-9])ns(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "Switch"),
    (
        re.compile(
            r"(?<![A-Za-z0-9])(?:ps\s*vita|playstation\s*vita|vita)(?:版)?(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
        "PS Vita",
    ),
    (re.compile(r"プレイステーション\s*(?:vita|ヴィータ)(?:版)?", re.IGNORECASE), "PS Vita"),
    (re.compile(r"(?<![A-Za-z0-9])(?:playstation\s*)?psp(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "PSP"),
    (re.compile(r"プレイステーションポータブル(?:版)?"), "PSP"),
    (re.compile(r"(?<![A-Za-z0-9])(?:nintendo\s*)?3ds(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "3DS"),
    (re.compile(r"(?<![A-Za-z0-9])(?:nintendo\s*)?(?:nds|ds(?:i)?)(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "DS"),
    (re.compile(r"(?<![A-Za-z0-9])(?:ニンテンドー\s*)?DS(?:i)?(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "DS"),
    (re.compile(r"(?<![A-Za-z0-9])gba(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "GBA"),
    (re.compile(r"ゲームボーイアドバンス(?:版)?"), "GBA"),
    (re.compile(r"(?<![A-Za-z0-9])(?:ps\s*3|playstation\s*3)(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "PS3"),
    (re.compile(r"(?<![A-Za-z0-9])(?:ps\s*4|playstation\s*4)(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "PS4"),
    (re.compile(r"(?<![A-Za-z0-9])(?:ps\s*5|playstation\s*5)(?:版)?(?![A-Za-z0-9])", re.IGNORECASE), "PS5"),
)
_PLATFORM_WORDS = {"switch", "psvita", "psp", "3ds", "ds", "gba", "ps3", "ps4", "ps5"}
_PLATFORM_MARKERS = {
    "switch": "switch",
    "psvita": "ps vita",
    "psp": "psp",
    "3ds": "3ds",
    "ds": "ds",
    "gba": "gba",
    "ps3": "ps3",
    "ps4": "ps4",
    "ps5": "ps5",
}
_PLATFORM_NORMALIZED_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:switch|ps\s*vita|psp|3ds|ds|gba|ps3|ps4|ps5)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def clean_title_search_query(value: str | None, *, max_length: int = 80) -> str:
    """Return a short, product-bearing query from a marketplace listing title.

    Listings often prepend store codes, edition, bonus-card, and shipping
    text. Passing that whole string to Xianyu weakens recall. Platform words
    stay in the query because they distinguish physical-game versions.
    An empty result means the source title held no product fingerprint strong
    enough for a safe title-only lookup.
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

    query_cleaned = _without_title_noise(query)
    text_cleaned = _without_title_noise(text)
    compact_query = _compact(query_cleaned)
    compact_text = _compact(text_cleaned)
    if not _has_distinctive_fingerprint(compact_query) or not compact_text:
        return False
    query_platforms = _platforms(query_cleaned)
    text_platforms = _platforms(text_cleaned)
    if query_platforms and (not text_platforms or query_platforms != text_platforms):
        return False
    if compact_query in compact_text:
        return True

    # Japanese game names commonly appear before or after the platform word.
    # Once matching platforms have been established, compare the actual game
    # fingerprint without that positional label so "Switch ラジルギ2" and
    # "ラジルギ2 NS" can match without admitting a different platform.
    if query_platforms:
        query_product = _compact(_without_platform_tokens(query_cleaned))
        text_product = _compact(_without_platform_tokens(text_cleaned))
        if (
            _has_distinctive_fingerprint(query_product)
            and query_product in text_product
        ):
            return True

    # Marketplace results often translate Japanese titles into Chinese while
    # preserving an official Latin title. A long shared Latin product name is
    # sufficient only after the platform check above has ruled out GBA/PSP/
    # Switch cross-version matches.
    if any(token in compact_text for token in _distinctive_latin_tokens(query_cleaned)):
        return True

    common = _longest_common_substring(compact_query, compact_text)
    if not common:
        return False
    if _CJK_OR_KANA_RE.search(common):
        return len(common) >= 4 and len(common) >= math.ceil(len(compact_query) * 0.5)
    return len(common) >= 8 and len(common) >= math.ceil(len(compact_query) * 0.55)


def _without_title_noise(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = _normalize_platform_aliases(normalized)
    normalized = _STORE_CODE_RE.sub(" ", normalized)
    for pattern in _TITLE_NOISE_PATTERNS:
        normalized = pattern.sub(" ", normalized)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    seen: set[str] = set()
    words: list[str] = []
    for word in normalized.split():
        key = word.casefold()
        if key in {"a", "b", "c", "d"} or key in seen:
            continue
        seen.add(key)
        words.append(word)
    return _SPACE_RE.sub(" ", " ".join(words)).strip()


def _normalize_platform_aliases(value: str) -> str:
    for pattern, replacement in _PLATFORM_ALIASES:
        value = pattern.sub(f" {replacement} ", value)
    return value


def _platforms(value: str) -> set[str]:
    normalized = f" {_SPACE_RE.sub(' ', _normalize_platform_aliases(value)).casefold()} "
    return {
        platform
        for platform, marker in _PLATFORM_MARKERS.items()
        if f" {marker} " in normalized
    }


def _without_platform_tokens(value: str) -> str:
    return _PLATFORM_NORMALIZED_RE.sub(" ", _normalize_platform_aliases(value))


def _distinctive_latin_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _LATIN_TOKEN_RE.findall(value)
        if token.casefold() not in _PLATFORM_WORDS
    }


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
