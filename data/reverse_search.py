"""Build reverse image search: extract Japanese keywords from xianyu titles, then search Mercari JP.

For each xianyu item:
1. Extract Japanese/English keywords from its title (CD info that the image shows)
2. Search Mercari JP with the most specific keywords (artist + album, or specific title)
3. Pick the top relevant match
4. Update market_items/wameiji + opportunities so each card has matching CD on both sides

This is "title-based reverse image search" - given that xianyu titles typically contain
the artist/title info visible in the CD image.
"""
# -*- coding: utf-8 -*-
import sys, os
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('PYTHONUNBUFFERED', '1')
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass
import asyncio, re, urllib.parse, sqlite3, time, json
from pathlib import Path
from playwright.async_api import async_playwright

DB = r'F:\WAMEIJI-XIANYU-full-handoff\data\cd_monitor.db'
JS_PATH = r'F:\WAMEIJI-XIANYU-full-handoff\data\_js_extract_jp.txt'
LOG_FILE = r'F:\WAMEIJI-XIANYU-full-handoff\data\_reverse_ingest.out'
JS = Path(JS_PATH).read_text(encoding='utf-8')

# Reset log file
with open(LOG_FILE, 'w', encoding='utf-8') as f:
    f.write('')

def log(*args):
    s = ' '.join(str(a) for a in args)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(s + '\n')
        f.flush()
    print(s)


def extract_keywords(title, catalog_no):
    """Extract prioritized Japanese search terms from a Chinese/English xianyu title."""
    found = []
    t = title or ''
    # Map catalog_no prefix -> artist
    catalog_map = {
        'Q-YOASOBI': 'YOASOBI',
        'Q-Ado': 'Ado',
        'Q-ADORU': 'Ado',
        'Q-FUJIIKAZE': '藤井風',
        'Q-YONEZU': '米津玄師',
        'Q-VAUNDY': 'Vaundy',
        'Q-KINGGNU': 'King Gnu',
        'Q-HIGEDAN': '髭男dism',
        'Q-SHEENA': '椎名林檎',
        'Q-OOR': 'ONE OK ROCK',
        'Q-LISA': 'LiSA',
        'Q-NOGIZAKA46': '乃木坂46',
        'Q-Sakurazaka46': '櫻坂46',
        'Q-HINATAZAKA46': '日向坂46',
    }
    artist = None
    for prefix, name in catalog_map.items():
        if catalog_no and catalog_no.startswith(prefix):
            artist = name
            break
    # Specific CD title keywords (high priority)
    title_hints = {
        'THE BOOK': ['THE BOOK', 'アイドル'],
        'アイドル': ['アイドル', 'THE BOOK'],
        '唱 ': ['Ado 唱', 'Ado 残夢'],
        '残夢': ['Ado 残夢', '残夢'],
        'IDOL': ['YOASOBI アイドル'],
        'CEREMONY': ['King Gnu CEREMONY'],
        'REJOICE': ['髭男dism REJOICE'],
        'HELP EVER': ['髭男dism HELP EVER HURT NEVER'],
        'STRAY SHEEP': ['米津玄師 STRAY SHEEP'],
        'Lemon': ['米津玄師 Lemon'],
        'Pretender': ['Official髭男dism Pretender'],
        '白日': ['King Gnu 白日'],
        '紅蓮華': ['LiSA 紅蓮華'],
        'Pretender': ['Official髭男dism Pretender'],
        '怪獣の花唄': ['Vaundy 怪獣の花唄'],
        'replica': ['Vaundy replica'],
        'Prema': ['藤井風 Prema'],
        'STRAY': ['米津玄師 STRAY SHEEP'],
        'THE FIRST': ['King Gnu THE FIRST'],
        'HOPE': ['髭男dism HOPE'],
        'Editorial': ['髭男dism Editorial'],
        'Niche': ['ONE OK ROCK Niche'],
        'Eye of the Storm': ['ONE OK ROCK Eye of the Storm'],
        '夜に駆ける': ['YOASOBI 夜に駆ける'],
        '夜游': ['YOASOBI 夜遊'],
    }
    for hint, jp_list in title_hints.items():
        if hint.lower() in t.lower():
            found.extend(jp_list)
    # Add artist terms (high priority)
    if artist:
        if artist == 'YOASOBI':
            found.extend(['YOASOBI アイドル', 'THE BOOK'])
        elif artist == 'Ado':
            found.extend(['Ado 残夢', 'Ado 唱', 'Ado'])
        elif artist == '髭男dism':
            found.extend(['Official髭男dism', '髭男dism REJOICE'])
        else:
            found.append(artist)
    # Dedupe, keep order
    seen = set()
    out = []
    for k in found:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:6]


async def search_mjp(page, kw, n=8):
    url = 'https://jp.mercari.com/search?keyword=' + urllib.parse.quote(kw, safe='') + '&status=on_sale'
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=45000)
        await page.wait_for_timeout(5500)
        items = await page.eval_on_selector_all("a[href*='/item/']", JS)
        results = []
        seen = set()
        for it in items:
            href = it.get('href', '')
            if not href or href in seen:
                continue
            seen.add(href)
            results.append({
                'href': href,
                'title': it.get('title', '')[:200],
                'price': int(it.get('price', 0) or 0),
                'image': it.get('image'),
            })
            if len(results) >= n:
                break
        return results
    except Exception as e:
        log(f'  err {kw}: {str(e)[:60]}')
        return []


async def main():
    log('==== Reverse image search (title-based) start ====')
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    cur = c.cursor()
    cur.execute("SELECT id, title, image_url, catalog_no FROM market_items WHERE source='xianyu' ORDER BY id")
    xianyu_items = cur.fetchall()
    log(f'xianyu items: {len(xianyu_items)}')

    # Track xianyu_id -> [wameiji market_items]
    xid_to_wids = {}

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        ctx = await b.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
            locale='ja-JP',
            timezone_id='Asia/Tokyo',
            viewport={'width': 1280, 'height': 900},
            extra_http_headers={'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.8'}
        )
        page = await ctx.new_page()
        for x in xianyu_items:
            xid = x['id']
            title = x['title']
            catalog = x['catalog_no']
            keywords = extract_keywords(title, catalog)
            if not keywords:
                # No keywords - keep the existing artist-level match from catalog
                continue
            log(f'\n[xianyu#{xid}] {title[:70]}')
            log(f'  cat={catalog}  kws={keywords}')
            best = None
            seen_hrefs = set()
            # Try each keyword in priority order
            for kw in keywords[:4]:
                if best:
                    break
                results = await search_mjp(page, kw, n=5)
                for r in results:
                    if r['href'] in seen_hrefs:
                        continue
                    if r['price'] <= 0 or r['price'] > 1_000_000:
                        continue
                    seen_hrefs.add(r['href'])
                    # Take the first valid result as best match
                    best = r
                    best['_matched_kw'] = kw
                    break
            if best:
                log(f'  -> BEST: {best["title"][:50].ljust(50)} JPY {best["price"]:>6}')
                # Insert into market_items
                ext_id = best['href'].split('/item/')[-1].split('?')[0]
                cur.execute(
                    "INSERT INTO market_items (source, source_site, external_item_id, catalog_no, title, price, currency, url, image_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    ('wameiji', 'mercari_jp', ext_id, catalog, best['title'], float(best['price']), 'JPY', best['href'], best['image'])
                )
                wid = cur.lastrowid
                xid_to_wids.setdefault(xid, []).append(wid)
            else:
                log(f'  -> no match found')
        await ctx.close()
        await b.close()

    # Now replace wameiji rows that don't have xianyu pairing AND create opportunities for the new ones
    # First: clear old wameiji rows (they will be re-inserted as we go)
    # Actually we added new rows, we want the relationship to be:
    #   for each xianyu item, one or more matching wameiji items
    # Build opportunities
    log('\n=== building opportunities ===')
    cur.execute("DELETE FROM opportunities")
    JPY_TO_CNY = 0.046
    pairs = 0
    for xid, wids in xid_to_wids.items():
        # Find xianyu info
        row = cur.execute("SELECT price, title, catalog_no FROM market_items WHERE id=?", (xid,)).fetchone()
        if not row:
            continue
        x_price = row[0] or 0
        x_title = row[1]
        catalog = row[2]
        for wid in wids:
            w_row = cur.execute("SELECT price FROM market_items WHERE id=?", (wid,)).fetchone()
            w_price = w_row[0] if w_row else 0
            landed = round(w_price * JPY_TO_CNY * 1.20 + 30, 2)
            profit = round((x_price or 0) - landed, 2)
            margin = round(profit / (x_price or 1), 4) if x_price else 0
            decision = 'weak_alert' if profit > 0 else 'skip'
            cur.execute(
                "INSERT INTO opportunities (catalog_no, wameiji_item_id, xianyu_item_id, xianyu_reference_price, expected_sale_price, landed_cost, expected_revenue, expected_profit, net_margin, match_confidence, valid_xianyu_sample_count, decision, status, link_unique_key, seller_nickname, publish_time, analysis_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (catalog, wid, xid, float(x_price or 0), float(x_price or 0), landed, float(x_price or 0), profit, margin, 0.95, 1, decision, 'active', f'{catalog}:w{wid}:x{xid}', 'reverse_search', time.strftime('%Y-%m-%d %H:%M:%S'), 'reverse_image_search')
            )
            pairs += 1
    c.commit()
    log(f'opportunities created: {pairs}')
    log('==== Reverse image search done ====')


import asyncio
asyncio.run(main())