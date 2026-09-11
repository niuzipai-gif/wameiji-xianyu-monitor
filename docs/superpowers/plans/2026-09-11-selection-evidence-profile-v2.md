# Selection Evidence Profile V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Show deterministic positive-reference product profiles and per-market latest-state coverage without letting price, availability, or old login observations change a candidate's reference score.

**Architecture:** Keep the existing SQLite tables and identity matcher unchanged. Add read-only projections over reference_products, reference_product_samples, and the latest row per (reference_product_id, market); expose them through the read-only API and the existing selection-board reference panel.

**Tech Stack:** Python 3.11, SQLite window functions, http.server JSON API, vanilla JavaScript, CSS, pytest.

---

## File structure

- Modify src/cd_monitor/services/reference_memory.py: product profiles and latest-market coverage.
- Modify tests/test_reference_market_observations.py: latest-row and unknown-evidence tests.
- Modify tests/test_reference_memory_storage.py: prove market facts do not alter an existing candidate match.
- Modify src/cd_monitor/web_server.py: bounded profile endpoint.
- Modify tests/test_reference_memory_interfaces.py: API and invalid-limit contract tests.
- Modify web/index.html, web/discovery-ui.js, and web/styles/kuro.css: board metrics and profile cards.
- Create tests/test_selection_board_reference_panel.py: static board/API contract test without a JavaScript runner.

No migration is needed. V2 is a deterministic projection of existing evidence and must not persist subjective labels.

### Task 1: Read-only product profile and latest-state coverage

**Files:**
- Modify: src/cd_monitor/services/reference_memory.py:447-488
- Modify: tests/test_reference_market_observations.py:9-78
- Modify: tests/test_reference_memory_storage.py:204-262

- [ ] **Step 1: Write failing profile and latest-row tests**

Import build_reference_product_profile. Add a test that records an old Xianyu login state then a newer found state while leaving Wameiji unobserved:

    def test_profile_and_status_use_latest_market_record_without_changing_identity_score(
        tmp_path: Path,
    ) -> None:
        db_path, product_id = _seed_reference_product(tmp_path)
        old_login = record_reference_market_observation(
            db_path, product_id=product_id, market="xianyu",
            observation_state="login_required",
            observed_at="2026-09-10T00:00:00+00:00",
        )
        current_found = record_reference_market_observation(
            db_path, product_id=product_id, market="xianyu",
            observation_state="found",
            observed_title="milet Walkin In My Lane 初回限定盤",
            version_evidence="初回限定盤", catalog_no="SECL-9999",
            price=9_999.0, currency="CNY",
            source_url="https://goofish.example/item/milet",
            observed_at="2026-09-11T00:00:00+00:00",
        )

        profile = build_reference_product_profile(db_path, product_id=product_id)
        status = reference_memory_status(db_path)

        assert old_login["observation_state"] == "login_required"
        assert profile["catalog_numbers"] == ["SECL-9999"]
        assert profile["markets"] == {"wameiji": None, "xianyu": current_found}
        assert profile["missing_evidence"] == ["wameiji:market_observation"]
        assert status["market_observation_states"] == {"found": 1, "login_required": 1}
        assert status["latest_market_coverage"]["xianyu"] == {
            "covered_product_count": 1,
            "unobserved_product_count": 0,
            "states": {"found": 1},
            "latest_observed_at": "2026-09-11T00:00:00Z",
        }
        assert status["latest_market_coverage"]["wameiji"] == {
            "covered_product_count": 0,
            "unobserved_product_count": 1,
            "states": {},
            "latest_observed_at": None,
        }

Add a tie-break test:

    def test_profile_uses_higher_observation_id_when_timestamps_match(tmp_path: Path) -> None:
        db_path, product_id = _seed_reference_product(tmp_path)
        record_reference_market_observation(
            db_path, product_id=product_id, market="wameiji",
            observation_state="found", observed_title="first",
            observed_at="2026-09-11T00:00:00+00:00",
        )
        expected = record_reference_market_observation(
            db_path, product_id=product_id, market="wameiji",
            observation_state="not_currently_listed", observed_title="second",
            observed_at="2026-09-11T00:00:00+00:00",
        )

        assert build_reference_product_profile(
            db_path, product_id=product_id
        )["markets"]["wameiji"] == expected

- [ ] **Step 2: Run the tests and verify the new symbol is absent**

Run:

    .venv\Scripts\python.exe -m pytest tests/test_reference_market_observations.py -q

Expected: collection fails because build_reference_product_profile does not yet exist.

- [ ] **Step 3: Implement deterministic projections**

In src/cd_monitor/services/reference_memory.py, add:

    _PROFILE_MARKETS = ("wameiji", "xianyu")

Add public functions with these exact signatures:

    def build_reference_product_profile(
        db_path: str | Path, *, product_id: int
    ) -> dict[str, object]: ...

    def list_reference_product_profiles(
        db_path: str | Path, *, limit: int = 100
    ) -> list[dict[str, object]]: ...

build_reference_product_profile must validate the positive ID, fetch the product and its extraction states, and return:

    {
        "product_id": product_id,
        "stable_key": stable_key,
        "barcode": barcode_or_none,
        "identity_tokens": sorted(_tokens_from_json(tokens_json)),
        "sample_count": sample_count,
        "extraction_states": sorted(unique_extraction_states),
        "catalog_numbers": sorted(unique_latest_catalog_numbers),
        "markets": {"wameiji": observation_or_none, "xianyu": observation_or_none},
        "missing_evidence": missing_evidence,
    }

Build missing_evidence in this exact order: append "barcode" only when the product barcode is NULL; then, in _PROFILE_MARKETS order, append "<market>:market_observation" when that market has no latest observation. Do not infer missing edition, artist, or category fields from OCR text.

Add private helper _latest_observations_by_product(conn, *, product_ids). Its query must select the same columns as _market_observation_dict and use:

    ROW_NUMBER() OVER (
      PARTITION BY reference_product_id, market
      ORDER BY observed_at DESC, id DESC
    ) AS row_number

Only rows where row_number = 1 belong in the nested result. Do not alter score_candidate_against_product, build_candidate_evidence, or refresh_discovery_candidate_reference_match.

Extend reference_memory_status with latest_market_coverage. For each fixed market, compute covered_product_count, unobserved_product_count, state counts from only latest rows, and maximum latest observed_at. Keep market_observation_states as its historical audit count.

- [ ] **Step 4: Add an explicit candidate-score isolation regression**

In tests/test_reference_memory_storage.py, extend test_candidate_match_is_persisted_without_rejecting_an_unmatched_candidate. Import record_reference_market_observation, fetch the one reference product ID after the two candidate matches are persisted, then append:

    with sqlite3.connect(db_path) as conn:
        product_id = int(conn.execute("SELECT id FROM reference_products").fetchone()[0])
    record_reference_market_observation(
        db_path, product_id=product_id, market="xianyu",
        observation_state="price_unfavorable", price=9_999.0, currency="CNY",
        observed_at="2026-09-11T00:00:00+00:00",
    )
    refreshed = refresh_discovery_candidate_reference_match(db_path, candidate_id)

    assert refreshed.score == matched.score
    assert read_candidate_reference_match(db_path, candidate_id)["score"] == matched.score

The test must prove price and state are not inputs to the existing match score; it must not add either field to any candidate evidence constructor.

- [ ] **Step 5: Run service tests and verify compatibility**

Run:

    .venv\Scripts\python.exe -m pytest tests/test_reference_memory_core.py tests/test_reference_memory_storage.py tests/test_reference_market_observations.py -q

Expected: PASS; barcode, token-overlap, and historical observation assertions retain their existing behavior.

- [ ] **Step 6: Commit the projection**

    git add src/cd_monitor/services/reference_memory.py tests/test_reference_market_observations.py
    git commit -m "feat: add latest reference evidence profiles"

### Task 2: Read-only profile API

**Files:**
- Modify: src/cd_monitor/web_server.py:86-90,537-565
- Modify: tests/test_reference_memory_interfaces.py:11-131

- [ ] **Step 1: Write a failing API contract test**

Make _seed_reference_observation return (observation, product_id), then add:

    def test_reference_memory_profiles_api_exposes_evidence_and_latest_market_state(
        tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "reference.db"
        observation, product_id = _seed_reference_observation(tmp_path, db_path)
        record_reference_market_observation(
            db_path, product_id=product_id, market="xianyu",
            observation_state="found",
            observed_title="milet Walkin In My Lane 初回限定盤",
            catalog_no="SECL-9999",
            observed_at="2026-09-11T00:00:00+00:00",
        )
        server = create_server("127.0.0.1", 0, db_path, static_dir="web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
        try:
            status = _get_json(f"{base_url}/api/reference-memory/status")
            profiles = _get_json(f"{base_url}/api/reference-memory/profiles?limit=1")
            code, invalid = _get_json_with_status(
                f"{base_url}/api/reference-memory/profiles?limit=0"
            )

            assert status["latest_market_coverage"]["xianyu"]["states"] == {"found": 1}
            assert profiles["items"][0]["product_id"] == product_id
            assert profiles["items"][0]["markets"]["wameiji"] == observation
            assert profiles["items"][0]["markets"]["xianyu"]["catalog_no"] == "SECL-9999"
            assert code == 400
            assert invalid == {"error": "invalid_limit"}
        finally:
            server.shutdown()
            server.server_close()

- [ ] **Step 2: Run it and verify the route is missing**

Run:

    .venv\Scripts\python.exe -m pytest tests/test_reference_memory_interfaces.py::test_reference_memory_profiles_api_exposes_evidence_and_latest_market_state -q

Expected: FAIL because /api/reference-memory/profiles is not routed.

- [ ] **Step 3: Add the bounded GET route**

Import list_reference_product_profiles beside the existing reference-memory service imports. Between the status and matches branches, add:

    if route == "/api/reference-memory/profiles":
        raw_limit = _query_param(urlparse(self.path).query, "limit")
        try:
            reference_limit = int(raw_limit) if raw_limit is not None else 100
            items = list_reference_product_profiles(db_path, limit=reference_limit)
        except (TypeError, ValueError):
            self._json({"error": "invalid_limit"}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json({"items": items})
        return

Do not add a write route or any importer, browser, or marketplace call.

- [ ] **Step 4: Run interface tests**

Run:

    .venv\Scripts\python.exe -m pytest tests/test_reference_memory_interfaces.py tests/test_reference_market_observations.py -q

Expected: PASS; invalid limits are 400, profile reads are bounded, and the other endpoints remain read-only.

- [ ] **Step 5: Commit the API**

    git add src/cd_monitor/web_server.py tests/test_reference_memory_interfaces.py
    git commit -m "feat: expose reference evidence profiles"

### Task 3: Selection-board latest evidence panel

**Files:**
- Modify: web/index.html:89-106
- Modify: web/discovery-ui.js:203-252,505-524
- Modify: web/styles/kuro.css:493-505,563-566
- Create: tests/test_selection_board_reference_panel.py

- [ ] **Step 1: Write a failing static UI contract test**

Create:

    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]

    def test_reference_panel_has_latest_market_and_profile_contract() -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "discovery-ui.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "styles" / "kuro.css").read_text(encoding="utf-8")

        assert 'id="referenceXianyuLatest"' in html
        assert 'id="referenceWameijiLatest"' in html
        assert 'id="referenceProfileList"' in html
        assert 'apiGet("/api/reference-memory/profiles?limit=3")' in script
        assert "latest_market_coverage" in script
        assert "missing_evidence" in script
        assert ".reference-profiles" in styles

- [ ] **Step 2: Run it and confirm the V2 DOM contract is absent**

Run:

    .venv\Scripts\python.exe -m pytest tests/test_selection_board_reference_panel.py -q

Expected: FAIL because the IDs, API request, and styles do not exist.

- [ ] **Step 3: Add the board data and rendering boundary**

In web/index.html, add these metric cards after referenceObservationCount:

    <div><small>闲鱼最新</small><strong id="referenceXianyuLatest">--</strong></div>
    <div><small>挖煤姬最新</small><strong id="referenceWameijiLatest">--</strong></div>

Add this after referenceObservationList:

    <div class="reference-profiles" id="referenceProfileList" aria-live="polite">
      <div class="empty-state">正在读取身份与待核验信息…</div>
    </div>

In web/discovery-ui.js, add:

    function latestMarketLabel(coverage) {
      const item = coverage && typeof coverage === "object" ? coverage : {};
      const states = item.states && typeof item.states === "object" ? item.states : {};
      return "覆盖 " + (Number(item.covered_product_count) || 0)
        + " · 已找到 " + (Number(states.found) || 0)
        + " · 待核验 " + (Number(item.unobserved_product_count) || 0);
    }

renderReferenceMemory must populate the two new metrics from status.latest_market_coverage and calculate its login warning only from latest_market_coverage.xianyu.states.login_required, never from historical market_observation_states. Render at most three view.referenceProfiles with stable key or barcode, sample count, catalog numbers, market latest referenceStateLabel values, and missing_evidence as “仍待确认：…”. Do not render a buy/reject label or derive text from price.

Extend refreshBoard with:

    apiGet("/api/reference-memory/profiles?limit=3").catch(() => null),

and set view.referenceProfiles to returned items or []. A profile request failure must not fail the opportunity feed.

- [ ] **Step 4: Add responsive scoped styles**

Change the metric grid to six columns and add:

    body.kuro .reference-profiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 10px; }
    body.kuro .reference-profile { padding: 12px; border: 1px solid rgba(219, 230, 246, 0.8); border-radius: 14px; background: rgba(255, 255, 255, 0.56); min-width: 0; }
    body.kuro .reference-profile b,
    body.kuro .reference-profile p { display: block; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    body.kuro .reference-profile p { margin-top: 5px; color: var(--kuro-muted); font-size: 12px; }
    @media (max-width: 560px) {
      body.kuro .reference-memory-metrics,
      body.kuro .reference-profiles { grid-template-columns: 1fr; }
    }

Inside the existing 900px media query, set both .reference-memory-metrics and .reference-profiles to two columns. Do not alter unrelated styles.

- [ ] **Step 5: Run static and API tests**

Run:

    .venv\Scripts\python.exe -m pytest tests/test_selection_board_reference_panel.py tests/test_reference_memory_interfaces.py -q

Expected: PASS.

- [ ] **Step 6: Manually verify the board, read-only**

Run the local web server without launching the discovery worker:

    .venv\Scripts\python.exe -m cd_monitor.cli web --db data\local\dual-market.db --port 9890

Open http://127.0.0.1:9890 in one task-created browser tab. Confirm the panel has six metrics, historic logins do not force a current login warning, profiles distinguish identity/current-market/missing facts, and no panel action opens a market, writes an account, purchases, or rejects a candidate. Close only the task-created browser tab, then stop the local test server.

- [ ] **Step 7: Commit the board**

    git add web/index.html web/discovery-ui.js web/styles/kuro.css tests/test_selection_board_reference_panel.py
    git commit -m "feat: show latest reference evidence on board"

### Task 4: Full verification on real local data

**Files:**
- Modify: none

- [ ] **Step 1: Run full V2 regression**

Run:

    .venv\Scripts\python.exe -m pytest tests/test_reference_memory_core.py tests/test_reference_memory_storage.py tests/test_reference_market_observations.py tests/test_reference_memory_discovery.py tests/test_reference_memory_interfaces.py tests/test_selection_board_reference_panel.py -q

Expected: PASS with no deselected or skipped test.

- [ ] **Step 2: Read the real data through supported interfaces**

Run:

    .venv\Scripts\python.exe -m cd_monitor.cli reference-status --db data\local\dual-market.db

Expected: product_count=125 and sample_count=125; latest_market_coverage is separate from historical state counts.

Start the local server and run:

    Invoke-RestMethod http://127.0.0.1:9890/api/reference-memory/profiles?limit=3 | ConvertTo-Json -Depth 8

Expected: three local-evidence profiles, explicit missing_evidence, and no more than one latest observation per market.

- [ ] **Step 3: Confirm commit and worktree scope**

Run:

    git status --short
    git log --oneline -3

Expected: the three V2 commits are present; pre-existing login-recovery changes remain unstaged and untouched. Do not make a verification-only commit.
