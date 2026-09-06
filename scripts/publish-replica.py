#!/usr/bin/env python3
"""Publish the local SQLite source of truth to the Render API.

The browser profiles and collector remain local.  This small publisher only
ships the SQLite database, compressed in transit, to the authenticated
``/api/sync/database`` endpoint.  Render's free filesystem is ephemeral, so
run this in a loop on the collector computer; a restart is recovered by the
next successful publish.
"""

from __future__ import annotations

import argparse
import gzip
import os
import sqlite3
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _checkpoint(db_path: Path) -> None:
    """Checkpoint WAL pages before reading the file for a consistent copy."""
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")


def publish_once(db_path: Path, api_url: str, token: str, timeout: int = 60) -> dict[str, object]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    _checkpoint(db_path)
    raw = db_path.read_bytes()
    if not raw.startswith(b"SQLite format 3\x00"):
        raise ValueError("database is not a SQLite file")
    body = gzip.compress(raw, compresslevel=6)
    endpoint = api_url.rstrip("/") + "/api/sync/database"
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "gzip",
            "X-CD-Sync-Token": token,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        import json

        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"replica rejected: {payload}")
    return {"ok": True, "bytes": len(raw), "compressed_bytes": len(body)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish local CD Monitor SQLite to a Render API")
    parser.add_argument("--db", default="data/local/takeover.db", help="local SQLite database")
    parser.add_argument("--url", default=os.getenv("CD_REPLICA_URL", ""), help="Render API origin")
    parser.add_argument("--token", default=os.getenv("CD_SYNC_TOKEN", ""), help="sync token")
    parser.add_argument("--interval-seconds", type=int, default=300, help="repeat interval; 0 means once")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    if not args.url.strip() or not args.token.strip():
        parser.error("--url/--token or CD_REPLICA_URL/CD_SYNC_TOKEN are required")
    db_path = Path(args.db).expanduser().resolve()
    while True:
        try:
            result = publish_once(db_path, args.url, args.token, args.timeout_seconds)
            print(f"[replica] published {result['bytes']} bytes ({result['compressed_bytes']} gzip)", flush=True)
        except (OSError, sqlite3.Error, HTTPError, URLError, ValueError, RuntimeError) as exc:
            print(f"[replica] error: {type(exc).__name__}: {exc}", flush=True)
        if args.interval_seconds <= 0:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
