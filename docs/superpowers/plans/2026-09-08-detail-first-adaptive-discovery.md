# Detail-first Adaptive Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Replace the page-local two-item collector with a persistent, detail-first queue that verifies Wameiji product pages before grouped Xianyu price sampling.

**Architecture:** Search pages only enqueue source URLs. SQLite owns a per-pool detail queue and a resale queue keyed by detail-confirmed product identity; a scan drains those queues under separate budgets. The worker records every stage and pauses a pool when a small, objective quality gate proves the parser or keyword direction is bad.

**Tech Stack:** Python 3.12, SQLite, pytest, Playwright-backed local browser adapters, static GitHub Pages UI, Render replica API.

---

## File structure

- src/cd_monitor/core/discovery.py: pool and candidate state with queue budgets and stage fields.
- src/cd_monitor/storage/sqlite.py: idempotent schema upgrade, URL-deduping migration, global queue reads, stage updates and stage-level run metrics.
- src/cd_monitor/services/discovery.py: search ingestion, detail queue drain, then resale queue grouping.
- src/cd_monitor/services/discovery_worker.py: reports paused-quality runs without issuing further browser actions.
- tests/test_discovery_storage.py: migration, queue ordering and URL-deduplication tests.
- tests/test_discovery_service.py: deterministic queue replay and group-query tests.
- tests/test_discovery_worker.py: budget and pause behavior tests.

### Task 1: Persist pipeline state and queue budgets

**Files:**
- Modify: src/cd_monitor/core/discovery.py
- Modify: src/cd_monitor/storage/sqlite.py
- Modify: tests/test_discovery_storage.py

- [ ] **Step 1: Write the failing storage tests**

    def test_detail_queue_uses_persistent_candidates_before_new_cards(tmp_path: Path) -> None:
        db_path = tmp_path / "selection.db"
        init_db(db_path)
        pool = list_discovery_pools(db_path)[0]
        first = upsert_discovery_candidate(db_path, _candidate(pool.id, "old", 900))
        upsert_discovery_candidate(db_path, _candidate(pool.id, "new", 800))
        queued = list_discovery_detail_queue(db_path, pool.id or 0, limit=1)
        assert [item.id for item in queued] == [first]

- [ ] **Step 2: Run the targeted test and confirm it fails**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_storage.py::test_detail_queue_uses_persistent_candidates_before_new_cards -q

Expected: failure because list_discovery_detail_queue does not exist.

- [ ] **Step 3: Add idempotent columns and storage helpers**

Add detail_budget, xianyu_query_budget, search_card_budget, queue_high_watermark, capture_state, and pause_reason to discovery_pools. Add pipeline_stage, product_key, detail_attempt_count, last_detail_attempt_at, last_detail_error, and detail_verified_at to discovery_candidates. Implement list_discovery_detail_queue(db_path, pool_id, limit) which returns active, unverified source URLs ordered by evidence then oldest first. The migration must merge same-pool same-URL history by retaining the strongest detail-verified source row and marking duplicate rows ignored.

- [ ] **Step 4: Run storage tests**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_storage.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

Run: git add src/cd_monitor/core/discovery.py src/cd_monitor/storage/sqlite.py tests/test_discovery_storage.py
Run: git commit -m "feat: persist detail-first discovery queues"

### Task 2: Make search ingestion enqueue-only and drain global source details

**Files:**
- Modify: src/cd_monitor/services/discovery.py
- Modify: tests/test_discovery_service.py

- [ ] **Step 1: Write failing replay tests**

Create a two-scan fixture: first search saves A, B and C with a one-item detail budget; second search returns only D. The next detail call must be B, not D. Create a second test that asserts Xianyu is never called for an unverified detail candidate.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_service.py -k "backlog or unverified_detail_candidate" -q

Expected: the old page-local loop chooses the current search page or lacks the new test names.

- [ ] **Step 3: Refactor the scan into explicit stages**

Use _ingest_search_candidates, _verify_detail_queue, and _detail_queue_priority helpers. Ingestion may save a bounded number of cards but must not call Xianyu. Verification reads list_discovery_detail_queue, calls fetch_wameiji_detail serially up to pool.detail_budget, updates stage after every result, and rejects unavailable, incomplete or non-media detail pages before any resale query.

- [ ] **Step 4: Run service tests**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_service.py -q

Expected: PASS, with the legacy detail-first tests retained.

- [ ] **Step 5: Commit**

Run: git add src/cd_monitor/services/discovery.py tests/test_discovery_service.py
Run: git commit -m "feat: drain verified Wameiji detail queue"

### Task 3: Group resale lookups by verified product identity

**Files:**
- Modify: src/cd_monitor/services/discovery.py
- Modify: src/cd_monitor/storage/sqlite.py
- Modify: tests/test_discovery_service.py

- [ ] **Step 1: Write the failing group-query test**

Create two verified source listings for SRCL-3520 with different Wameiji URLs and prices. Both must be evaluated, but fetch_xianyu receives SRCL-3520 exactly once.

- [ ] **Step 2: Run it and confirm it fails**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_service.py::test_two_verified_source_listings_for_one_catalog_share_one_xianyu_query -q

Expected: failure because the old loop calls Xianyu once per source listing.

- [ ] **Step 3: Add grouped resale evaluation**

Implement a resale-queue helper that only returns active detail-verified candidates needing a refresh. Group by catalog, JAN, or strict title key; consume pool.xianyu_query_budget per distinct group. Reuse existing sample cleaning and cost model for each source listing, and only stamp query time after the group evaluation ends.

- [ ] **Step 4: Run service and storage tests**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_service.py tests/test_discovery_storage.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

Run: git add src/cd_monitor/services/discovery.py src/cd_monitor/storage/sqlite.py tests/test_discovery_service.py
Run: git commit -m "feat: group resale samples by verified product"

### Task 4: Persist stage metrics and quality pauses

**Files:**
- Modify: src/cd_monitor/core/discovery.py
- Modify: src/cd_monitor/storage/sqlite.py
- Modify: src/cd_monitor/services/discovery.py
- Modify: src/cd_monitor/services/discovery_worker.py
- Modify: tests/test_discovery_service.py
- Modify: tests/test_discovery_worker.py

- [ ] **Step 1: Write failing stop-gate tests**

Create a four-detail fixture with no valid detail items. The pool must become paused_quality and must not query Xianyu. Add a worker test proving a paused-quality pool is skipped even when its keyword is due.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_service.py tests/test_discovery_worker.py -k "pause" -q

Expected: failure because the pool lacks a quality pause state.

- [ ] **Step 3: Implement bounded diagnostic behavior**

Persist detail_query_count, detail_verified_count, detail_rejected_count, xianyu_query_count and resale_sampled_count in discovery_runs. After a scan, pause the pool when at least four detail attempts yield less than 50% verified pages, or after two consecutive security/detail-redirect blocks. Preserve the existing Xianyu source cooldown behavior. DiscoveryWorker.run_once must skip capture_state paused_quality until a config update explicitly returns it to active.

- [ ] **Step 4: Run all discovery tests**

Run: .\.venv\Scripts\python.exe -m pytest tests/test_discovery_storage.py tests/test_discovery_service.py tests/test_discovery_worker.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

Run: git add src/cd_monitor/core/discovery.py src/cd_monitor/storage/sqlite.py src/cd_monitor/services/discovery.py src/cd_monitor/services/discovery_worker.py tests/test_discovery_storage.py tests/test_discovery_service.py tests/test_discovery_worker.py
Run: git commit -m "feat: pause low-quality discovery batches"

### Task 5: Validate the deployed behavior before any live scan

**Files:**
- Modify: README.md
- Modify: TAKEOVER_STATUS.md
- Test: tests/test_discovery_api.py
- Test: tests/test_pages_discovery_ui.py

- [ ] **Step 1: Add API and UI assertions for stage telemetry**

Add a discovery summary test that exposes capture_state and stage metrics. Keep the homepage assertion for the Xianyu left column, profit center and Wameiji detail right column.

- [ ] **Step 2: Run static and automated validation**

Run:
    node --check web/app.js
    node --check web/discovery-ui.js
    .\.venv\Scripts\python.exe -m pytest tests/test_discovery_storage.py tests/test_discovery_service.py tests/test_discovery_worker.py tests/test_discovery_api.py tests/test_pages_discovery_ui.py -q
    .\.venv\Scripts\python.exe -m pytest -q
    git diff --check

Expected: all tests pass and no whitespace errors.

- [ ] **Step 3: Run the controlled browser smoke only after tests pass**

Use one enabled pool with detail_budget = 4 and xianyu_query_budget = 3. Inspect saved HTML, screenshots, final URLs and stage counters. Stop immediately on a quality pause, login/security challenge or mismatch; do not try to bypass the site.

- [ ] **Step 4: Deploy only the validated source**

Run:
    git add README.md TAKEOVER_STATUS.md tests/test_discovery_api.py tests/test_pages_discovery_ui.py
    git commit -m "docs: document detail-first discovery recovery"
    git push origin main

Confirm Render health, GitHub Pages build and the three-column board after deployment.
