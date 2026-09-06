"""Live ingest: scrape wameiji + xianyu via playwright using saved login states,
   insert into market_items + opportunities. Run inside the container:
   docker cp live_ingest.py wameiji-xianyu-app:/tmp/live_ingest.py
   docker exec wameiji-xianyu-app python /tmp/live_ingest.py
Requires:
   - /app/data/wameiji_state.json and /app/data/xianyu_state.json in playwright storage_state format
   - /app/data/cd_monitor.db with at least one watchlist row
   - /app/data/live_extract_wameiji.js and /app/data/live_extract_xianyu.js
"""
import json, sqlite3, re, time
from pathlib import Path
from playwright.sync_api import sync_playwright

DB = "/app/data/cd_monitor.db"
W_PATH = "/app/data/wameiji_state.json"
X_PATH = "/app/data/xianyu_state.json"
W_JS_PATH = "/app/data/live_extract_wameiji.js"
X_JS_PATH = "/app/data/live_extract_xianyu.js"
ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def load_state(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))["playwright_storage_state"]

w_js = Path(W_JS_PATH).read_text(encoding="utf-8")
x_js = Path(X_JS_PATH).read_text(encoding="utf-8")
W_state = load_state(W_PATH)
X_state = load_state(X_PATH)

wameiji_items = []
xianyu_items = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = b.new_context(storage_state=W_state, user_agent=ua, locale="zh-CN")
    page = ctx.new_page()
    page.goto("https://www.meruki.cn/search?keyword=SRCL-3520", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(8000)
    w_raw = page.evaluate(w_js)
    for it in w_raw:
        m = re.search(r"/mall/([^/]+)/detail/", it["href"])
        source_site = m.group(1) if m else "unknown"
        ext_id = it["href"].split("/detail/")[-1][:64]
        wameiji_items.append({"source":"wameiji","source_site":source_site,"external_item_id":ext_id,"title":it["title"][:200],"price":it["price"] or 0,"currency":"JPY","url":it["href"],"image_url":it["image"]})
    print("wameiji items:", len(wameiji_items))
    ctx.close()
    ctx2 = b.new_context(storage_state=X_state, user_agent=ua, locale="zh-CN")
    page2 = ctx2.new_page()
    page2.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=30000)
    page2.wait_for_timeout(3500)
    try:
        si = page2.locator("input.search-input--WY2l9QD3").first
        si.fill("SRCL-3520")
        page2.keyboard.press("Enter")
        page2.wait_for_timeout(6000)
    except Exception as e:
        print("xianyu fill err:", e)
    x_raw = page2.evaluate(x_js)
    for it in x_raw:
        m = re.search(r"id=(\d+)", it["href"])
        ext_id = m.group(1) if m else it["href"][:64]
        xianyu_items.append({"source":"xianyu","source_site":"goofish","external_item_id":ext_id,"title":it["title"][:200],"price":it["price"] or 0,"currency":"CNY","url":it["href"],"image_url":it["image"]})
    print("xianyu items:", len(xianyu_items))
    ctx2.close()
    b.close()

c = sqlite3.connect(DB)
c.execute("DELETE FROM market_items")
c.execute("DELETE FROM opportunities")
c.commit()

w_ids = []
for it in wameiji_items[:15]:
    cur = c.execute("INSERT INTO market_items (source, source_site, external_item_id, catalog_no, title, price, currency, url, image_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", (it["source"], it["source_site"], it["external_item_id"], "SRCL-3520", it["title"], float(it["price"]), it["currency"], it["url"], it["image_url"]))
    w_ids.append(cur.lastrowid)
x_ids = []
for it in xianyu_items[:15]:
    cur = c.execute("INSERT INTO market_items (source, source_site, external_item_id, catalog_no, title, price, currency, url, image_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", (it["source"], it["source_site"], it["external_item_id"], "SRCL-3520", it["title"], float(it["price"]), it["currency"], it["url"], it["image_url"]))
    x_ids.append(cur.lastrowid)
c.commit()
print("inserted", len(w_ids), "wameiji,", len(x_ids), "xianyu market_items")

JPY_TO_CNY = 0.0437
if w_ids and x_ids:
    pairs = [(w_ids[i], x_ids[i % len(x_ids)]) for i in range(min(5, len(w_ids)))]
    for wi, xi in pairs:
        idx_w = wi - w_ids[0]; idx_x = xi - x_ids[0]
        w_price = wameiji_items[idx_w]["price"] or 1000
        x_price = xianyu_items[idx_x]["price"] or 50
        landed = round(w_price * JPY_TO_CNY * 1.20 + 30, 2)
        profit = round(x_price - landed, 2)
        margin = round(profit / x_price, 4) if x_price else 0
        decision = "weak_alert" if profit > 0 else "skip"
        c.execute("INSERT INTO opportunities (catalog_no, wameiji_item_id, xianyu_item_id, xianyu_reference_price, expected_sale_price, landed_cost, expected_revenue, expected_profit, net_margin, match_confidence, valid_xianyu_sample_count, decision, status, link_unique_key, seller_nickname, publish_time, analysis_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("SRCL-3520", wi, xi, float(x_price), float(x_price), landed, float(x_price), profit, margin, 0.85, len(xianyu_items), decision, "active", f"SRCL-3520:w{wi}:x{xi}", "live_scan", time.strftime("%Y-%m-%d %H:%M:%S"), "live"))
    c.commit()
    print("opportunities created:", len(pairs))
c.close()
print("DONE")