# Selection Preference Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect auditable user preference feedback for discovery candidates without treating supply conditions, prices, or market observations as product-style labels.

**Architecture:** An append-only SQLite table holds `keep`, `source_pending`, and `not_fit` events keyed to existing discovery candidates. Storage derives the newest label per candidate and an evidence-only readiness state. Local JSON endpoints expose writes and reads, while the selection board renders controls only on opportunity cards; reference-memory profiles stay read-only.

**Tech Stack:** Python 3.11, SQLite, `http.server` JSON routes, vanilla JavaScript, CSS, pytest.

---

### Task 1: Persist preference events and evidence-only readiness

**Files:**
- Modify: `src/cd_monitor/storage/migrations.py:107`
- Modify: `src/cd_monitor/storage/sqlite.py:3030`
- Create: `tests/test_selection_preference_feedback.py`

- [ ] **Step 1: Write failing storage tests**

```python
from cd_monitor.storage.sqlite import (
    init_db,
    insert_selection_preference_feedback,
    list_selection_preference_feedback,
    selection_preference_feedback_status,
)


def test_latest_feedback_per_candidate_drives_style_counts(tmp_path) -> None:
    db_path, candidate_id = _seed_candidate(tmp_path)
    insert_selection_preference_feedback(db_path, candidate_id, "keep", "版型符合")
    insert_selection_preference_feedback(db_path, candidate_id, "source_pending", "产品好，暂时没货")

    items = list_selection_preference_feedback(db_path, current_only=True)
    status = selection_preference_feedback_status(db_path)

    assert len(items) == 1
    assert items[0]["outcome"] == "source_pending"
    assert status["event_count"] == 2
    assert status["labeled_candidate_count"] == 1
    assert status["current_outcomes"] == {"keep": 0, "source_pending": 1, "not_fit": 0}
    assert status["state"] == "collecting_feedback"
```

Add parameterized invalid cases for an absent candidate, unknown outcome, non-text/over-500-character note, non-positive ID and invalid limits. Add a 30-candidate case with at least 10 positive latest labels and 10 `not_fit` labels asserting `ready_for_evaluation`, never an automatic score.

- [ ] **Step 2: Run the test and observe RED**

Run: `python -m pytest -q tests/test_selection_preference_feedback.py`

Expected: collection failure because the table and functions do not exist.

- [ ] **Step 3: Add schema and minimal storage functions**

Add this table next to `review_decisions`:

```sql
CREATE TABLE IF NOT EXISTS selection_preference_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('keep', 'source_pending', 'not_fit')),
  note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_selection_preference_feedback_candidate_latest
  ON selection_preference_feedback(candidate_id, created_at DESC, id DESC);
```

Define these exact symbols in `sqlite.py`:

```python
ALLOWED_SELECTION_PREFERENCE_OUTCOMES = frozenset({"keep", "source_pending", "not_fit"})

def insert_selection_preference_feedback(
    db_path: str | Path, candidate_id: int, outcome: str, note: str | None = None,
) -> dict[str, object]: ...

def list_selection_preference_feedback(
    db_path: str | Path, *, candidate_id: int | None = None,
    limit: int = 50, current_only: bool = False,
) -> list[dict[str, object]]: ...

def selection_preference_feedback_status(db_path: str | Path) -> dict[str, object]: ...
```

Verify `candidate_id` exists in `discovery_candidates`; normalize a blank note to `None`; reject non-string or over-500 notes. Use `ROW_NUMBER() OVER (PARTITION BY candidate_id ORDER BY created_at DESC, id DESC)` for current labels. Return fixed outcome keys and `ready_for_evaluation` only for 30 distinct current candidates with 10 positives (`keep` + `source_pending`) and 10 `not_fit`; otherwise return `collecting_feedback`.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `python -m pytest -q tests/test_selection_preference_feedback.py tests/test_review_decision.py tests/test_reference_memory_core.py`

Expected: PASS. Confirm feedback does not alter market observations, opportunity decisions or reference scores.

- [ ] **Step 5: Commit**

```bash
git add src/cd_monitor/storage/migrations.py src/cd_monitor/storage/sqlite.py tests/test_selection_preference_feedback.py
git commit -m "feat: record selection preference feedback"
```

### Task 2: Expose bounded local API routes

**Files:**
- Modify: `src/cd_monitor/web_server.py:86-129`, `:536-560`, `:1635-1655`
- Create: `tests/test_selection_preference_feedback_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_selection_feedback_api_writes_and_reads_a_current_label(tmp_path) -> None:
    db_path, candidate_id = _seed_candidate(tmp_path)
    server, thread, base_url = _start_server(db_path)
    try:
        code, created = _post_json(base_url + "/api/selection-feedback", {
            "candidate_id": candidate_id,
            "outcome": "source_pending",
            "note": "产品方向保留，等待货源",
        })
        status = _get_json(base_url + "/api/selection-feedback/status")
        items = _get_json(base_url + "/api/selection-feedback?current=1&limit=20")
        assert code == 201
        assert created["outcome"] == "source_pending"
        assert status["current_outcomes"]["source_pending"] == 1
        assert items["items"] == [created]
    finally:
        server.shutdown(); server.server_close()
```

Add invalid JSON-field cases (400), absent candidate (404), invalid limit/current query spellings (400) and monkeypatched `sqlite3.Error` (stable 500 without a database path).

- [ ] **Step 2: Run the API test and observe RED**

Run: `python -m pytest -q tests/test_selection_preference_feedback_api.py`

Expected: the POST returns 404 because the route is not registered.

- [ ] **Step 3: Implement local-only endpoints**

Import Task 1 functions and add:

```text
GET  /api/selection-feedback/status
GET  /api/selection-feedback?candidate_id=<positive integer>&current=<0|1>&limit=<1..200>
POST /api/selection-feedback
```

POST accepts only `candidate_id`, `outcome` and optional `note`. Return `{"error": "invalid_selection_feedback"}` with 400, `{"error": "candidate_not_found"}` with 404, and `{"error": "selection_feedback_unavailable"}` with 500 for SQLite failures. Require ASCII-digit limit 1–200 and only `0`/`1` for `current`. GET never writes, starts a browser or requests a market page.

- [ ] **Step 4: Verify GREEN and server regressions**

Run: `python -m pytest -q tests/test_selection_preference_feedback_api.py tests/test_web_server.py tests/test_web_server_live_endpoints.py`

Expected: PASS. Responses expose only feedback fields, never prices, URLs, Cookies or automatic decisions.

- [ ] **Step 5: Commit**

```bash
git add src/cd_monitor/web_server.py tests/test_selection_preference_feedback_api.py
git commit -m "feat: expose selection preference feedback"
```

### Task 3: Add candidate-only board controls

**Files:**
- Modify: `web/index.html`
- Modify: `web/discovery-ui.js:12-21`, `:475-505`, `:575-605`
- Modify: `web/styles/kuro.css:492-510`
- Create: `tests/test_selection_board_preference_feedback.py`

- [ ] **Step 1: Write a failing UI contract test**

```python
def test_selection_feedback_controls_are_candidate_scoped_and_reference_profiles_stay_read_only() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "discovery-ui.js").read_text(encoding="utf-8")
    assert 'id="selectionFeedbackState"' in html
    assert 'apiGet("/api/selection-feedback/status").catch(() => null)' in script
    assert 'data-selection-feedback="keep"' in script
    assert 'data-selection-feedback="source_pending"' in script
    assert 'data-selection-feedback="not_fit"' in script
    assert 'data-candidate-id="' in _function_block(script, "opportunityCard")
    reference_renderer = _function_block(script, "renderReferenceMemory")
    assert "data-selection-feedback" not in reference_renderer
```

Include mutation checks that remove a nonfatal catch, add an action to the reference renderer, interpolate a raw candidate string, or add automatic decision text.

- [ ] **Step 2: Run the UI test and observe RED**

Run: `python -m pytest -q tests/test_selection_board_preference_feedback.py`

Expected: FAIL because the status region, feedback fetch and controls do not exist.

- [ ] **Step 3: Implement neutral interaction**

Add a compact `selectionFeedbackState` near the reference-memory panel. Extend `view` with `selectionFeedbackStatus` and `selectionFeedback`, and fetch these nonfatally in `refreshBoard`:

```javascript
apiGet("/api/selection-feedback/status").catch(() => null),
apiGet("/api/selection-feedback?current=1&limit=200").catch(() => null),
```

Render `collecting_feedback` versus `ready_for_evaluation` as evidence state only. In `opportunityCard`, render the three button values only for a positive numeric `item.candidate_id`. Use one delegated click handler:

```javascript
apiPost("/api/selection-feedback", {
  candidate_id: Number(button.dataset.candidateId),
  outcome: button.dataset.selectionFeedback,
})
```

Call `refreshBoard()` after a successful write. When unavailable, render `偏好反馈暂不可用`; do not change candidate filtering, profit cards, review actions or reference profiles. Scope responsive CSS under `body.kuro`.

- [ ] **Step 4: Verify GREEN and board regressions**

Run: `python -m pytest -q tests/test_selection_board_preference_feedback.py tests/test_selection_board_reference_panel.py tests/test_ui_complete_features.py`

Expected: PASS. Reference profiles still render no action, price, URL, purchase text or automatic rejection.

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/discovery-ui.js web/styles/kuro.css tests/test_selection_board_preference_feedback.py
git commit -m "feat: collect selection preference feedback on board"
```

### Task 4: Verify feature boundaries and full regression

**Files:**
- Verify: all files declared in Tasks 1–3

- [ ] **Step 1: Run the focused feature suite**

Run: `python -m pytest -q tests/test_selection_preference_feedback.py tests/test_selection_preference_feedback_api.py tests/test_selection_board_preference_feedback.py tests/test_reference_memory_core.py tests/test_reference_market_observations.py tests/test_reference_memory_interfaces.py tests/test_selection_board_reference_panel.py`

Expected: PASS. Tests use only temporary databases and make no real candidate, market, account, browser or network write.

- [ ] **Step 2: Inspect for scope drift**

Run: `git diff --check main...HEAD` and `git diff --name-only main...HEAD`

Expected: only declared feedback files and documentation. No market collector, account state, price calculation, opportunity decision or reference-label change.

- [ ] **Step 3: Run complete regression from feature source**

Run:

```powershell
$env:PYTHONPATH = "$PWD\src;$env:PYTHONPATH"
& 'F:\WAMEIJI-XIANYU\WAMEIJI-XIANYU-handoff\.venv\Scripts\python.exe' -m pytest -q
```

Expected: all tests pass with only any pre-existing skipped or expected-failure markers. Do not start a browser, login, collector worker, Docker service or live market request.

- [ ] **Step 4: Correct only feedback-specific regressions**

For any Task 4 feedback regression, first add one failing focused test, apply one minimal correction, rerun that test plus the complete suite, then commit with a `fix:` message. Do not combine unrelated cleanup.
