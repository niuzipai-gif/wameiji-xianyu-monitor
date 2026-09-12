# Strict Net-Profit Pages Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recalculate the saved Wameiji-to-Xianyu pairs with complete evidenced costs, publish only opportunities whose unrounded net margin is at least 25%, and make the default GitHub Pages view reliably load that persistent snapshot and its two local images.

**Architecture:** A versioned strict-profit policy turns Wameiji detail evidence plus the user's fixed CNY costs into `DualMarketCostConfig`; the comparison service remains the single calculator and exposes a complete cost breakdown. The board API keeps all states for audit counts but projects only qualified rows as cards. The Pages exporter publishes only that qualified projection atomically, and the browser is snapshot-first unless the URL explicitly includes `?live=1`.

**Tech Stack:** Python 3.11, SQLite, pytest, vanilla JavaScript, Node browserless tests, Pillow, GitHub Pages.

---

## Locked policy and safety boundary

- Trade direction is Wameiji purchase in JPY to Xianyu sale in CNY only.
- Net margin is `net_profit_cny / xianyu_sale_cny`.
- Qualification requires complete evidence, positive profit, and unrounded margin `>= 0.25`.
- Fixed costs are international shipping 15 CNY, China postage 5 CNY, packaging 2 CNY, tax 0 CNY.
- Xianyu seller fee defaults to 1.6% of sale price without an assumed 60 CNY cap.
- Wameiji exchange rate, proxy fee, and Japan domestic shipping come from the saved detail snapshot. Unknown buyer-paid domestic shipping stays unknown.
- This plan never starts a collection command, browser crawl, worker, or scheduled task. It only reads saved evidence, recalculates, exports, tests, and deploys.
- Do not stage or modify `.superpowers/`.

## File map

- Create `src/cd_monitor/services/dual_market_profit_policy.py`: parse saved Wameiji cost evidence and construct the versioned strict cost policy.
- Create `tests/test_dual_market_profit_policy.py`: lock exchange-rate/proxy/domestic-shipping evidence behavior.
- Modify `src/cd_monitor/core/dual_market.py`: add explicit uncapped-fee and minimum-margin policy fields and qualified/below-margin statuses.
- Modify `src/cd_monitor/services/dual_market_service.py`: calculate on unrounded values and return the complete cost breakdown.
- Modify `tests/test_dual_market_service.py`: cover formula, exact 25% boundary, below-margin state, unknown costs, and currency direction.
- Create `scripts/reprice_dual_market_board.py`: recalculate existing current pairs from saved HTML only.
- Modify `scripts/collect_dual_market_batch.py`: make future rebuilds use the same strict policy instead of an empty configuration.
- Create `tests/test_reprice_dual_market_board.py` and modify `tests/test_collect_dual_market_batch_script.py`: prove offline-only repricing and future policy wiring.
- Modify `src/cd_monitor/web_server.py`: expose strategy metadata, funnel counts, and full cost breakdown while returning qualified cards only.
- Modify `tests/test_dual_market_api.py`: lock the API projection contract.
- Modify `src/cd_monitor/pages_snapshot.py`: publish only qualified cards and preserve audit counts in an atomic snapshot.
- Modify `tests/test_pages_snapshot.py`: lock eligible-only assets, valid zero-state, and last-good preservation.
- Modify `web/dual-market-data.js`: make static snapshot the default and gate live API behind `?live=1`.
- Modify `web/discovery-ui.js`: render only qualified cards, a visible funnel, and the full cost breakdown.
- Modify `web/index.html` and `web/styles/kuro.css`: give the opportunity feed the primary layout while preserving the confirmed horizontal left/center/right cards.
- Modify `tests/test_dual_market_pages_loader.py`, `tests/test_remote_pages_runtime.py`, `tests/test_pages_dual_market_ui.py`, and `tests/test_pages_discovery_ui.py`: lock default/static and explicit/live behavior.
- Replace `web/data/dual-market-snapshot.json`, its manifest, and the generated `web/assets/dual-market/YYYYMMDDTHHMMSSZZZZ/*` directory with the verified eligible-only snapshot.

### Task 1: Parse saved Wameiji cost evidence into a strict policy

**Files:**
- Create: `src/cd_monitor/services/dual_market_profit_policy.py`
- Create: `tests/test_dual_market_profit_policy.py`

- [x] **Step 1: Write failing evidence-parser tests**

```python
def test_saved_detail_extracts_displayed_rate_proxy_and_seller_paid_shipping() -> None:
    evidence = parse_wameiji_cost_evidence(
        "挖煤姬汇率：1 日元 ≈ 0.0455 人民币 代购手续费 200日元 日本境内运费 卖家承担"
    )
    assert evidence.exchange_rate_cny_per_jpy == pytest.approx(0.0455)
    assert evidence.proxy_fee_jpy == pytest.approx(200)
    assert evidence.japan_domestic_shipping_jpy == 0


def test_buyer_paid_shipping_without_amount_remains_unknown() -> None:
    evidence = parse_wameiji_cost_evidence(
        "挖煤姬汇率：1 日元 ≈ 0.0455 人民币 代购手续费 200日元 日本境内运费 买家承担"
    )
    assert evidence.japan_domestic_shipping_jpy is None


def test_strict_policy_uses_user_confirmed_costs_and_uncapped_fee() -> None:
    config = strict_profit_cost_config(
        WameijiCostEvidence(0.0455, 200, 230)
    )
    assert config.international_shipping_per_item_cny == 15
    assert config.china_reship_cny == 5
    assert config.packaging_cny == 2
    assert config.tax_cny == 0
    assert config.sales_fee_rate == pytest.approx(0.016)
    assert config.sales_fee_uncapped is True
    assert config.minimum_net_margin == pytest.approx(0.25)
```

- [x] **Step 2: Run the tests and confirm they fail because the policy module is missing**

Run: `pytest tests/test_dual_market_profit_policy.py -q`

Expected: FAIL with `ModuleNotFoundError` or missing symbols.

- [x] **Step 3: Implement a fail-closed parser and versioned policy**

```python
STRICT_PROFIT_POLICY_VERSION = "wameiji-xianyu-net-v1"

@dataclass(frozen=True, slots=True)
class WameijiCostEvidence:
    exchange_rate_cny_per_jpy: float | None
    proxy_fee_jpy: float | None
    japan_domestic_shipping_jpy: float | None


def strict_profit_cost_config(evidence: WameijiCostEvidence) -> DualMarketCostConfig:
    return DualMarketCostConfig(
        exchange_rate_cny_per_jpy=evidence.exchange_rate_cny_per_jpy,
        japan_domestic_shipping_jpy=evidence.japan_domestic_shipping_jpy,
        proxy_fee_jpy=evidence.proxy_fee_jpy,
        international_shipping_per_item_cny=15.0,
        china_reship_cny=5.0,
        packaging_cny=2.0,
        after_sale_reserve_cny=0.0,
        risk_reserve_cny=0.0,
        tax_cny=0.0,
        sales_fee_rate=0.016,
        sales_fee_cap_cny=None,
        sales_fee_uncapped=True,
        minimum_net_margin=0.25,
        policy_version=STRICT_PROFIT_POLICY_VERSION,
    )
```

The parser must prefer explicit numeric labels, map only explicit seller-paid shipping to zero, and never substitute the observed 0.0455/200 values when labels are missing.

- [x] **Step 4: Run and commit**

Run: `pytest tests/test_dual_market_profit_policy.py -q`

```powershell
git add -- src/cd_monitor/services/dual_market_profit_policy.py tests/test_dual_market_profit_policy.py
git commit -m "feat: define strict dual-market profit policy"
```

### Task 2: Enforce the unrounded 25% margin and expose the cost breakdown

**Files:**
- Modify: `src/cd_monitor/core/dual_market.py`
- Modify: `src/cd_monitor/services/dual_market_service.py`
- Modify: `tests/test_dual_market_service.py`

- [x] **Step 1: Add failing calculation tests**

Add tests that assert:

```python
assert outcome.status == "eligible"
assert breakdown["xianyu_sale_cny"] == pytest.approx(200)
assert breakdown["xianyu_seller_fee_cny"] == pytest.approx(3.2)
assert breakdown["wameiji_item_cny"] == pytest.approx(45.5)
assert breakdown["wameiji_domestic_shipping_cny"] == pytest.approx(10.465)
assert breakdown["wameiji_proxy_fee_cny"] == pytest.approx(9.1)
assert breakdown["international_shipping_cny"] == 15
assert breakdown["china_postage_cny"] == 5
assert breakdown["packaging_cny"] == 2
assert breakdown["tax_cny"] == 0
```

Also construct one comparison whose exact raw margin is `0.25` and one at `0.249999`; expect `eligible` and `below_margin` respectively. Assert JPY/CNY reversal raises `ValueError`, and every unknown cost returns `cost_pending` with its missing field names.

- [x] **Step 2: Run the targeted tests and confirm the new behavior fails**

Run: `pytest tests/test_dual_market_service.py -q`

Expected: FAIL because the current statuses are `ready/negative_profit` and the service rounds profit before qualification.

- [x] **Step 3: Extend the immutable policy snapshot and calculator**

Add these fields to `DualMarketCostConfig`:

```python
sales_fee_uncapped: bool | None = None
minimum_net_margin: float | None = None
policy_version: str | None = None
```

Use raw floats for qualification, round only stored/display values, and persist `eligible`, `below_margin`, or `cost_pending`. Add a pure `comparison_cost_breakdown(...)` helper returning every formula component and `missing_fields` without refetching data.

- [x] **Step 4: Run focused domain/storage tests**

Run: `pytest tests/test_dual_market_service.py tests/test_dual_market_storage.py tests/test_dual_market_core.py -q`

Expected: PASS; update legacy assertions only where the approved state vocabulary intentionally changed.

- [x] **Step 5: Commit**

```powershell
git add -- src/cd_monitor/core/dual_market.py src/cd_monitor/services/dual_market_service.py tests/test_dual_market_service.py tests/test_dual_market_storage.py tests/test_dual_market_core.py
git commit -m "feat: enforce strict net margin qualification"
```

### Task 3: Reprice saved pairs offline and wire future batches to the same policy

**Files:**
- Create: `scripts/reprice_dual_market_board.py`
- Create: `tests/test_reprice_dual_market_board.py`
- Modify: `scripts/collect_dual_market_batch.py`
- Modify: `tests/test_collect_dual_market_batch_script.py`

- [x] **Step 1: Write failing offline repricer tests**

Build a temporary SQLite database with saved Wameiji HTML paths and paired observations. Assert the repricer:

- reads the saved HTML and current observations only;
- writes one new immutable comparison per current canonical key;
- produces `eligible`, `below_margin`, and `cost_pending` counts;
- never calls `requests`, Playwright, Chrome, or a collector entry point;
- emits JSON including `policy_version`, `evaluated_count`, and state counts.

Add a source test proving `collect_dual_market_batch.py` calls `strict_profit_cost_config(...)` rather than `DualMarketCostConfig()`.

- [x] **Step 2: Run and confirm failure**

Run: `pytest tests/test_reprice_dual_market_board.py tests/test_collect_dual_market_batch_script.py -q`

Expected: FAIL because the repricer is missing and collection currently creates an empty cost configuration.

- [x] **Step 3: Implement the offline command**

The command writes both an audit summary and the API-shaped board used by the Pages exporter. Its concrete production invocation is:

```text
python scripts/reprice_dual_market_board.py --db "F:\WAMEIJI-XIANYU\WAMEIJI-XIANYU-handoff\data\local\dual-market.db" --json-output data/local/strict-reprice-result.json --board-output data/local/strict-board.json
```

For each current paired key, load the selected Wameiji observation's `raw_snapshot_path`, parse its exchange rate/proxy/domestic-shipping evidence, and call `rebuild_current_comparison`. A missing/unreadable snapshot must create `cost_pending`; it must not fall back to constants. Patch future batch rebuilds to use the same helper after their detail snapshot is persisted.

- [x] **Step 4: Run and commit**

Run: `pytest tests/test_reprice_dual_market_board.py tests/test_collect_dual_market_batch_script.py -q`

```powershell
git add -- scripts/reprice_dual_market_board.py scripts/collect_dual_market_batch.py tests/test_reprice_dual_market_board.py tests/test_collect_dual_market_batch_script.py
git commit -m "feat: reprice saved dual-market evidence offline"
```

### Task 4: Project only qualified opportunities through the board API

**Files:**
- Modify: `src/cd_monitor/web_server.py`
- Modify: `tests/test_dual_market_api.py`

- [x] **Step 1: Write failing API projection tests**

For a database containing all three comparison states, assert:

```python
assert board["strategy"]["policy_version"] == "wameiji-xianyu-net-v1"
assert board["strategy"]["minimum_net_margin"] == 0.25
assert board["summary"] == {
    "evaluated_count": 3,
    "eligible_count": 1,
    "below_margin_count": 1,
    "cost_pending_count": 1,
    "waiting_wameiji_count": 0,
    "waiting_xianyu_count": 0,
}
assert [row["comparison_id"] for row in board["eligible"]] == [eligible_id]
assert board["below_margin"] == []
assert board["cost_pending"] == []
assert board["eligible"][0]["calculation"]["cost_breakdown"]["international_shipping_cny"] == 15
```

- [x] **Step 2: Run and confirm the old ready/negative card projection fails**

Run: `pytest tests/test_dual_market_api.py -q`

- [x] **Step 3: Implement one auditable board projection**

Retain below-margin and pending counts, but do not serialize their product/image cards. Include strategy version, formula fields, generated/captured times, and the complete cost breakdown on each qualified card. Retain compatibility aliases only if existing local consumers need them; aliases must never reintroduce rejected cards.

- [x] **Step 4: Run and commit**

Run: `pytest tests/test_dual_market_api.py tests/test_discovery_api.py -q`

```powershell
git add -- src/cd_monitor/web_server.py tests/test_dual_market_api.py tests/test_discovery_api.py
git commit -m "feat: project eligible dual-market opportunities"
```

### Task 5: Make the Pages export eligible-only and atomically zero-safe

**Files:**
- Modify: `src/cd_monitor/pages_snapshot.py`
- Modify: `tests/test_pages_snapshot.py`

- [x] **Step 1: Rewrite the fixture and add failing tests**

Use a fixture with one qualified card and nonzero below-margin/pending counts. Assert only the qualified card creates two image assets, rejected arrays are absent or empty, and summary counts are preserved from the input board.

Add two safety cases:

```python
def test_zero_eligible_board_publishes_an_honest_zero_state_without_assets(...): ...
def test_failed_image_for_nonempty_eligible_input_preserves_last_good_snapshot(...): ...
```

- [x] **Step 2: Run and confirm current exporter publishes rejected cards**

Run: `pytest tests/test_pages_snapshot.py -q`

- [x] **Step 3: Implement qualified-only atomic output**

Only iterate `eligible` (or the temporary `ready` compatibility alias) for validation and asset copying. Permit a successful zero-card snapshot only when the source board itself has zero eligible cards. If source eligible rows exist but none survive validation, raise `SnapshotExportError` before replacing JSON, manifest, or the prior asset set.

- [x] **Step 4: Run and commit**

Run: `pytest tests/test_pages_snapshot.py -q`

```powershell
git add -- src/cd_monitor/pages_snapshot.py tests/test_pages_snapshot.py
git commit -m "feat: publish eligible-only Pages snapshots"
```

### Task 6: Make Pages snapshot-first and render the approved opportunity funnel

**Files:**
- Modify: `web/dual-market-data.js`
- Modify: `web/discovery-ui.js`
- Modify: `web/index.html`
- Modify: `web/styles/kuro.css`
- Modify: `tests/test_dual_market_pages_loader.py`
- Modify: `tests/test_remote_pages_runtime.py`
- Modify: `tests/test_pages_dual_market_ui.py`

- [x] **Step 1: Add failing loader/runtime tests**

Extend the Node harness with `storage_values` and `page_search`. Lock these cases:

1. Plain Pages URL plus saved API base/token: no collector request, no prompt, static snapshot requested.
2. `?live=1` plus token: live board requested with token.
3. `?live=1` plus live failure/empty invalid structure: static snapshot fallback requested and labelled.
4. Local same-origin runtime may continue to use live data.

- [x] **Step 2: Add failing UI contract tests**

Assert the renderer reads `eligible` only, emits no below-margin/pending cards, displays the four funnel numbers, uses `CNY` on the Xianyu side, uses `JPY（约 ... CNY）` on the Wameiji side, and renders cost, profit, margin, matching evidence, snapshot time, and the pre-order recheck warning.

- [x] **Step 3: Run and confirm failures**

Run: `pytest tests/test_dual_market_pages_loader.py tests/test_remote_pages_runtime.py tests/test_pages_dual_market_ui.py -q`

- [x] **Step 4: Implement the explicit live gate**

```javascript
const liveRequested = new URLSearchParams(window.location.search).get("live") === "1";
const result = await window.DualMarketData.load({ apiGet, live: liveRequested });
```

`DualMarketData.load` must request `data/dual-market-snapshot.json` directly when `live` is false. It may call `/api/dual-market/board` only when `live` is true, and must fall back to the snapshot on an error or an invalid/empty live response. Saved `localStorage` values alone never set `live=true`.

- [x] **Step 5: Implement the qualified renderer and primary feed layout**

Keep the confirmed two-cards-per-row desktop grid, with each card horizontally ordered Xianyu / analysis / Wameiji. Remove `nonReadyComparisonCard` from `renderDualMarketFeed`; replace it with one honest zero-state. Add compact cost rows so the feed remains the largest useful section rather than expanding the hero.

- [x] **Step 6: Run and commit**

```powershell
pytest tests/test_dual_market_pages_loader.py tests/test_remote_pages_runtime.py tests/test_pages_dual_market_ui.py -q
node --check web/dual-market-data.js
node --check web/discovery-ui.js
git add -- web/dual-market-data.js web/discovery-ui.js web/index.html web/styles/kuro.css tests/test_dual_market_pages_loader.py tests/test_remote_pages_runtime.py tests/test_pages_dual_market_ui.py
git commit -m "fix: make public opportunity feed snapshot-first"
```

### Task 7: Recalculate the saved 50-pair baseline and publish its verified snapshot

**Files:**
- Replace: `web/data/dual-market-snapshot.json`
- Replace: `web/data/dual-market-snapshot.manifest.json`
- Replace: generated `web/assets/dual-market/YYYYMMDDTHHMMSSZZZZ/*`
- Modify: `tests/test_pages_discovery_ui.py`

- [x] **Step 1: Resolve the actual local database path without starting services**

Run read-only repository/config checks and print the absolute resolved database path. Verify it is inside the current project's local data area and has the expected observation/comparison tables.

- [x] **Step 2: Prove collection remains paused before repricing**

Inspect the repository's scheduled-task/status helper and process list. Record that no collection process is active and the Discovery Worker task is disabled. Do not modify the task.

- [x] **Step 3: Reprice existing evidence only**

Run:

```powershell
python scripts/reprice_dual_market_board.py --db "F:\WAMEIJI-XIANYU\WAMEIJI-XIANYU-handoff\data\local\dual-market.db" --json-output data/local/strict-reprice-result.json --board-output data/local/strict-board.json
```

Expected current baseline: evaluated 50, eligible 4, cost pending 8, below margin 38. If the output differs, stop publication, inspect the exact per-state records and formula inputs, fix the parser/calculator, and rerun from the tests. Never force counts to match the expectation.

- [x] **Step 4: Export the local board without collecting**

Use either a board JSON produced directly from the same database or the already running local API after verifying its database path. Run:

```powershell
python scripts/export_dual_market_pages_snapshot.py --board-file data/local/strict-board.json --web-dir web
```

Expected: 4 qualified cards and 8 image assets for the current baseline.

- [x] **Step 5: Independently validate data, formula, images, and secret hygiene**

Check that every public card has margin `>= 0.25`, two distinct existing image files, left CNY/right JPY, the cost breakdown totals correctly, no absolute local path/cookie/token/raw HTML is present, and manifest SHA-256 values match the files.

- [x] **Step 6: Extend the Pages build test and commit sanitized evidence**

Run: `pytest tests/test_pages_discovery_ui.py tests/test_pages_snapshot.py -q`

```powershell
git add -- web/data/dual-market-snapshot.json web/data/dual-market-snapshot.manifest.json web/assets/dual-market tests/test_pages_discovery_ui.py
git diff --cached --name-only
git diff --cached --check
git commit -m "data: publish strict net-profit opportunities"
```

The staged file list must contain no `data/local`, browser profile, cookie, token, raw HTML, or collection logs.

### Task 8: Full verification, deploy, and public readback

**Files:**
- Verify all changed files; modify only to fix attributable failures.

- [x] **Step 1: Run focused and full regression suites**

```powershell
pytest tests/test_dual_market_profit_policy.py tests/test_dual_market_service.py tests/test_reprice_dual_market_board.py tests/test_collect_dual_market_batch_script.py tests/test_dual_market_api.py tests/test_pages_snapshot.py tests/test_dual_market_pages_loader.py tests/test_remote_pages_runtime.py tests/test_pages_dual_market_ui.py tests/test_pages_discovery_ui.py -q
pytest -q
node --check web/dual-market-data.js
node --check web/discovery-ui.js
```

Expected: all tests PASS. Investigate failures; do not weaken assertions or publish with unexplained red tests.

- [x] **Step 2: Build and independently inspect the static site**

```powershell
python scripts/build_pages.py --dest _site
```

Read `_site/data/dual-market-snapshot.json`, verify the expected eligible IDs/count and all referenced files, and confirm rejected records have no public card assets.

- [x] **Step 3: Verify local UI in both modes**

Open the local site normally and with `?live=1`. The normal static build must display the saved qualified snapshot; explicit live mode may use the API. Confirm the horizontal Xianyu/analysis/Wameiji order, currencies, breakdown, funnel, images, and zero-state behavior.

- [x] **Step 4: Recheck the collection safety boundary**

Repeat the scheduled-task and process checks. Expected: Discovery Worker remains disabled and there is no collection process.

- [x] **Step 5: Review the final diff against the approved specification**

Check every locked policy and exclusion, inspect `git diff --check`, and ensure `.superpowers/` remains untracked and unstaged. Commit any attributable test/integration corrections separately.

- [x] **Step 6: Push the verified branch to GitHub Pages**

```powershell
git push origin HEAD:main
```

Wait for the Pages workflow rather than assuming deployment succeeded.

- [x] **Step 7: Verify the public artifact and UI**

Read back:

```text
https://niuzipai-gif.github.io/wameiji-xianyu-monitor/data/dual-market-snapshot.json
https://niuzipai-gif.github.io/wameiji-xianyu-monitor/
```

Use both a clean browser context and a context preloaded with an old API URL/token. Both plain URLs must show the same qualified snapshot and working local images. Verify every image response, committed `generated_at`, policy version, funnel, currencies, and the 25% threshold. Only `?live=1` may attempt the live collector.

- [x] **Step 8: Report observable completion evidence**

Report commit SHA, public URL, public snapshot generation time, evaluated/eligible/below-margin/pending counts, tested image count, test totals, Pages workflow/readback status, and a final statement that collection remained paused throughout.
