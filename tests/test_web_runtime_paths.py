"""Verify scraper status sidecars follow the configured local data directory."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from cd_monitor.storage.sqlite import init_db
from cd_monitor.web_server import create_server


def test_scraper_status_reads_cd_data_dir(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "runtime-data"
    data_dir.mkdir()
    (data_dir / "_scraper_heartbeat.json").write_text(
        json.dumps({"status": "running", "count": 4}), encoding="utf-8"
    )
    monkeypatch.setenv("CD_DATA_DIR", str(data_dir))
    db_path = tmp_path / "runtime.db"
    init_db(db_path)
    server = create_server("127.0.0.1", 0, db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://{server.server_address[0]}:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/api/scrape/status", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["status"] == "running"
        assert payload["daemon_heartbeat"]["count"] == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
