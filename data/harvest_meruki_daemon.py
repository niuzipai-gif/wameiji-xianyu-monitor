#!/usr/bin/env python3
"""Daemon wrapper around harvest_meruki_malls.py - cycles every N seconds.

Uses the wameiji login state (meruki.cn cookies) to harvest Surugaya /
Lashinbang / Mercari / Rakuten through meruki.cn (avoids direct site
blocking of headless Chrome).
"""
import asyncio, importlib, os, sys, time, signal, json
from pathlib import Path

sys.path.insert(0, "/app/data")
DB = os.environ.get("CD_DB", "/var/lib/cd_monitor/cd_monitor.db")
HB  = "/app/data/_mall_heartbeat.json"
INTERVAL = int(os.environ.get("MALL_INTERVAL", "900"))

def hb(status, new=None, msg=""):
    try:
        d = {"status": status, "last_run": time.strftime("%Y-%m-%d %H:%M:%S")}
        if new is not None: d["new"] = new
        if msg: d["msg"] = msg
        Path(HB).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception: pass

_should_stop = False
def _stop(*_):
    global _should_stop
    _should_stop = True
signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

async def main():
    print("[harvest_daemon] starting, interval=" + str(INTERVAL) + "s", flush=True)
    while not _should_stop:
        try:
            os.environ["CD_DB"] = DB
            import harvest_meruki_malls as h
            importlib.reload(h)
            await h.main()
            # read heartbeat from h's output
            try:
                hdata = json.loads(Path(HB).read_text(encoding="utf-8"))
                hb("idle", new=hdata.get("new", 0),
                   msg="harvest new=" + str(hdata.get("new", 0)) +
                       " per_mall=" + json.dumps(hdata.get("per_mall", {})))
            except Exception:
                hb("idle", 0, "harvest done")
            # Trigger match after harvest
            try:
                import urllib.request
                req = urllib.request.Request("http://127.0.0.1:9890/api/scrape/match",
                                             data=b"{}",
                                             headers={"Content-Type": "application/json"},
                                             method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    print("[harvest_daemon] match trigger: " + r.read().decode()[:100], flush=True)
            except Exception as e:
                print("[harvest_daemon] match trigger err: " + str(e)[:120], flush=True)
        except Exception as e:
            print("[harvest_daemon] cycle err: " + str(e)[:200], flush=True)
            hb("error", 0, str(e)[:200])
        # sleep but check stop signal
        for _ in range(INTERVAL):
            if _should_stop: break
            await asyncio.sleep(1)
    print("[harvest_daemon] stopped", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
