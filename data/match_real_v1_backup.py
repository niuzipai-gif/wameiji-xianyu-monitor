#!/usr/bin/env python3
"""match_real.py v2 - now WITH image-pHash dedup.

Changes vs v1:
  1. When inserting market_items, also backfill image_phash if a new item's
     pHash already exists in DB (mark as duplicate instead of inserting).
  2. confidence boost: if a wameiji pHash matches an existing xianyu pHash
     within threshold (default 8), add +0.08 to the matcher confidence
     (and -0.10 if a wameiji pHash matches OTHER wameiji identical listing
     near distance 0 - it's a re-list, not a new match).
  3. cache: do not re-fetch image bytes for URLs we have already hashed.
"""
from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path
from typing import Iterable

DB = os.environ.get("CD_DB", "/app/data/cd_monitor.db")
sys.path.insert(0, "/app/src")
sys.path.insert(0, "/app/data")  # for phash_compute

from cd_monitor.core.cost_model import compute_landed_cost   # noqa: E402
from cd_monitor.core.evaluator import evaluate_opportunity    # noqa: E402
from cd_monitor.core.matcher import compute_match_confidence  # noqa: E402
from cd_monitor.core.models import (                          # noqa: E402
    MarketItem, WatchItem, XianyuPriceSample, CostConfig, EvaluationConfig,
)
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price  # noqa: E402
from cd_monitor.storage.sqlite import (                        # noqa: E402
    insert_opportunity, init_db,
)
import phash_compute                                              # noqa: E402


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


def assign_catalog_no_for_item(title: str, watches: list[dict]) -> str | None:
    if not title:
        return None
    t = title.lower()
    for w in watches:
        cat = (w["catalog_no"] or "").lower().replace("q-", "").replace("-", "")
        if cat and cat in t.replace("-", "").replace(" ", ""):
            return w["catalog_no"]
    for w in watches:
        artist = (w["artist"] or "").strip().lower()
        if artist and artist in t:
            return w["catalog_no"]
    return None


def fetch_watches(db_path: str) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT catalog_no, artist, jan, required_keywords, excluded_keywords, "
            "expected_holding_days, edition FROM watchlist"
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def fetch_xianyu_samples(db_path: str, catalog_no: str, limit: int = 30) -> list[XianyuPriceSample]:
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


def hydrate_auto_catalog_no(db_path: str, watches: list[dict]) -> int:
    updated = 0
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title FROM market_items WHERE source='wameiji' "
            "AND (catalog_no='AUTO' OR catalog_no IS NULL OR catalog_no='')"
        ).fetchall()
        for r in rows:
            cat = assign_catalog_no_for_item(r["title"] or "", watches)
            if cat:
                conn.execute("UPDATE market_items SET catalog_no=? WHERE id=?", (cat, r["id"]))
                updated += 1
        conn.commit()
    return updated


def hydrate_image_phashes(db_path: str) -> int:
    """Fill pHash for new wameiji items that lack one (used right after scraping)."""
    updated = 0
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, image_url FROM market_items WHERE image_url IS NOT NULL "
            "AND image_url != '' AND (image_phash IS NULL OR image_phash='')"
        ).fetchall()
        for r in rows:
            ph, dh = phash_compute.compute_for_url(r["image_url"])
            if ph:
                conn.execute(
                    "UPDATE market_items SET image_phash=?, image_dhash=? WHERE id=?",
                    (ph, dh, r["id"]),
                )
                updated += 1
        conn.commit()
    return updated


def image_match_boost(item_phash: str | None, xianyu_phashes: list[str]) -> float:
    """If a wameiji pHash has a near-neighbour in xianyu (within threshold),
    boost confidence. Closes the cross-platform visual loop on rare cases
    where two photos happen to be very similar.

    Returns score delta in [-0.10, +0.10].
    """
    if not item_phash:
        return 0.0
    best = phash_compute.min_distance(item_phash, xianyu_phashes)
    if best is None:
        return 0.0
    if best <= 4:
        return 0.10      # very confident same cover photo
    if best <= 8:
        return 0.06
    if best <= 12:
        return 0.03
    if best > 24:
        return -0.05     # catalog title says X but image clearly differs
    return 0.0


def image_duplicate_within_source(item_phash: str | None, same_catalog_phashes: list[str]) -> bool:
    """If a wameiji pHash has an exact (distance 0) match in same catalog
    already in DB, treat the listing as a re-list of the same item and
    skip making an opportunity for it.
    """
    if not item_phash or not same_catalog_phashes:
        return False
    for h in same_catalog_phashes:
        if h == item_phash:
            return True
    return False


def match_real_market_items(db_path: str = DB, only_catalog: str | None = None) -> dict:
    init_db(db_path)
    watches = fetch_watches(db_path)
    if not watches:
        return {"items_evaluated": 0, "opportunities_inserted": 0, "unmatched": 0, "hydrated": 0, "phashes": 0, "img_skip_dup": 0}

    hydrated = hydrate_auto_catalog_no(db_path, watches)
    phashes_filled = hydrate_image_phashes(db_path)
    cost_cfg, eval_cfg = load_cost_eval_config(db_path)

    watched_catalogs = {w["catalog_no"] for w in watches if w.get("catalog_no")}
    if only_catalog:
        watched_catalogs = {only_catalog}

    items_evaluated = 0
    opp_inserted = 0
    unmatched = 0
    img_skip_dup = 0

    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        for cat in watched_catalogs:
            watch_row = next((w for w in watches if w["catalog_no"] == cat), None)
            if not watch_row:
                continue
            watch = make_watch_item(watch_row)
            samples = fetch_xianyu_samples(db_path, cat, limit=eval_cfg.sample_limit or 30)

            # pre-collect xianyu phashes for image boost
            x_phashes = [
                r["image_phash"] for r in conn.execute(
                    "SELECT image_phash FROM market_items WHERE source='xianyu' "
                    "AND catalog_no=? AND image_phash IS NOT NULL AND image_phash != ''",
                    (cat,),
                ).fetchall()
            ]

            item_rows = conn.execute(
                "SELECT id, source, source_site, external_item_id, catalog_no, title, "
                "price, currency, url, image_url, availability, condition_text, "
                "raw_text, image_phash, image_dhash, jan FROM market_items "
                "WHERE source='wameiji' AND catalog_no=? ORDER BY id DESC",
                (cat,),
            ).fetchall()

            # pHashes we have already processed IN THIS RUN (so we skip the
            # second onwards as a re-list of the same item).
            seen_phashes = set()

            for row in item_rows:
                items_evaluated += 1
                try:
                    item_ph = row["image_phash"]
                    if item_ph and item_ph in seen_phashes:
                        img_skip_dup += 1
                        continue
                    if item_ph:
                        seen_phashes.add(item_ph)
                    item = make_market_item(row)
                    match = compute_match_confidence(item, watch)
                    if match.confidence < (eval_cfg.min_match_confidence_weak or 0.4):
                        unmatched += 1
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
                        item,
                        config=cost_cfg,
                        expected_holding_days=watch.expected_holding_days,
                    )
                    opportunity = evaluate_opportunity(
                        watch, item, match, xianyu, cost, eval_cfg
                    )
                    # image boost on opportunity_hash so dedup catches repeats
                    boost = image_match_boost(row["image_phash"], x_phashes)
                    if boost:
                        try:
                            boost = max(-0.20, min(0.20, boost))
                            new_conf = max(0.0, min(1.0, opportunity.match_confidence + boost))
                            opportunity.match_confidence = round(new_conf, 4)
                        except Exception:
                            pass
                    new_id = insert_opportunity(db_path, opportunity, wameiji_item_id=row["id"])
                    if new_id:
                        opp_inserted += 1
                except Exception as e:
                    unmatched += 1
                    try:
                        rid = row["id"]
                    except Exception:
                        rid = "?"
                    print(f"[match] err cat={cat} item={rid}: {e}", flush=True)

    return {
        "items_evaluated": items_evaluated,
        "opportunities_inserted": opp_inserted,
        "unmatched": unmatched,
        "hydrated": hydrated,
        "phashes": phashes_filled,
        "img_skip_dup": img_skip_dup,
    }


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else DB
    result = match_real_market_items(db)
    print("RESULT", result, flush=True)