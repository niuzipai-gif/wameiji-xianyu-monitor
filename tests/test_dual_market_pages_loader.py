from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_URL = (
    "https://niuzipai-gif.github.io/"
    "wameiji-xianyu-monitor/data/dual-market-snapshot.json"
)


def valid_board(
    *,
    generated_at: str = "2099-09-09T00:30:00+08:00",
    policy_version: str = "wameiji-xianyu-net-v2",
    minimum_net_margin: float = 0.0,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "generated_at": generated_at,
        "strategy": {
            "policy_version": policy_version,
            "trade_direction": "wameiji_jpy_to_xianyu_cny",
            "minimum_net_margin": minimum_net_margin,
        },
        "summary": {
            "evaluated_count": 1,
            "eligible_count": 1,
            "below_margin_count": 0,
            "cost_pending_count": 0,
            "waiting_wameiji_count": 0,
            "waiting_xianyu_count": 0,
        },
        "eligible": [{"comparison_id": 4}],
        "below_margin": [],
        "cost_pending": [],
        "waiting_wameiji": [],
        "waiting_xianyu": [],
        "collector": {"state": "paused"},
    }


def run_loader(
    responses: dict[str, dict[str, object]],
    assertion: str,
    *,
    live: bool = False,
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
window.DualMarketData.load({{ apiGet, live: {str(live).lower()} }}).then((result) => {{
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


def test_loader_defaults_to_the_pages_snapshot_without_calling_live_api() -> None:
    run_loader(
        {
            "/api/dual-market/board": {
                "ok": True,
                "status": 200,
                "body": valid_board(),
            },
            SNAPSHOT_URL: {"ok": True, "status": 200, "body": valid_board()},
        },
        f"""
if (result.mode !== "verified_static_snapshot" || result.unavailable) throw new Error(JSON.stringify(result));
if (requests.length !== 1 || requests[0].url !== {json.dumps(SNAPSHOT_URL)}) {{
  throw new Error("unexpected requests: " + JSON.stringify(requests));
}}
""",
    )


def test_loader_uses_live_api_only_when_explicitly_requested() -> None:
    run_loader(
        {
            "/api/dual-market/board": {
                "ok": True,
                "status": 200,
                "body": valid_board(),
            },
        },
        """
if (result.mode !== "live_api" || result.unavailable) throw new Error(JSON.stringify(result));
if (requests.length !== 1 || requests[0].url !== "/api/dual-market/board") {
  throw new Error("unexpected requests: " + JSON.stringify(requests));
}
""",
        live=True,
    )


def test_loader_falls_back_when_explicit_live_payload_is_empty_and_invalid() -> None:
    run_loader(
        {
            "/api/dual-market/board": {
                "ok": True,
                "status": 200,
                "body": {"summary": {}, "eligible": []},
            },
            SNAPSHOT_URL: {"ok": True, "status": 200, "body": valid_board()},
        },
        """
if (result.mode !== "verified_static_snapshot" || result.unavailable) {
  throw new Error(JSON.stringify(result));
}
if (!result.api_error.includes("schema_version")) throw new Error(result.api_error);
""",
        live=True,
    )


def test_loader_marks_an_old_snapshot_as_stale() -> None:
    run_loader(
        {
            SNAPSHOT_URL: {
                "ok": True,
                "status": 200,
                "body": valid_board(generated_at="2000-01-01T00:00:00+08:00"),
            },
        },
        """
if (result.mode !== "verified_static_snapshot" || !result.stale) {
  throw new Error(JSON.stringify(result));
}
""",
    )


def test_loader_accepts_a_legacy_v1_snapshot_only_at_its_declared_threshold() -> None:
    run_loader(
        {
            SNAPSHOT_URL: {
                "ok": True,
                "status": 200,
                "body": valid_board(
                    policy_version="wameiji-xianyu-net-v1",
                    minimum_net_margin=0.25,
                ),
            },
        },
        """
if (result.mode !== "verified_static_snapshot" || result.unavailable) {
  throw new Error(JSON.stringify(result));
}
if (result.strategy.policy_version !== "wameiji-xianyu-net-v1") {
  throw new Error(JSON.stringify(result.strategy));
}
""",
    )


def test_loader_returns_honest_unavailable_when_requested_live_and_snapshot_fail() -> None:
    run_loader(
        {
            "/api/dual-market/board": {"ok": False, "status": 404, "body": {}},
            SNAPSHOT_URL: {"ok": False, "status": 404, "body": {}},
        },
        """
if (!result.unavailable || result.mode !== "unavailable") throw new Error(JSON.stringify(result));
if (!result.error.includes("live=") || !result.error.includes("snapshot=")) throw new Error(result.error);
if (result.eligible.length !== 0 || result.collector.state !== "paused") {
  throw new Error(JSON.stringify(result));
}
""",
        live=True,
    )
