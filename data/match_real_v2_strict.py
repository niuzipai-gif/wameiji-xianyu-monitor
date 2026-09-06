#!/usr/bin/env python3
"""match_real_v2.py - Cross-catalog wameiji match pipeline.

Major changes vs match_real.py:
  1. Iterates ALL wameiji items (not pre-filtered by catalog_no) so that
     catalog_no='AUTO' items still get matched via cover_text jaccard,
     pHash distance, title token overlap, or JAN exact match.
  2. On-the-fly JAN extraction from title/raw_text using
     cd_monitor.core.identifiers.extract_jan_candidates (writes back to
     market_items.jan if NULL).
  3. Cover-text jaccard vs each xianyu sample's cover_text (cached). When
     jaccard >= 0.20 OR share >= 0.40, treats the wameiji item as
     matching that xianyu catalog and uses it as the candidate catalog.
  4. pHash hamming distance vs each xianyu sample's image_phash. Within
     distance 10 = strong image match, takes that catalog.
  5. Title token overlap: split on Japanese punctuation/whitespace,
     lowercase ASCII, keep tokens len>=2. Jaccard >= 0.25 with xianyu
     title token set = candidate catalog.
  6. Catalog pre-filter still works as a fast path (covers ~70% of
     matches for items that already have a non-AUTO catalog_no).
  7. On successful match, UPDATE market_items.catalog_no to the
     winning watchlist catalog_no so subsequent runs go through the
     fast path.
"""
from __future__ import annotations

import os
import re
import sys
import json

import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

DB = os.environ.get("CD_DB", "/app/data/cd_monitor.db")

def _enable_wal(db_path):
    try:
        with sqlite3.connect(db_path, timeout=60) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=60000")
            c.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass

sys.path.insert(0, "/app/src")
sys.path.insert(0, "/app/data")

from cd_monitor.core.identifiers import (  # noqa: E402
    extract_jan_candidates,
    extract_catalog_candidates,
    normalize_catalog_no,
    normalize_catalog_no_compact,
)
from cd_monitor.core.cost_model import compute_landed_cost       # noqa: E402
from cd_monitor.core.evaluator import evaluate_opportunity       # noqa: E402
from cd_monitor.core.matcher import compute_match_confidence     # noqa: E402
from cd_monitor.core.models import (                             # noqa: E402
    MarketItem, WatchItem, XianyuPriceSample, XianyuPriceEstimate, CostConfig, EvaluationConfig,
)
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price  # noqa: E402
from cd_monitor.storage.sqlite import (                          # noqa: E402
    insert_opportunity, init_db,
)
import ocr_cover                                                # noqa: E402
import phash_compute                                            # noqa: E402


# ---------- thresholds ----------
COVER_JACCARD_MIN = 0.20
COVER_SHARE_MIN = 0.40
PHASH_DISTANCE_STRONG = 10     # <= this is a strong image match
PHASH_DISTANCE_WEAK = 18       # <= this is a weak image match
TITLE_JACCARD_MIN = 0.25
JAN_AUTO_CONFIDENCE = 0.95
JAN_HARD_CONFIDENCE = 1.00


# ---------- helpers ----------
def hamming_hex(a: str, b: str) -> int:
    if not a or not b:
        return 999
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 999


def jan_confidence(text: str, watch_jan: str | None) -> tuple[float, str | None]:
    """If watch_jan matches any 8/12/13-digit candidate in text, return (1.0, jan)."""
    if not watch_jan:
        return 0.0, None
    cands = extract_jan_candidates(text or "")
    wj = re.sub(r"\D", "", watch_jan)
    if not wj:
        return 0.0, None
    if wj in cands:
        return JAN_HARD_CONFIDENCE, wj
    # JAN soft: same prefix-7 (e.g. both start with 49 for JP) is too loose; skip.
    return 0.0, None


def tokenize_jp_title(title: str) -> set[str]:
    """Split Japanese-aware title into normalized tokens (lowercase ASCII + JP kana/kanji runs)."""
    if not title:
        return set()
    # Strip URLs, parens content that is mostly punctuation
    t = re.sub(r"https?://\S+", " ", title)
    t = re.sub(r"[\(\[\u3010].*?[\)\]\u3011]", " ", t)
    # Split on whitespace + JP punctuation
    parts = re.split(r"[\s\u3000\u3001\u3002\uff0c\uff1a\uff01\uff1f\uff08\uff09\uff3b\uff3d\u3010\u3011\u30fb\u2026\u2027]+", t)
    out = set()
    for p in parts:
        p = p.strip().lower()
        if len(p) >= 2:
            out.add(p)
    return out


def title_jaccard(a: str, b: str) -> float:
    ta, tb = tokenize_jp_title(a), tokenize_jp_title(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def title_share(a: str, b: str) -> float:
    """Fraction of A tokens found in B."""
    ta, tb = tokenize_jp_title(a), tokenize_jp_title(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


# ---------- data access ----------
def load_cost_eval_config(db_path: str) -> tuple[CostConfig, EvaluationConfig]:
    cfg_cost = CostConfig()
    cfg_eval = EvaluationConfig()
    try:
        from cd_monitor.infrastructure.config.settings import load_config
        cfg = load_config(None)
        if cfg and hasattr(cfg, "cost"):
            cfg_cost = cfg.cost or cfg_cost
        if cfg and hasattr(cfg, "evaluation"):
            cfg_eval = cfg.evaluation or cfg_eval
    except Exception:
        pass
    return cfg_cost, cfg_eval


def fetch_watches(db_path: str) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT catalog_no, artist, jan, required_keywords, excluded_keywords, "
            "expected_holding_days, edition, match_mode FROM watchlist"
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def fetch_xianyu_index(db_path: str) -> dict:
    """Build a per-catalog index of xianyu samples with cover_text + phash + jan + tokens."""
    index = defaultdict(list)  # catalog_no -> list of dicts
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, catalog_no, title, price, url, image_url, raw_text, "
            "cover_text, image_phash, jan, fetched_at "
            "FROM market_items WHERE source='xianyu' ORDER BY fetched_at DESC"
        ).fetchall()
        for r in rows:
            index[r["catalog_no"]].append({
                "id": r["id"],
                "catalog_no": r["catalog_no"],
                "title": r["title"] or "",
                "price": float(r["price"] or 0),
                "url": r["url"],
                "image_url": r["image_url"],
                "cover_text": r["cover_text"] or "",
                "image_phash": r["image_phash"] or "",
                "jan": r["jan"] or "",
                "tokens": tokenize_jp_title(r["title"] or ""),
            })
    return index


def fetch_xianyu_samples_for_catalog(db_path: str, catalog_no: str, limit: int = 30) -> list[XianyuPriceSample]:
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, price, url, image_url, image_phash, image_dhash, cover_text, raw_text, fetched_at "
            "FROM market_items WHERE source='xianyu' AND catalog_no=? "
            "ORDER BY fetched_at DESC LIMIT ?",
            (catalog_no, limit),
        ).fetchall()
    samples = []
    for r in rows:
        try:
            price = float(r["price"] or 0)
        except Exception:
            price = 0.0
        samples.append(
            XianyuPriceSample(
                catalog_no=catalog_no,
                title=r["title"] or "",
                price_cny=price,
                url=r["url"],
                image_url=r["image_url"],
                image_phash=r["image_phash"] or None,
                image_dhash=r["image_dhash"] or None,
                cover_text=r["cover_text"] or None,
                seller_text=None,
                raw_text=r["raw_text"],
                market_item_id=r["id"],
            )
        )
    return samples


def make_market_item(row) -> MarketItem:
    def g(k, default=None):
        try:
            v = row[k]
            return default if v is None else v
        except (KeyError, IndexError):
            return default
    return MarketItem(
        source=g("source") or "wameiji",
        source_site=g("source_site"),
        external_item_id=g("external_item_id"),
        catalog_no=g("catalog_no"),
        title=g("title") or "",
        price=float(g("price") or 0),
        currency=g("currency") or "JPY",
        url=g("url"),
        image_url=g("image_url"),
        availability=g("availability") or "unknown_but_visible",
        condition_text=g("condition_text"),
        fees_hint=g("fees_hint"),
        raw_text=g("raw_text"),
        jan=g("jan"),
    )


def _parse_kw_field(v) -> list[str]:
    """Parse required/excluded keywords from various storage formats.
    Accepts: None, "", "[]", '["foo","bar"]', "foo,bar", or already a list.
    Returns a flat list of non-empty strings.
    """
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
        s = s.strip('[]').strip()
        if not s:
            return []
    return [x.strip().strip(chr(34)+chr(39)) for x in s.split(",") if x.strip()]

def make_watch_item(w: dict) -> WatchItem:
    kw = _parse_kw_field(w.get('required_keywords'))
    excl = _parse_kw_field(w.get('excluded_keywords'))
    return WatchItem(
        catalog_no=w["catalog_no"],
        artist=w.get("artist"),
        title_jp=w.get("title_jp"),
        title_cn=w.get("title_cn"),
        jan=w.get("jan"),
        required_keywords=kw,
        excluded_keywords=excl,
        match_mode=(w.get("match_mode") or "any").strip().lower(),
        expected_holding_days=int(w.get("expected_holding_days") or 30),
        edition=w.get("edition"),
    )


# ---------- signal computation ----------
def cross_platform_signals(
    item_row,
    item_title: str,
    item_raw_text: str,
    item_cover: str,
    item_phash: str,
    xianyu_index: dict,
    watch_by_catalog: dict,
) -> tuple[dict, dict, str | None]:
    """For a wameiji item, compute candidate catalogs and their evidence.

    Returns (signals_per_catalog, evidence_per_catalog, best_jan).
    signals_per_catalog[catalog_no] = {
        "cover_jaccard": max over xianyu samples,
        "cover_share": max over xianyu samples,
        "phash_dist": min over xianyu samples,
        "title_jaccard": max over xianyu samples,
        "title_share": max over xianyu samples,
        "jan": best JAN hit (None or string),
    }
    evidence_per_catalog[catalog_no] = list of strings describing top signals.
    """
    signals = defaultdict(lambda: {
        "cover_jaccard": 0.0,
        "cover_share": 0.0,
        "phash_dist": 999,
        "title_jaccard": 0.0,
        "title_share": 0.0,
        "jan": None,
    })
    best_jan = None

    item_text = " ".join([item_title or "", item_raw_text or "", item_cover or ""])

    for cat, samples in xianyu_index.items():
        if cat == "AUTO" or not cat:
            continue
        sig = signals[cat]
        for s in samples:
            # cover text jaccard
            if item_cover and s["cover_text"]:
                j = ocr_cover.cover_jaccard(item_cover, s["cover_text"])
                sh = ocr_cover.cover_share(item_cover, s["cover_text"])
                if j > sig["cover_jaccard"]:
                    sig["cover_jaccard"] = j
                if sh > sig["cover_share"]:
                    sig["cover_share"] = sh
            # pHash distance
            if item_phash and s["image_phash"]:
                d = hamming_hex(item_phash, s["image_phash"])
                if d < sig["phash_dist"]:
                    sig["phash_dist"] = d
            # title token jaccard
            if item_title and s["title"]:
                tj = title_jaccard(item_title, s["title"])
                ts = title_share(item_title, s["title"])
                if tj > sig["title_jaccard"]:
                    sig["title_jaccard"] = tj
                if ts > sig["title_share"]:
                    sig["title_share"] = ts
            # jan in xianyu title
            if s["jan"] and not sig["jan"]:
                wj = re.sub(r"\D", "", s["jan"])
                if wj and wj in extract_jan_candidates(item_text):
                    sig["jan"] = wj
                    if not best_jan:
                        best_jan = wj

    return dict(signals), {c: [] for c in signals}, best_jan


def candidate_catalogs_from_signals(
    item_row,
    item_title: str,
    item_raw_text: str,
    item_cover: str,
    item_phash: str,
    xianyu_index: dict,
    watched_catalogs: set,
) -> list[str]:
    """Return ordered list of catalog_nos that the wameiji item could match.

    Order = strongest signal first (JAN exact, then pHash<=10, then cover_jaccard>=0.20,
    then title_jaccard>=0.25, then existing catalog_no if watched).
    """
    candidates = []
    seen = set()
    item_text = " ".join([item_title or "", item_raw_text or "", item_cover or ""])

    # 1. JAN exact match against any xianyu item's JAN (across all catalogs)
    if item_text:
        item_jans = set(extract_jan_candidates(item_text))
        if item_jans:
            for cat, samples in xianyu_index.items():
                if cat in seen or cat not in watched_catalogs:
                    continue
                for s in samples:
                    if s["jan"] and re.sub(r"\D", "", s["jan"]) in item_jans:
                        candidates.append(cat)
                        seen.add(cat)
                        break

    # 2. pHash strong match
    if item_phash:
        for cat, samples in xianyu_index.items():
            if cat in seen or cat not in watched_catalogs:
                continue
            for s in samples:
                if s["image_phash"]:
                    d = hamming_hex(item_phash, s["image_phash"])
                    if d <= PHASH_DISTANCE_STRONG:
                        candidates.append(cat)
                        seen.add(cat)
                        break

    # 3. Cover text jaccard >= COVER_JACCARD_MIN
    if item_cover:
        for cat, samples in xianyu_index.items():
            if cat in seen or cat not in watched_catalogs:
                continue
            for s in samples:
                if not s["cover_text"]:
                    continue
                if ocr_cover.cover_jaccard(item_cover, s["cover_text"]) >= COVER_JACCARD_MIN:
                    candidates.append(cat)
                    seen.add(cat)
                    break
                if ocr_cover.cover_share(item_cover, s["cover_text"]) >= COVER_SHARE_MIN:
                    candidates.append(cat)
                    seen.add(cat)
                    break

    # 4. Title jaccard >= TITLE_JACCARD_MIN
    if item_title:
        for cat, samples in xianyu_index.items():
            if cat in seen or cat not in watched_catalogs:
                continue
            for s in samples:
                if not s["title"]:
                    continue
                if title_jaccard(item_title, s["title"]) >= TITLE_JACCARD_MIN:
                    candidates.append(cat)
                    seen.add(cat)
                    break
                if title_share(item_title, s["title"]) >= 0.5:
                    candidates.append(cat)
                    seen.add(cat)
                    break

    # 5. Existing catalog_no (fast path)
    existing = (item_row["catalog_no"] or "").strip()
    if existing and existing != "AUTO" and existing in watched_catalogs and existing not in seen:
        candidates.append(existing)
        seen.add(existing)

    # 6. P1 fallback: keyword/artist/title match against watched catalogs even without
    # xianyu samples. Lets newly-added catalogs (no scraped xianyu data yet) get
    # matched by title/artist/title_jp/title_cn from the watchlist itself.
    if item_title:
        item_lower = item_title.lower()
        item_compact = "".join(item_title.split()).lower()
        # Build watch_data locally (we don't have watch_by_catalog here, so iterate watched_catalogs)
        # Caller should pass watched_catalogs — for fallback we just check the row's catalog_no
        # and any artist-name substring in title.
        # (Keyword/title_jp/title_cn enrichment is done outside in match_real_market_items
        # because that function has watch_by_catalog; here we can only do catalog_no-equality
        # check, which step 5 already covers.)
        # So nothing to add here — but we DO want to keep this step as a placeholder
        # for future richer matching.
        pass

    return candidates


# ---------- main pipeline ----------
def match_real_market_items(db_path: str = DB, only_catalog: str | None = None) -> dict:
    _enable_wal(db_path)
    init_db(db_path)
    watches = fetch_watches(db_path)
    if not watches:
        return {"items_evaluated": 0, "opportunities_inserted": 0, "unmatched": 0,
                "hydrated": 0, "phashes": 0, "img_skip_dup": 0, "candidate_cats": 0,
                "cover_matches": 0, "phash_matches": 0, "jan_matches": 0,
                "title_matches": 0, "catalog_backfilled": 0}

    watched_catalogs = {w["catalog_no"] for w in watches if w.get("catalog_no")}
    watch_by_catalog = {w["catalog_no"]: w for w in watches if w.get("catalog_no")}

    cost_cfg, eval_cfg = load_cost_eval_config(db_path)
    # Runtime override: enforce quality thresholds (P0 - user complaint about bogus matches)
    try:
        eval_cfg.min_match_confidence_strong = 0.70   # was 0.55
        eval_cfg.min_match_confidence_weak = 0.55     # was 0.35
        eval_cfg.weak_profit_min_cny = 20            # was 15
        eval_cfg.strong_profit_min_cny = 15          # was 10
        eval_cfg.weak_margin_min = 0.15              # was 0.10
        eval_cfg.strong_margin_min = 0.20            # was 0.15
        eval_cfg.min_xianyu_samples_strong = 2
        eval_cfg.min_xianyu_samples_weak = 1
    except Exception:
        pass
    xianyu_index = fetch_xianyu_index(db_path)

    items_evaluated = 0
    opp_inserted = 0
    unmatched = 0
    img_skip_dup = 0
    cover_matches = 0
    phash_matches = 0
    jan_matches = 0
    title_matches = 0
    candidate_cats = 0
    catalog_backfilled = 0

    # Pre-compute xianyu phashes per catalog for boost
    x_phashes_by_catalog = {}
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        for cat in watched_catalogs:
            x_phashes_by_catalog[cat] = [
                r["image_phash"] for r in conn.execute(
                    "SELECT image_phash FROM market_items WHERE source='xianyu' "
                    "AND catalog_no=? AND image_phash IS NOT NULL AND image_phash != ''",
                    (cat,),
                ).fetchall() if r["image_phash"]
            ]

    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        # Fetch ALL wameiji items (don't pre-filter by catalog_no)
        sql = ("SELECT id, source, source_site, external_item_id, catalog_no, title, "
               "price, currency, url, image_url, availability, condition_text, "
               "raw_text, image_phash, image_dhash, jan, cover_text "
               "FROM market_items WHERE source='wameiji' ORDER BY id DESC")
        if only_catalog:
            sql += " AND catalog_no=?"
            params = (only_catalog,)
        else:
            params = ()
        item_rows = conn.execute(sql, params).fetchall()

        # Track inserted opportunity hashes so we don't double-insert within one run
        seen_hashes_this_run = set()
        # Track display sample id per inserted opportunity (for feed display)
        display_sample_ids = {}
        # Track catalog_no + jan updates to apply at the end (one UPDATE per item max)
        catalog_updates = {}
        jan_updates = {}

        for row in item_rows:
            items_evaluated += 1
            try:
                item = make_market_item(row)
                item_title = row["title"] or ""
                # Compilation/album-set titles share songs across multiple specific
                # albums; skip them so they don't generate spurious opp rows under
                # the wrong catalog (e.g. Ado 歌合集 containing 「逆光」would
                # otherwise be backfilled to TYCT-39145 逆光 single).
                _title_lower = (item_title or "").lower()
                if any(p in _title_lower for p in ["歌合集", "歌ってみた", "うたってみた", "まとめ", "セット", "ベスト", "best", "box", "collection", "complete", "ヒッツ", "hits", "大全"]):
                    continue
                item_raw_text = row["raw_text"] or ""
                item_cover = row["cover_text"] or ""
                item_phash = row["image_phash"] or ""

                # Backfill JAN if missing
                item_jan = row["jan"] or ""
                if not item_jan:
                    cands = extract_jan_candidates(" ".join([item_title, item_raw_text, item_cover]))
                    if cands:
                        item_jan = cands[0]
                        jan_updates[row["id"]] = item_jan
                        item.jan = item_jan

                # Determine candidate catalogs
                candidate_list = candidate_catalogs_from_signals(
                    row, item_title, item_raw_text, item_cover, item_phash,
                    xianyu_index, watched_catalogs,
                )
                # P1 fallback: when no xianyu-signal candidate exists, scan watchlist
                # for direct artist/title_jp/title_cn hit in item title. Lets newly-added
                # catalogs (no xianyu samples yet) still match by name.
                if not candidate_list and item_title:
                    item_compact = "".join(item_title.split()).lower()
                    for wcat, wrow in watch_by_catalog.items():
                        if wcat in candidate_list:
                            continue
                        # build candidate terms from watch row
                        terms = []
                        for k in ("artist", "title_jp", "title_cn"):
                            v = (wrow.get(k) or "").strip()
                            if v and len(v) >= 2:
                                terms.append("".join(v.split()).lower())
                        # also include catalog_no tokens if any
                        hit = False
                        for t in terms:
                            if t and t in item_compact:
                                hit = True
                                break
                        if hit:
                            candidate_list.append(wcat)

                # P2: filter out candidates whose watchlist required_keywords don'"'"'t match
                # the item title at all. Prevents cross-album false positives like a
                # "THE BOOK for," item sneaking into TYCT-69088 (THE BOOK 2).
                if candidate_list and item_title:
                    item_compact_kw = "".join((item_title + " " + (row["raw_text"] or "")).split()).lower()
                    filtered = []
                    for _cand in candidate_list:
                        _wr = watch_by_catalog.get(_cand)
                        if not _wr:
                            filtered.append(_cand)
                            continue
                        _kws = _parse_kw_field(_wr.get("required_keywords"))
                        if not _kws:
                            filtered.append(_cand)
                            continue
                        # match_mode "any" (default): OR; "all": every keyword must appear
                        _mode = (_wr.get("match_mode") or "any").strip().lower()
                        if _mode == "all":
                            _kw_match = True
                            for _kw in _kws:
                                if not _kw or "".join(_kw.split()).lower() not in item_compact_kw:
                                    _kw_match = False
                                    break
                        else:
                            _kw_match = False
                            for _kw in _kws:
                                if _kw and "".join(_kw.split()).lower() in item_compact_kw:
                                    _kw_match = True
                                    break
                        if _kw_match:
                            filtered.append(_cand)
                    if filtered:
                        candidate_list = filtered

                candidate_cats += len(candidate_list)
                # Count which signal classes fired (for telemetry)
                if any(True for _ in candidate_list):
                    pass
                # Re-run signal evaluation just for telemetry counting (cheap)
                _sig, _ev, _bj = cross_platform_signals(
                    row, item_title, item_raw_text, item_cover, item_phash,
                    xianyu_index, watch_by_catalog,
                )
                for cat, s in _sig.items():
                    if s["cover_jaccard"] >= COVER_JACCARD_MIN or s["cover_share"] >= COVER_SHARE_MIN:
                        cover_matches += 1
                    if s["phash_dist"] <= PHASH_DISTANCE_STRONG:
                        phash_matches += 1
                    if s["jan"]:
                        jan_matches += 1
                    if s["title_jaccard"] >= TITLE_JACCARD_MIN:
                        title_matches += 1

                # Try each candidate catalog; keep best opportunity
                best_opp_id = None
                best_conf = -1.0
                best_cat = None

                _original_item_catalog_no = item.catalog_no
                for cat in candidate_list:
                    if cat not in watch_by_catalog:
                        continue
                    watch_row = watch_by_catalog[cat]
                    watch = make_watch_item(watch_row)
                    # Restore ORIGINAL catalog_no so compute_match_confidence does not get catalog_no_exact for every candidate.
                    item.catalog_no = _original_item_catalog_no
                    match = compute_match_confidence(item, watch)

                    # Add cross-platform boosts to the base confidence
                    sig = _sig.get(cat, {})
                    boost = 0.0
                    notes = []
                    if sig.get("jan") and (watch.jan and re.sub(r"\D", "", watch.jan) == sig["jan"]):
                        boost = max(boost, 1.0 - match.confidence)  # hard confidence lift
                        notes.append(f"jan_exact:{sig['jan']}")
                    elif sig.get("jan"):
                        boost = max(boost, 0.20)
                        notes.append(f"jan_in_text:{sig['jan']}")
                    if sig.get("phash_dist", 999) <= PHASH_DISTANCE_STRONG:
                        boost = max(boost, 0.25)
                        notes.append(f"phash_dist:{sig['phash_dist']}")
                    elif sig.get("phash_dist", 999) <= PHASH_DISTANCE_WEAK:
                        boost = max(boost, 0.10)
                        notes.append(f"phash_dist_weak:{sig['phash_dist']}")
                    if sig.get("cover_jaccard", 0) >= COVER_JACCARD_MIN:
                        boost = max(boost, 0.20)
                        notes.append(f"cover_jaccard:{sig['cover_jaccard']:.2f}")
                    elif sig.get("cover_share", 0) >= COVER_SHARE_MIN:
                        boost = max(boost, 0.12)
                        notes.append(f"cover_share:{sig['cover_share']:.2f}")
                    if sig.get("title_jaccard", 0) >= TITLE_JACCARD_MIN:
                        boost = max(boost, 0.10)
                        notes.append(f"title_jaccard:{sig['title_jaccard']:.2f}")

                    new_conf = min(1.0, max(0.0, match.confidence + boost))
                    if new_conf < (eval_cfg.min_match_confidence_weak or 0.4):
                        continue
                    # Apply boost to match object so evaluator sees it
                    match.confidence = round(new_conf, 4)
                    if notes:
                        match.positive_reasons.extend(notes)

                    # Compute price samples + cost + opportunity
                    samples = fetch_xianyu_samples_for_catalog(db_path, cat, limit=eval_cfg.sample_limit or 30)
                    synth_used = False
                    # If no samples at all, fall through to synth.
                    if not samples:
                        item_price_jpy = float(item.price or 0)
                        if item_price_jpy < 200:
                            continue
                        # 4.5x is a conservative JP->CN CD arbitrage ratio.
                        synth_cny = item_price_jpy * cost_cfg.wameiji_exchange_rate * 4.5
                        samples = [XianyuPriceSample(
                            catalog_no=cat,
                            title="[synthetic] from wameiji: " + (item.title or ""),
                            price_cny=round(synth_cny, 2),
                            url="",
                            image_url="",
                            seller_text="synthetic_fallback",
                            raw_text="",
                            is_valid=True,
                            invalid_reason="synthetic_fallback",
                        )]
                        synth_used = True
                    # P0 defense: skip over-priced wameiji items (multi-disc sets, obi-only, etc)
                    item_price_hardcap = float(item.price or 0)
                    if item_price_hardcap > 15000:
                        unmatched_for_overprice = unmatched_for_overprice + 1 if 'unmatched_for_overprice' in dir() else 1
                        continue
                    xianyu = estimate_xianyu_price(
                        samples,
                        min_valid_price_cny=eval_cfg.min_valid_price_cny,
                        max_valid_price_cny=eval_cfg.max_valid_price_cny,
                        sample_limit=eval_cfg.sample_limit,
                        negotiation_discount=eval_cfg.negotiation_discount,
                        liquidity_discount_default=eval_cfg.liquidity_discount_default,
                        edition_confidence=max(0.7, match.confidence),
                        edition=watch.edition,
                        required_keywords=watch.required_keywords,
                        excluded_keywords=watch.excluded_keywords,
                    )
                    if synth_used:
                        xianyu = XianyuPriceEstimate(
                            reference_price_cny=xianyu.reference_price_cny,
                            valid_sample_count=1,
                            liquidity_status="thin",
                            expected_sale_price_cny=xianyu.expected_sale_price_cny,
                            valid_samples=xianyu.valid_samples,
                            invalid_samples=xianyu.invalid_samples,
                        )
                    cost = compute_landed_cost(
                        item, config=cost_cfg, expected_holding_days=watch.expected_holding_days,
                    )
                    opportunity = evaluate_opportunity(watch, item, match, xianyu, cost, eval_cfg)

                    if opportunity.opportunity_hash in seen_hashes_this_run:
                        continue

                    # P0 defense: never persist negative-profit opportunities (they pollute feed)
                    # P1 exception: if not synth_used and item price >= 300 yen, retry with
                    # synth (real samples may be for wrong album).
                    if opportunity.expected_profit is None or opportunity.expected_profit <= 0:
                        if not synth_used and float(item.price or 0) >= 300:
                            item_price_jpy = float(item.price or 0)
                            synth_cny = item_price_jpy * cost_cfg.wameiji_exchange_rate * 4.5
                            samples2 = [XianyuPriceSample(
                                catalog_no=cat,
                                title="[synthetic] from wameiji: " + (item.title or ""),
                                price_cny=round(synth_cny, 2),
                                url="",
                                image_url="",
                                seller_text="synthetic_fallback",
                                raw_text="",
                                is_valid=True,
                                invalid_reason="synthetic_fallback",
                            )]
                            xianyu2 = estimate_xianyu_price(
                                samples2,
                                min_valid_price_cny=eval_cfg.min_valid_price_cny,
                                max_valid_price_cny=eval_cfg.max_valid_price_cny,
                                sample_limit=eval_cfg.sample_limit,
                                negotiation_discount=eval_cfg.negotiation_discount,
                                liquidity_discount_default=eval_cfg.liquidity_discount_default,
                                edition_confidence=max(0.7, match.confidence),
                                edition=watch.edition,
                                required_keywords=watch.required_keywords,
                                excluded_keywords=watch.excluded_keywords,
                            )
                            xianyu2 = XianyuPriceEstimate(
                                reference_price_cny=xianyu2.reference_price_cny,
                                valid_sample_count=1,
                                liquidity_status="thin",
                                expected_sale_price_cny=xianyu2.expected_sale_price_cny,
                                valid_samples=xianyu2.valid_samples,
                                invalid_samples=xianyu2.invalid_samples,
                            )
                            opportunity = evaluate_opportunity(watch, item, match, xianyu2, cost, eval_cfg)
                            synth_used = True
                            if opportunity.opportunity_hash in seen_hashes_this_run:
                                continue
                        if opportunity.expected_profit is None or opportunity.expected_profit <= 0:
                            unmatched += 1
                            continue
                    # Pick a representative xianyu sample via multi-signal scoring.
                    # Score combines: phash distance (visual match), artist match, title_jp match,
                    # cover_text jaccard, and required_keywords hits. Pick the highest-scoring
                    # sample (ties broken by closest price). This way samples that are visually
                    # similar to the wameiji item win, even if they don't share an exact
                    # keyword with the watchlist (e.g. sample title uses different language).
                    display_sample_id = None
                    try:
                        _valid_samples = (xianyu.valid_samples if xianyu else []) or []
                        if _valid_samples:
                            _ref = float(opportunity.xianyu_reference_price or 0)
                            _item_phash = (item_phash or "").strip()
                            _item_cover = (item_cover or "").strip()
                            _kws = _parse_kw_field(watch.required_keywords) if watch else []
                            _artist = (watch.artist or "").strip() if watch else ""
                            _title_jp = (watch.title_jp or "").strip() if watch else ""
                            _kws_norm = ["".join(k.split()).lower() for k in _kws if k]
                            _artist_norm = "".join(_artist.split()).lower()
                            _title_jp_norm = "".join(_title_jp.split()).lower()
                            _wmode = ((watch.match_mode if watch else None) or "any").strip().lower() if watch else "any"
                            _scored = []
                            for _v in _valid_samples:
                                _vt = (_v.title or "").lower()
                                _vt_norm = "".join(_vt.split())
                                _ph = (getattr(_v, "image_phash", None) or "").strip()
                                _cv = (getattr(_v, "cover_text", None) or "").strip()
                                _cv_norm = "".join(_cv.split()).lower()
                                _score = 0.0
                                _ph_dist = None
                                # 1. phash distance (the visual-match signal)
                                if _item_phash and _ph:
                                    try:
                                        _ph_dist = hamming_hex(_item_phash, _ph)
                                    except Exception:
                                        _ph_dist = None
                                if _ph_dist is not None:
                                    if _ph_dist <= 5:
                                        _score += 0.55
                                    elif _ph_dist <= 10:
                                        _score += 0.40
                                    elif _ph_dist <= 18:
                                        _score += 0.20
                                    else:
                                        _score += max(0.0, (40 - _ph_dist) / 80.0)
                                # 2. artist hit
                                _artist_hit = bool(_artist_norm and _artist_norm in _vt_norm)
                                if _artist_hit:
                                    _score += 0.20
                                # 3. title_jp hit (album title appears in sample title)
                                if _title_jp_norm and _title_jp_norm in _vt_norm:
                                    _score += 0.18
                                # 4. cover_text jaccard vs wameiji item cover
                                if _cv and _item_cover:
                                    try:
                                        _cj = ocr_cover.cover_jaccard(_item_cover, _cv)
                                        if _cj >= 0.30:
                                            _score += 0.25
                                        elif _cj >= 0.15:
                                            _score += 0.12
                                    except Exception:
                                        pass
                                # 5. required_keyword hits (any)
                                _kw_hits = sum(1 for _kw in _kws_norm if _kw and _kw in _vt_norm)
                                if _kws_norm and _kw_hits:
                                    _score += min(0.15, 0.05 * _kw_hits)
                                # Penalties for "all" mode
                                if _wmode == "all":
                                    if _artist_norm and not _artist_hit:
                                        _score -= 0.40
                                    for _kw in _kws_norm:
                                        if _kw and _kw not in _vt_norm:
                                            _score -= 0.25
                                _price_diff = abs(float(_v.price_cny or 0) - _ref)
                                _scored.append((_score, _price_diff, _v))
                            # Hard floor: only surface xianyu samples that look like the same album.
                            # We accept a sample as a match if it has any of:
                            #   - strong phash distance (<= 12, same cover image)
                            #   - title_jp exact match in sample title
                            #   - cover_text jaccard >= 0.25 (OCR shows same album text)
                            # Otherwise display_sample_id stays None so web_server falls back to
                            # wameiji item image (avoids surfacing same-artist wrong-album samples).
                            _title_jp_hit = bool(_title_jp_norm and any(
                                _title_jp_norm in "".join((_v.title or "").split()).lower()
                                for _s, _, _v in _scored
                            ))
                            _sorted = sorted(_scored, key=lambda x: (-x[0], x[1]))
                            display_sample_id = None
                            for _s, _pd, _v in _sorted:
                                _vt_norm = "".join((_v.title or "").lower().split())
                                _ph_local = (getattr(_v, "image_phash", None) or "").strip()
                                _cv_local = (getattr(_v, "cover_text", None) or "").strip()
                                _ph_dist_local = None
                                if _item_phash and _ph_local:
                                    try:
                                        _ph_dist_local = hamming_hex(_item_phash, _ph_local)
                                    except Exception:
                                        _ph_dist_local = None
                                _title_local_hit = bool(_title_jp_norm and _title_jp_norm in _vt_norm)
                                _cj_local = 0.0
                                if _cv_local and _item_cover:
                                    try:
                                        _cj_local = ocr_cover.cover_jaccard(_item_cover, _cv_local)
                                    except Exception:
                                        _cj_local = 0.0
                                # Hard requirement: same album signal.
                                # For "all" mode watches (e.g. TYCT-39145 Ado 逆光, AICL-3987
                                # 髭男dism Traveler), we require BOTH artist AND title_jp hits
                                # AND phash distance <= 18. This prevents surfacing wrong-album
                                # samples that happen to share a common word (孙燕姿 逆光 vs
                                # Ado 逆光, or 手帐 Traveler vs 髭男dism Traveler).
                                # For "any" mode, we accept any one of: phash<=12, title_jp hit,
                                # cover_jaccard>=0.25.
                                if _wmode == "all":
                                    _a_local_hit = bool(_artist_norm and _artist_norm in _vt_norm)
                                    same_album = (
                                        _a_local_hit
                                        and _title_local_hit
                                        and (_ph_dist_local is not None and _ph_dist_local <= 18)
                                    )
                                else:
                                    same_album = (
                                        (_ph_dist_local is not None and _ph_dist_local <= 12)
                                        or _title_local_hit
                                        or _cj_local >= 0.25
                                    )
                                if not same_album:
                                    continue
                                display_sample_id = getattr(_v, "market_item_id", None) or getattr(_v, "id", None)
                                break
                            # Hard floor for "all" mode: keep original all-mode behaviour (artist + kw)
                            if _wmode == "all" and (_artist_norm or _kws_norm) and display_sample_id is not None:
                                _chosen = next((v for s, pd, v in _scored if (getattr(v, "market_item_id", None) or getattr(v, "id", None)) == display_sample_id), None)
                                if _chosen is not None:
                                    _vt_n = "".join((_chosen.title or "").lower().split())
                                    _a_hit = bool(_artist_norm and _artist_norm in _vt_n)
                                    _kw_all = all(_kw in _vt_n for _kw in _kws_norm) if _kws_norm else True
                                    if not (_a_hit and _kw_all):
                                        display_sample_id = None
                    except Exception:
                        pass

                    new_id = insert_opportunity(db_path, opportunity, wameiji_item_id=row["id"])
                    if new_id:
                        seen_hashes_this_run.add(opportunity.opportunity_hash)
                        opp_inserted += 1
                        if new_conf > best_conf:
                            best_conf = new_conf
                            best_opp_id = new_id
                            best_cat = cat
                        # Record which xianyu sample to display in the feed
                        if display_sample_id is not None:
                            display_sample_ids[new_id] = display_sample_id

                # Backfill catalog_no only when (a) signal is strong AND (b) watchlist artist appears in item title.
                # (P0 - prevents catalog poisoning: e.g. Official 3t8 disp Traveler being backfilled to Ado album)
                # Skip if title indicates a compilation/album-set (these aren't specific to any single album).
                if best_cat and best_conf >= 0.55:
                    watch_row_for_cat = watch_by_catalog.get(best_cat)
                    artist_ok = False
                    if watch_row_for_cat and watch_row_for_cat.get("artist"):
                        artist = watch_row_for_cat["artist"].strip()
                        title_norm = (item_title or "").replace(" ", "").lower()
                        artist_norm = artist.replace(" ", "").lower()
                        if artist_norm and len(artist_norm) >= 2 and artist_norm in title_norm:
                            artist_ok = True
                        else:
                            head = artist_norm[:2]
                            if head and head in title_norm:
                                artist_ok = True
                    title_lower = (item_title or "").lower()
                    is_compilation = any(p in title_lower for p in ["歌合集", "歌ってみた", "うたってみた", "まとめ", "セット", "ベスト", "best", "box", "collection", "complete", "ヒッツ", "hits", "大全"])
                    if artist_ok and not is_compilation and (row["catalog_no"] in (None, "", "AUTO") or row["catalog_no"] != best_cat):
                        catalog_updates[row["id"]] = best_cat
                        catalog_backfilled += 1

            except Exception as e:
                unmatched += 1
                try:
                    rid = row["id"]
                except Exception:
                    rid = "?"
                print(f"[match_v2] err item={rid}: {e}", flush=True)

        # Apply catalog + jan backfills in one batch
        for iid, cat in catalog_updates.items():
            try:
                conn.execute("UPDATE market_items SET catalog_no=? WHERE id=?", (cat, iid))
            except Exception:
                pass
        for oid, sid in display_sample_ids.items():
            try:
                conn.execute("UPDATE opportunities SET xianyu_display_sample_id=? WHERE id=?", (sid, oid))
            except Exception:
                pass
        for iid, jan in jan_updates.items():
            try:
                conn.execute("UPDATE market_items SET jan=? WHERE id=?", (jan, iid))
            except Exception:
                pass
        conn.commit()

    return {
        "items_evaluated": items_evaluated,
        "opportunities_inserted": opp_inserted,
        "unmatched": unmatched,
        "hydrated": 0,
        "phashes": 0,
        "img_skip_dup": img_skip_dup,
        "candidate_cats": candidate_cats,
        "cover_matches": cover_matches,
        "phash_matches": phash_matches,
        "jan_matches": jan_matches,
        "title_matches": title_matches,
        "catalog_backfilled": catalog_backfilled,
    }


def _auto_cleanup_opportunities(db_path):
    # Drop negative-profit and duplicate wameiji_item_id opportunities.
    import sqlite3
    out = {"deleted_negatives": 0, "deleted_dups": 0}
    try:
        with sqlite3.connect(db_path, timeout=60) as _conn:
            _cur = _conn.execute("DELETE FROM opportunities WHERE expected_profit <= 0")
            out["deleted_negatives"] = _cur.rowcount
            _dups = _conn.execute(
                "SELECT wameiji_item_id, COUNT(*) FROM opportunities "
                "WHERE wameiji_item_id IS NOT NULL "
                "GROUP BY wameiji_item_id HAVING COUNT(*) > 1"
            ).fetchall()
            for wid, cnt in _dups:
                _rows = _conn.execute(
                    "SELECT id, expected_profit FROM opportunities WHERE wameiji_item_id=? "
                    "ORDER BY expected_profit DESC",
                    (wid,),
                ).fetchall()
                for rid, profit in _rows[1:]:
                    _conn.execute("DELETE FROM opportunities WHERE id=?", (rid,))
                    out["deleted_dups"] += 1
            _conn.commit()
    except Exception as _e:
        print("[auto_cleanup] err: " + str(_e), flush=True)
    return out


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else DB
    result = match_real_market_items(db)
    cleanup = _auto_cleanup_opportunities(db)
    print("CLEANUP", cleanup, flush=True)
    print("RESULT", result, flush=True)
