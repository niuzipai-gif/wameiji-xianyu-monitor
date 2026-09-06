#!/usr/bin/env python3
"""ocr_cover.py - Perceptual cover-text extraction & cross-platform validation.

For each market_items row with image_url:
  1. Resolve the highest-resolution variant of the URL (e.g., mercari
     /thumb/.../240x240 -> /item/detail/orig/photos/.../1080x1080).
  2. Run tesseract (jpn+eng+chi_sim) to extract cover text.
  3. Cache the text in `cover_text` column for reuse.

For cross-platform matching:
  - Two items are "visually same album" if their cover_text share many
    tokens (jaccard >= 0.20 OR exact-substring >= 3 Japanese characters).
  - Confidence boost: +0.10 when this fires.
"""
from __future__ import annotations

import io
import os
import re
import sys
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))
import phash_compute  # noqa: E402

try:
    import pytesseract
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
except Exception as _e:                                # pragma: no cover
    sys.stderr.write("[ocr] missing pytesseract/Pillow: " + str(_e) + "\n")
    raise


def upgrade_image_url(url: str) -> str:
    """Map a thumb URL to a higher-res variant if known.

    Mercari static.mercdn.net:
      /thumb/item/webp/{id}_{n}.jpg?...       (240x240 webp)
        -> /item/detail/orig/photos/{id}_{n}.jpg    (1080x1080 jpeg)
    闲鱼 img.alicdn.com/bao/uploaded/iN/.../..._!!...-xy_item.heic_450x10000Q90.jpg_.webp
        (no obvious higher-res pattern; keep as-is)
    Yahoo auctions img-host patterns vary; no known upgrade.
    """
    if not url:
        return url
    # Mercari thumb -> detail
    m = re.match(r"https?://static\.mercdn\.net/thumb/item/(?:webp|jpg|jpeg)/([A-Za-z0-9_\-\.]+\.(?:jpg|jpeg|webp|png))(?:\?.*)?", url)
    if m:
        fname = m.group(1)
        return f"https://static.mercdn.net/item/detail/orig/photos/{fname}"
    return url


_TOKEN_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]+")


def tokenize(text: str) -> set:
    return set(t for t in _TOKEN_RE.findall(text or "") if len(t) >= 2)


def cover_jaccard(a: str, b: str) -> float:
    ta = tokenize(a)
    tb = tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cover_share(a: str, b: str) -> float:
    """Fraction of tokens in a that also appear in b."""
    ta = tokenize(a)
    tb = tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


def init_or_add_columns(db_path: str) -> None:
    with sqlite3.connect(db_path, timeout=60) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(market_items)").fetchall()}
        if "cover_text" not in cols:
            conn.execute("ALTER TABLE market_items ADD COLUMN cover_text TEXT")
        if "cover_text_lang" not in cols:
            conn.execute("ALTER TABLE market_items ADD COLUMN cover_text_lang TEXT")
        conn.commit()


def ocr_for_url(url: str, *, lang: str = "jpn+eng+chi_sim") -> Tuple[Optional[str], Optional[str]]:
    """Fetch high-res, run tesseract, return (text, lang)."""
    upgraded = upgrade_image_url(url)
    blob = phash_compute.fetch_bytes(upgraded)
    if not blob:
        return None, None
    try:
        img = Image.open(io.BytesIO(blob)).convert("RGB")
    except Exception:
        return None, None
    try:
        text = pytesseract.image_to_string(img, lang=lang)
    except Exception:
        return None, None
    text = (text or "").strip()
    if not text:
        return None, lang
    return text, lang


def backfill_cover_texts(db_path: str, *, only_source: str | None = None, only_missing: bool = True, max_attempts: int = 3) -> dict:
    """Walk market_items and fill cover_text / cover_text_lang.

    Args:
        db_path: sqlite path
        only_source: 'wameiji' or 'xianyu' or None for both
        only_missing: skip rows that already have cover_text
    """
    init_or_add_columns(db_path)
    sql = "SELECT id, image_url FROM market_items WHERE image_url IS NOT NULL AND image_url != ''"
    params: tuple = ()
    if only_source:
        sql += " AND source=?"
        params = (only_source,)
    if only_missing:
        sql += " AND (cover_text IS NULL OR cover_text='')"
    updated = 0
    failed = 0
    skipped = 0
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        for r in rows:
            for attempt in range(max_attempts):
                try:
                    text, lang = ocr_for_url(r["image_url"])
                    break
                except Exception as e:
                    text, lang = None, None
                    if attempt == max_attempts - 1:
                        print(f"[ocr] err id={r['id']} after {max_attempts} attempts: {str(e)[:60]}", flush=True)
            try:
                text, lang = ocr_for_url(r["image_url"])
            except Exception as e:
                text, lang = None, None
                print(f"[ocr] err id={r['id']}: {e}", flush=True)
            if text:
                conn.execute(
                    "UPDATE market_items SET cover_text=?, cover_text_lang=? WHERE id=?",
                    (text[:4000], lang, r["id"]),
                )
                updated += 1
            else:
                failed += 1
        conn.commit()
    return {"updated": updated, "failed": failed, "skipped": len(rows) - updated - failed}


def lookup_cover_text(db_path: str, image_url: str) -> Optional[str]:
    with sqlite3.connect(db_path, timeout=60) as conn:
        row = conn.execute(
            "SELECT cover_text FROM market_items WHERE image_url=? ORDER BY id DESC LIMIT 1",
            (image_url,),
        ).fetchone()
    if row and row[0]:
        return row[0]
    return None


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(_BASE_DIR / "cd_monitor.db")
    src = sys.argv[2] if len(sys.argv) > 2 else None
    res = backfill_cover_texts(db, only_source=src)
    print("RESULT", res, flush=True)
