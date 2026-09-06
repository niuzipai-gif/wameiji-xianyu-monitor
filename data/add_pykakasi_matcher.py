import sys
file_path = sys.argv[1] if len(sys.argv) > 1 else 'match_real_v2.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Add pykakasi-based fuzzy matcher near the top of match_real_market_items
helper = '''def _kks_to_romaji(s: str) -> str:
    """Convert katakana/hiragana to romaji. Falls back to lowercase if pykakasi unavailable."""
    try:
        import pykakasi
        kks = pykakasi.kakasi()
        return ''.join(x['hepburn'] for x in kks.convert(s)).lower()
    except Exception:
        return s.lower()

def _fuzzy_album_match(title_jp: str, item_title: str, threshold: float = 0.6) -> bool:
    """Check if watchlist title_jp appears (fuzzy) in item title.
    Handles Japanese kana/romaji mismatch by converting both sides to romaji."""
    if not title_jp or not item_title:
        return False
    import re
    title_jp_clean = re.sub(r'\s+', '', title_jp.lower())
    item_clean = re.sub(r'\s+', '', item_title.lower())
    item_clean = item_clean.replace('ー', '-').replace('−', '-')
    # Direct substring match
    if title_jp_clean in item_clean:
        return True
    # Romaji-based fuzzy match
    try:
        from difflib import SequenceMatcher
        jp_romaji = re.sub(r'\s+', '', _kks_to_romaji(title_jp))
        item_romaji = re.sub(r'\s+', '', _kks_to_romaji(item_clean))
        if jp_romaji and item_romaji:
            sim = SequenceMatcher(None, jp_romaji, item_romaji).ratio()
            if sim >= threshold:
                return True
    except Exception:
        pass
    return False

'''

if '_fuzzy_album_match' not in content:
    # Insert before match_real_market_items
    marker = 'def match_real_market_items(db_path: str = DB, only_catalog: str | None = None) -> dict:'
    content = content.replace(marker, helper + marker)
    print('OK added fuzzy matcher helper')
else:
    print('helper already exists')

# Now update p1_strong logic to use fuzzy matcher
old_p1_strong = '''                # P2c: compute p1_strong set - catalogs where BOTH watchlist artist AND title_jp
                # appear in item title (highest name-match signal)
                p1_strong = set()
                if item_title:
                    item_compact_strong = "".join(item_title.split()).lower()
                    for _wcat, _wrow in watch_by_catalog.items():
                        _artist = (_wrow.get("artist") or "").strip()
                        _title_jp = (_wrow.get("title_jp") or "").strip()
                        if _artist and _title_jp and len(_artist) >= 2 and len(_title_jp) >= 2:
                            _a = "".join(_artist.split()).lower()
                            _t = "".join(_title_jp.split()).lower()
                            if _a in item_compact_strong and _t in item_compact_strong:
                                p1_strong.add(_wcat)'''
new_p1_strong = '''                # P2c: compute p1_strong set - catalogs where BOTH watchlist artist AND title_jp
                # appear in item title (highest name-match signal)
                # Uses fuzzy romaji matching to handle Japanese kana/romaji variations
                p1_strong = set()
                if item_title:
                    item_compact_strong = item_title_norm.replace("ー", "-")
                    for _wcat, _wrow in watch_by_catalog.items():
                        _artist = (_wrow.get("artist") or "").strip()
                        _title_jp = (_wrow.get("title_jp") or "").strip()
                        if _artist and _title_jp and len(_artist) >= 2 and len(_title_jp) >= 2:
                            _a = "".join(_artist.split()).lower()
                            if _a in item_compact_strong:
                                if _fuzzy_album_match(_title_jp, item_title, threshold=0.6):
                                    p1_strong.add(_wcat)'''
if old_p1_strong in content:
    content = content.replace(old_p1_strong, new_p1_strong)
    print('OK updated p1_strong to use fuzzy matcher')
else:
    print('p1_strong NOT FOUND')

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
