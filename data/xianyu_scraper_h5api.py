"""xianyu_scraper_h5api.py - xianyu scraper via h5api JSON interception.

Reads watchlist, searches Goofish via the search page (which triggers an XHR
to h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/), and parses
the captured JSON response for clean item fields (title, price, area,
seller, image, item_id, publish time, tags). This is much more robust than
HTML scraping because:
  - The JSON schema is stable; HTML DOM changes don't break it.
  - image_url / item_id are the canonical API values, not lazy-load placeholders.
  - We get publish time and tags directly.

Same filters, dedup, and SQLite insertion as xianyu_scraper.py so the
schema and downstream matcher are unaffected.

Usage (inside container):
    SCRAPER_INTERVAL=900 nohup python3 -u xianyu_scraper_h5api.py &
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from playwright.async_api import async_playwright

DB = os.environ.get("CD_DB", "/var/lib/cd_monitor/cd_monitor.db")
STATE_FILE = "/app/data/xianyu_state.json"
HEARTBEAT_FILE = "/app/data/_xianyu_heartbeat.json"
LOG_FILE = "/app/data/_xianyu_h5api_scraper.log"
INTERVAL_SEC = int(os.environ.get("XIANYU_INTERVAL", "900"))

# h5api search endpoint that Goofish search page XHRs to.
H5API_SEARCH_FRAGMENT = "/h5/mtop.taobao.idlemtopsearch.pc.search/"
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# Background phash backfill: same as xianyu_scraper.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import phash_compute as _phc
    _HAS_PHASH = True
except Exception:
    _HAS_PHASH = False
_pending_phash: set = set()
_pending_lock = threading.Lock()


def _schedule_phash_backfill(image_url):
    if not _HAS_PHASH or not image_url:
        return
    with _pending_lock:
        _pending_phash.add(image_url)


def _phash_backfill_daemon():
    while True:
        time.sleep(2.0)
        try:
            with _pending_lock:
                pending = list(_pending_phash)
                _pending_phash.clear()
            for url in pending:
                try:
                    ph, dh = _phc.compute_for_url(url)
                except Exception:
                    continue
                if not ph:
                    continue
                try:
                    with sqlite3.connect(DB, timeout=10) as c:
                        c.execute(
                            "UPDATE market_items SET image_phash=?, image_dhash=? "
                            "WHERE image_url=? AND (image_phash IS NULL OR image_phash='')",
                            (ph, dh, url),
                        )
                        c.commit()
                except Exception:
                    pass
        except Exception:
            pass


if _HAS_PHASH:
    threading.Thread(target=_phash_backfill_daemon, daemon=True, name="xianyu-h5api-phash").start()

MERCH = ["生写", "自拍", "签名", "签名版", "签售", "周边", "徽章", "吧唧",
         "卡片", "亚克力", "钥匙扣", "票根", "海报", "毛巾", "t恤", "卫衣",
         "明信片", "卡贴", "透卡", "卡套", "夹层", "袋子", "亚克力牌", "立牌",
         "色纸", "手幅", "纸袋", "票夹", "专辑内页", "歌词卡", "小卡",
         "应援", "扇子", "马克杯", "水杯", "手机壳", "手机链", "镜子", "手链",
         "项链", "戒指", "耳环", "袜子", "贴纸", "笔记本", "本子", "胸针",
         "别针", "包装", "礼盒", "外封", "原盒", "空盒", "样品"]
FATAL = ["仅剩", "空盒", "盘无", "无盘", "无碟", "裸碟", "样品", "sample",
         "出租", "租赁", "代购", "代购费", "代下", "预订", "预购", "非卖品",
         "送人", "抽奖", "中奖", "免费送", "买一送", "买赠"]

CJK_CD_SIGNAL = ["专辑", "唱片", "初回", "通常盘", "限定盘", "限定版", "日版", "盘面"]


def log(msg, flush=True):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def write_heartbeat(status, count=None, msg=""):
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": status,
                    "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "count": count,
                    "msg": msg[:200],
                    "pid": os.getpid(),
                    "mode": "h5api",
                },
                f,
                ensure_ascii=False,
            )
    except Exception:
        pass


def _descendant_pids(root_pid):
    children = {}
    for pid_str in os.listdir("/proc"):
        if not pid_str.isdigit():
            continue
        try:
            with open("/proc/" + pid_str + "/stat", "rb") as f:
                st = f.read()
            rpar = st.rfind(b")")
            head = st[:rpar].split(b" ", 4)
            ppid = int(head[3])
            children.setdefault(ppid, []).append(int(pid_str))
        except Exception:
            pass
    out = set()
    stack = [int(root_pid)]
    while stack:
        cur = stack.pop()
        out.add(cur)
        for c in children.get(cur, []):
            if c not in out:
                stack.append(c)
    return out


def kill_chrome():
    my_pid = os.getpid()
    try:
        descendants = _descendant_pids(my_pid)
    except Exception:
        descendants = set()
    killed = 0
    for pid in descendants:
        if pid == my_pid:
            continue
        try:
            with open("/proc/" + str(pid) + "/comm") as f:
                name = f.read().strip()
            if "chrome" in name.lower() or "playwright" in name.lower():
                os.kill(pid, signal.SIGKILL)
                killed += 1
        except Exception:
            pass
    return killed


def is_merch(t: str) -> bool:
    return any(p in t for p in MERCH)


def is_fatal(t: str) -> bool:
    return any(p in t for p in FATAL)


def load_state():
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("playwright_storage_state", {})


def load_watchlist():
    with sqlite3.connect(DB, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT catalog_no, artist, title_cn, title_jp, required_keywords "
            "FROM watchlist WHERE enabled=1 ORDER BY id"
        ).fetchall()
    out = []
    for r in rows:
        kw = []
        for candidate in [r["artist"], r["title_cn"], r["title_jp"]]:
            if candidate and candidate.strip() and candidate not in kw:
                kw.append(candidate.strip())
        rk = (r["required_keywords"] or "").strip()
        if rk:
            for k in rk.split(","):
                k = k.strip()
                if k and k not in kw:
                    kw.append(k)
        if kw:
            out.append({"catalog_no": r["catalog_no"], "keywords": kw[:4]})
    return out


def get_existing_ids(site: str = "goofish") -> set:
    with sqlite3.connect(DB, timeout=30) as conn:
        rows = conn.execute(
            "SELECT external_item_id FROM market_items WHERE source='xianyu' AND source_site=?",
            (site,),
        ).fetchall()
    return {r[0] for r in rows if r[0]}


def insert_items(items: list) -> int:
    if not items:
        return 0
    inserted = 0
    with sqlite3.connect(DB, timeout=30) as conn:
        for it in items:
            try:
                conn.execute(
                    """INSERT INTO market_items
                       (source, source_site, external_item_id, catalog_no,
                        title, price, currency, url, image_url, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (
                        "xianyu",
                        "goofish",
                        it["external_item_id"],
                        it["catalog_no"],
                        it["title"],
                        float(it.get("price") or 0),
                        "CNY",
                        it["url"],
                        it.get("image_url"),
                    ),
                )
                inserted += 1
                _schedule_phash_backfill(it.get("image_url"))
            except Exception:
                pass
        conn.commit()
    return inserted


def _safe_get(obj, *keys, default=None):
    cur = obj
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list):
            try:
                cur = cur[k]
            except (IndexError, TypeError):
                return default
        else:
            return default
    return cur if cur is not None else default


def _parse_price(price_parts) -> float:
    """Convert h5api price field (list of {text: '...'}) to a float CNY value."""
    if not isinstance(price_parts, list):
        return 0.0
    raw = "".join(
        str(p.get("text", "")) for p in price_parts if isinstance(p, dict)
    ).replace("当前价", "").strip()
    raw = str(raw) if not isinstance(raw, str) else raw
    if "万" in raw:
        try:
            return float(raw.replace("¥", "").replace("万", "")) * 10000
        except (ValueError, TypeError):
            return 0.0
    try:
        return float(re.sub(r"[^0-9.]", "", raw))
    except (ValueError, TypeError):
        return 0.0


def parse_h5api_response(json_data: dict) -> list:
    """Extract clean item dicts from an h5api search response."""
    items = _safe_get(json_data, "data", "resultList", default=[]) or []
    if not items:
        return []
    out = []
    for entry in items:
        main = _safe_get(entry, "data", "item", "main", default={}) or {}
        ex = main.get("exContent") or {}
        click_args = _safe_get(main, "clickParam", "args", default={}) or {}
        item_id = str(ex.get("itemId") or "").strip()
        if not item_id:
            continue
        title = (ex.get("title") or "").strip()
        if not title:
            continue
        if len(title) > 200:
            title = title[:197] + "..."
        price = _parse_price(ex.get("price") or [])
        pic_url = ex.get("picUrl") or ""
        area = ex.get("area") or ""
        seller = ex.get("userNickName") or ""
        raw_link = main.get("targetUrl") or ""
        if raw_link.startswith("fleamarket://"):
            raw_link = "https://www.goofish.com/" + raw_link[len("fleamarket://"):]
        elif not raw_link:
            raw_link = f"https://www.goofish.com/item?id={item_id}"
        pub_ms = click_args.get("publishTime", "")
        publish_time = ""
        if isinstance(pub_ms, str) and pub_ms.isdigit():
            try:
                publish_time = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(int(pub_ms) / 1000)
                )
            except (ValueError, OSError):
                publish_time = ""
        out.append(
            {
                "item_id": item_id,
                "title": title,
                "price": price,
                "image_url": pic_url,
                "url": raw_link,
                "area": area,
                "seller": seller,
                "publish_time": publish_time,
            }
        )
    return out


async def search_one_keyword(page, keyword: str, *, max_pages: int = 1) -> list:
    """Search Goofish for keyword by listening to the h5api XHR.

    Returns a list of normalized item dicts from the h5api JSON response.
    Uses page.expect_response to capture the POST to h5api.m.goofish.com.
    """
    out = []
    search_url = "https://www.goofish.com/search?q=" + urllib.parse.quote(keyword)
    # First page: navigate to search URL, capture h5api POST.
    try:
        async with page.expect_response(
            lambda r: H5API_SEARCH_FRAGMENT in r.url and r.request.method == "POST",
            timeout=20000,
        ) as info:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        response = await info.value
        try:
            payload = await response.json()
        except Exception:
            payload = None
        if payload:
            out.extend(parse_h5api_response(payload))
    except Exception as e:
        log(f"  search err for {keyword}: {str(e)[:120]}")
        return out

    # Extra pages: scroll to bottom to trigger lazy-loaded results.
    for _ in range(max_pages - 1):
        try:
            async with page.expect_response(
                lambda r: H5API_SEARCH_FRAGMENT in r.url and r.request.method == "POST",
                timeout=12000,
            ) as info:
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
            response = await info.value
            try:
                payload = await response.json()
            except Exception:
                payload = None
            if payload:
                out.extend(parse_h5api_response(payload))
        except Exception:
            break
    return out


def passes_filters(item: dict, watch_keywords: list) -> bool:
    """Apply merch/fatal/watch-keyword whitelist same as xianyu_scraper.py."""
    title = item.get("title") or ""
    price = item.get("price") or 0
    if is_merch(title) or is_fatal(title):
        return False
    if price < 5 or price > 5000:
        return False
    tl = title.lower()
    cd_signal = any(p in tl for p in ["cd", "album", "single", "ep", "disc", "vinyl"])
    if not cd_signal:
        tb = title.encode("utf-8")
        cd_signal = any(kw.encode("utf-8") in tb for kw in CJK_CD_SIGNAL)
    watch_lower = [k.lower() for k in watch_keywords if k and len(k) >= 2]
    kw_hit = any(k in tl for k in watch_lower) if watch_lower else False
    strong_cd = any(m in title for m in ["OST", "Soundtrack", "初回限定", "通常盤", "Limited Edition"])
    return cd_signal and (kw_hit or strong_cd)


async def run_cycle(playwright, watches: list, existing_ids: set) -> dict:
    new_total = 0
    per_catalog: dict = {}
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--single-process"],
    )
    try:
        state = load_state()
        context = await browser.new_context(
            storage_state=state,
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 390, "height": 844},  # mobile-sized like the real Goofish app
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            color_scheme="light",
        )
        # Mobile-like anti-detection
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {} };
            """
        )
        page = await context.new_page()
        # Warm-up: visit homepage first to avoid instant fingerprint block.
        try:
            await page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        for w in watches:
            cat = w["catalog_no"]
            new_for_cat = 0
            for kw in w["keywords"][:4]:
                log(f"  h5api search goofish: {kw} (catalog={cat})")
                raw_items = await search_one_keyword(page, kw, max_pages=2)
                kept = []
                for it in raw_items:
                    eid = it["item_id"]
                    if eid in existing_ids:
                        continue
                    if not passes_filters(it, w.get("keywords") or []):
                        continue
                    it["catalog_no"] = cat
                    kept.append(
                        {
                            "external_item_id": eid,
                            "title": it["title"],
                            "price": it["price"],
                            "url": it["url"],
                            "image_url": it.get("image_url"),
                        }
                    )
                    existing_ids.add(eid)
                if kept:
                    n = insert_items(kept)
                    new_for_cat += n
                    log(f"    +{n} (kept {len(kept)}/{len(raw_items)})")
                else:
                    log(f"    +0 (kept 0/{len(raw_items)})")
            per_catalog[cat] = new_for_cat
            new_total += new_for_cat
            await page.wait_for_timeout(800)
        await context.close()
    finally:
        await browser.close()
    return {"new_total": new_total, "per_catalog": per_catalog}


async def main():
    if not Path(STATE_FILE).exists():
        log(f"!! state file missing: {STATE_FILE}")
        write_heartbeat("error", 0, "state_file_missing")
        return
    if not Path(DB).exists():
        log(f"!! db missing: {DB}")
        return
    log(f"starting xianyu h5api scraper, interval={INTERVAL_SEC}s")
    write_heartbeat("starting")
    async with async_playwright() as p:
        while True:
            try:
                watches = load_watchlist()
                existing = get_existing_ids()
                log(
                    f"cycle start: {len(watches)} watches, {len(existing)} existing xianyu ids"
                )
                write_heartbeat("running")
                res = await run_cycle(p, watches, existing)
                msg = f"cycle done new={res['new_total']} cats={sum(1 for v in res['per_catalog'].values() if v > 0)}"
                log(msg)
                write_heartbeat("idle", res["new_total"], msg)
                killed = kill_chrome()
                if killed:
                    log(f"killed {killed} chrome procs")
            except Exception as e:
                log(f"cycle err: {e}")
                write_heartbeat("error", 0, str(e)[:200])
                try:
                    kill_chrome()
                except Exception:
                    pass
            log(f"sleeping {INTERVAL_SEC}s...")
            await asyncio.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("interrupted")
