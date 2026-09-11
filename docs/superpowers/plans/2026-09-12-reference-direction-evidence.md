# Reference Direction Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use approved reference products to show product-direction evidence for unfamiliar candidates without making a commercial decision.

**Architecture:** A pure rule module extracts a fixed multilingual direction taxonomy. The local reference-memory service aggregates it per unique product, two local GET endpoints expose it, and the selection board presents it only as a detail-research hint.

**Tech Stack:** Python 3.11, standard-library `re`, SQLite read-only connection, existing HTTP server, vanilla JavaScript, pytest.

---

### Task 1: Pure direction extraction

**Files:** Create `src/cd_monitor/core/reference_directions.py`; create `tests/test_reference_directions.py`.

- [ ] Write tests that assert `初回限定 CD 同人 音楽` yields `physical_music`, `doujin_or_anime`, and `limited_or_first_edition`; `闲鱼 交易 角色 搜索 邮费 自理` yields no direction; and a candidate with `title="STEINS;GATE Switch 限定版"` plus raw page text containing CD must retain only the structured title text.
- [ ] Run `PYTHONPATH=$PWD/src python -m pytest -q tests/test_reference_directions.py` and verify imports fail before implementation.
- [ ] Add fixed labels and patterns: physical music (CD/专辑/音楽/album), console game (Switch/PS Vita/ゲーム/Nintendo), art/book (画集/设定集/小说/book), doujin/anime (同人/アニメ), and limited/first edition (初回/限定/特典/limited). Expose `extract_reference_directions(text)` and `build_candidate_direction_text(...)`. Use raw text only if all structured fields are empty; do not import database, browser, price, or availability code.
- [ ] Re-run the test; commit `feat: extract approved reference directions`.

### Task 2: Read-only aggregation and candidate evidence

**Files:** Modify `src/cd_monitor/services/reference_memory.py`; create `tests/test_reference_direction_services.py`.

- [ ] Write failing fixtures proving two screenshots of the same reference product count once, a CD candidate returns `state="positive_direction_covered"` with `decision_effect="none"`, and unknown direction returns `state="no_direction_evidence"`, empty directions, and `decision_effect="none"`.
- [ ] Run `PYTHONPATH=$PWD/src python -m pytest -q tests/test_reference_direction_services.py` and verify missing service functions fail.
- [ ] Add `list_reference_direction_summary(db_path)` and `list_discovery_candidate_direction_evidence(db_path, limit=200)`. They must use `_connect_existing_database_read_only`, count direction membership once per `reference_products.id`, return `source="approved_reference_samples"`, `network_requests=0`, fixed label order and shares rounded to four decimals. Candidate input is only `id`, title, artist, edition, catalog number, JAN, and raw text. The functions must not call `init_db`, refresh a match, query market observations, write a table, return price/availability, or decide an outcome.
- [ ] Run the new tests plus `test_reference_memory_core.py`, `test_reference_memory_storage.py`, and `test_reference_memory_discovery.py`; commit `feat: summarize positive reference directions`.

### Task 3: Local API contract

**Files:** Modify `src/cd_monitor/web_server.py`; modify `tests/test_reference_memory_interfaces.py`.

- [ ] Write failing HTTP tests for `GET /api/reference-memory/directions` and `GET /api/reference-memory/candidate-directions?limit=20`; assert `source="approved_reference_samples"`, `network_requests=0`, and all candidate payloads have `decision_effect="none"`. Test `limit=0`, `201`, and `abc` return HTTP 400 `{"error":"invalid_limit"}`. Test a service `sqlite3.Error` returns a non-sensitive direction error.
- [ ] Implement the two GET routes using the existing 1..200 limit parser and only catch `TypeError`, `ValueError`, and `sqlite3.Error`. Do not add POST routes or external calls.
- [ ] Run interface and service tests; commit `feat: expose local reference direction evidence`.

### Task 4: Non-decisive board presentation

**Files:** Modify `web/index.html`; modify `web/discovery-ui.js`; modify `tests/test_selection_board_reference_panel.py`.

- [ ] Write a failing static contract requiring `#referenceDirectionList`, non-fatal fetches for both direction APIs, and literal copy `正样本方向证据`. Assert the renderer has no purchase, rejection, price, or availability wording.
- [ ] Add `referenceDirections` and `candidateDirections` view state. Fetch both endpoints with `catch(() => null)`. Render each direction with `认可样本覆盖 N/总数` and `正样本方向证据，仍需详情核验`. Build a candidate-ID map and show a compact badge only for `positive_direction_covered`; do not warn or render absence text for `no_direction_evidence`.
- [ ] Run `node --check web/discovery-ui.js` plus reference-panel and selection-feedback tests; commit `feat: show positive reference direction evidence`.

### Task 5: Verification and safe merge

**Files:** Modify `docs/superpowers/specs/2026-09-12-reference-direction-evidence-design.md` only if a verified implementation contract differs.

- [ ] Run focused direction, reference-memory, interface, panel, and feedback tests; run `node --check web/discovery-ui.js` and `git diff --check`.
- [ ] Run the full suite with `PYTHONPATH=$PWD/src python -m pytest -q`; expect all non-skipped tests to pass with only pre-existing explicit xfails.
- [ ] Use a read-only SQLite connection against `data/local/dual-market.db` to report reference product/sample counts, unique-product direction counts, and candidate direction states. Confirm direction payloads have no price or market fields. Do not launch a browser, refresh matches, or write feedback.
- [ ] Before fast-forward merge, compare `git diff --name-only main..HEAD` with primary worktree `git status --porcelain`. Merge only when there is no overlap; do not reset, checkout, or overwrite user-owned dirty files.
