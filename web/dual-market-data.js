(function (global) {
  "use strict";

  const MAX_LIVE_AGE_MS = 180 * 60 * 1000;
  const SNAPSHOT_PATH = "data/dual-market-snapshot.json";

  function errorText(error) {
    return String(error && error.message || error || "unknown_error");
  }

  function normalizedBoard(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("dual-market board is not an object");
    }
    return {
      ...payload,
      summary: payload.summary && typeof payload.summary === "object" ? payload.summary : {},
      ready: Array.isArray(payload.ready) ? payload.ready : [],
      negative_profit: Array.isArray(payload.negative_profit) ? payload.negative_profit : [],
      cost_pending: Array.isArray(payload.cost_pending) ? payload.cost_pending : [],
      waiting_wameiji: Array.isArray(payload.waiting_wameiji) ? payload.waiting_wameiji : [],
      waiting_xianyu: Array.isArray(payload.waiting_xianyu) ? payload.waiting_xianyu : [],
      collector: payload.collector && typeof payload.collector === "object"
        ? payload.collector
        : { state: "paused" },
    };
  }

  function unavailableBoard(apiError, snapshotError) {
    return {
      unavailable: true,
      mode: "unavailable",
      summary: {},
      ready: [],
      negative_profit: [],
      cost_pending: [],
      waiting_wameiji: [],
      waiting_xianyu: [],
      collector: { state: "paused" },
      error: "live=" + errorText(apiError) + "; snapshot=" + errorText(snapshotError),
    };
  }

  async function load(options) {
    const apiGet = options && options.apiGet;
    if (typeof apiGet !== "function") throw new Error("apiGet is required");

    try {
      return {
        ...normalizedBoard(await apiGet("/api/dual-market/board")),
        mode: "live_api",
        unavailable: false,
        stale: false,
      };
    } catch (apiError) {
      const snapshotUrl = new URL(SNAPSHOT_PATH, document.baseURI).toString();
      try {
        const response = await fetch(snapshotUrl, {
          cache: "no-store",
          credentials: "omit",
        });
        if (!response.ok) throw new Error("GET snapshot -> " + response.status);
        const board = normalizedBoard(await response.json());
        const generatedAtMs = Date.parse(board.generated_at || "");
        return {
          ...board,
          mode: "verified_static_snapshot",
          unavailable: false,
          stale: !Number.isFinite(generatedAtMs)
            || Date.now() - generatedAtMs > MAX_LIVE_AGE_MS,
          api_error: errorText(apiError),
        };
      } catch (snapshotError) {
        return unavailableBoard(apiError, snapshotError);
      }
    }
  }

  global.DualMarketData = Object.freeze({ load });
})(window);
