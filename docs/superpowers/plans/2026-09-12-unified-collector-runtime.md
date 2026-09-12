# Unified Collector Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep local capture, Render replication, GitHub Pages data, and both command paths on the single active `dual-market.db` source of truth.

**Architecture:** Give all service launchers the same `data/local/dual-market.db` default and have `start-all.ps1` pass that value explicitly to each child. The worker will consume pending local debug commands as local-only work, then consume and acknowledge Render commands with their existing remote identity and completion protocol.

**Tech Stack:** PowerShell launchers, Python 3.11, SQLite, pytest.

---

## File structure

- `scripts/start-local.ps1`: local board default database.
- `scripts/start-replica.ps1`: source database uploaded to Render.
- `scripts/start-discovery.ps1`: browser collector default database; only the untouched parameter default changes.
- `scripts/start-all.ps1`: explicit shared database propagation to all three services.
- `scripts/install-collector-autostart.ps1`: scheduled tasks pass the same database explicitly.
- `src/cd_monitor/services/discovery_worker.py`: separates local command completion from remote command acknowledgement.
- `tests/test_collector_runtime_scripts.py`: static launch contract.
- `tests/test_discovery_worker.py`: local command execution regression.

### Task 1: Lock the shared launcher database contract

**Files:**
- Create: `tests/test_collector_runtime_scripts.py`
- Modify: `scripts/start-local.ps1`
- Modify: `scripts/start-replica.ps1`
- Modify: `scripts/start-discovery.ps1`
- Modify: `scripts/start-all.ps1`
- Modify: `scripts/install-collector-autostart.ps1`

- [x] **Step 1: Write the failing static-contract test.**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "data/local/dual-market.db"

def script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")

def test_all_collector_launchers_default_to_the_active_selection_database() -> None:
    for name in ("start-local.ps1", "start-replica.ps1", "start-discovery.ps1"):
        assert f'[string]$Database = "{DATABASE}"' in script(name)

def test_primary_and_autostart_launchers_pass_one_database_to_every_service() -> None:
    start_all = script("start-all.ps1")
    assert f'[string]$Database = "{DATABASE}"' in start_all
    assert start_all.count('"-Database", $Database') == 3
    autostart = script("install-collector-autostart.ps1")
    assert autostart.count(f'"-Database", "{DATABASE}"') == 3
```

- [x] **Step 2: Run test to verify it fails.**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_collector_runtime_scripts.py -q`

Expected: FAIL because the existing defaults still name `takeover.db` and neither aggregate launcher forwards `-Database`.

- [x] **Step 3: Apply the minimal launcher changes.**

Use `data/local/dual-market.db` as each individual service default. Add this parameter to `start-all.ps1`:

```powershell
[string]$Database = "data/local/dual-market.db"
```

Pass it to every child with these exact argument pairs:

```powershell
"-File", $LocalScript, "-Port", $Port, "-Database", $Database
"-File", $ReplicaScript, "-Database", $Database
"-File", $DiscoveryScript, "-Database", $Database
```

Set each scheduled task `ExtraArgs` to include:

```powershell
@("-Database", "data/local/dual-market.db")
```

Do not change the user-edited login preflight body in `start-discovery.ps1`.

- [x] **Step 4: Run test to verify it passes.**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_collector_runtime_scripts.py -q`

Expected: PASS.

- [x] **Step 5: Commit.**

```powershell
git add scripts/start-local.ps1 scripts/start-replica.ps1 scripts/start-discovery.ps1 scripts/start-all.ps1 scripts/install-collector-autostart.ps1 tests/test_collector_runtime_scripts.py
git commit -m "fix: unify collector runtime database"
```

### Task 2: Let the worker complete local debug commands

**Files:**
- Modify: `tests/test_discovery_worker.py`
- Modify: `src/cd_monitor/services/discovery_worker.py`

- [x] **Step 1: Write the failing local-command regression test.**

Add `create_collector_command` to the SQLite imports, then add this test:

```python
def test_worker_completes_local_scan_command_without_a_remote_client(tmp_path: Path) -> None:
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    update_discovery_pool(db_path, pool_id, {"keyword_budget": 1})
    command = create_collector_command(db_path, "scan_now", {"pool_id": pool_id})

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return []

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("an empty source scan must not query Xianyu")

    worker = DiscoveryWorker(
        db_path=db_path,
        fetch_wameiji=fetch_wameiji,
        fetch_wameiji_detail=_verified_detail,
        fetch_xianyu=fetch_xianyu,
    )
    result = asyncio.run(worker.run_once())

    assert result.command_count == 1
    assert list_collector_commands(db_path)[0]["id"] == command["id"]
    assert list_collector_commands(db_path)[0]["status"] == "completed"
```

- [x] **Step 2: Run test to verify it fails.**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_discovery_worker.py::test_worker_completes_local_scan_command_without_a_remote_client -q`

Expected: FAIL because `_fetch_commands` returns no commands without a remote client, leaving the local command `pending`.

- [x] **Step 3: Implement local/remote command ownership.**

Import `list_collector_commands`. In `_fetch_commands`, first read local rows with statuses `pending`, `accepted`, and `running`; exclude records whose `dedupe_key` starts with `remote:`; tag each retained dictionary with `_local_command_id` and `_command_origin = "local"`. Then append fetched Render rows after mirroring them, tagged `_command_origin = "remote"`.

In `_complete_commands`, always complete a local row with `complete_collector_command`. Only when `_command_origin == "remote"` call `self.command_client.complete(...)` first; if that acknowledgement fails, leave that remote mirror unfinished for retry. A local command must never call the Render client.

- [x] **Step 4: Run local and remote worker tests.**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_discovery_worker.py::test_worker_completes_local_scan_command_without_a_remote_client tests/test_discovery_worker.py::test_worker_executes_remote_scan_once_command_and_acknowledges -q`

Expected: PASS. The local test proves direct local UI commands are consumed, and the existing remote test proves Render acknowledgement and remote identity mirroring remain intact.

- [x] **Step 5: Commit.**

```powershell
git add src/cd_monitor/services/discovery_worker.py tests/test_discovery_worker.py
git commit -m "fix: execute local selection commands"
```

### Task 3: Replace mismatched live helpers and prove the end-to-end path

**Files:** Runtime processes only; no source file changes.

- [ ] **Step 1: Record source and replica summaries without printing secrets.**

Query local `dual-market.db` read-only and query the authenticated Render board through `.env` values loaded only inside the process. Print only summary counts and timestamps.

- [ ] **Step 2: Stop only the identified stale publisher and collector helper trees.**

Verify their exact command lines first. Stop the publisher that names `takeover.db` and the collector that runs the pre-fix worker, then confirm their process ids have exited. Do not stop the local API listener or any unrelated Python process.

- [ ] **Step 3: Start replacements hidden with explicit active database arguments.**

```powershell
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
  "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\\start-replica.ps1",
  "-Database", "data/local/dual-market.db", "-IntervalSeconds", "60"
)
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
  "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\\start-discovery.ps1",
  "-Database", "data/local/dual-market.db", "-PollSeconds", "60"
)
```

- [ ] **Step 4: Verify Render receives active source data.**

Within two publishing intervals, assert Render's `active_candidates`, `fresh_source_details`, `stale_source_details`, `resale_ready_candidates`, and `last_scan_at` equal the local summary. The remote board must include `research_candidates`.

- [ ] **Step 5: Submit one controlled remote scan command and wait for its terminal status.**

Post `{"command_type":"scan_now","pool_id":1}` to the authenticated Render endpoint, then poll only that command for at most two worker intervals. Accept `completed` or `human_required`; do not submit a purchase, message a seller, or retry a login bypass. Confirm the local database mirrors the same completed command identity.

- [ ] **Step 6: Run the focused regression suite.**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_collector_runtime_scripts.py tests/test_discovery_worker.py tests/test_discovery_api.py tests/test_replica_sync.py tests/test_pages_discovery_ui.py -q`

Expected: PASS.
