# 选品广场待深研候选信息流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the home-page information flow useful when no fresh price-comparison opportunity exists by showing active candidates that require more research, without treating them as quoted opportunities.

**Architecture:** Add a narrow SQLite projection for active research candidates and attach it to the existing `/api/discovery/board` response. The frontend renders verified opportunity cards first and, only in the all-items filter, renders separate research cards that consume the projection without accessing any price, currency, or profit field.

**Tech Stack:** Python 3.11, SQLite, stdlib HTTP server, static HTML/CSS/JavaScript, pytest.

---

## File structure

- `src/cd_monitor/storage/sqlite.py`: projects active candidates into evidence-only research-queue records and assigns evidence-freshness stages.
- `src/cd_monitor/web_server.py`: makes candidate source URLs usable from GitHub Pages and includes the queue in the board response.
- `web/discovery-ui.js`: renders separate, non-price research cards after opportunity cards.
- `web/styles/kuro.css`: distinguishes the research section and candidate cards without changing opportunity-card presentation.
- `tests/test_discovery_api.py`: proves the API exposes an evidence-only research queue.
- `tests/test_pages_discovery_ui.py`: prevents the Pages UI from reintroducing price language or losing the queue contract.

### Task 1: Lock the evidence-only board contract with a failing API test

**Files:**
- Modify: `tests/test_discovery_api.py`
- Test: `tests/test_discovery_api.py::test_selection_board_exposes_active_research_candidates_without_price_fields`

- [ ] **Step 1: Write the failing test**

```python
def test_selection_board_exposes_active_research_candidates_without_price_fields(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    db_path = tmp_path / "selection.db"
    init_db(db_path)
    pool_id = list_discovery_pools(db_path)[0].id
    assert pool_id is not None
    upsert_discovery_candidates(
        db_path,
        [
            DiscoveryCandidate(
                pool_id=pool_id,
                media_type="cd",
                identity_key="source:research-card",
                title="研究队列样本 CD 初回限定盤",
                source_url="/mall/market/detail/research-card",
                source_image_url="https://images.example.invalid/research-card.webp",
                source_price=1200,
                source_currency="JPY",
                availability="available",
                status="active",
            )
        ],
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    ...
    code, board = _request(f"{base_url}/api/discovery/board?access_token=viewer-secret")
    candidate = board["research_candidates"][0]
    assert candidate["candidate_title"] == "研究队列样本 CD 初回限定盤"
    assert candidate["research_stage"] == "source_detail_needed"
    assert candidate["source_url"] == "https://meruki.cn/mall/market/detail/research-card"
    assert not {"source_price", "source_currency", "expected_profit", "net_margin"} & set(candidate)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest -q tests/test_discovery_api.py::test_selection_board_exposes_active_research_candidates_without_price_fields`

Expected: FAIL because `research_candidates` is absent from the board payload.

- [ ] **Step 3: Commit the red test**

```powershell
git add tests/test_discovery_api.py
git commit -m "test: require evidence-only research queue"
```

### Task 2: Return active research candidates from the board API

**Files:**
- Modify: `src/cd_monitor/storage/sqlite.py:2520-2664`
- Modify: `src/cd_monitor/web_server.py:97-134,666-674,2760-2769`
- Test: `tests/test_discovery_api.py::test_selection_board_exposes_active_research_candidates_without_price_fields`

- [ ] **Step 1: Add the SQLite projection**

```python
def list_discovery_research_candidates(
    db_path: str | Path, limit: int = 12
) -> list[dict[str, object]]:
    """Return active candidates needing research without price evidence."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id AS candidate_id, title AS candidate_title, media_type,
                   catalog_no, jan, source_url, source_image_url AS image_url,
                   detail_verified,
                   CASE
                     WHEN detail_verified = 0 THEN 'source_detail_needed'
                     WHEN detail_verified_at IS NULL
                       OR datetime(detail_verified_at) < datetime(CURRENT_TIMESTAMP, ?)
                       THEN 'source_detail_refresh_needed'
                     WHEN last_xianyu_checked_at IS NULL
                       OR datetime(last_xianyu_checked_at) < datetime(CURRENT_TIMESTAMP, ?)
                       THEN 'resale_evidence_needed'
                     ELSE 'comparison_follow_up'
                   END AS research_stage
            FROM discovery_candidates
            WHERE status = 'active'
            ORDER BY CASE WHEN detail_verified = 0 THEN 0 ELSE 1 END,
                     datetime(first_seen_at) ASC, id ASC
            LIMIT ?
            """,
            (
                f"-{_DISCOVERY_SOURCE_DETAIL_FRESHNESS_MINUTES} minutes",
                f"-{_DISCOVERY_OPPORTUNITY_FRESHNESS_MINUTES} minutes",
                max(1, min(int(limit), 100)),
            ),
        ).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 2: Attach absolute source links to the projection**

```python
def _discovery_research_candidate_views(db_path: str | Path) -> list[dict[str, object]]:
    views = list_discovery_research_candidates(db_path, limit=12)
    for view in views:
        canonical_url = _canonical_wameiji_url("wameiji", str(view.get("source_url") or ""))
        if canonical_url:
            view["source_url"] = canonical_url
    return views
```

Import `list_discovery_research_candidates` next to `list_discovery_opportunities`, and append this exact field in the board response:

```python
"research_candidates": _discovery_research_candidate_views(db_path),
```

- [ ] **Step 3: Run the focused API test to verify it passes**

Run: `python -m pytest -q tests/test_discovery_api.py::test_selection_board_exposes_active_research_candidates_without_price_fields`

Expected: PASS; the record has `research_stage` and no price, currency, or profit key.

- [ ] **Step 4: Commit the API implementation**

```powershell
git add src/cd_monitor/storage/sqlite.py src/cd_monitor/web_server.py tests/test_discovery_api.py
git commit -m "feat: expose research candidates on selection board"
```

### Task 3: Render the research queue after verified opportunities

**Files:**
- Modify: `tests/test_pages_discovery_ui.py`
- Modify: `web/discovery-ui.js:435-586`
- Modify: `web/styles/kuro.css`
- Test: `tests/test_pages_discovery_ui.py::test_pages_build_includes_the_automatic_selection_board`

- [ ] **Step 1: Add static UI assertions before implementation**

```python
assert "research_candidates" in script
assert "待深研候选 · 不含报价" in script
assert "researchCandidateCard" in script
assert "researchStageLabel" in script
assert "candidate.source_price" not in script
assert "candidate.source_currency" not in script
assert "candidate.expected_profit" not in script
```

- [ ] **Step 2: Run the Pages test to verify it fails**

Run: `python -m pytest -q tests/test_pages_discovery_ui.py::test_pages_build_includes_the_automatic_selection_board`

Expected: FAIL because the research renderer does not exist.

- [ ] **Step 3: Implement non-price research cards**

Add `researchStageLabel(stage)` with the four design-contract labels. Add `researchCandidateCard(candidate)` that reads only `candidate_id`, `candidate_title`, `media_type`, `catalog_no`, `jan`, `source_url`, `image_url`, and `research_stage`; it may reuse `candidateDirectionMarkup` and the existing feedback-button markup.

Use this `renderFeed` result structure after the current opportunity sort:

```javascript
const researchCandidates = view.filter === "all"
  ? (Array.isArray(view.board.research_candidates) ? view.board.research_candidates : [])
  : [];
if (!items.length && !researchCandidates.length) {
  target.innerHTML = '<div class="empty-state">' + esc(emptyFeedMessage(summary, allItems.length)) + '</div>';
  return;
}
target.innerHTML = [
  items.map(opportunityCard).join(""),
  researchCandidates.length
    ? '<section class="research-queue"><div class="research-queue-head"><h3>待深研候选 · 不含报价</h3><p>它们需要补齐详情或市场证据，不代表淘汰。</p></div>'
      + researchCandidates.map(researchCandidateCard).join("") + '</section>'
    : "",
].join("");
```

Do not read price, currency, availability, decision, profit, margin, or a stock judgement from a research candidate.

- [ ] **Step 4: Add narrow styles**

```css
body.kuro .research-queue { display: grid; gap: 14px; margin-top: 26px; }
body.kuro .research-queue-head { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; }
body.kuro .research-candidate-card { grid-template-columns: minmax(0, 1fr) minmax(260px, 0.8fr); }
```

Style only the header, source link, and research-stage badge. Keep the existing verified `op-card` styles unchanged.

- [ ] **Step 5: Run the Pages test to verify it passes**

Run: `python -m pytest -q tests/test_pages_discovery_ui.py::test_pages_build_includes_the_automatic_selection_board`

Expected: PASS.

- [ ] **Step 6: Commit the frontend**

```powershell
git add web/discovery-ui.js web/styles/kuro.css tests/test_pages_discovery_ui.py
git commit -m "feat: show research queue after opportunity feed"
```

### Task 4: Verify the integrated board contract

**Files:**
- Test: `tests/test_discovery_api.py`
- Test: `tests/test_pages_discovery_ui.py`
- Test: `tests/test_selection_board_reference_panel.py`

- [ ] **Step 1: Run targeted tests and syntax checks**

Run:

```powershell
python -m pytest -q tests/test_discovery_api.py tests/test_pages_discovery_ui.py tests/test_selection_board_reference_panel.py
node --check web/discovery-ui.js
git diff --check main...HEAD
```

Expected: all tests pass, JavaScript syntax exits 0, and no whitespace errors appear.

- [ ] **Step 2: Build a static Pages artifact and inspect its contract**

Run:

```powershell
python scripts/build_pages.py --api-base https://collector.example --dest $env:TEMP\wameiji-research-queue-pages
```

Read the generated `index.html`, `discovery-ui.js`, and `runtime-config.js`. Confirm `discovery-ui.js` contains `待深研候选 · 不含报价`, while the runtime config contains the passed API base.

- [ ] **Step 3: Review the final diff**

Run:

```powershell
git status --short
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected: only this design/plan, API projection, UI/CSS, and targeted tests have changed.
