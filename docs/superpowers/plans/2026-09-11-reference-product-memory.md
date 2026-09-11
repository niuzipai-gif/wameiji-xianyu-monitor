# 正样本参考产品记忆 Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Import approved screenshot samples into an explainable local product-memory model and apply its evidence-based score to new discovery candidates without treating price as a training label.

**Architecture:** A pure core module tokenizes product evidence and scores a candidate against persisted positive reference products. A service layer performs local screenshot extraction (SHA-256, barcode, optional OCR) and SQLite persistence. The discovery service refreshes a candidate match after upsert. An explicit local CLI imports a folder; a separate observational record path persists limited, timestamped Xianyu/Wameiji identity and market facts without promoting price to a label; read-only API routes expose coverage and evidence.

**Tech Stack:** Python 3.11, sqlite3, subprocess, hashlib, Pillow, pyzbar, local Tesseract (`eng`/`jpn`/`chi_sim` only), pytest, existing HTTP server.

**Execution mode:** Inline execution, selected to meet the user-requested deadline. The user has authorized controlled real-page research for each reference product. It is limited to one product and two task-owned browser tabs at a time; record identity/version/status facts, close both tabs before the next product, do not enter credentials, solve CAPTCHAs, place orders, publish, or mutate accounts. A procurement-side unavailable or expensive result is recorded as time-bounded evidence only, never as a negative training label.

---

## 2026-09-11 执行记录

- 已先建立可恢复备份：`F:\WAMEIJI-XIANYU\WAMEIJI-XIANYU-handoff\data\local\dual-market.pre-reference-memory-20260911.db`；原库完整性检查为 `ok`。
- 已离线导入 `F:\竞品图存放处\参考选品` 的 125 张认可截图，得到 125 个独立正样本产品、7 个 EAN/JAN 条码锚点；本机 OCR 使用已校验的 `eng`、`jpn`、`chi_sim` 语言数据，导入不调用网络。证据文件为 `data/local/reference-memory-import-20260911.json`，随后重导入报告为 `imported_samples: 0`。
- 已实现纯证据匹配、幂等 SQLite 存储、发现候选的非阻断参考匹配刷新、离线 CLI，以及仅供读取的状态/匹配/市场观察 API。条码精确命中为 `1.0`；价格、无货和登录限制只存为独立的市场观察，绝不回写为负标签或淘汰结论。
- 已完成 12 个代表性产品的挖煤姬受控研究：9 条 `found`、3 条 `not_currently_listed`；闲鱼在 7 个条码锚点上止于 `login_required`，没有处理登录或验证码。研究页全部关闭，未关闭用户原有页面。该覆盖是可审计的代表性核验，不应误称为 125 个产品都已完成双站实查。
- 验证：核心/存储/观察测试 14 项、发现链路测试 2 项、接口测试 3 项均通过；实际库经隔离本地 API 回读为 125 样本、19 条观察。首次全量 `pytest -q` 为 `1690 passed, 1 skipped, 1 xfailed, 1 failed`，唯一失败是已有异步请求的 `<1s` 时序断言；它在随后连续 5 次单测和相邻用例顺序复跑中均通过（实测约 0.20--0.28 秒）。未为该环境时序抖动修改无关业务逻辑；最新一次完整复跑最终为 `1692 passed, 1 skipped, 1 xfailed`。
- 现有 `web_server.py` 有 72 条历史 Ruff 问题；本次新增核心/服务/测试文件的 Ruff 检查通过。常驻本地服务未被擅自重启，因此新只读 API 需由该服务下一次正常重启后才会出现在它的端口上；隔离临时服务已用同一真实数据库验证新接口。

---

### Task 1: Pure evidence contracts and deterministic matcher

**Files:**
- Create: src/cd_monitor/core/reference_memory.py
- Create: tests/test_reference_memory_core.py

- [ ] **Step 1: Write the failing tests**

~~~python
from cd_monitor.core.reference_memory import (
    ReferenceProductEvidence,
    build_candidate_evidence,
    score_candidate_against_product,
)


def test_exact_jan_is_a_full_strength_reference_match() -> None:
    product = ReferenceProductEvidence(
        product_id=7,
        stable_key="jan:4547366558180",
        barcode="4547366558180",
        tokens=frozenset({"milet", "walkin", "lane"}),
    )
    result = score_candidate_against_product(
        build_candidate_evidence(title="milet Walkin In My Lane", jan="4547366558180"),
        product,
    )
    assert result.match_kind == "exact_barcode"
    assert result.score == 1.0
    assert result.evidence["matched_barcode"] == "4547366558180"


def test_two_distinct_shared_words_make_an_explainable_fallback_match() -> None:
    product = ReferenceProductEvidence(
        product_id=8,
        stable_key="sample:abc",
        barcode=None,
        tokens=frozenset({"burnout", "syndromes", "special", "edition"}),
    )
    result = score_candidate_against_product(
        build_candidate_evidence(title="BURNOUT SYNDROMES special edition CD"), product
    )
    assert result.match_kind == "token_overlap"
    assert result.score >= 0.55
    assert result.evidence["shared_tokens"] == ["burnout", "special", "syndromes"]


def test_generic_media_word_alone_is_not_a_reference_match() -> None:
    product = ReferenceProductEvidence(
        product_id=9,
        stable_key="sample:def",
        barcode=None,
        tokens=frozenset({"milet", "walkin", "lane", "cd"}),
    )
    result = score_candidate_against_product(build_candidate_evidence(title="CD"), product)
    assert result.match_kind == "no_match"
    assert result.score == 0.0
~~~

- [ ] **Step 2: Run and verify RED**

Run: .venv/Scripts/python.exe -m pytest tests/test_reference_memory_core.py -q
Expected: collection fails because the new core module is missing.

- [ ] **Step 3: Implement the minimal matcher**

~~~python
@dataclass(frozen=True)
class CandidateEvidence:
    barcodes: frozenset[str]
    tokens: frozenset[str]


@dataclass(frozen=True)
class ReferenceProductEvidence:
    product_id: int
    stable_key: str
    barcode: str | None
    tokens: frozenset[str]


def score_candidate_against_product(
    candidate: CandidateEvidence, product: ReferenceProductEvidence
) -> ReferenceMatch:
    if product.barcode and product.barcode in candidate.barcodes:
        return ReferenceMatch(product.product_id, 1.0, "exact_barcode", {...})
    shared = sorted((candidate.tokens & product.tokens) - GENERIC_MEDIA_TOKENS)
    if len(shared) < 2:
        return ReferenceMatch(None, 0.0, "no_match", {"shared_tokens": shared})
~~~

Implement Unicode-safe tokenization, EAN/JAN collection through the existing identifier helpers, stable score rounding, and an immutable match result. Do not read files or databases in this module.

- [ ] **Step 4: Run and verify GREEN**

Run: .venv/Scripts/python.exe -m pytest tests/test_reference_memory_core.py -q
Expected: the three matcher tests pass.

- [ ] **Step 5: Commit**

~~~powershell
git add src/cd_monitor/core/reference_memory.py tests/test_reference_memory_core.py
git commit -m "feat: add explainable reference product matcher"
~~~

### Task 2: Screenshot extraction and idempotent storage

**Files:**
- Create: src/cd_monitor/services/reference_memory.py
- Modify: src/cd_monitor/storage/sqlite.py
- Create: tests/test_reference_memory_storage.py

- [ ] **Step 1: Write the failing tests**

~~~python
def test_import_reference_samples_is_idempotent_and_groups_exact_barcodes(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    extractor = FakeExtractor(
        {
            "one.png": ExtractedReferenceSample("sha-one", "4547366558180", "milet Walkin In My Lane"),
            "two.png": ExtractedReferenceSample("sha-two", "4547366558180", "milet Walkin In My Lane CD"),
        }
    )
    first = import_reference_samples(db_path, tmp_path, extractor=extractor)
    second = import_reference_samples(db_path, tmp_path, extractor=extractor)
    assert first.imported_samples == 2
    assert first.product_count == 1
    assert second.imported_samples == 0
    assert second.existing_samples == 2


def test_refresh_candidate_match_persists_evidence_without_rejecting_unmatched_candidate(tmp_path: Path) -> None:
    db_path, candidate_id = seeded_candidate_database(tmp_path)
    seed_reference_product(db_path, barcode="4547366558180", tokens={"milet", "walkin", "lane"})
    match = refresh_discovery_candidate_reference_match(db_path, candidate_id)
    assert match.match_kind == "token_overlap"
    assert read_candidate_match(db_path, candidate_id)["score"] == match.score
~~~

- [ ] **Step 2: Run and verify RED**

Run: .venv/Scripts/python.exe -m pytest tests/test_reference_memory_storage.py -q
Expected: import error for the missing service functions and tables.

- [ ] **Step 3: Implement local-only extraction and storage**

Add _migrate_reference_product_memory(conn) after the current discovery and priority migrations. It creates these tables:

~~~sql
CREATE TABLE IF NOT EXISTS reference_products (
  id INTEGER PRIMARY KEY,
  stable_key TEXT NOT NULL UNIQUE,
  barcode TEXT UNIQUE,
  tokens_json TEXT NOT NULL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reference_product_samples (
  id INTEGER PRIMARY KEY,
  checksum_sha256 TEXT NOT NULL UNIQUE,
  product_id INTEGER NOT NULL REFERENCES reference_products(id),
  source_path TEXT NOT NULL,
  barcode TEXT,
  extracted_text TEXT NOT NULL DEFAULT '',
  tokens_json TEXT NOT NULL,
  extraction_state TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reference_candidate_matches (
  candidate_id INTEGER PRIMARY KEY REFERENCES discovery_candidates(id),
  reference_product_id INTEGER REFERENCES reference_products(id),
  score REAL NOT NULL,
  match_kind TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
~~~

The service enumerates only supported local image files; hashes bytes; decodes EAN/JAN with pyzbar when available; calls local Tesseract only when present; reports available language capability; records extraction state instead of throwing when OCR is unavailable; supports an explicit local `--tessdata-dir` and `--refresh-existing` metadata pass without changing the product identity; persists idempotently; groups only exact barcode samples; and exposes refresh_discovery_candidate_reference_match plus read-only status and list functions. No HTTP client belongs in this module.

- [ ] **Step 4: Run and verify GREEN**

Run: .venv/Scripts/python.exe -m pytest tests/test_reference_memory_storage.py -q
Expected: all tests pass and re-import adds no rows.

- [ ] **Step 5: Commit**

~~~powershell
git add src/cd_monitor/services/reference_memory.py src/cd_monitor/storage/sqlite.py tests/test_reference_memory_storage.py
git commit -m "feat: persist positive reference product memory"
~~~

### Task 3: Discovery integration without changing its safety gates

**Files:**
- Modify: src/cd_monitor/services/discovery.py
- Modify: tests/test_discovery_service.py

- [ ] **Step 1: Write failing integration tests**

~~~python
async def test_discovery_persists_reference_evidence_after_candidate_upsert(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.db"
    seed_reference_product(db_path, barcode="4547366558180", tokens={"milet", "walkin", "lane"})
    result = await run_discovery_scan(..., fetch_wameiji=fixture_fetcher(...))
    assert result.candidate_count == 1
    assert read_only_reference_matches(db_path)[0]["match_kind"] == "exact_barcode"


async def test_reference_no_match_does_not_skip_detail_queue(tmp_path: Path) -> None:
    result = await run_discovery_scan(..., fetch_wameiji=fixture_fetcher(unmatched_item))
    assert result.detail_query_count == 1
~~~

- [ ] **Step 2: Run and verify RED**

Run: .venv/Scripts/python.exe -m pytest tests/test_discovery_service.py -k reference -q
Expected: the exact-reference test fails because discovery has not refreshed a match.

- [ ] **Step 3: Add bounded post-upsert refresh**

After every normal upsert result in discovery, refresh the resolved candidate IDs using the local service. The call must never change detail_verified, pipeline_stage, source login state, detail budget, or Xianyu budget. It must not score price fields. Extractor/storage errors are exposed as diagnostic metadata but do not fail the original detail-first flow.

- [ ] **Step 4: Run regression suite**

Run: .venv/Scripts/python.exe -m pytest tests/test_discovery_service.py tests/test_discovery_storage.py tests/test_discovery_worker.py -q
Expected: all pass, including the no-match candidate proceeding through the existing detail queue.

- [ ] **Step 5: Commit**

~~~powershell
git add src/cd_monitor/services/discovery.py tests/test_discovery_service.py
git commit -m "feat: score discovery candidates against reference memory"
~~~

### Task 4: Explicit offline import CLI and read-only inspection API

**Files:**
- Modify: src/cd_monitor/cli.py
- Modify: src/cd_monitor/web_server.py
- Create: tests/test_reference_memory_cli.py
- Create: tests/test_reference_memory_api.py

- [ ] **Step 1: Write failing CLI/API tests**

~~~python
def test_reference_import_cli_requires_explicit_folder_and_reports_counts(tmp_path: Path, capsys) -> None:
    code = main(["reference-import", "--db", str(tmp_path / "db.sqlite"), "--folder", str(tmp_path)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network_requests"] == 0
    assert payload["input_images"] == 0


def test_reference_memory_status_api_is_read_only(tmp_path: Path) -> None:
    response = call_api(tmp_path / "db.sqlite", "/api/reference-memory/status")
    assert response.status == 200
    assert response.json()["network_requests"] == 0
~~~

- [ ] **Step 2: Run and verify RED**

Run: .venv/Scripts/python.exe -m pytest tests/test_reference_memory_cli.py tests/test_reference_memory_api.py -q
Expected: parser and route errors because the command and routes do not yet exist.

- [ ] **Step 3: Add the explicit interfaces**

Add reference-import --db PATH --folder PATH in cli.py. It calls only the local service and prints JSON with input count, imported/existing samples, product count, barcode/OCR coverage, and network_requests: 0. Add GET /api/reference-memory/status and GET /api/reference-memory/matches?limit=N. Validate limit and return only persisted metadata/evidence. Do not add a web endpoint that accepts arbitrary local filesystem paths.

- [ ] **Step 4: Run regression tests**

Run: .venv/Scripts/python.exe -m pytest tests/test_reference_memory_cli.py tests/test_reference_memory_api.py tests/test_discovery_api.py -q
Expected: all pass, invalid limits are rejected, and there is no write route.

- [ ] **Step 5: Commit**

~~~powershell
git add src/cd_monitor/cli.py src/cd_monitor/web_server.py tests/test_reference_memory_cli.py tests/test_reference_memory_api.py
git commit -m "feat: expose local reference product memory"
~~~

### Task 5: Bounded market-observation queue and persistence

**Files:**
- Modify: src/cd_monitor/services/reference_memory.py
- Modify: src/cd_monitor/storage/sqlite.py
- Modify: src/cd_monitor/cli.py
- Modify: src/cd_monitor/web_server.py
- Create: tests/test_reference_market_observations.py

- [ ] **Step 1: Write failing tests**

Test that a product can store a timestamped Xianyu or Wameiji observation with a constrained state, title/version/catalog evidence, optional observed price/currency, and source URL; test that `price_unfavorable`, `not_currently_listed`, `login_required`, and `found` do not alter reference-product score or candidate eligibility. Test the CLI/API returns only persisted observations and never opens a browser.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reference_market_observations.py -q`
Expected: import error because the observation service, schema, and command do not exist.

- [ ] **Step 3: Implement observation-only persistence**

Add a `reference_market_observations` migration with product id, market (`xianyu` or `wameiji`), observed-at timestamp, bounded observation state, title/version/catalog/barcode evidence, optional price/currency, source URL and note. Add `reference-market-record` with explicit database, product id, market and observation state. Expose read-only status/counts and recent observations. Never let this path update `reference_score`, price policy, detail gating, or start a browser.

- [ ] **Step 4: Run and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reference_market_observations.py tests/test_reference_memory_cli.py tests/test_reference_memory_api.py -q`
Expected: valid observations persist; invalid markets/states fail closed; market observations cannot create negative labels.

- [ ] **Step 5: Conduct the authorized tab-bounded research pass**

After import, enumerate each reference product/sample. For each current item, use only task-owned browser tabs to search its extracted barcode/catalog/title on Xianyu and Wameiji, capture the product identity/version and a timestamped market state, call the explicit record command, then close both task tabs before advancing. Stop a site for that item at `login_required`, CAPTCHA, rate limit, or unavailable result; persist the bounded status and proceed to the next item. Existing user tabs must not be closed.

### Task 6: Authorized local training and end-to-end verification

**Files:**
- Runtime target: F:/WAMEIJI-XIANYU/WAMEIJI-XIANYU-handoff/data/local/dual-market.db
- Input: F:/竞品图存放处/参考选品
- Evidence output: data/local/reference-memory-import-20260911.json

- [ ] **Step 1: Preserve a recoverable local database copy**

Run: Copy-Item -LiteralPath data/local/dual-market.db -Destination data/local/dual-market.pre-reference-memory-20260911.db -ErrorAction Stop

- [ ] **Step 2: Run offline import**

Run: .venv/Scripts/python.exe -m cd_monitor.cli reference-import --db F:/WAMEIJI-XIANYU/WAMEIJI-XIANYU-handoff/data/local/dual-market.db --folder F:/竞品图存放处/参考选品
Expected: JSON reports 125 input images, no network requests, import counts, barcode/OCR coverage, and product count.

- [ ] **Step 3: Verify real exact-match evidence**

Use a read-only SQLite/API query to select a decoded barcode product, then score a candidate containing that barcode. Expected: exact_barcode, score 1.0, and source screenshot filename in the evidence.

- [ ] **Step 4: Verify the bounded market observations**

Run a read-only status query and verify that each imported product has up to two independent observations, every observation has an allowed state and timestamp, and any `price_unfavorable` / `not_currently_listed` row leaves the product positively remembered and eligible for future live recheck.

- [ ] **Step 5: Full verification**

Run: .venv/Scripts/python.exe -m pytest -q
Run: .venv/Scripts/python.exe -m ruff check src tests
Run: .venv/Scripts/python.exe -m cd_monitor.cli reference-import --db F:/WAMEIJI-XIANYU/WAMEIJI-XIANYU-handoff/data/local/dual-market.db --folder F:/竞品图存放处/参考选品

Expected: test suite and lint have zero failures. The second import reports imported_samples: 0 with unchanged catalog coverage.

- [ ] **Step 6: Commit only source, tests, and docs**

~~~powershell
git add docs/superpowers/specs/2026-09-11-reference-product-memory-design.md docs/superpowers/plans/2026-09-11-reference-product-memory.md src/cd_monitor/core/reference_memory.py src/cd_monitor/services/reference_memory.py src/cd_monitor/services/discovery.py src/cd_monitor/storage/sqlite.py src/cd_monitor/cli.py src/cd_monitor/web_server.py tests/test_reference_memory_core.py tests/test_reference_memory_storage.py tests/test_reference_memory_cli.py tests/test_reference_memory_api.py tests/test_discovery_service.py
git commit -m "feat: train reference product memory from approved samples"
~~~

## Plan self-review

- **Spec coverage:** Tasks 1–2 cover the positive-only evidence model and importer; Task 3 preserves detail-first gating; Task 4 provides explicit user-facing access; Task 5 records tab-bounded, volatile market facts without turning them into labels; Task 6 trains and proves idempotence with the actual supplied folder.
- **No-placeholder scan:** every task names files, an executable command, expected behavior, and implementation boundary.
- **Type consistency:** ReferenceProductEvidence, ReferenceMatch, refresh_discovery_candidate_reference_match, reference-import, and the three persistence tables are consistent throughout.
