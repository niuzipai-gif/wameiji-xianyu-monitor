#!/usr/bin/env python3
"""Continuous scraper v2 - with aggressive chrome cleanup."""
import sys, os, asyncio, time, json, sqlite3, urllib.parse, signal
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
DB = os.environ.get("CD_DB", "/var/lib/cd_monitor/cd_monitor.db")
HEARTBEAT_FILE = "/app/data/_scraper_heartbeat.json"
INTERVAL_SEC = int(os.environ.get("SCRAPER_INTERVAL", "900"))  # 15 min

ARTIST_KEYWORDS = {
    "Ado": ["Ado 狂言", "Ado Hibana", "Ado 唱"],
    "YOASOBI": ["YOASOBI THE BOOK", "YOASOBI ADRENA"],
    "髭男dism": ["Official髭男dism ESCAPARADE", "Official髭男dism Editorial", "Official髭男dism Traveler"],
    "King Gnu": ["King Gnu CEREMONY", "King Gnu THE GREATEST UNKNOWN", "King Gnu BOY"],
    "ONE OK ROCK": ["ONE OK ROCK Niche", "ONE OK ROCK Eye of the Storm"],
    "LiSA": ["LiSA ALTEREGO"],
    "椎名林檎": ["椎名林檎 逆輸入", "椎名林檎 勝訴"],
    "米津玄師": ["米津玄師 Lemon", "米津玄師 KICK BACK"],
    "Vaundy": ["Vaundy replica"],
    "藤井風": ["藤井風 HELP EVER HURT NEVER", "藤井風 LOVE ALL SERVE ALL"],
}

MERCH = ["生写真", "缶バッジ", "Tシャツ", "パーカー", "タオル", "ポーチ",
         "ステッカー", "ポスター", "うちわ", "アクスタ", "カレンダー",
         "ランヤード", "ストラップ", "クリアカード", "マスキングテープ"]
CD_INDICATORS = ["CD", "アルバム", "シングル", "ミニアルバム", "初回盤", "未開封", "通常盤"]

def write_heartbeat(status, count=None, msg=""):
    try:
        data = {"status": status, "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                "count": count, "msg": msg, "pid": os.getpid()}
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception: pass

def kill_chrome():
    """Aggressively kill all leftover chrome processes."""
    killed = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit(): continue
        if pid == str(os.getpid()): continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                name = f.read().strip()
            if "chrome" in name.lower() or "chromium" in name.lower():
                os.kill(int(pid), signal.SIGKILL)
                killed += 1
        except Exception: pass
    return killed

def is_merch(t):
    return any(p in t for p in MERCH)
def is_cd(t):
    return any(ind in t for ind in CD_INDICATORS)
def clean_title(t):
    import re
    t = re.sub(r"\s*のサムネイル.*$", "", t or "")
    return t.strip()

async def search_mjp(page, kw, n=5):
    url = "https://jp.mercari.com/search?keyword=" + urllib.parse.quote(kw, safe="") + "&status=on_sale"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)
        items = await page.eval_on_selector_all("a[href*='/item/']",
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
        results = []; seen = set()
        for it in items or []:
            if not it or not it.get("href") or it["href"] in seen: continue
            seen.add(it["href"])
            results.append({"href": it["href"], "title": clean_title(it.get("title","")), "price": int(it.get("price",0) or 0), "image": it.get("image")})
            if len(results) >= n: break
        return results
    except Exception as e:
        return []

async def main_loop():
    from playwright.async_api import async_playwright
    write_heartbeat("starting")
    print("[scraper] starting v2, interval=" + str(INTERVAL_SEC) + "s", flush=True)
    while True:
        try:
            write_heartbeat("running")
            async with async_playwright() as p:
                b = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--single-process"])
                ctx = await b.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width":1280,"height":900}
                )
                page = await ctx.new_page()

                c = sqlite3.connect(DB, timeout=60)
                c.row_factory = sqlite3.Row
                existing = set(r["external_item_id"] for r in c.execute("SELECT external_item_id FROM market_items WHERE source='wameiji'").fetchall())
                c.close()

                new_count = 0
                # Load watchlist once for catalog mapping
                _watch_list = []
                try:
                    sys.path.insert(0, "/app/data")
                    if "match_real" not in sys.modules:
                        import match_real  # noqa: F401
                except Exception:
                    pass
                try:
                    _wm = sqlite3.connect(DB, timeout=10)
                    _wm.row_factory = sqlite3.Row
                    _watch_list = [dict(r) for r in _wm.execute("SELECT catalog_no, artist FROM watchlist").fetchall()]
                    _wm.close()
                except Exception as _e:
                    print("[scraper] watchlist load err: " + str(_e)[:120], flush=True)

                for artist, kws in ARTIST_KEYWORDS.items():
                    for kw in kws:
                        try:
                            results = await search_mjp(page, kw, n=5)
                            for r in results:
                                ext = r["href"].split("/item/")[-1].split("?")[0]
                                if ext in existing: continue
                                if is_merch(r["title"]): continue
                                if not is_cd(r["title"]): continue
                                c = sqlite3.connect(DB, timeout=30)
                                cur = c.cursor()
                                # Try to assign catalog_no from watchlist by title match
                                _cat = ""
                                if _watch_list:
                                    try:
                                        from match_real import assign_catalog_no_for_item
                                        _c = assign_catalog_no_for_item(r["title"] or "", _watch_list)
                                        if _c: _cat = _c
                                    except Exception:
                                        _cat = ""
                                if not _cat:
                                    _cat = "AUTO"
                                cur.execute(
                                    "INSERT INTO market_items (source, source_site, external_item_id, catalog_no, title, price, currency, url, image_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                                    ("wameiji", "mercari_jp", ext, _cat, r["title"], float(r["price"]), "JPY", r["href"], r["image"])
                                )
                                c.commit()
                                c.close()
                                existing.add(ext)
                                new_count += 1
                        except Exception as e:
                            print("[scraper] err " + kw + ": " + str(e)[:60], flush=True)

                await ctx.close()
                await b.close()
            # Aggressive cleanup after cycle
            # ---- Real-data match pass ----
            try:
                sys.path.insert(0, "/app/data")
                import importlib
                if "match_real" in sys.modules:
                    importlib.reload(sys.modules["match_real"])
                else:
                    import match_real  # noqa: F401
                match_res = sys.modules["match_real"].match_real_market_items(DB)
                print("[scraper] match pipeline: " + str(match_res), flush=True)
            except Exception as e:
                print("[scraper] match pipeline err: " + str(e)[:200], flush=True)

            killed = kill_chrome()
            print("[scraper] cycle done, new=" + str(new_count) + ", killed_chrome=" + str(killed) + ", next in " + str(INTERVAL_SEC) + "s", flush=True)
            write_heartbeat("idle", count=new_count, msg="cycle done, killed " + str(killed) + " chrome procs")
        except Exception as e:
            print("[scraper] cycle error: " + str(e)[:120], flush=True)
            write_heartbeat("error", msg=str(e)[:200])
            kill_chrome()
        time.sleep(INTERVAL_SEC)

if __name__ == "__main__":
    asyncio.run(main_loop())