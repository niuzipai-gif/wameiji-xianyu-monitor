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
    MarketItem, WatchItem, XianyuPriceSample, CostConfig, EvaluationConfig,
)
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price  # noqa: E402
from cd_monitor.storage.sqlite import (                          # noqa: E402
    insert_opportunity, init_db,
)
import ocr_cover                                                # noqa: E402
import phash_compute                                            # noqa: E402


# ---------- thresholds ----------
COVER_JACCARD_MIN = 0.08   # lowered from 0.20 to surface more candidates on small/partial OCR
COVER_SHARE_MIN = 0.20        # lowered from 0.40
PHASH_DISTANCE_STRONG = 14    # raised from 10 to cover mercari CDN crops
PHASH_DISTANCE_WEAK = 22      # raised from 18
TITLE_JACCARD_MIN = 0.10      # lowered from 0.25 to surface cross-lang matches
TITLE_SHARE_MIN = 0.40        # new - if 40% tokens in wameiji title appear in xianyu, consider match
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
            "expected_holding_days, edition FROM watchlist"
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
            "SELECT title, price, url, image_url, raw_text, fetched_at "
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
                seller_text=None,
                raw_text=r["raw_text"],
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


def make_watch_item(w: dict) -> WatchItem:
    kw = []
    if w.get("required_keywords"):
        kw = [s.strip() for s in str(w["required_keywords"]).split(",") if s.strip()]
    excl = []
    if w.get("excluded_keywords"):
        excl = [s.strip() for s in str(w["excluded_keywords"]).split(",") if s.strip()]
    return WatchItem(
        catalog_no=w["catalog_no"],
        artist=w.get("artist"),
        jan=w.get("jan"),
        required_keywords=kw,
        excluded_keywords=excl,
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
    watches: list | None = None,
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
                if title_share(item_title, s["title"]) >= TITLE_SHARE_MIN:
                    candidates.append(cat)
                    seen.add(cat)
                    break

    # 5. Artist-name fallback: if wameiji title contains a watched artist,
    #    add all of that artist's watched catalogs as weak candidates.
    if item_title:
        t = item_title.lower()
        artists_in_title = set()
        wlist = watches or []
        for w in wlist:
            artist = (w.get("artist") or "").strip().lower()
            if not artist or len(artist) < 2:
                continue
            if artist in t:
                artists_in_title.add(artist)
        for w in wlist:
            artist = (w.get("artist") or "").strip().lower()
            if artist in artists_in_title:
                cat = w.get("catalog_no") or ""
                if cat in watched_catalogs and cat not in seen:
                    candidates.append(cat)
                    seen.add(cat)

    # 6. Existing catalog_no (fast path)
    existing = (item_row["catalog_no"] or "").strip()
    if existing and existing != "AUTO" and existing in watched_catalogs and existing not in seen:
        candidates.append(existing)
        seen.add(existing)

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
        # Track catalog_no + jan updates to apply at the end (one UPDATE per item max)
        catalog_updates = {}
        jan_updates = {}

        for row in item_rows:
            items_evaluated += 1
            try:
                item = make_market_item(row)
                item_title = row["title"] or ""
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
                    xianyu_index, watched_catalogs, watches=watches,
                )
                if not candidate_list:
                    unmatched += 1
                    continue

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
                    if not samples:
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
                    cost = compute_landed_cost(
                        item, config=cost_cfg, expected_holding_days=watch.expected_holding_days,
                    )
                    opportunity = evaluate_opportunity(watch, item, match, xianyu, cost, eval_cfg)

                    if opportunity.opportunity_hash in seen_hashes_this_run:
                        continue

                    new_id = insert_opportunity(db_path, opportunity, wameiji_item_id=row["id"])
                    if new_id:
                        seen_hashes_this_run.add(opportunity.opportunity_hash)
                        opp_inserted += 1
                        if new_conf > best_conf:
                            best_conf = new_conf
                            best_opp_id = new_id
                            best_cat = cat

                # Backfill catalog_no if missing/auto
                if best_cat and (row["catalog_no"] in (None, "", "AUTO") or row["catalog_no"] != best_cat):
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


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else DB
    result = match_real_market_items(db)
    print("RESULT", result, flush=True)