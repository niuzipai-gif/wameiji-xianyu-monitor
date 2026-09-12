# Dual-Market Pages Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a compact two-column dual-market feed whose cards always show a traceable Xianyu image on the left, a truthful calculation state in the middle, and a verified Wameiji image on the right, both locally and on GitHub Pages.

**Architecture:** Keep `/api/dual-market/board` as the authoritative local source. Add a fail-closed exporter that converts currently verified board rows into a sanitized static JSON snapshot plus locally hosted image assets; the browser tries the live API first and falls back to that snapshot when running on Pages. The homepage becomes a two-column grid on desktop while preserving the three-part comparison inside each card.

**Tech Stack:** Python 3.11, requests, Pillow, pytest, vanilla JavaScript, CSS, GitHub Pages.

---

## File map

- Create `src/cd_monitor/pages_snapshot.py`: validate a dual-market board, download/verify public product images, and atomically publish static snapshot assets.
- Create `scripts/export_dual_market_pages_snapshot.py`: small command-line adapter for a live board URL or a JSON fixture.
- Create `tests/test_pages_snapshot.py`: behavioral tests for sanitization, image verification, partial rejection, and preservation of the last good snapshot.
- Create `web/dual-market-data.js`: isolated live-API/static-snapshot loader with freshness metadata.
- Create `tests/test_dual_market_pages_loader.py`: Node-backed tests for live success, static fallback, and total failure.
- Modify `web/index.html`: load the data module and describe the two-column feed.
- Modify `web/discovery-ui.js`: decouple the three API reads, consume the new loader, and label static/stale snapshots honestly.
- Modify `web/styles/kuro.css`: implement the confirmed desktop two-column feed and compact three-part cards.
- Modify `tests/test_pages_dual_market_ui.py`: lock the DOM/CSS/status language contract.
- Modify `tests/test_pages_discovery_ui.py`: prove the Pages build contains the snapshot loader, snapshot JSON, and local image assets.
- Create `web/data/dual-market-snapshot.json`: latest sanitized verified snapshot.
- Create `web/data/dual-market-snapshot.manifest.json`: export audit metadata and hashes.
- Create `web/assets/dual-market/YYYYMMDDTHHMMSS+HHMM/<comparison-id>-<source>.<ext>`: copied public listing images used by Pages.

### Task 1: Build the fail-closed snapshot exporter

**Files:**
- Create: `src/cd_monitor/pages_snapshot.py`
- Create: `tests/test_pages_snapshot.py`

- [ ] **Step 1: Write tests for a successful two-sided export**

```python
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from cd_monitor.pages_snapshot import DownloadedImage, SnapshotExportError, export_pages_snapshot


def png_bytes(colour: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (160, 160), colour).save(output, format="PNG")
    return output.getvalue()


def verified_board() -> dict[str, object]:
    return {
        "summary": {"ready_count": 0, "negative_profit_count": 0, "cost_pending_count": 1},
        "ready": [],
        "negative_profit": [],
        "cost_pending": [{
            "comparison_id": 4,
            "canonical_product_key": "catalog:nzs955|edition:initial_limited|condition:sealed",
            "xianyu": {
                "source": "xianyu", "title": "Kiss Plan 初回限定盘B", "price": 121,
                "currency": "CNY", "url": "https://www.goofish.com/item?id=1",
                "image_url": "https://img.alicdn.com/example.png", "evidence_level": "search_card",
                "captured_at": "2026-09-08T23:28:24+08:00", "condition_group": "sealed",
            },
            "wameiji": {
                "source": "wameiji", "title": "Kiss Plan 初回限定盤B", "price": 2250,
                "currency": "JPY", "url": "https://meruki.cn/mall/paypay/detail/z1",
                "image_url": "https://auctions.c.yimg.jp/example.png",
                "evidence_level": "detail_verified",
                "captured_at": "2026-09-08T23:28:19+08:00", "condition_group": "sealed",
            },
            "calculation": {"status": "cost_pending", "sale_price_cny": 121},
        }],
        "waiting_wameiji": [], "waiting_xianyu": [], "collector": {"state": "paused"},
    }


def test_export_rewrites_both_images_and_writes_hash_manifest(tmp_path: Path) -> None:
    images = {
        "https://img.alicdn.com/example.png": png_bytes("gold"),
        "https://auctions.c.yimg.jp/example.png": png_bytes("pink"),
    }

    def fetch(url: str) -> DownloadedImage:
        return DownloadedImage(body=images[url], content_type="image/png")

    result = export_pages_snapshot(
        verified_board(), tmp_path / "web",
        generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"), fetch_image=fetch,
    )
    payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
    card = payload["cost_pending"][0]
    assert card["xianyu"]["image_url"].startswith("assets/dual-market/20260909T003000+0800/")
    assert card["wameiji"]["image_url"].startswith("assets/dual-market/20260909T003000+0800/")
    assert "img.alicdn.com" not in result.snapshot_path.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    for asset in manifest["assets"]:
        body = (tmp_path / "web" / asset["path"]).read_bytes()
        assert asset["sha256"] == hashlib.sha256(body).hexdigest()
```

- [ ] **Step 2: Run the exporter test and verify the module is missing**

Run: `pytest tests/test_pages_snapshot.py::test_export_rewrites_both_images_and_writes_hash_manifest -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'cd_monitor.pages_snapshot'`.

- [ ] **Step 3: Add rejection and last-good-snapshot tests**

```python
def test_export_drops_a_card_when_either_source_image_fails(tmp_path: Path) -> None:
    board = verified_board()
    board["cost_pending"] = board["cost_pending"] * 2
    board["cost_pending"][1] = {**board["cost_pending"][1], "comparison_id": 5}

    calls = 0
    def fetch(url: str) -> DownloadedImage:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise SnapshotExportError("image download failed")
        return DownloadedImage(png_bytes("blue"), "image/png")

    result = export_pages_snapshot(
        board, tmp_path / "web",
        generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"), fetch_image=fetch,
    )
    payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
    assert len(payload["cost_pending"]) == 1
    assert result.dropped_comparison_ids == (5,)


def test_all_failed_cards_preserve_the_previous_snapshot(tmp_path: Path) -> None:
    web = tmp_path / "web"
    target = web / "data" / "dual-market-snapshot.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"previous": true}', encoding="utf-8")

    def fail(_url: str) -> DownloadedImage:
        raise SnapshotExportError("blocked")

    with pytest.raises(SnapshotExportError, match="no publishable comparisons"):
        export_pages_snapshot(
            verified_board(), web,
            generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"), fetch_image=fail,
        )
    assert target.read_text(encoding="utf-8") == '{"previous": true}'
```

- [ ] **Step 4: Implement the exporter with strict source and image checks**

```python
# src/cd_monitor/pages_snapshot.py
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
from PIL import Image

ALLOWED_IMAGE_HOSTS = frozenset({"img.alicdn.com", "auctions.c.yimg.jp"})
IMAGE_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class SnapshotExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedImage:
    body: bytes
    content_type: str


@dataclass(frozen=True)
class SnapshotExportResult:
    snapshot_path: Path
    manifest_path: Path
    asset_paths: tuple[Path, ...]
    dropped_comparison_ids: tuple[int, ...]


def download_public_image(url: str) -> DownloadedImage:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise SnapshotExportError(f"image host is not allowed: {parsed.hostname or 'missing'}")
    response = requests.get(url, timeout=(5, 20), headers={"User-Agent": "WAMEIJI-XIANYU snapshot exporter/1"})
    response.raise_for_status()
    return DownloadedImage(response.content, response.headers.get("Content-Type", ""))


def _verified_image(download: DownloadedImage) -> tuple[bytes, str, int, int]:
    if not download.content_type.casefold().startswith("image/") or len(download.body) < 256:
        raise SnapshotExportError("response is not a usable image")
    with Image.open(io.BytesIO(download.body)) as image:
        image.verify()
    with Image.open(io.BytesIO(download.body)) as image:
        width, height = image.size
        image_format = str(image.format or "").upper()
    if width < 80 or height < 80 or image_format not in IMAGE_EXTENSIONS:
        raise SnapshotExportError("image dimensions or format are not publishable")
    return download.body, IMAGE_EXTENSIONS[image_format], width, height


def _safe_https_url(value: object) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _finite_price(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _validated_card(card: object) -> tuple[int, str, dict[str, object], dict[str, object]]:
    if not isinstance(card, dict):
        raise SnapshotExportError("comparison is not an object")
    comparison_id = card.get("comparison_id")
    canonical_key = str(card.get("canonical_product_key") or "").strip()
    xianyu = card.get("xianyu")
    wameiji = card.get("wameiji")
    if not isinstance(comparison_id, int) or not canonical_key:
        raise SnapshotExportError("comparison identity is missing")
    if not isinstance(xianyu, dict) or not isinstance(wameiji, dict):
        raise SnapshotExportError("comparison is missing a source")
    if xianyu.get("evidence_level") != "search_card" or wameiji.get("evidence_level") != "detail_verified":
        raise SnapshotExportError("comparison evidence levels are invalid")
    for source in (xianyu, wameiji):
        source_key = str(source.get("canonical_product_key") or canonical_key)
        if source_key != canonical_key or not _finite_price(source.get("price")):
            raise SnapshotExportError("comparison key or price is invalid")
        if not _safe_https_url(source.get("url")) or not _safe_https_url(source.get("image_url")):
            raise SnapshotExportError("source URL is invalid")
    return comparison_id, canonical_key, xianyu, wameiji


def export_pages_snapshot(
    board: dict[str, object], web_dir: Path, *, generated_at: datetime,
    fetch_image: Callable[[str], DownloadedImage] = download_public_image,
) -> SnapshotExportResult:
    categories = ("ready", "negative_profit", "cost_pending")
    snapshot_id = generated_at.strftime("%Y%m%dT%H%M%S%z")
    web_dir = Path(web_dir)
    assets_root = web_dir / "assets" / "dual-market"
    assets_root.mkdir(parents=True, exist_ok=True)
    final_asset_dir = assets_root / snapshot_id
    if final_asset_dir.exists():
        raise SnapshotExportError(f"snapshot asset directory already exists: {snapshot_id}")
    staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}-", dir=assets_root))
    accepted: dict[str, list[dict[str, object]]] = {name: [] for name in categories}
    dropped: list[int] = []
    manifest_assets: list[dict[str, object]] = []
    try:
        for category in categories:
            rows = board.get(category, [])
            if not isinstance(rows, list):
                raise SnapshotExportError(f"board category is not a list: {category}")
            for raw_card in rows:
                card_files: list[Path] = []
                try:
                    comparison_id, _key, xianyu, wameiji = _validated_card(raw_card)
                    card = copy.deepcopy(raw_card)
                    card_assets: list[dict[str, object]] = []
                    for source_name, source in (("xianyu", xianyu), ("wameiji", wameiji)):
                        body, extension, width, height = _verified_image(fetch_image(str(source["image_url"])))
                        filename = f"{comparison_id}-{source_name}{extension}"
                        staged_file = staging / filename
                        staged_file.write_bytes(body)
                        card_files.append(staged_file)
                        relative = (Path("assets") / "dual-market" / snapshot_id / filename).as_posix()
                        card[source_name]["image_url"] = relative
                        card_assets.append({
                            "path": relative, "source": source_name, "comparison_id": comparison_id,
                            "sha256": hashlib.sha256(body).hexdigest(), "width": width, "height": height,
                        })
                    accepted[category].append(card)
                    manifest_assets.extend(card_assets)
                except (KeyError, OSError, requests.RequestException, SnapshotExportError, ValueError):
                    for staged_file in card_files:
                        staged_file.unlink(missing_ok=True)
                    if isinstance(raw_card, dict) and isinstance(raw_card.get("comparison_id"), int):
                        dropped.append(raw_card["comparison_id"])
                    continue
        published_count = sum(len(accepted[name]) for name in categories)
        if published_count == 0:
            raise SnapshotExportError("no publishable comparisons")
        os.replace(staging, final_asset_dir)
        payload: dict[str, object] = {
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "mode": "verified_static_snapshot",
            "summary": {
                "ready_count": len(accepted["ready"]),
                "negative_profit_count": len(accepted["negative_profit"]),
                "cost_pending_count": len(accepted["cost_pending"]),
                "waiting_wameiji_count": 0,
                "waiting_xianyu_count": 0,
            },
            "ready": accepted["ready"],
            "negative_profit": accepted["negative_profit"],
            "cost_pending": accepted["cost_pending"],
            "waiting_wameiji": [],
            "waiting_xianyu": [],
            "collector": {"state": "paused"},
        }
        manifest: dict[str, object] = {
            "schema_version": 1, "snapshot_id": snapshot_id,
            "generated_at": generated_at.isoformat(), "published_comparisons": published_count,
            "dropped_comparison_ids": sorted(set(dropped)), "assets": manifest_assets,
        }
        data_dir = web_dir / "data"
        manifest_path = data_dir / "dual-market-snapshot.manifest.json"
        snapshot_path = data_dir / "dual-market-snapshot.json"
        _atomic_json(manifest_path, manifest)
        _atomic_json(snapshot_path, payload)
        return SnapshotExportResult(
            snapshot_path=snapshot_path, manifest_path=manifest_path,
            asset_paths=tuple(web_dir / item["path"] for item in manifest_assets),
            dropped_comparison_ids=tuple(sorted(set(dropped))),
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)
```

- [ ] **Step 5: Run the exporter tests**

Run: `pytest tests/test_pages_snapshot.py -q`

Expected: PASS for the success, partial rejection, invalid-image, wrong-evidence, and last-good preservation cases.

- [ ] **Step 6: Commit the exporter core**

```powershell
git add -- src/cd_monitor/pages_snapshot.py tests/test_pages_snapshot.py
git commit -m "feat: export verified Pages snapshots"
```

### Task 2: Add the explicit local export command

**Files:**
- Create: `scripts/export_dual_market_pages_snapshot.py`
- Modify: `tests/test_pages_snapshot.py`

- [ ] **Step 1: Write a CLI test using a board fixture file**

```python
import importlib.util


def test_export_cli_accepts_a_local_board_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    board_file = tmp_path / "board.json"
    board_file.write_text(json.dumps(verified_board(), ensure_ascii=False), encoding="utf-8")
    script = Path("scripts/export_dual_market_pages_snapshot.py")
    spec = importlib.util.spec_from_file_location("pages_snapshot_cli", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fetch(_url: str) -> DownloadedImage:
        return DownloadedImage(png_bytes("purple"), "image/png")

    exit_code = module.main(
        ["--board-file", str(board_file), "--web-dir", str(tmp_path / "web"),
         "--generated-at", "2026-09-09T00:30:00+08:00"],
        fetch_image=fetch,
    )
    assert exit_code == 0
    assert '"published_comparisons": 1' in capsys.readouterr().out
```

- [ ] **Step 2: Run the CLI test and verify the script is missing**

Run: `pytest tests/test_pages_snapshot.py::test_export_cli_accepts_a_local_board_file -q`

Expected: FAIL because `scripts/export_dual_market_pages_snapshot.py` does not exist.

- [ ] **Step 3: Implement the command adapter**

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cd_monitor.pages_snapshot import DownloadedImage, download_public_image, export_pages_snapshot


def load_board(*, board_url: str, board_file: str) -> dict[str, object]:
    if board_file:
        return json.loads(Path(board_file).read_text(encoding="utf-8"))
    response = requests.get(board_url, timeout=(5, 20))
    response.raise_for_status()
    return response.json()


def main(
    argv: list[str] | None = None, *,
    fetch_image: Callable[[str], DownloadedImage] = download_public_image,
) -> int:
    parser = argparse.ArgumentParser(description="Publish a verified dual-market Pages snapshot")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--board-url", default="http://127.0.0.1:9890/api/dual-market/board")
    source.add_argument("--board-file", default="")
    parser.add_argument("--web-dir", default="web")
    parser.add_argument("--generated-at", default="")
    args = parser.parse_args(argv)
    generated_at = datetime.fromisoformat(args.generated_at) if args.generated_at else datetime.now().astimezone()
    result = export_pages_snapshot(
        load_board(board_url=args.board_url, board_file=args.board_file),
        Path(args.web_dir), generated_at=generated_at, fetch_image=fetch_image,
    )
    print(json.dumps({
        "published_comparisons": len(result.asset_paths) // 2,
        "dropped_comparison_ids": result.dropped_comparison_ids,
        "snapshot": str(result.snapshot_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI and exporter tests**

Run: `pytest tests/test_pages_snapshot.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the command**

```powershell
git add -- scripts/export_dual_market_pages_snapshot.py tests/test_pages_snapshot.py
git commit -m "feat: add Pages snapshot export command"
```

### Task 3: Make the browser fall back to the static snapshot

**Files:**
- Create: `web/dual-market-data.js`
- Create: `tests/test_dual_market_pages_loader.py`
- Modify: `web/index.html`
- Modify: `web/discovery-ui.js`

- [ ] **Step 1: Write browserless loader tests**

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def run_loader(responses: dict[str, dict[str, object]], assertion: str) -> None:
    harness = f'''const fs=require("fs"),vm=require("vm");
const responses={json.dumps(responses)}; const requests=[];
const window={{}}; const document={{baseURI:"https://niuzipai-gif.github.io/wameiji-xianyu-monitor/"}};
const fetch=async url=>{{requests.push(String(url));const r=responses[String(url)];
 if(!r) throw new Error("network"); return {{ok:r.ok,status:r.status,json:async()=>r.body}};}};
vm.runInNewContext(fs.readFileSync("web/dual-market-data.js","utf8"),{{window,document,fetch,URL,Date}});
window.DualMarketData.load({{apiGet:path=>fetch(path).then(r=>{{if(!r.ok)throw Error(String(r.status));return r.json();}})}})
.then(result=>{{{assertion}}}).catch(error=>{{console.error(error);process.exit(1);}});'''
    result = subprocess.run(["node", "-e", harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_loader_prefers_the_live_board() -> None:
    run_loader({"/api/dual-market/board": {"ok": True, "status": 200, "body": {"summary": {}, "collector": {"state": "paused"}}}},
               'if(result.mode!=="live_api")throw Error(JSON.stringify(result));')


def test_loader_uses_pages_snapshot_after_api_404() -> None:
    snapshot_url = "https://niuzipai-gif.github.io/wameiji-xianyu-monitor/data/dual-market-snapshot.json"
    run_loader({
        "/api/dual-market/board": {"ok": False, "status": 404, "body": {}},
        snapshot_url: {"ok": True, "status": 200, "body": {"mode": "verified_static_snapshot", "generated_at": "2026-09-09T00:30:00+08:00", "summary": {}}},
    }, 'if(result.mode!=="verified_static_snapshot")throw Error(JSON.stringify(result));')


def test_loader_returns_an_honest_unavailable_state_when_both_sources_fail() -> None:
    snapshot_url = "https://niuzipai-gif.github.io/wameiji-xianyu-monitor/data/dual-market-snapshot.json"
    run_loader({
        "/api/dual-market/board": {"ok": False, "status": 404, "body": {}},
        snapshot_url: {"ok": False, "status": 404, "body": {}},
    }, 'if(!result.unavailable||!result.error.includes("live=")||!result.error.includes("snapshot="))'
       'throw Error(JSON.stringify(result));')
```

- [ ] **Step 2: Run loader tests and verify the module is missing**

Run: `pytest tests/test_dual_market_pages_loader.py -q`

Expected: FAIL because `web/dual-market-data.js` does not exist.

- [ ] **Step 3: Implement the loader**

```javascript
(function (global) {
  "use strict";
  const MAX_LIVE_AGE_MS = 180 * 60 * 1000;

  async function load(options) {
    const apiGet = options.apiGet;
    try {
      return { ...(await apiGet("/api/dual-market/board")), mode: "live_api", unavailable: false };
    } catch (apiError) {
      const snapshotUrl = new URL("data/dual-market-snapshot.json", document.baseURI).toString();
      try {
        const response = await fetch(snapshotUrl, { cache: "no-store", credentials: "omit" });
        if (!response.ok) throw new Error("GET snapshot -> " + response.status);
        const board = await response.json();
        const generatedAtMs = Date.parse(board.generated_at || "");
        return {
          ...board,
          mode: "verified_static_snapshot",
          unavailable: false,
          stale: !Number.isFinite(generatedAtMs) || Date.now() - generatedAtMs > MAX_LIVE_AGE_MS,
          api_error: String(apiError && apiError.message || apiError),
        };
      } catch (snapshotError) {
        return {
          unavailable: true, mode: "unavailable", summary: {}, ready: [], negative_profit: [],
          cost_pending: [], waiting_wameiji: [], waiting_xianyu: [], collector: { state: "paused" },
          error: "live=" + String(apiError && apiError.message || apiError) +
            "; snapshot=" + String(snapshotError && snapshotError.message || snapshotError),
        };
      }
    }
  }
  global.DualMarketData = Object.freeze({ load });
})(window);
```

- [ ] **Step 4: Wire independent reads into the homepage**

Load `<script src="dual-market-data.js"></script>` before `discovery-ui.js`. Replace the all-or-nothing `Promise.all` inputs with:

```javascript
const emptyBoard = { summary: {}, pools: [], opportunities: [] };
const [board, commandPayload, dualMarketBoard] = await Promise.all([
  apiGet("/api/discovery/board").catch(() => emptyBoard),
  apiGet("/api/discovery/commands").catch(() => ({ items: [] })),
  window.DualMarketData.load({ apiGet }),
]);
```

In `renderDiscoveryStatus`, render static modes before collector status:

```javascript
if (view.dualMarketBoard.mode === "verified_static_snapshot") {
  const when = timeLabel(view.dualMarketBoard.generated_at);
  setDiscoveryStatus(
    view.dualMarketBoard.stale
      ? "Pages 历史快照 · " + when + " · 不代表当前可买"
      : "Pages 已核验快照 · " + when + " · 采集仅在本机运行",
    view.dualMarketBoard.stale ? "paused_quality" : "idle",
  );
  return;
}
```

- [ ] **Step 5: Run loader and existing Pages runtime tests**

Run: `pytest tests/test_dual_market_pages_loader.py tests/test_pages_dual_market_ui.py tests/test_remote_pages_runtime.py -q`

Expected: PASS with no Render token prompt regression and no legacy Pages WebSocket.

- [ ] **Step 6: Commit the runtime fallback**

```powershell
git add -- web/dual-market-data.js web/index.html web/discovery-ui.js tests/test_dual_market_pages_loader.py
git commit -m "feat: load verified snapshot on Pages"
```

### Task 4: Implement the approved two-column compact feed

**Files:**
- Modify: `web/index.html`
- Modify: `web/styles/kuro.css`
- Modify: `tests/test_pages_dual_market_ui.py`

- [ ] **Step 1: Add layout contract tests**

```python
def test_home_feed_is_two_columns_with_compact_three_part_cards() -> None:
    css = Path("web/styles/kuro.css").read_text(encoding="utf-8")
    homepage = Path("web/index.html").read_text(encoding="utf-8")
    assert "body.kuro #homeFeed" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "grid-template-columns: minmax(0, 1fr) minmax(130px, .7fr) minmax(0, 1fr)" in css
    assert "max-height: 245px" in css
    assert "一行两条机会" in homepage
    assert "左侧闲鱼" in homepage and "右侧挖煤姬" in homepage
```

- [ ] **Step 2: Run the layout test and verify it fails against the current full-width feed**

Run: `pytest tests/test_pages_dual_market_ui.py::test_home_feed_is_two_columns_with_compact_three_part_cards -q`

Expected: FAIL because the current feed is one column and each card uses the global large-card dimensions.

- [ ] **Step 3: Apply the confirmed layout**

Add focused rules after the existing discovery-card block:

```css
body.kuro #home .hero { min-height: 310px; }
body.kuro #home .hero-copy { padding: 30px 32px 24px; }
body.kuro #home h2 { font-size: 38px; margin: 10px 0 12px; }
body.kuro #home .lead { font-size: 14px; line-height: 1.7; }
body.kuro #home .stats { margin-top: 18px; }
body.kuro #home .stat { padding: 12px; }
body.kuro #home .mascot-stage { min-height: 310px; padding-top: 12px; }
body.kuro #home .mascot-stage img { max-height: 245px; }
body.kuro #homeFeed {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 15px;
}
body.kuro #homeFeed .discovery-op-card {
  grid-template-columns: minmax(0, 1fr) minmax(130px, .7fr) minmax(0, 1fr);
  gap: 10px;
  min-height: 260px;
  padding: 12px;
  border-radius: 22px;
}
body.kuro #homeFeed .discovery-op-card .side-product {
  flex-direction: column;
  gap: 8px;
  padding: 10px;
}
body.kuro #homeFeed .discovery-op-card .thumb {
  width: 100%;
  height: 132px;
  border-radius: 11px;
}
body.kuro #homeFeed .discovery-op-card .analysis { padding: 11px 8px; }
body.kuro #homeFeed .discovery-op-card .grid2 { grid-template-columns: 1fr; gap: 7px; }
body.kuro #homeFeed .discovery-op-card .metric { padding: 7px; }
body.kuro #homeFeed .discovery-op-card .metric strong { font-size: 14px; }

@media (max-width: 1180px) {
  body.kuro #homeFeed { grid-template-columns: 1fr; }
  body.kuro #homeFeed .discovery-op-card {
    grid-template-columns: minmax(0, 1fr) minmax(175px, .7fr) minmax(0, 1fr);
  }
}
@media (max-width: 760px) {
  body.kuro #homeFeed .discovery-op-card { grid-template-columns: 1fr; }
  body.kuro #homeFeed .discovery-op-card .analysis { order: -1; }
}
```

Update the heading copy to “一行两条机会；每条卡内左侧闲鱼、中间判断、右侧挖煤姬”。 Preserve the existing evidence labels and profit-pending copy.

- [ ] **Step 4: Run layout and renderer tests**

Run: `pytest tests/test_pages_dual_market_ui.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the layout**

```powershell
git add -- web/index.html web/styles/kuro.css tests/test_pages_dual_market_ui.py
git commit -m "feat: compact the dual-market feed"
```

### Task 5: Publish the current verified data and prove the Pages artifact contains it

**Files:**
- Create: `web/data/dual-market-snapshot.json`
- Create: `web/data/dual-market-snapshot.manifest.json`
- Create: `web/assets/dual-market/<snapshot-id>/*`
- Modify: `tests/test_pages_discovery_ui.py`

- [ ] **Step 1: Extend the Pages build test**

```python
import json

from PIL import Image


def test_pages_build_copies_verified_snapshot_and_images(tmp_path: Path) -> None:
    destination = tmp_path / "site"
    subprocess.run([sys.executable, "scripts/build_pages.py", "--dest", str(destination)], check=True)
    payload = json.loads((destination / "data" / "dual-market-snapshot.json").read_text(encoding="utf-8"))
    cards = payload["ready"] + payload["negative_profit"] + payload["cost_pending"]
    assert cards
    for card in cards:
        for source in ("xianyu", "wameiji"):
            assert card[source]["image_url"].startswith("assets/dual-market/")
            image = destination / card[source]["image_url"]
            assert image.is_file()
            with Image.open(image) as opened:
                opened.verify()
```

- [ ] **Step 2: Run the Pages build test before exporting**

Run: `pytest tests/test_pages_discovery_ui.py::test_pages_build_copies_verified_snapshot_and_images -q`

Expected: FAIL because no static snapshot has been committed yet.

- [ ] **Step 3: Export the current live board without resuming collection**

Run:

```powershell
python scripts/export_dual_market_pages_snapshot.py --board-url http://127.0.0.1:9890/api/dual-market/board --web-dir web
```

Expected: JSON output with `published_comparisons` greater than zero, two assets for every published card, and no collection command issued.

- [ ] **Step 4: Verify the exported files independently**

Run:

```powershell
python -c "import hashlib,json; from pathlib import Path; from PIL import Image; root=Path('web'); p=json.loads((root/'data/dual-market-snapshot.json').read_text(encoding='utf-8')); cards=p['ready']+p['negative_profit']+p['cost_pending']; assert cards; [Image.open(root/c[s]['image_url']).verify() for c in cards for s in ('xianyu','wameiji')]; print({'cards':len(cards),'images':len(cards)*2})"
```

Expected: prints matching non-zero card and image counts without an exception.

- [ ] **Step 5: Run the Pages build tests**

Run: `pytest tests/test_pages_discovery_ui.py tests/test_pages_dual_market_ui.py -q`

Expected: PASS and `_site` contains the exact snapshot and image files.

- [ ] **Step 6: Commit only sanitized static evidence**

```powershell
git add -- web/data/dual-market-snapshot.json web/data/dual-market-snapshot.manifest.json web/assets/dual-market tests/test_pages_discovery_ui.py
git commit -m "data: publish verified dual-market snapshot"
```

Before committing, run `git diff --cached --name-only` and confirm it contains no `data/local`, browser profile, Cookie, token, raw HTML, or collection log path.

### Task 6: Regression check, local visual check, and Pages deployment

**Files:**
- Verify only; change production files only in response to a failing test attributable to this feature.

- [ ] **Step 1: Run focused Python and JavaScript checks**

```powershell
pytest tests/test_pages_snapshot.py tests/test_dual_market_pages_loader.py tests/test_pages_dual_market_ui.py tests/test_pages_discovery_ui.py tests/test_remote_pages_runtime.py tests/test_dual_market_api.py -q
node --check web/dual-market-data.js
node --check web/discovery-ui.js
```

Expected: all selected tests PASS and both scripts pass syntax checking.

- [ ] **Step 2: Build and inspect the static site**

```powershell
python scripts/build_pages.py --dest _site
python -c "import json; from pathlib import Path; p=Path('_site'); d=json.loads((p/'data/dual-market-snapshot.json').read_text(encoding='utf-8')); cards=d['ready']+d['negative_profit']+d['cost_pending']; assert len(cards)>=1; assert all((p/c[s]['image_url']).is_file() for c in cards for s in ('xianyu','wameiji')); print(len(cards))"
```

Expected: prints a positive card count.

- [ ] **Step 3: Read back the local 9890 page in a browser**

Reload `http://127.0.0.1:9890/?fresh=<commit>` and verify:

- the collector remains paused;
- the current verified rows render from the live API;
- a desktop viewport shows two cards in one row;
- both source images render and the center says “待算” when cost is incomplete;
- the left and right panels open only their respective source URLs.

- [ ] **Step 4: Commit any test-only integration adjustments**

```powershell
git status --short
git add -- tests web src scripts
git diff --cached --check
git commit -m "test: verify dual-market Pages delivery"
```

Skip this commit when Step 1–3 require no tracked change. Never add `.superpowers/`.

- [ ] **Step 5: Push the verified branch to `main`**

```powershell
git push origin HEAD:main
```

Expected: push succeeds and the `web/**` path triggers `.github/workflows/deploy-pages.yml`.

- [ ] **Step 6: Verify the deployed public files and UI**

Read back:

```text
https://niuzipai-gif.github.io/wameiji-xianyu-monitor/data/dual-market-snapshot.json
https://niuzipai-gif.github.io/wameiji-xianyu-monitor/
```

Confirm the public JSON has the same `generated_at` and local asset paths as the committed snapshot, every referenced image returns an image response, the page shows the two-column cards, and the status explicitly says “Pages 已核验快照” or “Pages 历史快照”. Do not claim success until this public readback passes.
