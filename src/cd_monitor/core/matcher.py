from __future__ import annotations

import re

from cd_monitor.core.identifiers import (
    extract_catalog_candidates,
    extract_jan_candidates,
    normalize_catalog_no,
    normalize_catalog_no_compact,
)
from cd_monitor.core.models import MarketItem, MatchResult, WatchItem
from difflib import SequenceMatcher


def _fuzzy_album_in(title_jp: str, item_title: str, threshold: float = 0.6) -> bool:
    if not title_jp or not item_title:
        return False
    title_norm = re.sub(r's+', '', title_jp.lower())
    item_norm = re.sub(r's+', '', item_title.lower())
    if title_norm in item_norm:
        return True
    tokens = re.findall(r'[A-Za-z0-9぀-ヿ一-鿿]+', item_title)
    if not tokens:
        return False
    max_window = min(5, len(tokens))
    try:
        import pykakasi
        kks = pykakasi.kakasi()
        jp_romaji = re.sub(r's+', '', ''.join(x['hepburn'] for x in kks.convert(title_jp)).lower())
    except Exception:
        jp_romaji = ''
    for n in range(1, max_window + 1):
        for i in range(0, len(tokens) - n + 1):
            window = ''.join(tokens[i:i + n]).lower()
            window = window.replace('ー', '-').replace('−', '-')
            if not window or len(window) < 3:
                continue
            if title_norm in window or window in title_norm:
                return True
            if jp_romaji:
                try:
                    import pykakasi
                    kks = pykakasi.kakasi()
                    win_romaji = re.sub(r's+', '', ''.join(x['hepburn'] for x in kks.convert(window)).lower())
                except Exception:
                    win_romaji = ''
                if win_romaji:
                    sim = SequenceMatcher(None, jp_romaji, win_romaji).ratio()
                    if sim >= threshold:
                        return True
    return False


FATAL_KEYWORDS = {
    "only_bonus": ["仅特典", "特典のみ", "特典单出"],
    "empty_case": ["空盒", "ケースのみ"],
    "no_disc": ["無盤", "盘なし", "ディスクなし", "无盘"],
}
NEGATIVE_KEYWORDS = {
    "rental": ["レンタル", "レンタル落ち", "租赁"],
    "sample": ["見本", "サンプル", "sample"],
    "poor_condition": ["盤傷", "ケース割れ", "破損"],
}


def compute_match_confidence(item: MarketItem, watch_item: WatchItem) -> MatchResult:
    text = " ".join(
        value
        for value in [item.title, item.raw_text, item.condition_text, item.catalog_no, item.jan]
        if value
    )
    text_lower = text.lower()
    confidence = 0.4
    matched: list[str] = []
    positives: list[str] = []
    negatives: list[str] = []
    fatal: list[str] = []

    watch_jan = watch_item.jan
    if watch_jan and watch_jan in extract_jan_candidates(text):
        confidence = 1.0
        matched.append("jan_exact")
        positives.append("raw_text_contains_jan")
    else:
        catalog = normalize_catalog_no(watch_item.catalog_no)
        compact = normalize_catalog_no_compact(watch_item.catalog_no)
        candidates = extract_catalog_candidates(text)
        compact_candidates = {normalize_catalog_no_compact(candidate) for candidate in candidates}
        if catalog in candidates or normalize_catalog_no(item.catalog_no) == catalog:
            confidence = 0.85
            matched.append("catalog_no_exact")
        elif compact and compact in compact_candidates:
            confidence = 0.80
            matched.append("catalog_no_compact")
        elif compact and compact.lower() in text_lower.replace("-", "").replace(" ", ""):
            confidence = 0.65
            matched.append("catalog_no_fuzzy")

    catalog = normalize_catalog_no(watch_item.catalog_no)
    if catalog and catalog in item.title.upper():
        confidence += 0.08
        positives.append("title_contains_catalog_no")
    if watch_item.artist and watch_item.artist.lower() in text_lower:
        confidence += 0.05
        positives.append("artist_match")
    title_jp = (watch_item.title_jp or "").strip()
    title_cn = (watch_item.title_cn or "").strip()
    if title_jp and len(title_jp) >= 2 and (
        title_jp in (item.title or "")
        or _fuzzy_album_in(title_jp, item.title or "")
    ):
        confidence += 0.30
        positives.append("title_jp_match")
    if title_cn and len(title_cn) >= 2 and title_cn in (item.title or ""):
        confidence += 0.25
        positives.append("title_cn_match")
    if watch_item.edition and watch_item.edition in text:
        confidence += 0.08
        positives.append("edition_match")
    # Normalize the full text once for fuzzy matching (kana/romaji cross-script)
    _text_norm = re.sub(r'\s+', '', text.lower().replace('ー', '-'))
    for keyword in watch_item.required_keywords:
        if not keyword:
            continue
        if keyword in text:
            confidence += 0.05
            positives.append(f"required_keyword:{keyword}")
            continue
        # Fuzzy: accept when every token of the keyword appears in the text
        # OR every romaji-token of the keyword appears via sliding window with
        # SequenceMatcher similarity >= 0.7. Handles '髭男dism ESCAPARADE' matching
        # 'Official髭男dism エスカパレード CD' where ESCAPARADE exists as kana
        # (pykakasi: 'esukapareedo' vs 'escaparade' has fuzzy similarity > 0.7).
        from difflib import SequenceMatcher as _SM
        _kw_tokens = re.findall(r'[A-Za-z0-9぀-ヿ一-鿿]+', keyword)
        _all_present = bool(_kw_tokens) and all(t.lower() in _text_norm for t in _kw_tokens)
        if not _all_present:
            try:
                import pykakasi
                _kks = pykakasi.kakasi()
                _kw_romaji_tokens = [
                    ''.join(x['hepburn'] for x in _kks.convert(t)).lower()
                    for t in _kw_tokens
                ]
                _text_tokens = re.findall(r'[A-Za-z0-9぀-ヿ一-鿿]+', text)
                if _kw_romaji_tokens and _text_tokens:
                    _threshold = 0.6
                    # Convert text tokens to romaji once
                    try:
                        _text_romaji_tokens = [
                            ''.join(x['hepburn'] for x in _kks.convert(_t)).lower()
                            for _t in _text_tokens
                        ]
                    except Exception:
                        _text_romaji_tokens = [t.lower() for t in _text_tokens]
                    # Match a keyword token if any text romaji-token contains it,
                    # is contained in it, or has SequenceMatcher similarity >= threshold.
                    def _token_match(_rt, _texts):
                        for _wt in _texts:
                            if not _wt:
                                continue
                            if _rt in _wt or _wt in _rt:
                                return True
                            if _SM(None, _rt, _wt).ratio() >= _threshold:
                                return True
                        return False
                    # Count how many keyword tokens have a match somewhere in text
                    _matches = sum(1 for _rt in _kw_romaji_tokens if _token_match(_rt, _text_romaji_tokens))
                    # Accept if ALL kw tokens matched OR at least 70% matched (lenient for kanji/romaji split)
                    if _matches == len(_kw_romaji_tokens):
                        _all_present = True
                    elif _matches >= max(1, int(len(_kw_romaji_tokens) * 0.7)):
                        _all_present = True
            except Exception:
                pass
        if _all_present:
            confidence += 0.05
            positives.append(f"required_keyword_fuzzy:{keyword}")
        else:
            confidence -= 0.25
            negatives.append(f"missing_required_keyword:{keyword}")

    for flag, keywords in FATAL_KEYWORDS.items():
        if any(keyword.lower() in text_lower for keyword in keywords):
            fatal.append(flag)
            negatives.append(flag)
            confidence -= 0.60 if flag != "no_disc" else 0.70
    for reason, keywords in NEGATIVE_KEYWORDS.items():
        if any(keyword.lower() in text_lower for keyword in keywords):
            negatives.append(reason)
            confidence -= 0.40 if reason == "rental" else 0.30 if reason == "sample" else 0.20
    for keyword in watch_item.excluded_keywords:
        if keyword and keyword.lower() in text_lower:
            negatives.append(f"excluded_keyword:{keyword}")
            confidence -= 0.25

    return MatchResult(
        confidence=round(min(1.0, max(0.0, confidence)), 4),
        matched_identifiers=matched,
        positive_reasons=positives,
        negative_reasons=negatives,
        fatal_flags=fatal,
    )

