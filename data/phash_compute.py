#!/usr/bin/env python3
"""phash_compute.py - Perceptual image hashing for visual product matching.

Uses imagehash phash (8x8 dct-based) + dhash (difference hash) for two
fingerprints. Computes both because phash is robust to scaling/jpeg
artifacts while dhash catches small local changes that phash may miss.
"""
from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Tuple

try:
    import imagehash
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
except Exception as e:
    sys.stderr.write("[phash] missing imagehash/Pillow: " + str(e) + "\n")
    raise

_BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = os.environ.get("PHASH_CACHE_DIR", str(_BASE_DIR / "_phash_cache"))
TIMEOUT_SEC = int(os.environ.get("PHASH_FETCH_TIMEOUT", "12"))
MAX_BYTES = int(os.environ.get("PHASH_MAX_BYTES", str(2 * 1024 * 1024)))


def _url_fingerprint(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


def _cache_path(url):
    return Path(CACHE_DIR) / (_url_fingerprint(url) + ".bin")


def fetch_bytes(url, *, allow_disk_cache=True):
    if not url:
        return None
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    p = _cache_path(url)
    if allow_disk_cache and p.exists():
        try:
            return p.read_bytes()
        except Exception:
            pass
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5",
    }
    if "mercdn" in url:
        headers["Referer"] = "https://jp.mercari.com/"
    elif "alicdn" in url or "goofish" in url or "taobaocdn" in url:
        headers["Referer"] = "https://www.goofish.com/"
    try:
        import requests as _req
        r = _req.get(url, headers=headers, timeout=TIMEOUT_SEC, stream=True)
        if r.status_code == 200:
            blob = b""
            for chunk in r.iter_content(chunk_size=65536):
                blob += chunk
                if len(blob) > MAX_BYTES:
                    break
            if blob:
                try:
                    p.write_bytes(blob)
                except Exception:
                    pass
                return blob
    except Exception:
        pass
    try:
        from urllib.request import Request, urlopen
        req = Request(url, headers=headers)
        with urlopen(req, timeout=TIMEOUT_SEC) as resp:
            blob = resp.read(MAX_BYTES)
        if blob:
            try:
                p.write_bytes(blob)
            except Exception:
                pass
            return blob
    except Exception:
        return None
    return None


def compute_hashes_for_bytes(blob):
    if not blob:
        return None, None
    try:
        img = Image.open(io.BytesIO(blob)).convert("RGB")
        ph = imagehash.phash(img, hash_size=8)
        dh = imagehash.dhash(img, hash_size=8)
        return str(ph), str(dh)
    except Exception:
        return None, None


def compute_for_url(url):
    blob = fetch_bytes(url)
    if not blob:
        return None, None
    return compute_hashes_for_bytes(blob)


def hamming(a, b):
    if not a or not b:
        return None
    try:
        ha = imagehash.hex_to_hash(a)
        hb = imagehash.hex_to_hash(b)
        return int(ha - hb)
    except Exception:
        return None


def min_distance(target_hash, candidates):
    distances = [d for d in (hamming(target_hash, c) for c in candidates) if d is not None]
    if not distances:
        return None
    return min(distances)


def looks_like_match(min_d, *, phash_threshold=10, dhash_threshold=8):
    if min_d is None:
        return False
    return min_d <= phash_threshold


def init_or_add_column(db_path):
    with sqlite3.connect(db_path, timeout=60) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(market_items)").fetchall()}
        if "image_phash" not in cols:
            conn.execute("ALTER TABLE market_items ADD COLUMN image_phash TEXT")
        if "image_dhash" not in cols:
            conn.execute("ALTER TABLE market_items ADD COLUMN image_dhash TEXT")
        conn.commit()


def backfill_phashes(db_path, only_source=None):
    init_or_add_column(db_path)
    sql = "SELECT id, image_url FROM market_items WHERE image_url IS NOT NULL AND image_url != '' AND (image_phash IS NULL OR image_phash='')"
    params = ()
    if only_source:
        sql += " AND source=?"
        params = (only_source,)
    updated = 0
    failed = 0
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        for r in rows:
            ph, dh = compute_for_url(r["image_url"])
            if ph:
                conn.execute("UPDATE market_items SET image_phash=?, image_dhash=? WHERE id=?", (ph, dh, r["id"]))
                updated += 1
            else:
                failed += 1
        conn.commit()
    return {"updated": updated, "failed": failed, "skipped": len(rows) - updated - failed}


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(_BASE_DIR / "cd_monitor.db")
    src = sys.argv[2] if len(sys.argv) > 2 else None
    res = backfill_phashes(db, only_source=src)
    print("RESULT", res, flush=True)
