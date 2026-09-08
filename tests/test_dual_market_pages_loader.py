from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_URL = (
    "https://niuzipai-gif.github.io/"
    "wameiji-xianyu-monitor/data/dual-market-snapshot.json"
)


def run_loader(
    responses: dict[str, dict[str, object]],
    assertion: str,
) -> None:
    harness = f"""
const fs = require("fs");
const vm = require("vm");
const responses = {json.dumps(responses)};
const requests = [];
const window = {{}};
const document = {{
  baseURI: "https://niuzipai-gif.github.io/wameiji-xianyu-monitor/",
}};
const fetch = async (url, options) => {{
  requests.push({{ url: String(url), options: options || {{}} }});
  const response = responses[String(url)];
  if (!response) throw new Error("network failure for " + url);
  return {{
    ok: Boolean(response.ok),
    status: Number(response.status),
    json: async () => response.body,
  }};
}};
const context = {{ window, document, fetch, URL, Date, console }};
vm.createContext(context);
vm.runInContext(fs.readFileSync("web/dual-market-data.js", "utf8"), context);
const apiGet = async (path) => {{
  requests.push({{ url: path, options: {{ api: true }} }});
  const response = responses[path];
  if (!response || !response.ok) throw new Error("GET " + path + " -> " + (response ? response.status : "network"));
  return response.body;
}};
window.DualMarketData.load({{ apiGet }}).then((result) => {{
  {assertion}
}}).catch((error) => {{
  console.error(error.stack || error);
  process.exitCode = 1;
}});
"""
    result = subprocess.run(
        ["node", "-e", harness],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_loader_prefers_the_live_board() -> None:
    run_loader(
        {
            "/api/dual-market/board": {
                "ok": True,
                "status": 200,
                "body": {"summary": {}, "collector": {"state": "paused"}},
            }
        },
        """
if (result.mode !== "live_api" || result.unavailable) throw new Error(JSON.stringify(result));
if (requests.length !== 1 || requests[0].url !== "/api/dual-market/board") {
  throw new Error("unexpected requests: " + JSON.stringify(requests));
}
""",
    )


def test_loader_uses_pages_snapshot_after_api_404() -> None:
    run_loader(
        {
            "/api/dual-market/board": {"ok": False, "status": 404, "body": {}},
            SNAPSHOT_URL: {
                "ok": True,
                "status": 200,
                "body": {
                    "mode": "verified_static_snapshot",
                    "generated_at": "2099-09-09T00:30:00+08:00",
                    "summary": {"cost_pending_count": 3},
                    "cost_pending": [{"comparison_id": 4}],
                },
            },
        },
        f"""
if (result.mode !== "verified_static_snapshot" || result.unavailable || result.stale) {{
  throw new Error(JSON.stringify(result));
}}
if (!requests.some((request) => request.url === {json.dumps(SNAPSHOT_URL)})) {{
  throw new Error("snapshot was not requested: " + JSON.stringify(requests));
}}
""",
    )


def test_loader_marks_an_old_snapshot_as_stale() -> None:
    run_loader(
        {
            "/api/dual-market/board": {"ok": False, "status": 404, "body": {}},
            SNAPSHOT_URL: {
                "ok": True,
                "status": 200,
                "body": {
                    "generated_at": "2000-01-01T00:00:00+08:00",
                    "summary": {},
                },
            },
        },
        """
if (result.mode !== "verified_static_snapshot" || !result.stale) {
  throw new Error(JSON.stringify(result));
}
""",
    )


def test_loader_returns_honest_unavailable_state_when_both_sources_fail() -> None:
    run_loader(
        {
            "/api/dual-market/board": {"ok": False, "status": 404, "body": {}},
            SNAPSHOT_URL: {"ok": False, "status": 404, "body": {}},
        },
        """
if (!result.unavailable || result.mode !== "unavailable") throw new Error(JSON.stringify(result));
if (!result.error.includes("live=") || !result.error.includes("snapshot=")) {
  throw new Error(result.error);
}
if (result.cost_pending.length !== 0 || result.collector.state !== "paused") {
  throw new Error(JSON.stringify(result));
}
""",
    )
