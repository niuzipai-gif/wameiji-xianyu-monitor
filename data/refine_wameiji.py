"""Refine wameiji items: replace non-CD matches with CD matches.

For each wameiji row whose title doesn't include CD/アルバム/シングル/etc.,
search Mercari JP with a more CD-specific keyword (artist + CD) and replace.
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
    except Exception: pass
import asyncio, re, urllib.parse, sqlite3, time
from pathlib import Path
from playwright.async_api import async_playwright

DB = r'F:\WAMEIJI-XIANYU-full-handoff\data\cd_monitor.db'
JS_PATH = r'F:\WAMEIJI-XIANYU-full-handoff\data\_js_extract_jp.txt'
LOG_FILE = r'F:\WAMEIJI-XIANYU-full-handoff\data\_refine.out'
JS = Path(JS_PATH).read_text(encoding='utf-8')

with open(LOG_FILE, 'w', encoding='utf-8') as f:
    f.write('')

def log(*args):
    s = ' '.join(str(a) for a in args)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(s + '\n')
        f.flush()
    print(s)

CD_TERMS = ['CD', 'アルバム', 'シングル', 'レコード', 'DVD', 'Blu-ray', 'LP', 'EP', 'VICTOR', '完全生産限定', '初回限定', '通常盤', 'TYPE-', 'Type-']

def is_cd(title):
    t = (title or '').lower()
    return any(term.lower() in t for term in CD_TERMS)

# Specific album CD names for each artist - to give better search queries
ALBUM_NAMES = {
    'YOASOBI': ['THE BOOK', 'アイドル'],
    'Ado': ['残夢', '狂言', '歌ってみた'],
    '藤井風': ['LOVE ALL SERVE ALL', 'HELP EVER HURT NEVER', 'Prema'],
    '米津玄師': ['STRAY SHEEP', 'Lemon', 'Bremen', 'Plazma', '地球儀'],
    'Vaundy': ['replica', 'strobo', '怪獣の花唄'],
    'King Gnu': ['CEREMONY', 'THE GREATEST UNKNOWN', 'BOY'],
    '髭男dism': ['REJOICE', 'Editorial', 'HELP EVER HURT NEVER', 'ESCAPARADE', 'Traveler'],
    '椎名林檎': ['逆輸入', '航空局', '三文ゴシップ', '放生会', '浮き名', '勝訴'],
    'ONE OK ROCK': ['Nicheシンドローム', 'Eye of the Storm', '35xxxv', '人生×僕'],
    'LiSA': ['LANDER', 'LANDSPACE', 'LEO-NiNE', '紅蓮華', 'ALTEREGO'],
    '乃木坂46': ['My Respect', 'MUST BE SORRY', 'ずっと', '33rd', '31st', '37th', '38th', '39th'],
    '櫻坂46': ['Start over!', '自業自得', 'UDAGAWA GENERATION', '承認欲求', '不協和音'],
    '日向坂46': ['ってか', 'Am I ready?', '月と星が踊るMidnight', 'こんなに好きになっちゃっていいの？', '卒業写真だけが知ってる'],
}

CATALOG_ARTIST_MAP = {
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

def get_artist(catalog_no):
    if not catalog_no:
        return None
    for prefix, name in CATALOG_ARTIST_MAP.items():
        if catalog_no.startswith(prefix):
            return name
    return None

def get_keyword_searches(catalog_no, xianyu_title):
    """Generate prioritized search queries for this xianyu item."""
    artist = get_artist(catalog_no)
    titles_for_artist = ALBUM_NAMES.get(artist, [])
    matched_title = None
    for t in titles_for_artist:
        if t.lower() in (xianyu_title or '').lower():
            matched_title = t
            break
    # Build priority search list
    searches = []
    if matched_title and artist:
        searches.append(f'{artist} {matched_title} CD')  # Most specific
        searches.append(f'{matched_title} CD')
        searches.append(f'{artist} {matched_title}')
    if artist:
        searches.append(f'{artist} CD')  # Generic CD search
        searches.append(f'{artist} アルバム')
    # Try xianyu title's Japanese terms
    t = xianyu_title or ''
    known_jp_titles = ['THE BOOK', 'アイドル', '残夢', 'Prema', 'STRAY SHEEP', 'Lemon', 'CEREMONY', 'REJOICE', 'HELP EVER HURT NEVER', 'Nicheシンドローム', 'Eye of the Storm', 'ALTEREGO', 'ってか']
    for kw in known_jp_titles:
        if kw.lower() in t.lower() and kw not in searches:
            searches.append(f'{artist} {kw} CD' if artist else f'{kw} CD')
    return searches[:6]


async def search_mjp(page, kw, n=10):
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
    log('==== Refine: replace non-CD wameiji items ====')
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    cur = c.cursor()

    # Find ALL wameiji items currently in DB, regardless of CD-ness (we just re-do everything for consistency)
    # Actually only fix non-CD ones
    cur.execute("""SELECT w.id as wid, w.title, w.url, w.catalog_no, o.xianyu_item_id,
                          (SELECT title FROM market_items WHERE id=o.xianyu_item_id) as x_title,
                          (SELECT price FROM market_items WHERE id=o.xianyu_item_id) as x_price
                   FROM market_items w
                   JOIN opportunities o ON o.wameiji_item_id = w.id
                   WHERE w.source='wameiji'""")
    rows = cur.fetchall()
    log(f'wameiji items: {len(rows)}')

    non_cd_items = [r for r in rows if not is_cd(r['title'])]
    cd_items = [r for r in rows if is_cd(r['title'])]
    log(f'non-CD: {len(non_cd_items)}, CD: {len(cd_items)}')

    # For non-CD, try refined search
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        ctx = await b.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
            locale='ja-JP',
            timezone_id='Asia/Tokyo',
            viewport={'width': 1280, 'height': 900},
        )
        page = await ctx.new_page()

        updated = 0
        for r in non_cd_items:
            wid = r['wid']
            catalog = r['catalog_no']
            x_title = r['x_title']
            log(f'\n[wid={wid}] catalog={catalog}, xianyu={x_title[:60]}')
            log(f'  current (non-CD): {r["title"][:60]}')
            searches = get_keyword_searches(catalog, x_title)
            log(f'  searches: {searches}')
            new_best = None
            seen_hrefs = set()
            for kw in searches:
                if new_best:
                    break
                results = await search_mjp(page, kw, n=8)
                for res in results:
                    if res['href'] in seen_hrefs:
                        continue
                    if not is_cd(res['title']):
                        continue
                    if res['price'] <= 0 or res['price'] > 1_000_000:
                        continue
                    seen_hrefs.add(res['href'])
                    new_best = res
                    break
            if new_best:
                ext_id = new_best['href'].split('/item/')[-1].split('?')[0]
                cur.execute("UPDATE market_items SET title=?, price=?, currency='JPY', url=?, image_url=?, source_site='mercari_jp', external_item_id=? WHERE id=?",
                            (new_best['title'], float(new_best['price']), new_best['href'], new_best['image'], ext_id, wid))
                # Recompute opportunity profit/labels
                if r['x_price']:
                    landed = round(new_best['price'] * 0.046 * 1.20 + 30, 2)
                    profit = round((r['x_price'] or 0) - landed, 2)
                    decision = 'weak_alert' if profit > 0 else 'skip'
                    cur.execute("UPDATE opportunities SET landed_cost=?, expected_profit=?, decision=? WHERE wameiji_item_id=?",
                                (landed, profit, decision, wid))
                log(f'  -> UPDATED: {new_best["title"][:50].ljust(50)} JPY {new_best["price"]:>6}')
                updated += 1
            else:
                log(f'  -> no CD match found, will skip')

        c.commit()
        log(f'\nupdated {updated} wameiji items to be CDs')
        await ctx.close()
        await b.close()
    log('==== Refine done ====')

import asyncio
asyncio.run(main())