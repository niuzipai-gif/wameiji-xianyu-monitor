(function (global) {
  "use strict";

  const MAX_LIVE_AGE_MS = 180 * 60 * 1000;
  const SNAPSHOT_PATH = "data/dual-market-snapshot.json";
  const POLICY_VERSION = "wameiji-xianyu-net-v1";
  const TRADE_DIRECTION = "wameiji_jpy_to_xianyu_cny";
  const MINIMUM_NET_MARGIN = 0.25;
  const SUMMARY_COUNT_KEYS = [
    "evaluated_count",
    "eligible_count",
    "below_margin_count",
    "cost_pending_count",
    "waiting_wameiji_count",
    "waiting_xianyu_count",
  ];

  function errorText(error) {
    return String(error && error.message || error || "unknown_error");
  }

  function normalizedBoard(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("dual-market board is not an object");
    }
    if (Number(payload.schema_version) !== 2) {
      throw new Error("schema_version must be 2");
    }
    const strategy = payload.strategy;
    if (!strategy || typeof strategy !== "object" || Array.isArray(strategy)) {
      throw new Error("strategy is required");
    }
    if (strategy.policy_version !== POLICY_VERSION) {
      throw new Error("unsupported policy_version");
    }
    if (strategy.trade_direction !== TRADE_DIRECTION) {
      throw new Error("unsupported trade_direction");
    }
    if (!Number.isFinite(Number(strategy.minimum_net_margin))
      || Number(strategy.minimum_net_margin) < MINIMUM_NET_MARGIN) {
      throw new Error("minimum_net_margin must be at least 0.25");
    }
    const summary = payload.summary;
    if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
      throw new Error("summary is required");
    }
    for (const key of SUMMARY_COUNT_KEYS) {
      const value = Number(summary[key]);
      if (!Number.isInteger(value) || value < 0) {
        throw new Error("summary." + key + " must be a non-negative integer");
      }
    }
    if (!Array.isArray(payload.eligible)) {
      throw new Error("eligible must be an array");
    }
    if (Number(summary.eligible_count) !== payload.eligible.length) {
      throw new Error("summary.eligible_count does not match eligible rows");
    }
    return {
      ...payload,
      strategy: { ...strategy },
      summary: { ...summary },
      eligible: payload.eligible.slice(),
      below_margin: Array.isArray(payload.below_margin) ? payload.below_margin.slice() : [],
      cost_pending: Array.isArray(payload.cost_pending) ? payload.cost_pending.slice() : [],
      waiting_wameiji: Array.isArray(payload.waiting_wameiji) ? payload.waiting_wameiji.slice() : [],
      waiting_xianyu: Array.isArray(payload.waiting_xianyu) ? payload.waiting_xianyu.slice() : [],
      collector: payload.collector && typeof payload.collector === "object"
        ? { ...payload.collector }
        : { state: "paused" },
    };
  }

  function unavailableBoard(apiError, snapshotError) {
    return {
      unavailable: true,
      mode: "unavailable",
      schema_version: 2,
      strategy: {
        policy_version: POLICY_VERSION,
        trade_direction: TRADE_DIRECTION,
        minimum_net_margin: MINIMUM_NET_MARGIN,
      },
      summary: {
        evaluated_count: 0,
        eligible_count: 0,
        below_margin_count: 0,
        cost_pending_count: 0,
        waiting_wameiji_count: 0,
        waiting_xianyu_count: 0,
      },
      eligible: [],
      below_margin: [],
      cost_pending: [],
      waiting_wameiji: [],
      waiting_xianyu: [],
      collector: { state: "paused" },
      error: "live=" + errorText(apiError) + "; snapshot=" + errorText(snapshotError),
    };
  }

  async function load(options) {
    const live = Boolean(options && options.live);
    const apiGet = options && options.apiGet;
    let apiError = new Error("not_requested");

    if (live) {
      try {
        if (typeof apiGet !== "function") throw new Error("apiGet is required for live mode");
        return {
          ...normalizedBoard(await apiGet("/api/dual-market/board")),
          mode: "live_api",
          unavailable: false,
          stale: false,
        };
      } catch (error) {
        apiError = error;
      }
    }

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
        stale: !Number.isFinite(generatedAtMs) || Date.now() - generatedAtMs > MAX_LIVE_AGE_MS,
        api_error: live ? errorText(apiError) : "",
      };
    } catch (snapshotError) {
      return unavailableBoard(apiError, snapshotError);
    }
  }

  global.DualMarketData = Object.freeze({ load });
})(window);
