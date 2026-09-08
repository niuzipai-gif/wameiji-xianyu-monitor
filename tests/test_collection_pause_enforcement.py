"""Regression coverage for the global dual-market collection pause."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cd_monitor import web_server
from cd_monitor.core.models import WatchItem
from cd_monitor.services.discovery_worker import DiscoveryWorker
from cd_monitor.storage.sqlite import (
    add_watch,
    init_db,
    list_collector_commands,
    list_discovery_pools,
    update_discovery_pool,
)
from cd_monitor.web_server import create_server


def _post_json(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        finally:
            error.close()


def _start_server(db_path: Path) -> tuple[object, str]:
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    return server, base_url


@pytest.mark.parametrize(
    ("route", "payload", "with_watch"),
    [
        ("/api/scan/live-html", {"catalog_no": "SRCL-3520"}, False),
        ("/api/scan/live-watchlist", {}, True),
    ],
)
def test_paused_live_scan_endpoints_reject_before_browser_capture(
    tmp_path: Path,
    monkeypatch,
    route: str,
    payload: dict[str, object],
    with_watch: bool,
) -> None:
    monkeypatch.delenv("DUAL_MARKET_COLLECTION_PAUSED", raising=False)
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    if with_watch:
        add_watch(db_path, WatchItem(catalog_no="SRCL-3520"))

    captures: list[tuple[object, ...]] = []

    async def capture(*args, **_kwargs):
        captures.append(args)
        return {
            "status": "ok",
            "opportunity_count": 0,
            "opportunity_ids": [],
            "wameiji_capture": {"status": "ok"},
            "xianyu_capture": {"status": "ok"},
        }

    monkeypatch.setattr(web_server, "capture_and_evaluate_live_html", capture)
    server, base_url = _start_server(db_path)
    try:
        status, response = _post_json(f"{base_url}{route}", payload)
    finally:
        server.shutdown()
        server.server_close()

    assert status == 409
    assert response["error"] == "collector_paused"
    assert captures == []


@pytest.mark.parametrize("route", ["/api/scrape/now", "/api/scrape/full"])
def test_paused_scrape_endpoints_reject_before_worker_or_subprocess(
    tmp_path: Path,
    monkeypatch,
    route: str,
) -> None:
    monkeypatch.delenv("DUAL_MARKET_COLLECTION_PAUSED", raising=False)
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    server, base_url = _start_server(db_path)
    worker_starts: list[object] = []
    popen_calls: list[tuple[object, ...]] = []

    class InertThread:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        def start(self) -> None:
            worker_starts.append(self)

    def forbidden_popen(*args, **_kwargs):
        popen_calls.append(args)
        raise AssertionError("paused scrape endpoint attempted to create a subprocess")

    try:
        # The HTTP server has already started with the real threading module.
        # Replacing only future imports prevents the unguarded legacy routes
        # from launching a real background worker during this red/green test.
        with monkeypatch.context() as request_patches:
            request_patches.setitem(sys.modules, "threading", types.SimpleNamespace(Thread=InertThread))
            request_patches.setattr(subprocess, "Popen", forbidden_popen)
            status, response = _post_json(f"{base_url}{route}", {})
    finally:
        server.shutdown()
        server.server_close()

    assert status == 409
    assert response["error"] == "collector_paused"
    assert worker_starts == []
    assert popen_calls == []


def test_discovery_startup_checks_pause_before_browser_probe_or_worker_start() -> None:
    script = Path("scripts/start-discovery.ps1").read_text(encoding="utf-8")

    assert "function Test-DualMarketCollectionPaused" in script
    function_start = script.index("function Test-DualMarketCollectionPaused")
    pause_guard = script.index("if (Test-DualMarketCollectionPaused)", function_start)
    guard_to_browser_setup = script[pause_guard : script.index('"BROWSER_ENABLED"', pause_guard)]

    assert '[string]::IsNullOrWhiteSpace($value)' in script[function_start:pause_guard]
    assert "return $true" in script[function_start:pause_guard]
    assert "exit 0" in guard_to_browser_setup
    assert pause_guard < script.index("& $Python -X utf8 -c", pause_guard)
    assert pause_guard < script.index("& $Python -m cd_monitor.cli discovery-worker", pause_guard)


def test_discovery_startup_defaults_to_pause_before_requiring_an_env_file() -> None:
    script = Path("scripts/start-discovery.ps1").read_text(encoding="utf-8")

    pause_guard = script.index("if (Test-DualMarketCollectionPaused)")
    missing_env_file_check = script.index(
        "if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf))"
    )

    assert pause_guard < missing_env_file_check


def test_paused_discovery_commands_reject_pool_activation_without_queueing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DUAL_MARKET_COLLECTION_PAUSED", raising=False)
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    server, base_url = _start_server(db_path)
    try:
        status, response = _post_json(
            f"{base_url}/api/discovery/commands",
            {
                "command_type": "set_pool",
                "pool_id": pool_id,
                "updates": {"enabled": True},
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    assert status == 409
    assert response == {"error": "collector_paused"}
    assert list_collector_commands(db_path) == []


def test_paused_worker_skips_active_pools_and_completes_remote_commands_without_fetching(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DUAL_MARKET_COLLECTION_PAUSED", raising=False)
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    active_pool, remotely_enabled_pool = list_discovery_pools(db_path)
    assert active_pool.id is not None
    assert remotely_enabled_pool.id is not None
    update_discovery_pool(db_path, remotely_enabled_pool.id, {"enabled": False})
    fetch_calls: list[str] = []

    async def fetch_wameiji(_keyword: str) -> list[object]:
        fetch_calls.append("wameiji")
        return []

    async def fetch_wameiji_detail(_item: object) -> object | None:
        fetch_calls.append("wameiji_detail")
        return None

    async def fetch_xianyu(_query: str) -> list[object]:
        fetch_calls.append("xianyu")
        return []

    class PendingCommandClient:
        def __init__(self) -> None:
            self.completions: list[tuple[int, str, dict[str, object]]] = []

        def fetch_pending(self) -> list[dict[str, object]]:
            return [
                {
                    "id": 701,
                    "command_type": "set_pool",
                    "payload": {
                        "pool_id": remotely_enabled_pool.id,
                        "updates": {"enabled": True},
                    },
                }
            ]

        def complete(
            self, command_id: int, *, status: str, result: dict[str, object]
        ) -> dict[str, object]:
            self.completions.append((command_id, status, result))
            return {"id": command_id, "status": status, "result": result}

    command_client = PendingCommandClient()
    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=fetch_wameiji_detail,
        fetch_xianyu=fetch_xianyu,
        command_client=command_client,
    )

    result = asyncio.run(worker.run_once())

    assert result.scan_count == 0
    assert fetch_calls == []
    assert next(pool for pool in list_discovery_pools(db_path) if pool.id == remotely_enabled_pool.id).enabled is False
    assert command_client.completions == [
        (
            701,
            "completed",
            {"runs": 0, "status": "paused", "reason": "collector_paused"},
        )
    ]
