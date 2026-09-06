#!/usr/bin/env python3
"""Harvest CD items from meruki.cn malls using the wameiji login state.

meruki.cn (挖煤姬) proxies Japanese second-hand CD sources through
/mall/<name>?keyword=<kw>:
  - surugaya    -> 駿河屋
  - lashinbang  -> Lashinbang
  - mercari     -> Mercari JP
  - rakuten     -> 楽天 (Rakuten)
  - rakuma      -> 楽天ラクマ (Rakuma)
  - paypay      -> Yahoo PayPay (Yahoo flea market)
  - bunjang     -> 闪电市场 (Bunjang KR)

We re-use the same live_extract_wameiji.js that the manual explore script
uses, then persist each hit as a market_items row with source='wameiji'
and source_site=<mall-name>. Title heuristics (is_cd / is_merch / is_fatal)
match multi_source_scraper.py so downstream match_real stays source-agnostic.

One-shot cycle (no main loop) so it can be invoked from cron / Task Scheduler
without leaking chrome. Each cycle reuses a single Playwright browser but
rebuilds the page after a dead-page signal (xianyu scraper can SIGKILL chrome
in the same container if it runs in parallel).
"""
from __future__ import annotations
import asyncio
import json
import os
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path
from playwright.async_api import async_playwright

import os as _os
_BASE = _os.environ.get("HARVEST_BASE") or _os.path.dirname(_os.path.abspath(__file__))
DB = _os.environ.get("CD_DB") or (_os.path.join(_BASE, "cd_monitor.db") if _os.path.exists(_os.path.join(_BASE, "cd_monitor.db")) else "/app/data/cd_monitor.db")
STATE_FILE = _os.path.join(_BASE, "wameiji_state.json")
EXTRACT_JS_PATH = _os.path.join(_BASE, "live_extract_wameiji.js")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

ARTIST_KEYWORDS = {
    "Ado": ["Ado \u72c2\u8a00", "Ado Hibana", "Ado \u5535", "Ado \u6b8b\u5922"],
    "YOASOBI": ["YOASOBI THE BOOK", "YOASOBI \u30a2\u30a4\u30c9\u30eb", "YOASOBI \u7fa4\u9752", "YOASOBI ADRENA"],
    "\u9b0f\u7537dism": ["Official\u9b0f\u7537dism ESCAPARADE", "Official\u9b0f\u7537dism Editorial", "Official\u9b0f\u7537dism Traveler"],
    "King Gnu": ["King Gnu CEREMONY", "King Gnu THE GREATEST UNKNOWN", "King Gnu BOY"],
    "ONE OK ROCK": ["ONE OK ROCK Niche", "ONE OK ROCK Eye of the Storm", "ONE OK ROCK Luxury Disease"],
    "LiSA": ["LiSA ALTEREGO", "LiSA \u7d05\u84b9\u83ef", "LiSA \u708e"],
    "\u690e\u540d\u6797\u6854": ["\u690e\u540d\u6797\u6854 \u9006\u8f38\u5165", "\u690e\u540d\u6797\u6854 \u52dd\u8a34", "\u690e\u540d\u6797\u6854 \u4e09\u6587\u5802"],
    "\u7c73\u6d25\u7384\u5e2b": ["\u7c73\u6d25\u7384\u5e2b Lemon", "\u7c73\u6d25\u7384\u5e2b KICK BACK", "\u7c73\u6d25\u7384\u5e2b BOOTLEG"],
    "Vaundy": ["Vaundy replica", "Vaundy \u602a\u7363\u306e\u82b1\u548f", "Vaundy \u88f8\u306e\u52c7\u8005"],
    "\u8429\u4e95\u98a8": ["\u8429\u4e95\u98a8 HELP EVER HURT NEVER", "\u8429\u4e95\u98a8 LOVE ALL SERVE ALL", "\u8429\u4e95\u98a8 \u6b7b\u306c\u306e\u304c\u3044\u3044\u308f"],
}

# Malls that proxy through meruki.cn. rakuma/paypay/bunjang are noisier
# (lots of general flea-market noise) and use the strict CD filter below.
MALLS = ("surugaya", "lashinbang", "mercari", "rakuten", "rakuma", "paypay", "bunjang")
# Malls where title must match a known watch catalog/artist OR contain a strong
# CD keyword. These are general-merchandise proxies where we cannot trust a
# bare CD indicator ("\u672a\u958b\u5c01" alone was matching Pokemon card packs).
STRICT_MALLS = {"rakuten", "paypay", "bunjang", "rakuma"}
MERCH = ("\u751f\u5199\u771f", "\u7f36\u30d0\u30c3\u30b8", "T\u30b7\u30e3\u30c4", "\u30d1\u30fc\u30ab\u30fc", "\u30bf\u30aa\u30eb", "\u30dd\u30fc\u30c1", "\u30b9\u30c6\u30c3\u30ab\u30fc", "\u30dd\u30b9\u30bf\u30fc", "\u3046\u3061\u308f", "\u30a2\u30af\u30b9\u30bf", "\u30ab\u30ec\u30f3\u30c0\u30fc", "\u30e9\u30f3\u30e3\u30fc\u30c9", "\u30b9\u30c8\u30e9\u30c3\u30d7", "\u30af\u30ea\u30a2\u30ab\u30fc\u30c9", "\u30de\u30b9\u30ad\u30f3\u30b0\u30c6\u30fc\u30d7", "Blu-ray", "BD", "DVD", "\u30da\u30f3\u30e9\u30a4\u30c8", "アクションフィギュア", "フィギュア", "figma", "ソフビ", "ぬいぐるみ", "マスコット", "キーホルダー", "クリアファイル", "アクリルスタンド", "ストラップチャーム", "ガチャガチャ", "Amiibo", "バッジ", "トレカ", "ゲーム", "アプリ", "GOODS")
# Strong CD cues only. Dropped "\u672a\u958b\u5c01" which false-positived card packs.
CD_INDICATORS = ("CD", "\u30a2\u30eb\u30d0\u30e0", "Album", "Mini Album", "\u30b7\u30f3\u30b0\u30eb", "\u30df\u30cb\u30a2\u30eb\u30d0\u30e0", "\u521d\u56de\u76e4", "\u901a\u5e38\u76e4", "Disc", "disk", "EP", "LP", "OST", "\u30bd\u30a6\u30eb\u30c8\u30c3\u30af", "Soundtrack", "Original Soundtrack")
FATAL_SUBSTR = ("\u30b1\u30fc\u30b9\u306e\u307f", "\u7a7a\u76d2", "\u7121\u76e4", "\u30c7\u30a3\u30b9\u30af\u306a\u3057", "\u7279\u5178\u306e\u307f", "\u76e4\u50b5", "\u30ec\u30f3\u30bf\u30eb", "\u30b5\u30f3\u30d7\u30eb", "\u898b\u672c\u76e4", "sample")

def is_merch(t): return any(p in t for p in MERCH)
def is_cd(t): return any(ind in t for ind in CD_INDICATORS)
def is_fatal(t): return any(p in t for p in FATAL_SUBSTR)
def clean_title(t):
    import re as _re
    return _re.sub(r"\s*\u306e\u30b5\u30e0\u30cd\u30a4\u30eb.*$", "", t or "").strip()

def existing_keys(conn, mall):
    return {r[0] for r in conn.execute("SELECT external_item_id FROM market_items WHERE source='wameiji' AND source_site=?", (mall,)).fetchall()}

def pick_catalog(title, watches):
    if not title: return "AUTO"
    t = title.lower()
    for w in watches:
        cat = (w["catalog_no"] or "").lower().replace("q-", "").replace("-", "")
        if cat and cat in t.replace("-", "").replace(" ", ""):
            return w["catalog_no"]
    for w in watches:
        artist = (w["artist"] or "").strip().lower()
        if artist and artist in t:
            return w["catalog_no"]
    return "AUTO"


def _strict_cd_check(title, watches):
    """Strict CD filter for noisy malls.

    Return True iff title maps to a known watchlist catalog/artist OR contains
    a strong CD indicator. Used for rakuten/paypay/bunjang/rakuma which mix
    general flea-market goods in.
    """
    if pick_catalog(title, watches) != "AUTO":
        return True
    return is_cd(title)


async def harvest_one(page, mall, kw, extract_js):
    """Navigate, evaluate extract JS, return (items, error_tag).

    error_tag == 'dead' means the page was closed (xianyu scraper killed chrome,
    or playwright self-recovered). Caller should rebuild page and retry once.
    error_tag == 'soft' is a transient error, just keep going.
    """
    url = "https://www.meruki.cn/mall/" + mall + "?keyword=" + urllib.parse.quote(kw)
    try:
        page.set_default_timeout(20000)
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(4500)
        items = await page.evaluate(extract_js)
        return (items or []), None
    except Exception as e:
        msg = str(e)[:120]
        dead = "has been closed" in msg or "Target page" in msg or "Target closed" in msg or "Connection closed" in msg
        print("  [" + mall + "] " + kw[:30] + " ERR: " + msg, flush=True)
        return [], ("dead" if dead else "soft")


async def _ensure_page(b, state, page_ref):
    """Re-create context/page when previous page died."""
    try:
        if page_ref["page"] is not None:
            await page_ref["page"].evaluate("() => 1")
            return page_ref["page"]
    except Exception:
        pass
    try:
        if page_ref["ctx"] is not None:
            await page_ref["ctx"].close()
    except Exception:
        pass
    page_ref["ctx"] = await b.new_context(storage_state=state, user_agent=UA, locale="ja-JP")
    page_ref["page"] = await page_ref["ctx"].new_page()
    print("  [page] recreated browser context (page died)", flush=True)
    return page_ref["page"]


async def main():
    if not Path(STATE_FILE).exists():
        print("ERR: state missing", flush=True)
        return 1
    state = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))["playwright_storage_state"]
    extract_js = Path(EXTRACT_JS_PATH).read_text(encoding="utf-8")
    Path("/app/data/_mall_heartbeat.json").write_text(json.dumps({"status": "running", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
    new = 0
    per_mall = {m: 0 for m in MALLS}
    skipped = {m: 0 for m in MALLS}
    skipped_nocd = {m: 0 for m in MALLS}
    conn = sqlite3.connect(DB, timeout=60)
    watches = [{"catalog_no": r[0], "artist": r[1]} for r in conn.execute("SELECT catalog_no, artist FROM watchlist").fetchall()]
    existing = {m: existing_keys(conn, m) for m in MALLS}
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page_ref = {"ctx": None, "page": None}
        for mall in MALLS:
            page = await _ensure_page(b, state, page_ref)
            for artist, kws in ARTIST_KEYWORDS.items():
                for kw in kws:
                    items, err = await harvest_one(page, mall, kw, extract_js)
                    if err == "dead":
                        page = await _ensure_page(b, state, page_ref)
                        items, err = await harvest_one(page, mall, kw, extract_js)
                    seen = set()
                    for it in items:
                        href = (it or {}).get("href") or ""
                        if not href or href in seen: continue
                        seen.add(href)
                        title = clean_title((it or {}).get("title", ""))
                        if not title or is_merch(title) or is_fatal(title):
                            skipped[mall] += 1
                            continue
                        # strict filter for noisy malls (rakuten/paypay/bunjang/rakuma)
                        if mall in STRICT_MALLS:
                            if not _strict_cd_check(title, watches):
                                skipped_nocd[mall] += 1
                                continue
                        else:
                            if not is_cd(title):
                                skipped_nocd[mall] += 1
                                continue
                        ext = (mall + ":" + href).split("/")[-1][:80]
                        if ext in existing[mall]: continue
                        cat = pick_catalog(title, watches)
                        try:
                            conn.execute("INSERT INTO market_items (source, source_site, external_item_id, catalog_no, title, price, currency, url, image_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", ("wameiji", mall, ext, cat, title, float((it or {}).get("price", 0) or 0), "JPY", href, (it or {}).get("image")))
                            conn.commit()
                            existing[mall].add(ext)
                            new += 1
                            per_mall[mall] += 1
                        except sqlite3.IntegrityError:
                            pass
        try:
            if page_ref["ctx"] is not None:
                await page_ref["ctx"].close()
        except Exception:
            pass
        await b.close()
    Path("/app/data/_mall_heartbeat.json").write_text(json.dumps({"status": "done", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "new": new, "per_mall": per_mall, "skipped": skipped, "skipped_nocd": skipped_nocd}, ensure_ascii=False), encoding="utf-8")
    print("mall_harvest done new=" + str(new) + " per_mall=" + str(per_mall) + " skipped=" + str(skipped) + " skipped_nocd=" + str(skipped_nocd), flush=True)
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
