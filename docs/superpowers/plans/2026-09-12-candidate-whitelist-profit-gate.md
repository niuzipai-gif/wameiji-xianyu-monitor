# 可选品白名单与正利润门槛实施计划

> **Execution:** Apply this plan in the isolated worktree only. The user has
> explicitly confirmed that every imported reference candidate remains in the
> selectable pool; no task below authorizes buying, messaging, listing, or
> altering browser login state.

**Goal:** Make the product-selection board preserve every active, reference-matched
candidate as a selectable candidate, while promoting a product into the main
opportunity feed only after current exact-product evidence proves a strictly
positive net profit.

**Architecture:** Keep evidence collection and profit calculation intact. Replace
the current research-only candidate projection with a whitelist projection that
returns every active candidate plus a price-evidence readiness state. Change the
dual-market threshold from the former fixed 25% margin to `net_profit > 0`,
version the policy, and let both the live API and the Pages loader validate the
new contract without treating old snapshots as live evidence.

**Tech stack:** Python 3.11, SQLite, stdlib HTTP server, static JavaScript,
pytest, GitHub Pages static build.

---

## 1. Red tests for whitelist and positive-profit contracts

**Files:**
- Modify: `tests/test_discovery_api.py`
- Modify: `tests/test_pages_discovery_ui.py`
- Modify: `tests/test_dual_market_profit_policy.py`
- Modify: `tests/test_dual_market_api.py`
- Modify: `tests/test_dual_market_pages_loader.py`
- Modify: `tests/test_pages_snapshot.py`

**Work:**

1. Assert `/api/discovery/board` returns an all-active `selectable_candidates`
   collection even when a source detail or resale price is missing.
2. Assert each candidate contains only identity, source and readiness metadata;
   it must not leak a price or a profit figure into the no-price queue.
3. Retain `research_candidates` as a byte-equivalent compatibility alias.
4. Make the UI contract assert the new `可选品池` wording, the explicit
   `暂缺价格不降级` rule, and the absence of price fields in pool cards.
5. Assert the default strict policy uses zero as the margin threshold and that
   an exact, fully-costed comparison with a tiny positive net profit is eligible.
6. Assert the live API, Pages loader and snapshot exporter accept the v2,
   positive-profit policy; legacy v1 snapshots remain accepted only as
   historical snapshots with their declared 25% threshold.

**Run:** `pytest tests/test_discovery_api.py tests/test_pages_discovery_ui.py tests/test_dual_market_profit_policy.py tests/test_dual_market_api.py tests/test_dual_market_pages_loader.py tests/test_pages_snapshot.py -q`

## 2. Produce the whitelist projection and board summary

**Files:**
- Modify: `src/cd_monitor/storage/sqlite.py`
- Modify: `src/cd_monitor/web_server.py`

**Work:**

1. Add `list_discovery_selectable_candidates`, capped high enough to return the
   full active reference pool, not a twelve-card research sample.
2. Derive a read-only `profit_readiness` state in this order: missing source
   detail/price, stale detail or comparison, missing resale evidence, fully
   evidenced but awaiting a positive calculation, and confirmed positive profit.
   The projection must never emit a rejection state.
3. Preserve the old research-stage value as compatibility metadata where it is
   useful, but do not use it to remove or downgrade a candidate.
4. Add `selectable_candidates` and `profit_ready_candidates` to the summary,
   while keeping the older `active_candidates` meaning stable for existing
   callers.
5. Publish `selectable_candidates` from `/api/discovery/board` and make
   `research_candidates` point at the exact same projection during migration.
6. Change the opportunities query to require current source and resale evidence,
   exact-match safeguards, complete costs, and strictly positive profit; remove
   the pool's old minimum-profit/minimum-margin and decision-label exclusions.

**Run:** `pytest tests/test_discovery_api.py -q`

## 3. Apply the positive-profit policy end to end

**Files:**
- Modify: `src/cd_monitor/services/dual_market_profit_policy.py`
- Modify: `src/cd_monitor/web_server.py`
- Modify: `src/cd_monitor/pages_snapshot.py`
- Modify: `web/dual-market-data.js`

**Work:**

1. Introduce `wameiji-xianyu-net-v2` whose effective threshold is zero and
   preserve the strict `raw_profit > 0` condition in the calculation service.
2. Expose the same v2 policy from `/api/dual-market/board`; keep the existing
   `below_margin` storage/status compatibility but describe it publicly as not
   currently profitable.
3. Allow snapshot export for a finite non-negative margin threshold and require
   positive profit. Do not rewrite a v1 snapshot into a v2 snapshot; its policy
   metadata remains historical evidence.
4. Update the Pages loader to accept either a validated v1 historical snapshot
   at >=25% or the current v2 zero-margin policy, rejecting mismatched versions
   and thresholds.

**Run:** `pytest tests/test_dual_market_profit_policy.py tests/test_dual_market_api.py tests/test_dual_market_pages_loader.py tests/test_pages_snapshot.py -q`

## 4. Render the whitelist as the home-page secondary information flow

**Files:**
- Modify: `web/discovery-ui.js`
- Modify: `web/index.html`
- Modify: `web/styles.css` only if spacing/readability needs it

**Work:**

1. Replace research-card names and labels with `可选品池` and the explicit
   readiness labels: `等待进货价`, `等待闲鱼价`, `价格需复核`,
   `等待利润核算`, and `已核实正利润`.
2. Render all selectable cards after the main positive-profit feed. Their
   explanatory text must say a temporary lack of stock, low purchase price, or
   fresh evidence does not remove the item from the pool.
3. Read `selectable_candidates` first and fall back to the legacy alias so a
   rolling backend deployment cannot blank the page.
4. Replace all home-page `净利率至少 25%` wording with `净利润为正`, including
   empty states, KPI/funnel labels, and the policy hint. Keep historical
   snapshot labels clear that they are not a current purchasing claim.
5. Keep any manual `not_fit` feedback explicit as a user action; do not use it
   as an automatic exclusion rule.

**Run:** `pytest tests/test_pages_discovery_ui.py tests/test_pages_dual_market_ui.py -q`

## 5. Build, inspect, and deploy safely

**Files:**
- Generated verification target: `_site/` (not committed)

**Work:**

1. Run the focused regression suites, then `python -m compileall -q
   src/cd_monitor` and the static build.
2. Inspect the generated site for the new static strings and verify no local
   runtime data or token is copied into `_site/`.
3. Commit only the isolated-worktree changes. Compare the feature branch with
   `origin/main`, confirm a fast-forward is safe, push to `main`, then confirm
   the Pages workflow and the public Pages endpoint reflect the new revision.
4. Read the remote dual-market board with the existing authorized local access
   configuration and report the factual current count; do not manufacture a
   profitable card if the live evidence has not reached it yet.

**Run:**
`pytest tests/test_discovery_api.py tests/test_pages_discovery_ui.py tests/test_dual_market_profit_policy.py tests/test_dual_market_api.py tests/test_dual_market_pages_loader.py tests/test_pages_snapshot.py tests/test_pages_dual_market_ui.py -q`

**Done condition:** The live board retains every active reference candidate in
the selectable pool, Pages labels use positive net profit rather than a fixed
25% gate, regression tests pass, and the deployed page is verifiably updated.
