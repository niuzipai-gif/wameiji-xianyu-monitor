"""run_one_cycle.py — single scraping cycle (no matching).

Matching is now triggered via HTTP /api/scrape/match so it runs inside the
web_server process and avoids 9p / WAL contention between two python processes.
"""
import os, sys, asyncio, time, sqlite3
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_PROJECT_SRC = _BASE_DIR.parent / "src"
for _path in (_BASE_DIR, _PROJECT_SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import multi_source_scraper as mss

DB = os.environ.get('CD_DB', str(_BASE_DIR / 'cd_monitor.db'))


def _open_db(db_path: str, timeout: int = 60):
    """Open sqlite with WAL + long busy_timeout to survive 9p transient locks."""
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


async def main() -> None:
    print('[one_cycle] starting at ' + time.strftime('%Y-%m-%d %H:%M:%S'), flush=True)
    enabled = os.environ.get('ENABLED_SOURCES', 'mercari_jp,yahoo_auctions,suruga_ya').split(',')
    source_fns = {
        'mercari_jp':     mss.search_mercari,
        'yahoo_auctions': mss.search_yahoo_auctions,
        'suruga_ya':      mss.search_suruga_ya,
    }
    enabled_fns = {s.strip(): source_fns[s.strip()] for s in enabled if s.strip() in source_fns}
    print('[one_cycle] enabled: ' + ', '.join(enabled_fns.keys()), flush=True)

    scrape_res = {"new": 0, "per_source": {}}
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            watches = mss.load_watch(mss.DB)
            scrape_res = await mss.run_cycle(p, source_fns=enabled_fns, watches=watches)
    except Exception as e:
        print('[one_cycle] scrape err: ' + str(e)[:200], flush=True)

    killed = mss.kill_chrome()
    print('[one_cycle] result=' + str(scrape_res) + ' killed_chrome=' + str(killed), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
