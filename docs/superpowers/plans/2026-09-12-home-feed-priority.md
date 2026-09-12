# 选品广场首页信息流优先 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the selection-board opportunity feed the visible priority on the home page while keeping supporting research evidence available after the feed.

**Architecture:** Preserve the existing static GitHub Pages frontend and its runtime API calls. Reorder only the home-page DOM and restyle the existing `.hero` into a compact selection-board summary; use native `details` to hide the evidence panel by default without changing any JavaScript data bindings.

**Tech Stack:** Static HTML, CSS, browser JavaScript, pytest static-contract tests.

---

## File structure

- `web/index.html`: defines sidebar and the home-page information hierarchy.
- `web/styles/kuro.css`: defines the compact overview and collapsed evidence presentation.
- `tests/test_selection_board_reference_panel.py`: protects the home-page hierarchy and preserves reference-panel DOM contracts.

### Task 1: Lock the information hierarchy with a failing static test

**Files:**
- Modify: `tests/test_selection_board_reference_panel.py`
- Test: `tests/test_selection_board_reference_panel.py::test_home_prioritizes_opportunity_feed_over_secondary_evidence`

- [ ] **Step 1: Write the failing test**

```python
def test_home_prioritizes_opportunity_feed_over_secondary_evidence() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert '<details class="secondary-evidence">' in html
    assert html.index('id="homeFeed"') < html.index('id="referenceMemoryTitle"')
    assert 'class="mascot-stage"' not in html
    assert "Design Direction" not in html
    assert "<b>Mascot</b>" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_selection_board_reference_panel.py::test_home_prioritizes_opportunity_feed_over_secondary_evidence`

Expected: FAIL because the current evidence panel precedes `#homeFeed`, no `secondary-evidence` details exists, and promotional cards remain.

- [ ] **Step 3: Commit the red test**

```powershell
git add tests/test_selection_board_reference_panel.py
git commit -m "test: require feed-first home hierarchy"
```

### Task 2: Put the opportunity feed first and retain research evidence

**Files:**
- Modify: `web/index.html:34-132`
- Test: `tests/test_selection_board_reference_panel.py::test_home_prioritizes_opportunity_feed_over_secondary_evidence`

- [ ] **Step 1: Replace promotional sidebar cards with the existing action and status cards**

Delete the two `side-card` elements containing `Design Direction` and `Mascot`. Keep the existing button ids `sideNewTaskBtn`, `sideImportBtn`, and `sideScanDiscoveryBtn`, plus `healthCard`.

- [ ] **Step 2: Replace the tall hero body with the compact selection-board overview**

```html
<div class="hero selection-stream-overview">
  <div class="hero-copy">
    <div class="eyebrow">Selection board · evidence-first</div>
    <h2>选品广场</h2>
    <p class="lead">先看已核验的商品机会；正样本、登录与采集状态在信息流之后按需展开。</p>
    <div class="stats">...</div>
  </div>
</div>
```

Keep the four existing KPI ids inside `.stats`: `kpiToday`, `kpiProfit`, `kpiMax`, and `kpiHitRate`.

- [ ] **Step 3: Move the existing feed header and `#homeFeed` immediately after the overview**

Keep all existing filter button ids and data attributes. The DOM order must place `#homeFeed` before the reference-memory section.

- [ ] **Step 4: Wrap the unchanged reference-memory section after the feed**

```html
<details class="secondary-evidence">
  <summary><span>研究依据与运行状态</span><span>正样本、登录与采集状态</span></summary>
  <section class="reference-memory-panel" aria-labelledby="referenceMemoryTitle">...</section>
</details>
```

Keep all existing ids inside the section, including `referenceMemoryTitle`, `referenceSampleCount`, `referenceDirectionList`, `referenceObservationList`, and `referenceProfileList`.

- [ ] **Step 5: Run the focused test to verify it passes**

Run: `python -m pytest -q tests/test_selection_board_reference_panel.py::test_home_prioritizes_opportunity_feed_over_secondary_evidence`

Expected: PASS.

- [ ] **Step 6: Commit the markup**

```powershell
git add web/index.html tests/test_selection_board_reference_panel.py
git commit -m "feat: prioritize selection feed on home"
```

### Task 3: Make the overview compact and evidence expandable

**Files:**
- Modify: `web/styles/kuro.css:333-395,493-515,659-664`
- Test: `tests/test_selection_board_reference_panel.py`

- [ ] **Step 1: Add compact overview and native-details styles**

```css
body.kuro .selection-stream-overview { min-height: 0; margin-bottom: 20px; }
body.kuro .selection-stream-overview .hero-copy { padding: 26px 30px; }
body.kuro .selection-stream-overview h2 { font-size: 32px; margin: 8px 0; }
body.kuro .selection-stream-overview .lead { font-size: 14px; line-height: 1.65; }
body.kuro .secondary-evidence { display: block; margin-top: 26px; }
body.kuro .secondary-evidence > summary { cursor: pointer; }
```

Leave `.hero .stats` available to `keepBoardKpisVisible()` and keep the existing breakpoint rules valid for `.reference-memory-panel`.

- [ ] **Step 2: Run all reference-panel contract tests**

Run: `python -m pytest -q tests/test_selection_board_reference_panel.py`

Expected: PASS.

- [ ] **Step 3: Check JavaScript syntax and changed-file whitespace**

Run:

```powershell
node --check web/discovery-ui.js
git diff --check
```

Expected: both commands exit with code 0.

- [ ] **Step 4: Commit the styles**

```powershell
git add web/styles/kuro.css
git commit -m "style: compact home selection overview"
```

### Task 4: Regression verification

**Files:**
- Test: `tests/test_selection_board_reference_panel.py`
- Test: `tests/test_ui_complete_features.py`

- [ ] **Step 1: Run the affected test suite**

Run: `python -m pytest -q tests/test_selection_board_reference_panel.py tests/test_ui_complete_features.py`

Expected: all tests pass.

- [ ] **Step 2: Review the committed diff**

Run:

```powershell
git status --short
git log --oneline main..HEAD
git diff --check main...HEAD
```

Expected: only the planned HTML, CSS, test, and design/plan documentation changes exist; whitespace check passes.
