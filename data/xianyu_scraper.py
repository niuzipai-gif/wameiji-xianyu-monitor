"""xianyu_scraper.py - Scrape Goofish (Xianyu) using saved login state.

Reads watchlist rows, searches Goofish for each catalog via Playwright
(using /app/data/xianyu_state.json), persists results into market_items
with source='xianyu', source_site='goofish'. Safe to run repeatedly:
existing external_item_ids are skipped.

Usage (inside container):
    SCRAPER_INTERVAL=900 nohup python3 -u xianyu_scraper.py &
"""
from __future__ import annotations
import json, os, re, sys, time, sqlite3, urllib.parse, signal, asyncio, threading
from pathlib import Path
from playwright.async_api import async_playwright

DB = os.environ.get("CD_DB", "/var/lib/cd_monitor/cd_monitor.db")
STATE_FILE = "/app/data/xianyu_state.json"
HEARTBEAT_FILE = "/app/data/_xianyu_heartbeat.json"
LOG_FILE = "/app/data/_xianyu_scraper.log"
INTERVAL_SEC = int(os.environ.get("XIANYU_INTERVAL", "900"))

# Background phash backfill: after every insert, schedule an async phash
# computation so display_sample_id picker can use visual signal.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import phash_compute as _phc
    _HAS_PHASH = True
except Exception:
    _HAS_PHASH = False

_pending_phash = set()
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
                    with sqlite3.connect(DB, timeout=10) as _c:
                        _c.execute(
                            "UPDATE market_items SET image_phash=?, image_dhash=? "
                            "WHERE image_url=? AND (image_phash IS NULL OR image_phash="")",
                            (ph, dh, url),
                        )
                        _c.commit()
                except Exception:
                    pass
        except Exception:
            pass


if _HAS_PHASH:
    threading.Thread(target=_phash_backfill_daemon, daemon=True, name="xianyu-phash").start()

SEARCH_URL = "https://www.goofish.com/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# Reject merch/non-CD rows quickly using cheap title heuristics
MERCH = ["生写", "自拍", "签名", "签名版", "签售", "周边", "徽章", "吧唧",
         "卡片", "亚克力", "钥匙扣", "票根", "海报", "毛巾", "t恤", "卫衣",
         "明信片", "卡贴", "透卡", "卡套", "夹层", "袋子", "亚克力牌", "立牌",
         "色纸", "吧唧", "手幅", "纸袋", "票夹", "专辑内页", "歌词卡", "小卡",
         "应援", "扇子", "马克杯", "水杯", "手机壳", "手机链", "镜子", "手链",
         "项链", "戒指", "耳环", "袜子", "贴纸", "笔记本", "本子", "胸针",
         "别针", "袋子", "包装", "礼盒", "外封", "原盒", "空盒", "样品"]
FATAL = ["仅剩", "空盒", "盘无", "无盘", "无碟", "裸碟", "样品", "sample",
         "出租", "租赁", "代购", "代购费", "代下", "预订", "预购", "非卖品",
         "送人", "抽奖", "中奖", "免费送", "买一送", "买赠"]

EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  // Goofish uses <a href="/item?id=..."> anchors; feed-page renders cards
  const anchors = document.querySelectorAll('a[href*="/item"]');
  for (const a of anchors) {
    if (out.length >= 30) break;
    const href = a.href || '';
    if (!/\/item/.test(href)) continue;
    let m = href.match(/id=(\d+)/);
    if (!m) continue;
    const itemId = m[1];
    if (seen.has(itemId)) continue;
    // Title: try several selectors
    let title = '';
    const titleEl = a.querySelector('[class*="main-title"]') || a.querySelector('span') || a;
    title = (titleEl?.innerText || a.getAttribute('title') || '').split(/[\u2014\u2013\n]/)[0].trim();
    if (title.length > 80) title = title.slice(0, 77) + '...';
    // Price
    let price = 0;
    const priceEl = a.querySelector('[class*="number--"]') || a.querySelector('span[class*="price"]');
    if (priceEl) {
      const t = (priceEl.innerText || '').replace(/[^0-9.]/g, '');
      price = parseFloat(t) || 0;
    }
    // Image
    const img = a.querySelector('img[class*="feeds-image"]') || a.querySelector('img');
    const image = img?.src || img?.getAttribute('data-src') || null;
    if (!title) continue;
    seen.add(itemId);
    out.push({ id: itemId, href, title, price, image });
  }
  return out;
}
"""

def log(msg, flush=True):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=flush)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def write_heartbeat(status, count=None, msg=''):
    try:
        data = {
            'status': status,
            'last_run': time.strftime('%Y-%m-%d %H:%M:%S'),
            'count': count,
            'msg': msg[:200],
            'pid': os.getpid(),
        }
        with open(HEARTBEAT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _descendant_pids(root_pid):
    """Walk /proc to find all pids whose PPID chain leads back to root_pid."""
    children = {}
    for pid_str in os.listdir('/proc'):
        if not pid_str.isdigit():
            continue
        try:
            with open('/proc/' + pid_str + '/stat', 'rb') as f:
                st = f.read()
            rpar = st.rfind(b')')
            head = st[:rpar].split(b' ', 4)
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
    """Kill only OUR own chrome/playwright descendants (PPID chain).

    Previous version scanned /proc and SIGKILLed every chrome on the box,
    which killed the parallel mall harvest loop (harvest_meruki_malls.py)
    inside the same container.
    """
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
            with open('/proc/' + str(pid) + '/comm') as f:
                name = f.read().strip()
            if 'chrome' in name.lower() or 'playwright' in name.lower():
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
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f).get('playwright_storage_state', {})


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
        # Build keyword list: artist > title_cn > title_jp
        for candidate in [r['artist'], r['title_cn'], r['title_jp']]:
            if candidate and candidate.strip() and candidate not in kw:
                kw.append(candidate.strip())
        # Also split required_keywords by comma
        rk = (r['required_keywords'] or '').strip()
        if rk:
            for k in rk.split(','):
                k = k.strip()
                if k and k not in kw:
                    kw.append(k)
        if kw:
            out.append({'catalog_no': r['catalog_no'], 'keywords': kw[:4]})
    return out


def get_existing_ids(site: str = 'goofish') -> set:
    with sqlite3.connect(DB, timeout=30) as conn:
        rows = conn.execute(
            "SELECT external_item_id FROM market_items WHERE source='xianyu' AND source_site=?",
            (site,)
        ).fetchall()
    return {r[0] for r in rows if r[0]}


def insert_items(items: list[dict]) -> int:
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
                    ('xianyu', 'goofish', it['external_item_id'], it['catalog_no'],
                     it['title'], float(it['price'] or 0), 'CNY',
                     it['url'], it.get('image_url'))
                )
                inserted += 1
                _schedule_phash_backfill(it.get("image_url"))
            except Exception as e:
                # duplicate key, etc.
                pass
        conn.commit()
    return inserted


async def search_one(page, keyword: str, max_items: int = 20) -> list[dict]:
    """Search Goofish for keyword, return parsed items."""
    out = []
    try:
        # Goofish search: navigate to search page with query
        url = f"https://www.goofish.com/search?q={urllib.parse.quote(keyword)}"
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(4000)
        # Scroll down to load more results
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
            await page.wait_for_timeout(1500)
        raw = await page.evaluate(EXTRACT_JS)
        for r in raw or []:
            if r and r.get('id') and r.get('title'):
                out.append({
                    'external_item_id': r['id'],
                    'title': r['title'][:200],
                    'price': float(r.get('price') or 0),
                    'url': r.get('href') or f"https://www.goofish.com/item?id={r['id']}",
                    'image_url': r.get('image'),
                })
                if len(out) >= max_items:
                    break
    except Exception as e:
        log(f"  search err for {keyword}: {str(e)[:120]}")
    return out


async def run_cycle(playwright, watches: list, existing_ids: set) -> dict:
    new_total = 0
    per_catalog = {}
    browser = await playwright.chromium.launch(
        headless=True,
        args=['--no-sandbox', '--disable-dev-shm-usage', '--single-process'],
    )
    try:
        state = load_state()
        context = await browser.new_context(
            storage_state=state,
            user_agent=UA,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            viewport={'width': 1280, 'height': 900},
        )
        page = await context.new_page()
        for w in watches:
            cat = w['catalog_no']
            new_for_cat = 0
            for kw in w['keywords'][:4]:  # P1: 4 keywords per catalog
                log(f"  search goofish: {kw} (catalog={cat})")
                items = await search_one(page, kw, max_items=25)
                # Filter: skip merch/fatal, skip already-seen
                kept = []
                # Build per-watch whitelist (artist + title keywords)
                watch_w = w.get('keywords') or []
                watch_w_lower = [k.lower() for k in watch_w if k and len(k) >= 2]
                cjk_kw = [b'\xe4\xb8\x93\xe8\xbe\x91', b'\xe5\x94\xbf\xe7\x89\x87', b'\xe5\x88\x9d\xe5\x9b\x9e', b'\xe9\x80\x9a\xe5\xb8\xb8\xe7\x9b\xa4', b'\xe9\x99\x90\xe5\xae\x9a\xe7\x9b\xa4', b'\xe9\x99\x90\xe5\xae\x9a\xe7\x89\x88', b'\xe6\x97\xa5\xe7\x89\x88', b'\xe7\x9b\x98\xe9\x9d\xa2']
                for it in items:
                    if it['external_item_id'] in existing_ids:
                        continue
                    t = it['title'] or ''
                    if is_merch(t) or is_fatal(t):
                        continue
                    if it['price'] < 5 or it['price'] > 5000:
                        continue
                    # P0 (post-cleanup): require BOTH CD-positive signal AND watch-keyword hit
                    # OR strong CD-specific markers (OST/Soundtrack/初回/通常盤) which imply actual CD
                    tl = t.lower()
                    cd_signal = any(p in tl for p in ['cd', 'album', 'single', 'ep', 'disc', 'vinyl'])
                    if not cd_signal:
                        tb = t.encode('utf-8')
                        cd_signal = any(kw in tb for kw in cjk_kw)
                    kw_hit = any(k in tl for k in watch_w_lower) if watch_w_lower else False
                    strong_cd = any(m in t for m in ['OST', 'Soundtrack', '初回限定', '通常盤', 'Limited Edition'])
                    if not (cd_signal and (kw_hit or strong_cd)):
                        continue
                    it['catalog_no'] = cat
                    kept.append(it)

                    existing_ids.add(it['external_item_id'])
                if kept:
                    n = insert_items(kept)
                    new_for_cat += n
                    log(f"    +{n} (kept {len(kept)}/{len(items)})")
            per_catalog[cat] = new_for_cat
            new_total += new_for_cat
            # tiny gap between catalogs to be polite
            await page.wait_for_timeout(800)
        await context.close()
    finally:
        await browser.close()
    return {'new_total': new_total, 'per_catalog': per_catalog}


async def main():
    if not Path(STATE_FILE).exists():
        log(f"!! state file missing: {STATE_FILE}")
        write_heartbeat('error', 0, 'state_file_missing')
        return
    if not Path(DB).exists():
        log(f"!! db missing: {DB}")
        return
    log(f"starting xianyu scraper, interval={INTERVAL_SEC}s")
    write_heartbeat('starting')
    async with async_playwright() as p:
        # main loop
        while True:
            try:
                watches = load_watchlist()
                existing = get_existing_ids()
                log(f"cycle start: {len(watches)} watches, {len(existing)} existing xianyu ids")
                write_heartbeat('running')
                res = await run_cycle(p, watches, existing)
                msg = f"cycle done new={res['new_total']} cats={sum(1 for v in res['per_catalog'].values() if v>0)}"
                log(msg)
                write_heartbeat('idle', res['new_total'], msg)
                killed = kill_chrome()
                if killed:
                    log(f"killed {killed} chrome procs")
            except Exception as e:
                log(f"cycle err: {e}")
                write_heartbeat('error', 0, str(e)[:200])
                try:
                    kill_chrome()
                except Exception:
                    pass
            log(f"sleeping {INTERVAL_SEC}s...")
            await asyncio.sleep(INTERVAL_SEC)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("interrupted")

