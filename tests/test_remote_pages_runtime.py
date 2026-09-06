"""Regression checks for the GitHub Pages viewer runtime."""
from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_browserless_app_harness(
    assertion: str,
    *,
    api_base: str,
    page_origin: str,
    access_token: str = "",
    capture_websocket: bool = False,
    include_discovery: bool = False,
    run_initial_timeouts: bool = False,
    prompt_value: str = "",
    require_access_token: bool = False,
) -> None:
    """Run ``app.js`` with a minimal browser surface in Node.

    The harness intentionally triggers DOMContentLoaded: that is where the
    viewer decides whether it should load the legacy per-item dashboard.
    """

    harness = f'''\
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const discoverySource = {"fs.readFileSync(process.argv[2], 'utf8')" if include_discovery else 'null'};
const listeners = {{ DOMContentLoaded: [] }};
const requests = [];
const websocketUrls = [];
const webSockets = [];
const classes = {{ add() {{}}, remove() {{}}, toggle() {{}} }};
const storageValues = {{}};
const storage = {{
  getItem(key) {{ return storageValues[key] || ""; }},
  setItem(key, value) {{ storageValues[key] = String(value); }},
  removeItem(key) {{ delete storageValues[key]; }},
}};
const timers = [];
let promptCount = 0;
const document = {{
  readyState: "loading",
  addEventListener(name, listener) {{ (listeners[name] ||= []).push(listener); }},
  querySelectorAll() {{ return []; }},
  querySelector() {{ return null; }},
  getElementById() {{ return null; }},
  body: {{ classList: classes, dataset: {{}}, setAttribute() {{}}, removeAttribute() {{}} }},
  documentElement: {{ classList: classes, dataset: {{}}, setAttribute() {{}}, removeAttribute() {{}} }},
}};
const window = {{
  location: {{ href: {page_origin!r}, origin: {page_origin!r}, search: "" }},
  CD_MONITOR_CONFIG: {{ apiBase: {api_base!r}, accessToken: {access_token!r} }},
  localStorage: storage,
  prompt() {{ promptCount += 1; return {prompt_value!r}; }},
}};
window.window = window;
const context = {{
  console,
  Intl,
  URL,
  URLSearchParams,
  window,
  document,
  localStorage: storage,
  setInterval() {{ return 1; }},
  clearInterval() {{}},
  setTimeout(callback) {{ timers.push(callback); return timers.length; }},
  clearTimeout() {{}},
  fetch: async (url) => {{
    requests.push(String(url));
    const authorized = !{str(require_access_token).lower()} || String(url).includes("access_token={prompt_value}");
    const payload = String(url).includes("/commands") ? {{ items: [] }} : {{ summary: {{}}, pools: [], opportunities: [] }};
    return {{ ok: authorized, status: authorized ? 200 : 401, json: async () => payload, text: async () => "" }};
  }},
}};
if ({str(capture_websocket).lower()}) {{
  context.WebSocket = class FakeWebSocket {{
    constructor(url) {{
      this.readyState = 1;
      this.listeners = {{}};
      websocketUrls.push(String(url));
      webSockets.push(this);
    }}
    addEventListener(type, listener) {{ (this.listeners[type] ||= []).push(listener); }}
    emit(type, event) {{ for (const listener of this.listeners[type] || []) listener(event); }}
  }};
}}
vm.createContext(context);
vm.runInContext(source, context, {{ filename: "app.js" }});
if (discoverySource) vm.runInContext(discoverySource, context, {{ filename: "discovery-ui.js" }});
for (const listener of listeners.DOMContentLoaded) {{
  try {{ listener(); }} catch (_) {{}}
}}
if ({str(run_initial_timeouts).lower()}) {{
  for (const callback of timers.splice(0)) {{
    try {{ callback(); }} catch (_) {{}}
  }}
}}
let settled = Promise.resolve();
for (let index = 0; index < 8; index += 1) settled = settled.then(() => Promise.resolve());
settled.then(() => {{
  {assertion}
}}).catch((error) => {{ console.error(error.stack || error); process.exit(1); }});
'''
    result = subprocess.run(
        ["node", "-e", harness, "web/app.js", "web/discovery-ui.js"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_remote_pages_does_not_start_legacy_dashboard_requests() -> None:
    """A Pages viewer should leave data loading to the selection-board module."""

    _run_browserless_app_harness(
        'if (requests.length) throw new Error("legacy API requests: " + requests.join(", "));',
        api_base="https://collector.example",
        page_origin="https://viewer.example/",
    )


def test_same_origin_live_feed_uses_configured_api_helpers() -> None:
    """The local collector keeps its WebSocket feed after the remote split."""

    _run_browserless_app_harness(
        'if (websocketUrls[0] !== "wss://collector.example/ws?access_token=viewer-token") '
        'throw new Error("unexpected websocket: " + websocketUrls.join(", ") '
        '+ "; config=" + JSON.stringify(window.CD_MONITOR_CONFIG) '
        '+ "; helper-token=" + window.CD_MONITOR_API.configuredApiToken());',
        api_base="https://collector.example",
        page_origin="https://collector.example",
        access_token="viewer-token",
        capture_websocket=True,
    )


def test_live_feed_uses_bridged_refresh_helper() -> None:
    """A WebSocket event must call the refresh helper exposed by the app module."""

    _run_browserless_app_harness(
        'const ws = webSockets[0]; let refreshes = 0; '
        'window.refreshAll = () => { refreshes += 1; }; '
        'try { ws.emit("message", { data: JSON.stringify({ type: "watch_action_started", '
        'payload: { watch_id: 7, action: "scan", status: "completed" } }) }); } '
        'catch (error) { throw new Error("live-feed event crashed: " + error.message); } '
        'if (refreshes !== 1) throw new Error("unexpected refreshes=" + refreshes);',
        api_base="https://collector.example",
        page_origin="https://collector.example",
        capture_websocket=True,
    )


def test_remote_pages_prompts_before_the_selection_board_fetches() -> None:
    """The first Pages visit should not emit unauthenticated Render requests."""

    _run_browserless_app_harness(
        'if (promptCount !== 1) throw new Error("expected one prompt, got " + promptCount); '
        'if (requests.some((url) => !url.includes("access_token=viewer-token"))) '
        'throw new Error("unauthenticated request: " + requests.join(", "));',
        api_base="https://collector.example",
        page_origin="https://viewer.example",
        include_discovery=True,
        run_initial_timeouts=True,
        prompt_value="viewer-token",
        require_access_token=True,
    )
