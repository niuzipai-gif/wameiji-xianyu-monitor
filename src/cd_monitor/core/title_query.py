from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

# These words describe condition, packaging, fulfilment, or a media format.
# They are useful discovery terms but cannot identify one specific CD/game in a
# Xianyu result list. Remove them before using a marketplace title as a query
# or accepting a title-only match.
_TITLE_NOISE_PATTERNS = (
    re.compile(r"【全品[^】]*】"),
    re.compile(r"完全(?:生産|生产)限定版", re.IGNORECASE),
    re.compile(
        r"初回(?:(?:生産|生产)(?:限定)?|限定)?(?:盤|盘|版)?[Ａ-ＤA-D]?|"
        r"通常(?:盤|盘|版)|限定(?:盤|盘|版)",
        re.IGNORECASE,
    ),
    re.compile(
        r"新品(?:同様)?|未開封|中古|極上美品|美品|"
        r"帯(?:付き|有り|あり|なし)?|外付|先着特典|封入特典|特典付き?"
    ),
    re.compile(r"一部未使用|未使用に近い|接近未使用|開封済み|使用済み"),
    re.compile(r"メガジャケ(?:付(?:き)?)?"),
    re.compile(r"トレカ(?:[Ａ-ＺA-Z]?タイプ?\d*種?)?|フォトカード(?:付)?|生写真"),
    re.compile(r"送料無料|メール便|最短翌日配達対応|楽天ブックス|オリジナルステッカー"),
    re.compile(
        # Japanese reseller titles commonly attach ``付`` directly to a Latin
        # format name (for example ``Blu-ray付``). ``\b`` does not see a
        # boundary before a CJK character, leaving the format in a supposedly
        # product-only query and causing a same-product result to be rejected.
        r"(?<![A-Za-z])(?:blu[\s-]?ray|bd|dvd|cd|disc|album|single|edition|limited|bonus)"
        r"(?![A-Za-z])(?:付(?:き)?|同梱)?",
        re.IGNORECASE,
    ),
    re.compile(r"ブルーレイ|ディスク|アルバム|シングル"),
    re.compile(r"国内正規品|廃盤|マキシ(?:シングル)?"),
    re.compile(r"全品\d*倍?|男性|女性"),
    re.compile(r"ゲームソフト|ソフト|ニンテンドー|プレイステーション"),
    re.compile(r"\b(?:nintendo|playstation|game)\b", re.IGNORECASE),
    # Eight-digit values in a rendered listing are usually source/listing IDs,
    # never a Japanese JAN (which is validated separately as 13 digits).
    re.compile(r"\b\d{8,}\b"),
)

_SPACE_RE = re.compile(r"\s+")
_SEPARATOR_RE = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+")
_CJK_OR_KANA_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LATIN_TOKEN_RE = re.compile(r"[a-z]{3,}", re.IGNORECASE)
_QUERY_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_HAN_ANCHOR_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_KANA_ANCHOR_RE = re.compile(r"[\u3040-\u30ff]{8,}")
_SHORT_KANA_ANCHOR_RE = re.compile(r"[\u3040-\u30ff]{3,}")
_TRACKLIST_START_RE = re.compile(
    r"(?:收录|收錄|収録|歌曲目录|歌曲目錄|曲目(?:包括|包含)?|track\s*list)",
    re.IGNORECASE,
)
_GENERIC_LATIN_TOKENS = {
    "collector",
    "collectors",
    "collection",
    "complete",
    "edition",
    "limited",
    "original",
    "special",
    "soundtrack",
    "standard",
    "version",
}
_GENERIC_KANA_ANCHORS = {
    "オリジナルサウンドトラック",
    "サウンドトラック",
}
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
_PLATFORM_WORDS = {
    "switch",
    "nintendo",
    "ns",
    "playstation",
    "psvita",
    "vita",
    "psp",
    "3ds",
    "ds",
    "gba",
    "ps3",
    "ps4",
    "ps5",
}
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
_PLATFORM_QUERY_LABELS = {
    "switch": "Switch",
    "psvita": "PS Vita",
    "psp": "PSP",
    "3ds": "3DS",
    "ds": "DS",
    "gba": "GBA",
    "ps3": "PS3",
    "ps4": "PS4",
    "ps5": "PS5",
}


@dataclass(frozen=True, slots=True)
class TitleQueryVariant:
    """A marketplace query plus product facts that each result must retain."""

    query: str
    required_any_terms: tuple[str, ...] = ()


def source_edition_required_terms(value: str | None) -> tuple[str, ...]:
    """Return the edition evidence a title-only resale sample must retain.

    Search queries intentionally drop edition words so a marketplace can still
    recall translated listings.  That is only safe when the returned listing
    then explicitly proves it is the same edition.  In particular, a standard
    game must never become a price reference for a source ``限定版``.
    """

    source = unicodedata.normalize("NFKC", str(value or ""))
    if re.search(r"完全(?:生産|生产)?限定(?:版|盤)?", source, re.IGNORECASE):
        return ("完全生产", "完全生産", "完全限定")
    if re.search(r"初回(?:限定)?(?:版|盤)?", source, re.IGNORECASE):
        # Chinese resale titles often shorten 初回限定版 to 限定版. Require a
        # real edition suffix rather than bare ``限定`` so date-limited shop
        # promotions (for example ``9/5限定``) still cannot masquerade as a
        # product edition.
        return (
            "初回",
            "首发",
            "首發",
            "first press",
            "限定版",
            "限定盘",
            "限定盤",
            "限量版",
        )
    if re.search(r"(?:限定|limited)(?:版|盤|edition)?", source, re.IGNORECASE):
        return ("限定版", "限定盘", "限定盤", "限量版", "limited edition")
    if re.search(r"(?:通常|普通|standard)(?:版|盤|edition)?", source, re.IGNORECASE):
        return ("通常", "普通", "标准", "標準", "standard")
    return ()


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
    anchor_query = _latin_han_anchor_query(cleaned)
    if anchor_query:
        return anchor_query[:max_length].strip()
    return cleaned[:max_length].strip()


def title_alias_lookup_query(value: str | None, *, max_length: int = 80) -> str:
    """Extract only the product title for a public metadata title lookup.

    Marketplace queries keep the platform because it prevents cross-platform
    pricing. A public title catalogue needs the opposite: condition, edition,
    and platform words obscure the canonical title and make exact entity
    verification less reliable.
    """

    cleaned = _without_title_noise(value)
    cleaned = _SPACE_RE.sub(" ", _without_platform_tokens(cleaned)).strip()
    return cleaned[:max_length]


def build_alias_search_query(
    alias: str | None, source_title: str | None
) -> TitleQueryVariant | None:
    """Build a conservative Xianyu query from an entity-verified title alias."""

    alias_query = clean_title_search_query(alias)
    if not alias_query:
        return None

    source = unicodedata.normalize("NFKC", str(source_title or ""))
    parts = [alias_query]
    platforms = _platforms(source)
    if len(platforms) == 1:
        parts.append(_PLATFORM_QUERY_LABELS[next(iter(platforms))])

    required_any_terms = source_edition_required_terms(source)
    if re.search(r"完全(?:生産|生产)限定", source, re.IGNORECASE):
        parts.append("完全生产限定版")
    elif "初回" in source:
        parts.append("初回限定版")
    elif "限定" in source:
        parts.append("限定版")

    return TitleQueryVariant(
        query=" ".join(parts),
        required_any_terms=required_any_terms,
    )


def matches_title_search_query(
    text: str | None,
    query: str | None,
    *,
    required_any_terms: tuple[str, ...] = (),
) -> bool:
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
        return _has_required_any_term(text, required_any_terms)

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
            return _has_required_any_term(text, required_any_terms)

    # Marketplace results often translate Japanese titles into Chinese while
    # preserving an official Latin title. A long shared Latin product name is
    # sufficient only after the platform check above has ruled out GBA/PSP/
    # Switch cross-version matches.
    latin_tokens = _distinctive_latin_token_sequence(query_cleaned)
    latin_token_set = set(latin_tokens)
    han_anchors = _han_anchors(query_cleaned)
    # A platform-only Latin token (for example ``Vita``) is not a product
    # fingerprint.  When the query is otherwise made from Han title chunks,
    # every chunk is part of the identity: sharing only the series name must
    # not let a different subtitle through.  Queries with one genuine Latin
    # product title retain the looser translated-title path used by Riviera.
    han_anchor_required = bool(han_anchors) and (
        not latin_tokens or len(latin_tokens) >= 2
    )
    han_anchor_present = all(anchor in compact_text for anchor in han_anchors)
    kana_anchors = _distinctive_kana_anchors(query_cleaned)
    short_kana_anchors = _distinctive_short_kana_anchors(query_cleaned)
    # A short English series name such as ``MONSTER HUNTER`` can be shared by
    # many unrelated soundtrack listings. When the source also gives a long,
    # non-generic Japanese variant name, require that variant instead of
    # turning loose series tags into price evidence. Three or more consecutive
    # English title words remain specific enough to support translation-only
    # listings such as ``THE LAST STORY``.
    kana_anchor_required = len(latin_tokens) < 3 and bool(kana_anchors)
    # One or two Latin words are often an artist rather than a release title.
    # When accompanied by a short Japanese title cue (RYTHEM + ホウキ雲, or
    # ORANGE RANGE + アスタリスク), retain that cue so another release by the
    # same artist cannot pass on the artist tokens alone.
    short_kana_anchor_required = (
        1 <= len(latin_tokens) <= 2
        and not query_platforms
        and bool(short_kana_anchors)
    )
    kana_anchor_present = any(anchor in compact_text for anchor in kana_anchors)
    short_kana_anchor_present = any(
        anchor in compact_text for anchor in short_kana_anchors
    )
    # Multiple English product words must stay consecutive.  Treating them as
    # an unordered bag lets unrelated listing descriptions combine “Last” and
    # “Story” from separate phrases into a false ``THE LAST STORY`` match.
    latin_phrase = "".join(latin_tokens)
    if len(latin_tokens) >= 2 and latin_phrase not in compact_text:
        return False
    text_latin_token_set = set(_distinctive_latin_token_sequence(text_cleaned))
    # Latin anchors must be complete words. Compact substring membership made
    # the short album name ``fade`` match the unrelated title ``fadeouts``.
    # Punctuation and case variants are already normalized by tokenization.
    shared_latin_tokens = latin_token_set & text_latin_token_set
    if shared_latin_tokens and len(shared_latin_tokens) >= min(2, len(latin_token_set)):
        # A short recall query such as ``X JAPAN Longing 切望`` needs the
        # Han anchor as well: otherwise an unrelated collection merely
        # listing the song Longing becomes a false same-product sample.
        if not (
            (han_anchor_required and not han_anchor_present)
            or (kana_anchor_required and not kana_anchor_present)
            or (short_kana_anchor_required and not short_kana_anchor_present)
        ):
            return _has_required_any_term(text, required_any_terms)

    # The fallback longest-common-substring check cannot bypass a Han anchor.
    # Otherwise an unrelated version that shares a long Latin title fragment
    # (for example, another X JAPAN Longing release) becomes false evidence.
    if (han_anchor_required and not han_anchor_present) or (
        kana_anchor_required and not kana_anchor_present
    ) or (
        short_kana_anchor_required and not short_kana_anchor_present
    ):
        return False

    common = _longest_common_substring(compact_query, compact_text)
    if not common:
        return False
    if _CJK_OR_KANA_RE.search(common):
        matched = len(common) >= 4 and len(common) >= math.ceil(len(compact_query) * 0.5)
    else:
        matched = len(common) >= 8 and len(common) >= math.ceil(len(compact_query) * 0.55)
    return matched and _has_required_any_term(text, required_any_terms)


def _without_title_noise(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    # Search-card titles often append a full track list. Those song names are
    # evidence about the advertised album, not alternate product titles.
    normalized = _TRACKLIST_START_RE.split(normalized, maxsplit=1)[0]
    normalized = normalized.translate(str.maketrans({"Ø": "0", "ø": "0", "Φ": "0", "φ": "0"}))
    normalized = _fold_latin_diacritics(normalized)
    normalized = _normalize_platform_aliases(normalized)
    normalized = _STORE_CODE_RE.sub(" ", normalized)
    for pattern in _TITLE_NOISE_PATTERNS:
        normalized = pattern.sub(" ", normalized)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    seen: set[str] = set()
    words: list[str] = []
    for word in normalized.split():
        key = word.casefold()
        if key in {"a", "b", "c", "d", "用"} or key in seen:
            continue
        seen.add(key)
        words.append(word)
    return _SPACE_RE.sub(" ", " ".join(words)).strip()


def _fold_latin_diacritics(value: str) -> str:
    """Keep a public Latin alias searchable without damaging Japanese kana.

    Applying NFKD to the whole title and dropping combining marks would also
    strip Japanese dakuten.  Decompose only characters explicitly named as
    Latin letters, so ``Hakuōki`` becomes ``Hakuoki`` while ``ジャンヌ`` stays
    intact.
    """

    folded: list[str] = []
    for char in value:
        if "LATIN" not in unicodedata.name(char, ""):
            folded.append(char)
            continue
        folded.extend(
            part
            for part in unicodedata.normalize("NFKD", char)
            if not unicodedata.combining(part)
        )
    return "".join(folded)


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


def _distinctive_latin_token_sequence(value: str) -> list[str]:
    return [
        token.casefold()
        for token in _LATIN_TOKEN_RE.findall(value)
        if token.casefold() not in _PLATFORM_WORDS
        and token.casefold() not in _GENERIC_LATIN_TOKENS
    ]


def _han_anchors(value: str) -> list[str]:
    return _HAN_ANCHOR_RE.findall(value)


def _distinctive_kana_anchors(value: str) -> list[str]:
    return [
        anchor
        for anchor in _KANA_ANCHOR_RE.findall(value)
        if anchor not in _GENERIC_KANA_ANCHORS
    ]


def _distinctive_short_kana_anchors(value: str) -> list[str]:
    return [
        anchor
        for anchor in _SHORT_KANA_ANCHOR_RE.findall(value)
        if anchor not in _GENERIC_KANA_ANCHORS
    ]


def _latin_han_anchor_query(value: str) -> str | None:
    """Build a short recall query when a listing has both Latin and Han title cues.

    Japanese reseller titles often include condition, store, and artist
    metadata after the product name.  On Xianyu, sending all of it can return
    unrelated cards.  The first three Latin product tokens plus a two-Han
    anchor retain the product fingerprint while remaining short enough for the
    marketplace search endpoint.
    """
    latin_tokens = [
        token
        for token in _QUERY_LATIN_TOKEN_RE.findall(value)
        if token.casefold() not in _GENERIC_LATIN_TOKENS
        and token.casefold() not in _PLATFORM_WORDS
    ]
    han_anchors = _han_anchors(value)
    if len(latin_tokens) < 2 or not han_anchors:
        return None
    return " ".join([*latin_tokens[:3], han_anchors[0][:2]])


def _compact(value: str) -> str:
    return _SEPARATOR_RE.sub("", value).casefold()


def _has_required_any_term(text: str | None, terms: tuple[str, ...]) -> bool:
    if not terms:
        return True
    compact_text = _compact(unicodedata.normalize("NFKC", str(text or "")))
    return any(_compact(term) in compact_text for term in terms)


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
