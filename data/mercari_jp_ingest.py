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
import json, sqlite3, time, re
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright

DB = r'F:\WAMEIJI-XIANYU-full-handoff\data\cd_monitor.db'
JS_PATH = r'F:\WAMEIJI-XIANYU-full-handoff\data\_js_extract_jp.txt'
JS = Path(JS_PATH).read_text(encoding='utf-8')

ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'

QUERY_OVERRIDES = {
    'Q-YOASOBI-IDOL': ['YOASOBI アイドル', 'YOASOBI THE BOOK', 'YOASOBI アルバム'],
    'Q-YOASOBI-CD': ['YOASOBI CD', 'YOASOBI アルバム'],
    'Q-Ado-CD': ['Ado アルバム', 'Ado CD'],
    'Q-ADORU-UTA': ['Ado 唱', 'Ado 残夢', 'Ado アルバム'],
    'Q-FUJIIKAZE-CD': ['藤井風 アルバム', '藤井風 CD', 'Fujii Kaze'],
    'Q-YONEZU-CD': ['米津玄師 アルバム', '米津玄師 Lemon', '米津玄師 CD'],
    'Q-VAUNDY-CD': ['Vaundy アルバム', 'Vaundy 怪獣の花唄', 'Vaundy replica'],
    'Q-KINGGNU-CD': ['King Gnu CEREMONY', 'King Gnu アルバム', 'King Gnu CD'],
    'Q-HIGEDAN-CD': ['髭男dism アルバム', 'Official髭男dism', 'HELP EVER HURT NEVER'],
    'Q-SHEENA-CD': ['椎名林檎 アルバム', '椎名林檎 CD', '東京事変'],
    'Q-OOR-CD': ['ONE OK ROCK アルバム', 'ONE OK ROCK CD'],
    'Q-LISA-CD': ['LiSA アルバム', 'LiSA 紅蓮華', 'LiSA CD'],
    'Q-NOGIZAKA46-CD': ['乃木坂46 シングル', '乃木坂46 CD'],
    'Q-Sakurazaka46-CD': ['櫻坂46 シングル', '櫻坂46 CD'],
    'Q-HINATAZAKA46-CD': ['日向坂46 シングル', '日向坂46 CD'],
}


def jp_query(catalog_no, artist, title_jp):
    if catalog_no in QUERY_OVERRIDES:
        return QUERY_OVERRIDES[catalog_no]
    base = artist or ''
    if title_jp and title_jp.strip() and title_jp.strip().lower() not in (artist or '').lower():
        base += ' ' + title_jp.strip()
    return [base.strip()] if base.strip() else ['CD']

def is_relevant(title, queries):
    t = (title or '').lower()
    # CD-like products include CD, アルバム, シングル, レコード, DVD, Blu-ray
    cd_terms = ['cd', 'アルバム', 'シングル', 'レコード', 'dvd', 'blu-ray', 'blu ray', 'lp', 'ep ']
    is_cd = any(term in t for term in cd_terms)
    if not is_cd:
        return False
    for q in queries:
        qclean = re.sub(r'\s+', ' ', q).strip().lower()
        if qclean and qclean in t:
            return True
        for token in re.split(r'\s+', q):
            tok = token.strip().lower()
            if len(tok) >= 3 and tok in t:
                return True
    return False
def search_mjp(page, q, n=10):
    url = 'https://jp.mercari.com/search?keyword=' + quote(q, safe='') + '&status=on_sale'
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(5500)
        for _ in range(2):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(800)
        items = page.eval_on_selector_all('a[href*=\'/item/\']', JS)
        seen = set()
        unique = []
        for it in items:
            h = it.get('href')
            if not h or h in seen:
                continue
            seen.add(h)
            unique.append(it)
            if len(unique) >= n:
                break
        return unique
    except Exception as e:
        log('  search err:', q, str(e)[:80])
        return []


LOG_FILE = r'F:\WAMEIJI-XIANYU-full-handoff\data\_mjp_ingest.out'
def log(*args, **kw):
    s = ' '.join(str(a) for a in args)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(s + '\n')
            f.flush()
    except Exception:
        pass
    print(s)

def main():
    log('==== Mercari JP ingest start ====')
    c = sqlite3.connect(DB, timeout=60)
    cur = c.cursor()
    cur.execute('SELECT id, catalog_no, artist, title_jp, title_cn FROM watchlist WHERE enabled IS NULL OR enabled = 1')
    watchlist = cur.fetchall()
    log('watchlist rows:', len(watchlist))

    new_w_items = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        ctx = b.new_context(
            user_agent=ua, locale='ja-JP', timezone_id='Asia/Tokyo',
            viewport={'width': 1280, 'height': 900},
            extra_http_headers={'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.8'}
        )
        page = ctx.new_page()
        for (wid, cno, artist, title_jp, title_cn) in watchlist:
            queries = jp_query(cno, artist, title_jp)
            log('\n[' + cno + '] artist=' + str(artist) + '  queries=' + str(queries))
            seen_hrefs = set()
            added = 0
            for q in queries:
                if added >= 5:
                    break
                items = search_mjp(page, q, n=10)
                for it in items:
                    if added >= 5:
                        break
                    href = it.get('href', '')
                    if not href or href in seen_hrefs:
                        continue
                    title = (it.get('title') or '').strip()
                    price = int(it.get('price') or 0)
                    image = it.get('image')
                    if not title or len(title) < 3:
                        continue
                    if price <= 0 or price > 1_000_000:
                        continue
                    if not is_relevant(title, queries):
                        continue
                    seen_hrefs.add(href)
                    ext_id = href.split('/item/')[-1].split('?')[0]
                    new_w_items.append({'cno': cno, 'ext_id': ext_id, 'title': title, 'price': price, 'href': href, 'image': image})
                    added += 1
                    log('  +', title[:50].ljust(50), 'JPY', str(price).rjust(6), href[-30:])
            if added == 0:
                log('  [warn] no relevant match, using top search hits as fallback')
                for q in queries:
                    items = search_mjp(page, q, n=3)
                    for it in items[:3]:
                        href = it.get('href', '')
                        if not href or href in seen_hrefs:
                            continue
                        title = (it.get('title') or '').strip()
                        price = int(it.get('price') or 0)
                        image = it.get('image')
                        if not title or len(title) < 3:
                            continue
                        if price <= 0 or price > 1_000_000:
                            continue
                        seen_hrefs.add(href)
                        ext_id = href.split('/item/')[-1].split('?')[0]
                        new_w_items.append({'cno': cno, 'ext_id': ext_id, 'title': title, 'price': price, 'href': href, 'image': image})
                        added += 1
                        log('  ~', title[:50].ljust(50), 'JPY', str(price).rjust(6))
                    if added > 0:
                        break
        ctx.close()
        b.close()

    log('\n=== inserting into DB ===')
    cur.execute("DELETE FROM market_items WHERE source='wameiji'")
    cur.execute('DELETE FROM opportunities')
    w_id_by_cno = {}
    for it in new_w_items:
        cur.execute(
            "INSERT INTO market_items (source, source_site, external_item_id, catalog_no, title, price, currency, url, image_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            ('wameiji', 'mercari_jp', it['ext_id'], it['cno'], it['title'], float(it['price']), 'JPY', it['href'], it['image'])
        )
        wid = cur.lastrowid
        w_id_by_cno.setdefault(it['cno'], []).append(wid)
    log('inserted wameiji market_items:', len(new_w_items))

    JPY_TO_CNY = 0.046
    pairs_created = 0
    for cno, wid_list in w_id_by_cno.items():
        x_rows = cur.execute("SELECT id, price, title FROM market_items WHERE source='xianyu' AND catalog_no=? ORDER BY id", (cno,)).fetchall()
        if not x_rows:
            continue
        for i, wid in enumerate(wid_list):
            xid, x_price, x_title = x_rows[i % len(x_rows)]
            w_price_row = cur.execute('SELECT price FROM market_items WHERE id=?', (wid,)).fetchone()
            w_price = w_price_row[0] if w_price_row else 0
            landed = round(w_price * JPY_TO_CNY * 1.20 + 30, 2)
            profit = round((x_price or 0) - landed, 2)
            margin = round(profit / (x_price or 1), 4) if x_price else 0
            decision = 'weak_alert' if profit > 0 else 'skip'
            cur.execute(
                "INSERT INTO opportunities (catalog_no, wameiji_item_id, xianyu_item_id, xianyu_reference_price, expected_sale_price, landed_cost, expected_revenue, expected_profit, net_margin, match_confidence, valid_xianyu_sample_count, decision, status, link_unique_key, seller_nickname, publish_time, analysis_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cno, wid, xid, float(x_price or 0), float(x_price or 0), landed, float(x_price or 0), profit, margin, 0.85, len(x_rows), decision, 'active', f'{cno}:w{wid}:x{xid}', 'mercari_jp_live', time.strftime('%Y-%m-%d %H:%M:%S'), 'live')
            )
            pairs_created += 1
    c.commit()
    log('opportunities created:', pairs_created)
    log('==== Mercari JP ingest done ====')

if __name__ == '__main__':
    main()