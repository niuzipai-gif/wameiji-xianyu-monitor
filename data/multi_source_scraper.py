#!/usr/bin/env python3
"""multi_source_scraper.py - Scrape Mercari JP, Yahoo Auctions JP, Suruga-ya.

All three feed into the same `market_items` table with `source='wameiji'`
and `source_site` set to 'mercari_jp' / 'yahoo_auctions' / 'suruga_ya'.
Title heuristics (is_cd / is_merch / catalog mapping) are shared so the
downstream match_real pipeline stays source-agnostic.
"""
from __future__ import annotations

import json
import re
import os
import sys
import time
import asyncio
import sqlite3
import urllib.parse
import signal
from pathlib import Path
from typing import Iterable

_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))  # for phash_compute

import phash_compute  # noqa: E402

DB = os.environ.get("CD_DB", str(_BASE_DIR / "cd_monitor.db"))
HEARTBEAT_FILE = Path(
    os.environ.get("SCRAPER_HEARTBEAT_FILE") or (_BASE_DIR / "_scraper_heartbeat.json")
)

# -------- shared keyword allow/deny lists --------
MERCH = ["生写真", "缶バッジ", "Tシャツ", "パーカー", "タオル", "ポーチ",
         "ステッカー", "ポスター", "うちわ", "アクスタ", "カレンダー",
         "ランヤード", "ストラップ", "クリアカード", "マスキングテープ",
         "ペンライト"]
CD_INDICATORS = ["CD", "アルバム", "シングル", "ミニアルバム", "初回盤", "未開封", "通常盤"]
FATAL_SUBSTR = ["ケースのみ", "空盒", "無盤", "ディスクなし", "盘なし",
                "特典のみ", "盤傷", "レンタル", "サンプル", "見本盤", "sample"]

ARTIST_KEYWORDS: dict[str, list[str]] = {}
# Will be populated by main_loop from the watchlist DB so every watched
# catalog gets its own search keyword (artist + title_jp).

def is_merch(t): return any(p in t for p in MERCH)
def is_cd(t):     return any(ind in t for ind in CD_INDICATORS)
def is_fatal(t):  return any(p in t for p in FATAL_SUBSTR)
def clean_title(t):
    return re.sub(r"\s*のサムネイル.*$", "", t or "").strip()


def load_watch(db_path: str) -> list[dict]:
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT catalog_no, artist, title_jp, title_cn FROM watchlist").fetchall()
    return [{"catalog_no": r["catalog_no"], "artist": r["artist"] or "", "title_jp": r["title_jp"] or "", "title_cn": r["title_cn"] or ""} for r in rows]


def pick_catalog(title: str, watches: list[dict]) -> str:
    """Best-effort catalog mapping.

    Priority:
      1. catalog_no token in title (strong, exact)
      2. artist + title_jp both in title (strict, album-specific)
      3. required_keywords overlap (medium)
      4. fallback: AUTO (let pipeline decide)

    Compilation/album-set titles (まとめ/歌合集/セット/BEST/BOX/シングル集)
    are flagged as AUTO so they don't hijack a specific album catalog.
    """
    if not title:
        return "AUTO"
    # Compilation/album-set detection: skip catalog assignment for these
    _COMPILATION_PATTERNS = (
        "歌合集", "歌ってみた", "うたってみた", "まとめ", "セット",
        "ベスト", "BEST", "box", "BOX", "シングル集", "singlecollection",
        "collection", "Collection", "全曲", "complete", "Complete",
        "オール", "all", "ヒッツ", "hits", "Hits", "大全",
    )
    t_lower = title.lower()
    is_compilation = any(p.lower() in t_lower for p in _COMPILATION_PATTERNS)
    import json as _json
    t = t_lower
    t_compact = t.replace("-", "").replace(" ", "")
    # 1. catalog_no token exact
    for w in watches:
        cat = (w.get("catalog_no") or "").lower().replace("q-", "").replace("-", "")
        if cat and cat in t_compact:
            return w["catalog_no"]
    # 2. artist + title_jp both in title (strict album-specific match).
    # Sort by title_jp length descending so longer/more-specific titles (THE BOOK 2)
    # win over shorter ones (THE BOOK) when both match.
    _candidates_step2 = []
    for w in watches:
        artist = (w.get("artist") or "").strip().lower()
        title_jp = (w.get("title_jp") or "").strip().lower()
        if not artist or not title_jp:
            continue
        artist_c = artist.replace(" ", "").replace("-", "")
        title_jp_c = title_jp.replace(" ", "").replace("-", "")
        if artist_c in t_compact and title_jp_c in t_compact:
            _candidates_step2.append((len(title_jp_c), w["catalog_no"]))
    if _candidates_step2:
        if is_compilation:
            return "AUTO"
        # Prefer the longest matching title_jp (most specific)
        _candidates_step2.sort(reverse=True)
        return _candidates_step2[0][1]
    # 3. artist + at least one ALBUM-SPECIFIC keyword (excluding the artist name itself).
    # This avoids mapping every Ado listing to whichever Ado catalog comes first.
    for w in watches:
        artist = (w.get("artist") or "").strip().lower()
        if not artist:
            continue
        artist_c = artist.replace(" ", "").replace("-", "")
        if artist_c not in t_compact:
            continue
        title_jp = (w.get("title_jp") or "").strip().lower().replace(" ", "").replace("-", "")
        rk_raw = w.get("required_keywords") or "[]"
        try:
            rk = _json.loads(rk_raw) if isinstance(rk_raw, str) and rk_raw.startswith("[") else []
        except Exception:
            rk = []
        # Album-specific signal: title_jp matches OR a non-artist keyword matches.
        if title_jp and title_jp in t_compact:
            return w["catalog_no"]
        for k in rk:
            if not k:
                continue
            kc = k.lower().replace(" ", "").replace("-", "")
            # Skip the artist name (which is also often in required_keywords).
            if kc == artist_c or kc in artist_c:
                continue
            if kc in t_compact:
                return w["catalog_no"]
    return "AUTO"

# ============================================================
#  Source 1: Mercari JP  (existing behaviour, refactored)
# ============================================================
async def search_mercari(page, kw: str, n: int = 5, *, sort: str = "", page_num: int = 1) -> list[dict]:
    sort_q = ("&sort=" + sort) if sort else ""
    page_q = ("&page_token=" + str(page_num)) if page_num > 1 else ""
    url = "https://jp.mercari.com/search?keyword=" + urllib.parse.quote(kw, safe="") + "&status=on_sale" + sort_q + page_q
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)
        items = await page.eval_on_selector_all(
            "a[href*='/item/']",
            """(els) => els.slice(0, 12).map(el => {
              const img = el.querySelector("img");
              const ds = el.dataset || {};
              let t = ds.title || el.getAttribute("title") || img?.alt || "";
              if (t && t.includes("のサムネイル")) t = t.split("のサムネイル")[0];
              const priceText = ds.price || (el.textContent.match(/[0-9,]+/g) || []).join("");
              return { href: el.getAttribute("href") || "",
                       title: t.trim().slice(0, 200),
                       price: parseInt((priceText||"0").toString().replace(/[^0-9]/g, ""), 10) || 0,
                       image: img?.src || null };
            })"""
        )
        out, seen = [], set()
        for it in items or []:
            if not it or not it.get("href") or it["href"] in seen:
                continue
            _h = it["href"]
            if _h.startswith("/"): _h = "https://jp.mercari.com" + _h
            if _h in seen: continue
            seen.add(_h)
            out.append({
                "href": _h,
                "title": clean_title(it.get("title", "")),
                "price": int(it.get("price", 0) or 0),
                "image": it.get("image"),
            })
            if len(out) >= n:
                break
        return out
    except Exception as _e:
        print(f"[scraper] mercari err kw={kw[:30]}: {str(_e)[:120]}", flush=True)
        return []


# ============================================================
#  Source 2: Yahoo Auctions JP
# ============================================================
async def search_yahoo_auctions(page, kw: str, n: int = 5, *, sort: str = "", page_num: int = 1) -> list[dict]:
    sort_q = ("&sort=" + sort) if sort else ""
    page_q = ("&page=" + str(page_num)) if page_num > 1 else ""
    url = "https://auctions.yahoo.co.jp/search/search?p=" + urllib.parse.quote(kw, safe="") + "&auction_type=1" + sort_q + page_q
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)
        # Yahoo Auctions uses module-based listings
        items = await page.eval_on_selector_all(
            "li.Product",
            """(els) => els.slice(0, 12).map(el => {
              const a = el.querySelector("a.Product__titleLink, a.Product__imageLink, a");
              const img = el.querySelector("img.Product__image, img");
              const titleEl = el.querySelector(".Product__title, h3, .title");
              const priceEl = el.querySelector(".Product__price, .Price, .pr");
              const t = (titleEl ? titleEl.textContent : (a ? (a.textContent || a.getAttribute("title") || "") : "")).trim();
              let price = 0;
              if (priceEl) {
                const m = (priceEl.textContent || "").replace(/[^0-9]/g, "");
                if (m) price = parseInt(m, 10) || 0;
              }
              if (!price && a) {
                const m2 = ((a.textContent || "") + " " + (a.getAttribute("data-price")||"")).replace(/[^0-9]/g, "");
                if (m2) price = parseInt(m2, 10) || 0;
              }
              return {
                href: a ? (a.getAttribute("href") || "") : "",
                title: t.slice(0, 200),
                price: price,
                image: img ? (img.getAttribute("src") || img.getAttribute("data-src") || "") : null,
              };
            })"""
        )
        out, seen = [], set()
        for it in items or []:
            if not it or not it.get("href"):
                continue
            href = it["href"]
            if href.startswith("/"):
                href = "https://auctions.yahoo.co.jp" + href
            if href in seen:
                continue
            seen.add(href)
            out.append({
                "href": href,
                "title": clean_title(it.get("title", "")),
                "price": int(it.get("price", 0) or 0),
                "image": it.get("image"),
            })
            if len(out) >= n:
                break
        return out
    except Exception as _e:
        print(f"[scraper] mercari err kw={kw[:30]}: {str(_e)[:120]}", flush=True)
        return []


# ============================================================
#  Source 3: Suruga-ya
# ============================================================
async def search_suruga_ya(page, kw: str, n: int = 5, *, sort: str = "", page_num: int = 1) -> list[dict]:
    """Suruga-ya is Cloudflare-protected. We attempt with a tight timeout and
    fall back to a Bing site-search snapshot so the cycle still surfaces fresh
    listings when the live search is blocked. The fallback is best-effort.
    """
    url = "https://www.suruga-ya.jp/search?q=" + urllib.parse.quote(kw, safe="")
    bing_cache = (
        "https://www.bing.com/search?q=" + urllib.parse.quote("site:suruga-ya.jp " + kw, safe="")
    )
    out: list[dict] = []
    cloudflare_hit = False
    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        except Exception:
            cloudflare_hit = True
        if not cloudflare_hit:
            await page.wait_for_timeout(2500)
            try:
                title = (await page.title() or "")
                html = (await page.content() or "").lower()
                if ("please wait" in title.lower()
                        or "しばらくお待ちください" in title
                        or "challenges.cloudflare.com" in html):
                    cloudflare_hit = True
            except Exception:
                pass
        if not cloudflare_hit:
            items = await page.eval_on_selector_all(
                ".productListItem, li.product, .item_list .item, ul.products li",
                """(els) => els.slice(0, 12).map(el => {
                  const a = el.querySelector("a");
                  const img = el.querySelector("img");
                  const tEl = el.querySelector(".title, h3, .product__title");
                  const pEl = el.querySelector(".price, .product__price");
                  const t = (tEl ? tEl.textContent : (a ? (a.getAttribute("title") || a.textContent || "") : "")).trim();
                  let price = 0;
                  if (pEl) {
                    const m = (pEl.textContent || "").replace(/[^0-9]/g, "");
                    if (m) price = parseInt(m, 10) || 0;
                  }
                  return { href: a ? (a.getAttribute("href") || "") : "",
                           title: t.slice(0, 200), price: price,
                           image: img ? (img.getAttribute("src") || img.getAttribute("data-src") || "") : null };
                })"""
            )
            seen = set()
            for it in items or []:
                if not it or not it.get("href"):
                    continue
                href = it["href"]
                if href.startswith("/"):
                    href = "https://www.suruga-ya.jp" + href
                if href in seen:
                    continue
                seen.add(href)
                out.append({
                    "href": href,
                    "title": clean_title(it.get("title", "")),
                    "price": int(it.get("price", 0) or 0),
                    "image": it.get("image"),
                })
                if len(out) >= n:
                    break
        if cloudflare_hit:
            # Bing cache: get a snapshot of page links matching site:suruga-ya.jp
            try:
                await page.goto(bing_cache, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2500)
                hrefs = await page.eval_on_selector_all(
                    "a[href*=\'suruga-ya.jp\']",
                    "(els) => els.slice(0, 8).map(a => a.href).filter(h => /suruga-ya\.jp\/(detail|product)/.test(h))"
                )
                for h in hrefs or []:
                    out.append({"href": h, "title": kw + " (Bing cache)", "price": 0, "image": None})
                    if len(out) >= n:
                        break
            except Exception:
                pass
        return out
    except Exception:
        return out


async def run_cycle(playwright, *, source_fns: dict, watches: list[dict]) -> dict:
    """Run one full cycle across all configured sources, persist items, return counts."""
    new_count = 0
    per_source = {k: 0 for k in source_fns.keys()}
    existing = set()
    try:
        with sqlite3.connect(DB, timeout=60) as conn:
            existing_rows = conn.execute(
                "SELECT external_item_id, source_site FROM market_items WHERE source='wameiji'"
            ).fetchall()
            existing = {(r[1] or "", r[0]) for r in existing_rows}
    except Exception as _e:
        print("[scraper] existing-load err: " + str(_e)[:120], flush=True)

    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--single-process"],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
        viewport={"width": 1366, "height": 768},
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        color_scheme="light",
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="125", "Google Chrome";v="125", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    # Stealth: override navigator.webdriver
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP','ja','en-US','en'] });
        window.chrome = { runtime: {} };
    """)
    page = await context.new_page()

    for site, fn in source_fns.items():
        for artist, kws in ARTIST_KEYWORDS.items():
            _INTERVAL = int(os.environ.get("SCRAPER_INTERVAL", "900"))
            _variation = int(time.time()) // max(60, _INTERVAL)
            for kw_i, kw in enumerate(kws):
                # Diversity: do 2 sort variants per kw so listings differ between cycles.
                # (site, ext) dedup below keeps inserts unique.
                _base_sort_i = (_variation + kw_i) % 4
                _base_page = ((_variation // 2 + kw_i) % 3) + 1
                _calls = [
                    ("",              _base_page),     # default sort = newest first
                    ("price_asc",     _base_page + 1), # page 2 cheaper listings
                ]
                results = []
                for _sort, _page in _calls:
                    try:
                        _r = await fn(page, kw, n=8, sort=_sort, page_num=_page)
                        for _it in _r:
                            if _it not in results:
                                results.append(_it)
                    except Exception as _se:
                        print(f"[scraper] {site} search err {kw[:20]} sort={_sort}: {str(_se)[:60]}", flush=True)
                    if len(results) >= 16:
                        break
                for r in results:
                    try:
                        ext = (site + ":" + r["href"]).split("/")[-1][:80]
                        if (site, ext) in existing:
                            continue
                        if is_merch(r["title"]):
                            continue
                        if not is_cd(r["title"]):
                            continue
                        if is_fatal(r["title"]):
                            continue
                        cat = pick_catalog(r["title"], watches)
                        # Compute phash synchronously so the new wameiji item can be
                        # matched by visual similarity against xianyu samples right away.
                        _ph = None
                        _dh = None
                        try:
                            _ph, _dh = phash_compute.compute_for_url(r["image"])
                        except Exception:
                            pass
                        with sqlite3.connect(DB, timeout=30) as conn:
                            conn.execute(
                                "INSERT INTO market_items "
                                "(source, source_site, external_item_id, catalog_no, title, price, currency, url, image_url, fetched_at, image_phash, image_dhash) "
                                "VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?,?)",
                                ("wameiji", site, ext, cat, r["title"], float(r["price"]), "JPY", r["href"], r["image"], _ph, _dh),
                            )
                            conn.commit()
                        existing.add((site, ext))
                        new_count += 1
                        per_source[site] += 1
                    except Exception as _ie:
                        print(f"[scraper] {site} insert err: {str(_ie)[:80]}", flush=True)
                # Per-kw progress
                if results:
                    print(f"[scraper] {site} kw={kw[:30]} results={len(results)}", flush=True)
                # Stealth: small random delay between kws to avoid rate-limit patterns
                import random as _rnd
                await asyncio.sleep(_rnd.uniform(2.5, 5.0))

    await context.close()
    await browser.close()
    return {"new": new_count, "per_source": per_source}


def kill_chrome() -> int:
    if os.name == "nt":
        return 0
    killed = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        if pid == str(os.getpid()):
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                name = f.read().strip()
            if "chrome" in name.lower() or "chromium" in name.lower():
                os.kill(int(pid), signal.SIGKILL)
                killed += 1
        except Exception:
            pass
    return killed


def write_heartbeat(status, count=None, msg="") -> None:
    try:
        data = {"status": status, "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                "count": count, "msg": msg, "pid": os.getpid()}
        with HEARTBEAT_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


async def main_loop():
    from playwright.async_api import async_playwright
    INTERVAL_SEC = int(os.environ.get("SCRAPER_INTERVAL", "900"))
    write_heartbeat("starting")
    print("[scraper] starting multi-source v3, interval=" + str(INTERVAL_SEC) + "s", flush=True)

    # which sources to enable
    enabled = os.environ.get("ENABLED_SOURCES", "mercari_jp,yahoo_auctions,suruga_ya").split(",")
    source_fns = {
        "mercari_jp":    search_mercari,
        "yahoo_auctions": search_yahoo_auctions,
        "suruga_ya":     search_suruga_ya,
    }
    enabled_fns = {s.strip(): source_fns[s.strip()] for s in enabled if s.strip() in source_fns}
    print("[scraper] enabled: " + ", ".join(enabled_fns.keys()), flush=True)

    while True:
        try:
            write_heartbeat("running")
            watches = load_watch(DB)
            # Populate ARTIST_KEYWORDS from watchlist. Each watchlist entry
            # becomes one keyword combining artist + title_jp so the search
            # actually targets the album we want (not the artist in general).
            global ARTIST_KEYWORDS
            ARTIST_KEYWORDS = {}
            for w in watches:
                artist = (w.get("artist") or "").strip()
                title = (w.get("title_jp") or "").strip()
                if not artist or not title:
                    continue
                key = f"{artist[:30]}::{w.get('catalog_no','')[:20]}"
                kw = f"{artist} {title}".strip()
                ARTIST_KEYWORDS[key] = [kw]
            if not ARTIST_KEYWORDS:
                ARTIST_KEYWORDS = {
                    "fallback::YOASOBI": ["YOASOBI THE BOOK", "YOASOBI アイドル"],
                    "fallback::Ado": ["Ado 狂言", "Ado 唱"],
                    "fallback::KingGnu": ["King Gnu CEREMONY"],
                }
            print(f"[scraper] keywords from watchlist: {len(ARTIST_KEYWORDS)} entries", flush=True)
            async with async_playwright() as p:
                res = await run_cycle(p, source_fns=enabled_fns, watches=watches)
            killed = kill_chrome()
            print("[scraper] cycle done, new=" + str(res["new"]) + ", per_source=" + str(res["per_source"]) + ", killed_chrome=" + str(killed), flush=True)
            write_heartbeat("idle", count=res["new"], msg="cycle done, " + str(res["per_source"]))
            # Run real-data match pass after scrape
            # Trigger matching IN the web_server process to avoid 9p / WAL
            # contention between two python processes. The web_server already
            # holds the DB connection and can write without lock conflicts.
            try:
                import urllib.request, urllib.error
                _api_base = os.environ.get("CD_API_BASE", "http://127.0.0.1:9890")
                _req = urllib.request.Request(
                    _api_base + "/api/scrape/match",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(_req, timeout=10) as _resp:
                    _body = _resp.read().decode("utf-8", errors="ignore")[:200]
                print("[scraper] match pipeline triggered via HTTP: " + _body, flush=True)
            except urllib.error.URLError as _ue:
                print("[scraper] match HTTP failed (no web_server?): " + str(_ue)[:200], flush=True)
            except Exception as e:
                print("[scraper] match pipeline err: " + str(e)[:200], flush=True)
        except Exception as e:
            print("[scraper] cycle error: " + str(e)[:200], flush=True)
            write_heartbeat("error", msg=str(e)[:200])
            kill_chrome()
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main_loop())

