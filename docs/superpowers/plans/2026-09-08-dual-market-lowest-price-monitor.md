# Dual-Market Lowest-Price Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the asymmetric Wameiji-candidate/Xianyu-price-pool flow with a verifiable dual-market stream that selects the lowest eligible listing on each side and calculates conservative resale profit.

**Architecture:** Treat a rendered, user-authorized page as the only source of product facts. Each site produces immutable `ListingObservation` records with HTML/screenshot provenance; a product-key resolver groups only demonstrated same-product listings; a comparison snapshot references exactly one eligible Wameiji purchase observation and one eligible Xianyu resale observation. The existing browser worker remains disabled throughout implementation and all collection tests use static fixtures.

**Tech Stack:** Python 3.11 dataclasses, SQLite migrations, existing Playwright capture boundary, stdlib HTTP server, static GitHub Pages JavaScript, pytest.

---

## Data-acquisition contract to preserve in every task

1. Use rendered, visible search cards and detail pages from a user-authorized normal browser session; do not call undocumented search APIs or replay cookies through direct HTTP.
2. Persist raw HTML path, screenshot path, page URL, capture timestamp, parser version, and source before extracting a record; network-response logs may assist parser diagnosis but never supply a price, title, image, or ID.
3. Wameiji search cards are discovery-only. A Wameiji price is eligible for profit only after its own detail page has verified title, JPY price, availability, and product image.
4. Xianyu search cards may form an explicitly labelled `search_card` asking-price observation; a later Xianyu detail capture upgrades it to `detail_verified`. Neither kind may be attached to a different product group.
5. Capture must stop without another browser navigation when a login page, captcha/security page, parser mismatch, missing primary image, conflicting product key, or pause state is detected.

### Task 1: Add source-neutral observation and comparison domain types

**Files:**
- Create: `src/cd_monitor/core/dual_market.py`
- Modify: `src/cd_monitor/core/models.py`
- Test: `tests/test_dual_market_core.py`

- [ ] **Step 1: Write the failing lowest-eligible-selection test**

```python
from cd_monitor.core.dual_market import ListingObservation, select_lowest_eligible


def test_select_lowest_eligible_ignores_an_incomplete_cheaper_listing() -> None:
    observations = [
        ListingObservation(
            source="wameiji", source_listing_id="box-only", canonical_product_key="jan:4547366123456",
            title="Album 外箱のみ", price=100, currency="JPY", url="https://meruki.cn/detail/box",
            image_url="https://img.example/box.jpg", availability="available", condition_group="complete_used",
            completeness="incomplete", evidence_level="detail_verified", captured_at="2026-09-08T00:00:00Z",
        ),
        ListingObservation(
            source="wameiji", source_listing_id="complete", canonical_product_key="jan:4547366123456",
            title="Album 初回限定盤 CD+DVD", price=1280, currency="JPY", url="https://meruki.cn/detail/complete",
            image_url="https://img.example/complete.jpg", availability="available", condition_group="complete_used",
            completeness="complete", evidence_level="detail_verified", captured_at="2026-09-08T00:00:00Z",
        ),
    ]

    selected = select_lowest_eligible(observations, source="wameiji")

    assert selected is not None
    assert selected.source_listing_id == "complete"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_dual_market_core.py::test_select_lowest_eligible_ignores_an_incomplete_cheaper_listing -q`  
Expected: FAIL because `cd_monitor.core.dual_market` does not exist.

- [ ] **Step 3: Add the smallest source-neutral types and selector**

```python
@dataclass(frozen=True, slots=True)
class ListingObservation:
    source: Literal["wameiji", "xianyu"]
    source_listing_id: str
    canonical_product_key: str | None
    title: str
    price: float
    currency: str
    url: str
    image_url: str
    availability: str
    condition_group: str
    completeness: str
    evidence_level: Literal["search_card", "detail_verified"]
    captured_at: str
    raw_snapshot_path: str | None = None
    screenshot_path: str | None = None
    source_detail_fee: float | None = None


def is_eligible(observation: ListingObservation, source: str) -> bool:
    return (
        observation.source == source
        and observation.availability == "available"
        and observation.completeness == "complete"
        and bool(observation.url and observation.image_url and observation.title)
        and observation.price > 0
        and (source != "wameiji" or observation.evidence_level == "detail_verified")
    )


def select_lowest_eligible(
    observations: Iterable[ListingObservation], *, source: Literal["wameiji", "xianyu"]
) -> ListingObservation | None:
    candidates = [item for item in observations if is_eligible(item, source)]
    return min(candidates, key=lambda item: (item.price, item.source_listing_id)) if candidates else None
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `pytest tests/test_dual_market_core.py::test_select_lowest_eligible_ignores_an_incomplete_cheaper_listing -q`  
Expected: PASS.

- [ ] **Step 5: Add separate failing tests for sold, missing-image, and Wameiji-search-card rejection**

```python
@pytest.mark.parametrize("field,value", [
    ("availability", "sold_out"),
    ("image_url", ""),
    ("evidence_level", "search_card"),
])
def test_wameiji_ineligible_records_never_win_lowest_price(field: str, value: str) -> None:
    ...
```

- [ ] **Step 6: Implement only the predicate changes required by the new tests, then run the file**

Run: `pytest tests/test_dual_market_core.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cd_monitor/core/dual_market.py src/cd_monitor/core/models.py tests/test_dual_market_core.py
git commit -m "feat: define dual-market listing observations"
```

### Task 2: Normalize both existing parser outputs without cross-source assumptions

**Files:**
- Modify: `src/cd_monitor/core/dual_market.py`
- Modify: `src/cd_monitor/sources/wameiji_browser.py`
- Modify: `src/cd_monitor/sources/xianyu_browser.py`
- Test: `tests/test_dual_market_normalization.py`

- [ ] **Step 1: Write the failing Wameiji normalizer test**

```python
def test_wameiji_detail_normalizes_to_a_detail_verified_observation() -> None:
    item = MarketItem(
        source="wameiji", title="Album SRCL-3520 初回限定盤", price=1280,
        currency="JPY", external_item_id="m-1", catalog_no="SRCL-3520",
        url="https://meruki.cn/mall/mercari/detail/m-1", image_url="https://img.example/a.jpg",
        availability="available", condition_text="二手", detail_verified=True,
    )

    observation = observation_from_wameiji(item, captured_at="2026-09-08T00:00:00Z")

    assert observation.canonical_product_key == "catalog:srcl3520"
    assert observation.evidence_level == "detail_verified"
    assert observation.source_listing_id == "m-1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_dual_market_normalization.py::test_wameiji_detail_normalizes_to_a_detail_verified_observation -q`  
Expected: FAIL because `observation_from_wameiji` is absent.

- [ ] **Step 3: Implement source-specific normalizers in `dual_market.py`**

```python
def observation_from_wameiji(item: MarketItem, *, captured_at: str, snapshot_path: str | None = None,
                              screenshot_path: str | None = None) -> ListingObservation:
    return ListingObservation(
        source="wameiji",
        source_listing_id=_require_source_id(item.external_item_id, item.url),
        canonical_product_key=canonical_product_key(item.catalog_no, item.jan, item.title, item.detail_verified),
        title=item.title,
        price=item.price,
        currency=item.currency,
        url=_require_http_url(item.url),
        image_url=_require_product_image(item.image_url),
        availability=item.availability,
        condition_group=normalize_condition(item.condition_text),
        completeness=classify_completeness(item.title, item.raw_text),
        evidence_level="detail_verified" if item.detail_verified else "search_card",
        captured_at=captured_at,
        raw_snapshot_path=snapshot_path,
        screenshot_path=screenshot_path,
        source_detail_fee=item.japan_domestic_shipping_jpy,
    )
```

Implement `observation_from_xianyu` with the same URL/image requirements, a URL-derived source ID only for Xianyu-local dedupe, `currency="CNY"`, and `evidence_level="search_card"`.

- [ ] **Step 4: Add and verify a failing title-only Xianyu test**

```python
def test_xianyu_title_only_sample_stays_unpaired_without_structured_identity() -> None:
    sample = XianyuPriceSample(catalog_no="album title", title="Album title", price_cny=300,
                               url="https://goofish.com/item/2", image_url="https://img.example/x.jpg")
    observation = observation_from_xianyu(sample, captured_at="2026-09-08T00:00:00Z")
    assert observation.canonical_product_key is None
```

Run: `pytest tests/test_dual_market_normalization.py -q`  
Expected before implementation: FAIL; after implementation: PASS.

- [ ] **Step 5: Add parser-output fixtures for actual card contracts**

Use existing `data/mock/` conventions. Add one Wameiji detail fixture with a primary image and one Xianyu card fixture with image/title/price/link. Tests must assert that merchant logos, placeholders, and image-less cards cannot normalize.

- [ ] **Step 6: Run focused parser and normalization suites**

Run: `pytest tests/test_wameiji_browser_runner.py tests/test_discovery_browser_queries.py tests/test_dual_market_normalization.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cd_monitor/core/dual_market.py src/cd_monitor/sources/wameiji_browser.py src/cd_monitor/sources/xianyu_browser.py data/mock tests/test_dual_market_normalization.py
git commit -m "feat: normalize two-sided listing evidence"
```

### Task 3: Persist capture provenance and immutable dual-market observations

**Files:**
- Modify: `src/cd_monitor/storage/sqlite.py`
- Modify: `src/cd_monitor/core/dual_market.py`
- Test: `tests/test_dual_market_storage.py`

- [ ] **Step 1: Write the failing round-trip test**

```python
def test_observation_round_trip_keeps_source_image_and_capture_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.db"
    initialize_database(db_path)
    observation_id = insert_listing_observation(db_path, WAMEIJI_DETAIL_OBSERVATION)

    restored = get_listing_observation(db_path, observation_id)

    assert restored.source == "wameiji"
    assert restored.image_url == "https://img.example/complete.jpg"
    assert restored.raw_snapshot_path == "snapshots/wameiji/one.html"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dual_market_storage.py::test_observation_round_trip_keeps_source_image_and_capture_evidence -q`  
Expected: FAIL because the table and repository functions do not exist.

- [ ] **Step 3: Add idempotent tables and repository functions**

Add these tables in the existing SQLite initialization/migration path; do not modify or delete legacy rows:

```sql
CREATE TABLE IF NOT EXISTS listing_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL CHECK(source IN ('wameiji', 'xianyu')),
  source_listing_id TEXT NOT NULL,
  canonical_product_key TEXT,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  currency TEXT NOT NULL,
  url TEXT NOT NULL,
  image_url TEXT NOT NULL,
  availability TEXT NOT NULL,
  condition_group TEXT NOT NULL,
  completeness TEXT NOT NULL,
  evidence_level TEXT NOT NULL CHECK(evidence_level IN ('search_card', 'detail_verified')),
  raw_snapshot_path TEXT,
  screenshot_path TEXT,
  source_detail_fee REAL,
  captured_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, source_listing_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_listing_observations_product
  ON listing_observations(canonical_product_key, source, captured_at DESC);
```

Implement `insert_listing_observation`, `get_listing_observation`, and `list_current_observations`. `list_current_observations` must return the most recent observation per `(source, source_listing_id)` without overwriting history.

- [ ] **Step 4: Run round-trip test and add an idempotent-migration test**

```python
def test_initialize_database_preserves_legacy_discovery_rows_when_adding_observations(tmp_path: Path) -> None:
    ...
```

Run: `pytest tests/test_dual_market_storage.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cd_monitor/storage/sqlite.py src/cd_monitor/core/dual_market.py tests/test_dual_market_storage.py
git commit -m "feat: persist dual-market capture evidence"
```

### Task 4: Create product groups and comparison snapshots from exact two-sided records

**Files:**
- Modify: `src/cd_monitor/core/dual_market.py`
- Modify: `src/cd_monitor/storage/sqlite.py`
- Create: `src/cd_monitor/services/dual_market_service.py`
- Test: `tests/test_dual_market_service.py`

- [ ] **Step 1: Write the failing exact-reference test**

```python
def test_build_comparison_references_each_side_lowest_eligible_observation(tmp_path: Path) -> None:
    wameiji_id = insert_listing_observation(tmp_path / "db.sqlite", WAMEIJI_1280)
    xianyu_id = insert_listing_observation(tmp_path / "db.sqlite", XIANYU_298)

    comparison = rebuild_current_comparison(tmp_path / "db.sqlite", "catalog:srcl3520", COST_CONFIG)

    assert comparison.wameiji_observation_id == wameiji_id
    assert comparison.xianyu_observation_id == xianyu_id
    assert comparison.expected_profit > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dual_market_service.py::test_build_comparison_references_each_side_lowest_eligible_observation -q`  
Expected: FAIL because `rebuild_current_comparison` is absent.

- [ ] **Step 3: Add comparison snapshot storage and service**

```sql
CREATE TABLE IF NOT EXISTS price_comparisons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_product_key TEXT NOT NULL,
  wameiji_observation_id INTEGER NOT NULL REFERENCES listing_observations(id),
  xianyu_observation_id INTEGER NOT NULL REFERENCES listing_observations(id),
  cost_config_json TEXT NOT NULL,
  landed_cost_cny REAL NOT NULL,
  sale_price_cny REAL NOT NULL,
  expected_profit_cny REAL NOT NULL,
  net_margin REAL NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready', 'negative_profit', 'cost_pending')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_price_comparisons_product_time
  ON price_comparisons(canonical_product_key, created_at DESC);
```

`rebuild_current_comparison` must select each side independently with `select_lowest_eligible`, refuse different/empty product keys, calculate costs with the existing cost model, and create `cost_pending` rather than invent missing inputs.

- [ ] **Step 4: Add edge-case tests and verify**

```python
def test_build_comparison_returns_waiting_when_only_one_source_has_an_eligible_listing() -> None: ...
def test_lower_xianyu_price_creates_negative_profit_history_not_buy_recommendation() -> None: ...
def test_comparison_never_uses_a_sample_from_another_product_key() -> None: ...
```

Run: `pytest tests/test_dual_market_service.py tests/test_dual_market_storage.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cd_monitor/services/dual_market_service.py src/cd_monitor/core/dual_market.py src/cd_monitor/storage/sqlite.py tests/test_dual_market_service.py
git commit -m "feat: compare two-sided lowest eligible prices"
```

### Task 5: Turn browser capture into a provenance-first, source-independent intake boundary

**Files:**
- Modify: `src/cd_monitor/services/live_browser_capture.py`
- Modify: `src/cd_monitor/services/discovery_worker.py`
- Create: `src/cd_monitor/services/dual_market_capture.py`
- Test: `tests/test_dual_market_capture.py`

- [ ] **Step 1: Write the failing capture-manifest test using a fake Playwright page**

```python
async def test_search_capture_returns_snapshot_paths_before_normalizing_cards(tmp_path: Path, fake_playwright) -> None:
    result = await capture_observations(
        source="xianyu", query="SRCL-3520", output_root=tmp_path,
        config=PAUSED_TEST_CONFIG, playwright_factory=fake_playwright,
    )

    assert result.status == "captured"
    assert result.snapshot_path.endswith(".html")
    assert result.screenshot_path.endswith(".png")
    assert result.observations[0].raw_snapshot_path == result.snapshot_path
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dual_market_capture.py::test_search_capture_returns_snapshot_paths_before_normalizing_cards -q`  
Expected: FAIL because `capture_observations` does not exist.

- [ ] **Step 3: Implement `capture_observations` as a thin wrapper**

It must call the existing `capture_search_html`, parse only the saved HTML through the source adapter, normalize to observations, and attach the returned snapshot/screenshot paths. It must not parse `network_log_path`, issue direct HTTP requests, or call a detail URL.

```python
async def capture_observations(...):
    summary = await capture_search_html(...)
    if summary["status"] != "ok":
        return CaptureResult.human_required(summary)
    html = Path(summary["snapshot_path"]).read_text(encoding="utf-8")
    status = adapter.parse_search_html(html, WatchItem(catalog_no=query))
    return CaptureResult.from_adapter(status, snapshot_path=..., screenshot_path=...)
```

- [ ] **Step 4: Add a failing pause test, then implement the guard**

```python
async def test_paused_capture_does_not_construct_or_call_a_browser_fetcher() -> None:
    called = False
    async def forbidden_capture(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not run")

    result = await capture_observations(..., capture_enabled=False, capture_fn=forbidden_capture)

    assert result.status == "paused"
    assert called is False
```

Run: `pytest tests/test_dual_market_capture.py -q`  
Expected: PASS.

- [ ] **Step 5: Route actual capture through an explicit `capture_enabled` flag defaulting to false**

The normal `DiscoveryWorker` must not call the new intake while disabled. Preserve current scheduled-task disablement and do not change `scripts/start-discovery.ps1` in this task.

- [ ] **Step 6: Commit**

```bash
git add src/cd_monitor/services/live_browser_capture.py src/cd_monitor/services/discovery_worker.py src/cd_monitor/services/dual_market_capture.py tests/test_dual_market_capture.py
git commit -m "feat: capture dual-market evidence with pause guard"
```

### Task 6: Add bounded Wameiji detail and Xianyu detail evidence upgrades

**Files:**
- Modify: `src/cd_monitor/sources/xianyu_browser.py`
- Modify: `src/cd_monitor/services/live_browser_capture.py`
- Modify: `src/cd_monitor/services/dual_market_capture.py`
- Test: `tests/test_xianyu_detail_parser.py`
- Test: `tests/test_dual_market_capture.py`

- [ ] **Step 1: Write the failing Xianyu-detail parser test**

```python
def test_xianyu_detail_parser_upgrades_an_existing_search_card_without_changing_its_listing_id() -> None:
    search = XianyuPriceSample(
        catalog_no="SRCL-3520", title="Artist Album SRCL-3520 初回限定盤", price_cny=298,
        url="https://www.goofish.com/item?id=x-1", image_url="https://img.example/x-1.jpg",
    )
    status = XianyuBrowserAdapter(enabled=True).parse_detail_html(XIANYU_DETAIL_HTML, search)

    assert status.status == "ok"
    assert status.items[0].url == search.url
    assert status.items[0].price_cny == 298
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_xianyu_detail_parser.py::test_xianyu_detail_parser_upgrades_an_existing_search_card_without_changing_its_listing_id -q`  
Expected: FAIL because `parse_detail_html` does not exist.

- [ ] **Step 3: Implement only safe detail upgrading**

`parse_detail_html` must accept an already-known card, reject security/login documents, take title/condition/image only from the primary detail component, preserve the original item URL/ID, and return `human_required` if the page cannot establish a same-product detail. It must not follow recommended-item links or purchase controls.

- [ ] **Step 4: Add Wameiji detail-capture manifest test**

```python
async def test_wameiji_detail_capture_records_same_url_and_detail_evidence(tmp_path: Path, fake_playwright) -> None:
    result = await capture_wameiji_detail_observation(WAMEIJI_SEARCH_OBSERVATION, ...)
    assert result.observation.evidence_level == "detail_verified"
    assert result.observation.url == WAMEIJI_SEARCH_OBSERVATION.url
```

- [ ] **Step 5: Verify all detail capture tests**

Run: `pytest tests/test_wameiji_browser_runner.py tests/test_xianyu_detail_parser.py tests/test_dual_market_capture.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cd_monitor/sources/xianyu_browser.py src/cd_monitor/services/live_browser_capture.py src/cd_monitor/services/dual_market_capture.py tests/test_xianyu_detail_parser.py tests/test_dual_market_capture.py
git commit -m "feat: verify lowest-price detail evidence"
```

### Task 7: Build a source-independent, bounded collection coordinator

**Files:**
- Create: `src/cd_monitor/services/dual_market_worker.py`
- Modify: `src/cd_monitor/services/discovery_worker.py`
- Test: `tests/test_dual_market_worker.py`

- [ ] **Step 1: Write the failing no-cross-navigation test**

```python
async def test_worker_collects_each_source_once_then_stops_before_detail_when_pair_is_missing() -> None:
    calls: list[tuple[str, str]] = []
    worker = DualMarketWorker(
        capture_search=lambda source, query: record_call(calls, source, query),
        capture_detail=lambda observation: fail_if_called(),
        capture_enabled=True,
    )

    result = await worker.run_product_key("catalog:srcl3520")

    assert calls == [("wameiji", "SRCL-3520"), ("xianyu", "SRCL-3520")]
    assert result.status == "waiting_wameiji_detail"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dual_market_worker.py::test_worker_collects_each_source_once_then_stops_before_detail_when_pair_is_missing -q`  
Expected: FAIL because `DualMarketWorker` does not exist.

- [ ] **Step 3: Implement a single-key, bounded coordinator**

The worker sequence is exactly:

```text
capture Wameiji search once
→ persist observations
→ capture Xianyu search once for the same verified key
→ persist observations
→ select lowest eligible on each side
→ if Wameiji candidate is a search card, capture one Wameiji detail
→ if identity/availability is unclear, stop as waiting/human_required
→ create comparison snapshot only when both sides are eligible
```

It must not use a global Xianyu sample cache to serve another product key. Hard limits are one product key, one search page per source, one Wameiji detail, and at most one Xianyu detail per run.

- [ ] **Step 4: Add stop-condition tests**

```python
async def test_worker_stops_after_security_status_without_calling_the_other_source() -> None: ...
async def test_worker_stops_when_product_keys_conflict() -> None: ...
async def test_worker_returns_negative_profit_comparison_without_buy_recommendation() -> None: ...
```

- [ ] **Step 5: Run worker and service suites**

Run: `pytest tests/test_dual_market_worker.py tests/test_dual_market_service.py tests/test_dual_market_capture.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cd_monitor/services/dual_market_worker.py src/cd_monitor/services/discovery_worker.py tests/test_dual_market_worker.py
git commit -m "feat: coordinate bounded two-sided price capture"
```

### Task 8: Expose separate waiting streams and exact comparison records through the API

**Files:**
- Modify: `src/cd_monitor/storage/sqlite.py`
- Modify: `src/cd_monitor/web_server.py`
- Test: `tests/test_dual_market_api.py`

- [ ] **Step 1: Write the failing board payload test**

```python
def test_dual_market_board_returns_waiting_and_ready_streams(client, populated_db) -> None:
    payload = client.get("/api/dual-market/board").json()

    assert payload["waiting_wameiji"][0]["source"] == "wameiji"
    assert payload["ready"][0]["xianyu"]["listing_id"] == "x-1"
    assert payload["ready"][0]["wameiji"]["listing_id"] == "m-1"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dual_market_api.py::test_dual_market_board_returns_waiting_and_ready_streams -q`  
Expected: FAIL with a 404 or missing route.

- [ ] **Step 3: Implement a read-only board endpoint**

Return only records from `listing_observations` and `price_comparisons`. Each ready item must nest explicit `xianyu`, `wameiji`, and `calculation` objects; no `xianyu_reference_price` fallback and no legacy `opportunities` data may populate this route.

- [ ] **Step 4: Add API tests for missing image, no counterpart, negative-profit, and paused collector state**

Run: `pytest tests/test_dual_market_api.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cd_monitor/storage/sqlite.py src/cd_monitor/web_server.py tests/test_dual_market_api.py
git commit -m "feat: expose dual-market comparison board"
```

### Task 9: Render the real three-state information flow without legacy sample fallbacks

**Files:**
- Modify: `web/discovery-ui.js`
- Modify: `web/index.html`
- Modify: `web/styles/kuro.css`
- Test: `tests/test_pages_dual_market_ui.py`

- [ ] **Step 1: Write the failing static UI contract test**

```python
def test_dual_market_ui_renders_source_specific_images_and_waiting_state() -> None:
    javascript = Path("web/discovery-ui.js").read_text(encoding="utf-8")
    assert "item.xianyu.image_url" in javascript
    assert "item.wameiji.image_url" in javascript
    assert "waiting_wameiji" in javascript
    assert "xianyu_reference_price" not in extract_dual_market_card_renderer(javascript)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_pages_dual_market_ui.py::test_dual_market_ui_renders_source_specific_images_and_waiting_state -q`  
Expected: FAIL because the dual-market renderer does not exist.

- [ ] **Step 3: Implement exactly three render paths**

```javascript
function readyComparisonCard(item) {
  return renderSide("xianyu", item.xianyu)
    + renderCalculation(item.calculation)
    + renderSide("wameiji", item.wameiji);
}

function waitingObservationCard(item, missingSource) {
  return renderSingleSource(item, missingSource);
}
```

The ready card must show the Xianyu left image/link/price and Wameiji right image/link/JPY price directly from the nested payload. The middle must label the sale price as the current lowest eligible asking price and disclose evidence level. A waiting card must not display a profit number.

- [ ] **Step 4: Add a DOM-free rendering test fixture for all three states**

Run: `pytest tests/test_pages_dual_market_ui.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/discovery-ui.js web/index.html web/styles/kuro.css tests/test_pages_dual_market_ui.py
git commit -m "feat: render dual-market lowest-price flow"
```

### Task 10: Verify the disabled collector and prepare the one-key smoke protocol

**Files:**
- Modify: `README.md`
- Create: `docs/dual-market-one-key-smoke.md`
- Test: `tests/test_dual_market_worker.py`

- [ ] **Step 1: Write the failing disabled-default test**

```python
def test_dual_market_capture_is_disabled_by_default(project_config: ProjectConfig) -> None:
    assert project_config.browser.dual_market_capture_enabled is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_dual_market_worker.py::test_dual_market_capture_is_disabled_by_default -q`  
Expected: FAIL until the default setting and config parsing exist.

- [ ] **Step 3: Implement default-disabled configuration and document the required explicit enablement**

Document that the first real run requires the user to explicitly lift pause, logs in manually, selects one known product key, permits one Wameiji search + one Xianyu search + maximum one detail per side, and then reviews saved screenshot/HTML/record IDs before any budget increase.

- [ ] **Step 4: Run the full relevant offline suite**

Run:

```bash
pytest tests/test_dual_market_core.py tests/test_dual_market_normalization.py tests/test_dual_market_storage.py tests/test_dual_market_service.py tests/test_dual_market_capture.py tests/test_xianyu_detail_parser.py tests/test_dual_market_worker.py tests/test_dual_market_api.py tests/test_pages_dual_market_ui.py -q
```

Expected: PASS with no browser process or live network operation.

- [ ] **Step 5: Review changes and commit**

```bash
git diff --check
git status --short
git add README.md docs/dual-market-one-key-smoke.md src/cd_monitor/config.py tests/test_dual_market_worker.py
git commit -m "docs: define guarded dual-market smoke protocol"
```

## Completion criteria

- Every ready comparison card references two concrete records from the same product key.
- The selected price on each side is the lowest eligible current observation for that side, never a global/reference pool value.
- Each visual image/link comes from the referenced observation, not a merchant logo, placeholder, or another product.
- Browser collection is still disabled unless the user explicitly enables a one-key bounded run.
- The full offline suite passes before any request is made to either marketplace.
