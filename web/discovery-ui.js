// Automatic selection-board controls. This is deliberately separate from the
// legacy per-item watch UI so GitHub Pages can operate the collector through
// Render without needing a browser profile on the viewing computer.
(function () {
  "use strict";

  // app.js still supplies task, ranking and settings views.  This module owns
  // the homepage so that its renderer cannot be replaced by the legacy cards.
  window.CD_MONITOR_API = window.CD_MONITOR_API || {};
  window.CD_MONITOR_API.selectionBoardOwnsHome = true;

  const view = {
    board: null,
    dualMarketBoard: null,
    commands: [],
    referenceStatus: null,
    referenceObservations: [],
    referenceProfiles: null,
    referenceDirections: null,
    candidateDirections: [],
    selectionFeedbackStatus: null,
    selectionFeedback: [],
    filter: "all",
    query: "",
    advancedFilter: null,
    refreshing: false,
    liveApiBlocked: false,
    liveMode: false,
  };

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function cny(value) {
    if (value === null || value === undefined || value === "") return "--";
    const number = Number(value);
    return Number.isFinite(number)
      ? number.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + " CNY"
      : "--";
  }

  function jpy(value, exchangeRate) {
    if (value === null || value === undefined || value === "") return "--";
    const number = Number(value);
    const rate = Number(exchangeRate);
    if (!Number.isFinite(number)) return "--";
    const raw = number.toLocaleString("ja-JP", { maximumFractionDigits: 0 }) + " JPY";
    if (!Number.isFinite(rate) || rate <= 0) return raw + "（折合 CNY 待配置）";
    const converted = (number * rate).toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return raw + "（约 " + converted + " CNY）";
  }

  function displayJpyCnyRate() {
    const boardRate = Number(view.dualMarketBoard && view.dualMarketBoard.display_exchange_rate_cny_per_jpy);
    if (Number.isFinite(boardRate) && boardRate > 0) return boardRate;
    const configuredRate = Number(window.JPY_TO_CNY || window.JPY_RATE);
    return Number.isFinite(configuredRate) && configuredRate > 0 ? configuredRate : 0.046;
  }

  function percent(value) {
    const number = Number(value);
    return Number.isFinite(number) ? (number * 100).toFixed(0) + "%" : "--";
  }

  function typeLabel(type) {
    return type === "physical_game" ? "实体游戏" : "CD";
  }

  function timeLabel(value) {
    if (!value) return "尚未扫描";
    const date = new Date(String(value).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function setText(id, text) {
    const element = document.getElementById(id);
    if (element && element.textContent !== text) element.textContent = text;
  }

  function pauseReasonLabel(reason) {
    const labels = {
      consecutive_detail_access_blocks: "详情页访问受阻",
      detail_verification_rate_below_50_percent: "详情页验证率过低",
      source_url_duplicate_rate_above_5_percent: "搜索结果 URL 重复异常",
    };
    return labels[reason] || "采集质量需要检查";
  }

  function setDiscoveryStatus(text, state) {
    setText("discoveryStatusText", text);
    const element = document.getElementById("discoveryStatus");
    if (!element) return;
    ["idle", "running", "paused_quality"].forEach((name) => element.classList.remove(name));
    element.classList.add(state);
  }

  function renderDiscoveryStatus(summary) {
    if (view.dualMarketBoard && view.dualMarketBoard.collector) {
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
      if (view.dualMarketBoard.unavailable) {
        setDiscoveryStatus("双边证据流暂不可用 · 不展示旧机会卡", "paused_quality");
        return;
      }
      const collectorState = String(view.dualMarketBoard.collector.state || "paused");
      if (collectorState === "paused") {
        setDiscoveryStatus("采集已暂停 · 仅展示已保存的双边商品证据", "idle");
        return;
      }
    }
    const pools = Array.isArray(view.board && view.board.pools) ? view.board.pools : [];
    const pausedPool = pools.find((pool) => pool && pool.enabled && pool.capture_state === "paused_quality");
    if (pausedPool) {
      setDiscoveryStatus(
        "已自动暂停 · " + typeLabel(pausedPool.media_type) + " · " + pauseReasonLabel(pausedPool.pause_reason),
        "paused_quality",
      );
      return;
    }
    const xianyuLoginState = String(summary.xianyu_login_state || "").trim();
    const resaleReady = Number(summary.resale_ready_candidates) || 0;
    if (xianyuLoginState === "login_required") {
      const detailSuffix = resaleReady > 0
        ? " · 已核验 " + resaleReady + " 条挖煤姬详情"
        : "";
      setDiscoveryStatus("闲鱼需要扫码登录" + detailSuffix, "idle");
      return;
    }
    const freshDetails = Number(summary.fresh_source_details) || 0;
    const staleDetails = Number(summary.stale_source_details) || 0;
    if (resaleReady > 0) {
      setDiscoveryStatus(
        "已核验 " + resaleReady + " 条挖煤姬详情 · 等待闲鱼价格样本",
        "idle",
      );
      return;
    }
    if (freshDetails > 0) {
      setDiscoveryStatus(
        "已核验 " + freshDetails + " 条挖煤姬详情 · 等待下一轮闲鱼比价",
        "idle",
      );
      return;
    }
    if (staleDetails > 0) {
      setDiscoveryStatus(
        staleDetails + " 条挖煤姬详情等待重新核验 · 当前不显示利润卡",
        "idle",
      );
      return;
    }
    if (summary.last_scan_at) {
      setDiscoveryStatus("已同步 · " + timeLabel(summary.last_scan_at), "running");
      return;
    }
    setDiscoveryStatus("等待本机采集", "idle");
  }

  function setCommandMessage(text, error) {
    const element = document.getElementById("discoveryCommandStatus");
    if (!element) return;
    element.textContent = text;
    element.classList.toggle("error", Boolean(error));
  }

  function apiGet(path) {
    if (view.liveApiBlocked) {
      return Promise.reject(new Error("viewer access token is unavailable"));
    }
    if (typeof window.getJson === "function") return window.getJson(path);
    return fetch(path, { cache: "no-store", credentials: "include" }).then((response) => {
      if (!response.ok) throw new Error("GET " + path + " -> " + response.status);
      return response.json();
    });
  }

  function apiPost(path, payload) {
    if (view.liveApiBlocked) {
      return Promise.reject(new Error("public snapshot mode is read-only"));
    }
    if (typeof window.postJson === "function") return window.postJson(path, payload);
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
      credentials: "include",
    }).then(async (response) => {
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || ("POST " + path + " -> " + response.status));
      return body;
    });
  }

  function toLegacyOpportunity(item) {
    return {
      ...item,
      item_title: item.item_title || item.candidate_title,
      purchase_price_jpy: item.purchase_price_jpy || item.source_price,
      xianyu_price_cny: item.xianyu_price_cny || item.xianyu_reference_price,
      xianyu_item_title: "闲鱼可比样本",
      availability: item.availability || "available",
    };
  }

  function renderKpis(summary) {
    const dualSummary = view.dualMarketBoard && view.dualMarketBoard.summary;
    if (dualSummary) {
      const eligibleItems = Array.isArray(view.dualMarketBoard.eligible) ? view.dualMarketBoard.eligible : [];
      const evaluatedCount = Number(dualSummary.evaluated_count) || 0;
      const eligibleCount = Number(dualSummary.eligible_count) || 0;
      const belowMarginCount = Number(dualSummary.below_margin_count) || 0;
      const costPendingCount = Number(dualSummary.cost_pending_count) || 0;
      const profits = eligibleItems.map((item) => Number(item.calculation && item.calculation.expected_profit_cny) || 0);
      setText("kpiToday", String(eligibleCount));
      setText("kpiProfit", cny(profits.reduce((total, value) => total + value, 0)));
      setText("kpiMax", cny(profits.reduce((maximum, value) => Math.max(maximum, value), 0)));
      setText("kpiHitRate", (evaluatedCount ? Math.round(eligibleCount / evaluatedCount * 100) : 0) + "%");
      setText("funnelEvaluated", String(evaluatedCount));
      setText("funnelCostPending", String(costPendingCount));
      setText("funnelBelowMargin", String(belowMarginCount));
      setText("funnelEligible", String(eligibleCount));
      renderDiscoveryStatus(summary);
      return;
    }
    const items = Array.isArray(view.board && view.board.opportunities) ? view.board.opportunities : [];
    const activeCandidates = Number(summary.active_candidates) || 0;
    const freshSourceDetails = Number(summary.fresh_source_details);
    const comparisonCandidates = Number.isFinite(freshSourceDetails)
      ? freshSourceDetails
      : activeCandidates;
    const totalExpectedProfit = Number(summary.total_expected_profit);
    const expectedProfit = Number.isFinite(totalExpectedProfit)
      ? totalExpectedProfit
      : items.reduce((total, item) => total + (Number(item.expected_profit) || 0), 0);
    const highestExpectedProfit = Number(summary.highest_expected_profit);
    const highestProfit = Number.isFinite(highestExpectedProfit)
      ? highestExpectedProfit
      : items.reduce((maximum, item) => Math.max(maximum, Number(item.expected_profit) || 0), 0);
    const hitRate = comparisonCandidates > 0
      ? Math.min(100, Math.round((Number(summary.active_opportunities) || items.length) / comparisonCandidates * 100))
      : 0;
    setText("kpiToday", String(Number(summary.active_opportunities) || items.length || 0));
    setText("kpiProfit", cny(expectedProfit));
    setText("kpiMax", cny(highestProfit));
    setText("kpiHitRate", hitRate + "%");
    renderDiscoveryStatus(summary);
  }

  function referenceStateLabel(state) {
    const labels = {
      found: "已找到",
      price_unfavorable: "信息待复核",
      not_currently_listed: "当前未见",
      login_required: "等待登录",
      blocked: "受阻",
    };
    return labels[state] || "待复核";
  }

  function referenceMissingEvidenceLabel(value) {
    const labels = {
      "barcode": "条码",
      "xianyu:market_observation": "闲鱼市场观察",
      "wameiji:market_observation": "挖煤姬市场观察",
    };
    return labels[String(value)] || "其他待核验信息";
  }

  function latestMarketLabel(coverage) {
    const safeCoverage = coverage && typeof coverage === "object" ? coverage : {};
    const states = safeCoverage.states && typeof safeCoverage.states === "object"
      ? safeCoverage.states
      : {};
    const count = (value) => Math.max(0, Number(value) || 0);
    const covered = count(safeCoverage.covered_product_count);
    const found = count(states.found);
    const unobserved = count(safeCoverage.unobserved_product_count);
    return "覆盖 " + covered + " · 已找到 " + found + " · 待核验 " + unobserved;
  }

  function renderReferenceMemory() {
    const status = view.referenceStatus;
    const stateElement = document.getElementById("referenceMemoryState");
    const directionsElement = document.getElementById("referenceDirectionList");
    const observationsElement = document.getElementById("referenceObservationList");
    const profilesElement = document.getElementById("referenceProfileList");
    if (!status) {
      setText("referenceProductCount", "--");
      setText("referenceSampleCount", "--");
      setText("referenceBarcodeCount", "--");
      setText("referenceObservationCount", "--");
      setText("referenceXianyuLatest", "--");
      setText("referenceWameijiLatest", "--");
      if (stateElement) {
        stateElement.className = "status idle";
        stateElement.textContent = "暂未读取";
      }
      if (observationsElement) {
        observationsElement.innerHTML = '<div class="empty-state">参考库暂不可用；不影响当前机会流。</div>';
      }
      if (profilesElement) {
        profilesElement.innerHTML = '<div class="empty-state">身份与待核验信息暂不可用</div>';
      }
      if (directionsElement) {
        directionsElement.innerHTML = '<div class="empty-state">正样本方向证据暂不可用</div>';
      }
      return;
    }
    setText("referenceProductCount", String(Number(status.product_count) || 0));
    setText("referenceSampleCount", String(Number(status.sample_count) || 0));
    setText("referenceBarcodeCount", String(Number(status.barcode_sample_count) || 0));
    setText("referenceObservationCount", String(Number(status.market_observation_count) || 0));
    const latestMarketCoverage = status.latest_market_coverage && typeof status.latest_market_coverage === "object"
      ? status.latest_market_coverage
      : {};
    const xianyuCoverage = latestMarketCoverage.xianyu && typeof latestMarketCoverage.xianyu === "object"
      ? latestMarketCoverage.xianyu
      : {};
    const wameijiCoverage = latestMarketCoverage.wameiji && typeof latestMarketCoverage.wameiji === "object"
      ? latestMarketCoverage.wameiji
      : {};
    const xianyuStates = xianyuCoverage.states && typeof xianyuCoverage.states === "object"
      ? xianyuCoverage.states
      : {};
    setText("referenceXianyuLatest", latestMarketLabel(xianyuCoverage));
    setText("referenceWameijiLatest", latestMarketLabel(wameijiCoverage));
    const loginPending = Number(xianyuStates.login_required) || 0;
    if (stateElement) {
      stateElement.className = "status " + (loginPending > 0 ? "warn" : "ok");
      stateElement.textContent = loginPending > 0
        ? "闲鱼待登录 " + loginPending + " 条"
        : "本地记忆已就绪";
    }
    if (directionsElement) {
      const summary = view.referenceDirections;
      const directions = summary && Array.isArray(summary.directions) ? summary.directions : null;
      const referenceCount = Math.max(0, Number(summary && summary.reference_product_count) || 0);
      if (!directions) {
        directionsElement.innerHTML = '<div class="empty-state">正样本方向证据暂不可用</div>';
      } else if (!directions.length || !referenceCount) {
        directionsElement.innerHTML = '<div class="empty-state">尚无可展示的正样本方向证据；不会影响当前候选。</div>';
      } else {
        directionsElement.innerHTML = directions.slice(0, 3).map((item) => {
          const direction = item && typeof item === "object" ? item : {};
          const label = direction.label || "未命名方向";
          const covered = Math.max(0, Number(direction.reference_product_count) || 0);
          return [
            '<article class="reference-profile">',
              '<b>' + esc(label) + "</b>",
              '<p>认可样本覆盖 ' + esc(covered) + "/" + esc(referenceCount) + "</p>",
              '<p>正样本方向证据，仍需详情核验</p>',
            "</article>",
          ].join("");
        }).join("");
      }
    }
    if (profilesElement) {
      const profiles = view.referenceProfiles;
      if (!Array.isArray(profiles)) {
        profilesElement.innerHTML = '<div class="empty-state">身份与待核验信息暂不可用</div>';
      } else if (!profiles.length) {
        profilesElement.innerHTML = '<div class="empty-state">尚无参考身份档案；导入样本不会自动联网或触发采购。</div>';
      } else {
        profilesElement.innerHTML = profiles.slice(0, 3).map((item) => {
          const profile = item && typeof item === "object" ? item : {};
          const identity = profile.stable_key || profile.barcode || "未记录身份";
          const sampleCount = Math.max(0, Number(profile.sample_count) || 0);
          const catalogNumbers = Array.isArray(profile.catalog_numbers) ? profile.catalog_numbers : [];
          const catalogText = catalogNumbers.length
            ? " · 品番：" + catalogNumbers.map((value) => String(value)).join("、")
            : "";
          const markets = profile.markets && typeof profile.markets === "object" ? profile.markets : {};
          const xianyuState = markets.xianyu && typeof markets.xianyu === "object"
            ? markets.xianyu.observation_state
            : "";
          const wameijiState = markets.wameiji && typeof markets.wameiji === "object"
            ? markets.wameiji.observation_state
            : "";
          const missingEvidence = Array.isArray(profile.missing_evidence) ? profile.missing_evidence : [];
          const missingText = missingEvidence.length
            ? missingEvidence.map(referenceMissingEvidenceLabel).join("、")
            : "无";
          return [
            '<article class="reference-profile">',
              '<b>身份：' + esc(identity) + "</b>",
              '<p>样本：' + esc(sampleCount) + esc(catalogText) + "</p>",
              '<p>当前市场：闲鱼 ' + esc(referenceStateLabel(xianyuState)) + " · 挖煤姬 " + esc(referenceStateLabel(wameijiState)) + "</p>",
              '<p>仍待确认：' + esc(missingText) + "</p>",
            "</article>",
          ].join("");
        }).join("");
      }
    }
    if (!observationsElement) return;
    const items = Array.isArray(view.referenceObservations) ? view.referenceObservations : [];
    if (!items.length) {
      observationsElement.innerHTML = '<div class="empty-state">尚无市场观察。导入样本不会自动联网或触发采购。</div>';
      return;
    }
    observationsElement.innerHTML = items.slice(0, 3).map((item) => {
      const state = String(item.observation_state || "");
      return [
        '<article class="reference-observation">',
          '<span class="status ' + (state === "found" ? "ok" : (state === "login_required" ? "warn" : "idle")) + '">' + esc(referenceStateLabel(state)) + "</span>",
          '<div><b>' + esc(item.observed_title || "未命名参考") + "</b>",
          '<p>' + esc(item.market === "wameiji" ? "挖煤姬" : "闲鱼") + " · " + esc(timeLabel(item.observed_at)) + "</p></div>",
        "</article>",
      ].join("");
    }).join("");
  }

  function renderSelectionFeedback() {
    const element = document.getElementById("selectionFeedbackState");
    if (!element) return;
    const status = view.selectionFeedbackStatus;
    if (!status) {
      element.textContent = "偏好反馈暂不可用";
      element.className = "status warn";
      return;
    }
    const current = status.current_outcomes || {};
    const labeled = Number(status.labeled_candidate_count) || 0;
    const positive = (Number(current.keep) || 0) + (Number(current.source_pending) || 0);
    const notFit = Number(current.not_fit) || 0;
    if (status.state === "ready_for_evaluation") {
      element.textContent = "偏好证据可评估 · 正向 " + positive + " · 不符合 " + notFit;
      element.className = "status good";
      return;
    }
    element.textContent = "偏好反馈收集中 · 已标注 " + labeled + " / 30";
    element.className = "status blue";
  }

  function safeHttpUrl(value) {
    const url = String(value || "").trim();
    const absoluteUrl = url.startsWith("//") ? "https:" + url : url;
    return /^https?:\/\/[^\s]+$/i.test(absoluteUrl) ? absoluteUrl : "";
  }

  function usableProductImage(value) {
    const relative = String(value || "").trim();
    let url = safeHttpUrl(relative);
    if (!url && /^assets\/dual-market\/[A-Za-z0-9+._/-]+$/.test(relative) && !relative.includes("..")) {
      url = new URL(relative, document.baseURI).toString();
    }
    if (!url) return "";
    return /searchlist|placeholder|\/logo(?:[._/]|$)|sigmerchantimg\/logo|paypaay|mokaki\.cn\/sigimage\/icon/i.test(url) ? "" : url;
  }

  function liquidityChip(value) {
    if (value === "normal") return '<span class="status ok">流动性正常</span>';
    if (value === "thin") return '<span class="status warn">样本偏少</span>';
    if (value === "dead") return '<span class="status bad">无有效成交</span>';
    return '<span class="status idle">' + esc(value || "流动性待验证") + '</span>';
  }

  function decisionChip(value) {
    if (value === "strong_alert") return '<span class="status ok">强提醒</span>';
    if (value === "weak_alert") return '<span class="status blue">弱提醒</span>';
    if (value === "skip" || value === "reject") return '<span class="status idle">仅供复核</span>';
    return '<span class="status warn">' + esc(value || "待复核") + '</span>';
  }

  function thumbMarkup(imageUrl, lineOne, lineTwo) {
    const image = usableProductImage(imageUrl);
    if (image) return '<img src="' + esc(image) + '" alt="" loading="lazy" />';
    return '<span>' + esc(lineOne) + '<br />' + esc(lineTwo) + '</span>';
  }

  function sideMarkup(kind, href, body) {
    const safeHref = safeHttpUrl(href);
    if (!safeHref) return '<div class="side-product ' + kind + '">' + body + '</div>';
    const title = kind === "market" ? "打开挖煤姬商品详情页" : "打开闲鱼可比商品";
    return '<a class="side-product ' + kind + ' side-link" href="' + esc(safeHref) + '" target="_blank" rel="noopener" title="' + esc(title) + '">' + body + '</a>';
  }

  function candidateDirectionMarkup(candidateId) {
    if (!Number.isSafeInteger(candidateId) || candidateId <= 0) return "";
    const item = (Array.isArray(view.candidateDirections) ? view.candidateDirections : []).find(
      (candidate) => Number(candidate && candidate.candidate_id) === candidateId
    );
    if (!item || item.state !== "positive_direction_covered") return "";
    const directions = Array.isArray(item.directions) ? item.directions : [];
    const labels = directions
      .map((direction) => direction && direction.label ? String(direction.label) : "")
      .filter(Boolean);
    if (!labels.length) return "";
    return '<div class="reason reference-direction-evidence">正样本方向证据，仍需详情核验：' + esc(labels.join("、")) + '</div>';
  }

  function currentFeedbackOutcome(candidateId) {
    const feedback = (Array.isArray(view.selectionFeedback) ? view.selectionFeedback : []).find(
      (item) => Number(item && item.candidate_id) === candidateId
    );
    return feedback && feedback.outcome ? String(feedback.outcome) : "";
  }

  function selectionFeedbackMarkup(candidateId, currentOutcome) {
    if (!Number.isSafeInteger(candidateId) || candidateId <= 0) return "";
    return [
      '<div class="selection-feedback-actions" data-selection-feedback-candidate="' + esc(candidateId) + '">',
        '<span>产品方向：' + esc({ keep: "保留", source_pending: "供应待找", not_fit: "手动排除" }[currentOutcome] || "未标注") + '</span>',
        '<button type="button" data-selection-feedback="keep" data-candidate-id="' + esc(candidateId) + '">保留</button>',
        '<button type="button" data-selection-feedback="source_pending" data-candidate-id="' + esc(candidateId) + '">供应待找</button>',
        '<button type="button" data-selection-feedback="not_fit" data-candidate-id="' + esc(candidateId) + '">手动排除</button>',
      '</div>',
    ].join("");
  }

  function profitReadinessLabel(readiness) {
    const labels = {
      source_price_needed: "等待进货价",
      resale_price_needed: "等待闲鱼价",
      refresh_needed: "价格需复核",
      profit_pending: "等待利润核算",
      profit_ready: "已核实正利润",
    };
    return labels[String(readiness || "")] || "等待利润证据";
  }

  function selectableCandidateCard(candidate) {
    const candidateId = Number(candidate.candidate_id);
    const title = candidate.candidate_title || "未命名候选";
    const identity = candidate.catalog_no || candidate.jan || "待补品番 / JAN";
    const sourceUrl = safeHttpUrl(candidate.source_url);
    const directionMarkup = candidateDirectionMarkup(candidateId);
    const feedbackMarkup = selectionFeedbackMarkup(candidateId, currentFeedbackOutcome(candidateId));
    return [
      '<article class="op-card research-candidate-card" data-selectable-candidate-id="' + esc(candidateId || "") + '">',
        '<div class="research-candidate-product">',
          '<div class="thumb research-thumb">',
            thumbMarkup(candidate.image_url, "可选品", typeLabel(candidate.media_type)),
          '</div>',
          '<div class="product">',
            '<span class="tag research-tag">可选品</span>',
            '<span class="tag source-tag">' + esc(typeLabel(candidate.media_type)) + '</span>',
            '<h4>' + esc(title) + '</h4>',
            '<div class="desc">品番 / JAN：' + esc(identity) + '</div>',
          '</div>',
        '</div>',
        '<div class="analysis research-analysis">',
          '<div class="metric"><small>利润状态</small><strong>' + esc(profitReadinessLabel(candidate.profit_readiness)) + '</strong></div>',
          directionMarkup,
          '<div class="reason">暂缺价格不降级：无货、没有低价或证据过期都只会进入复核，仍保留在可选品池。</div>',
          feedbackMarkup,
        '</div>',
        sourceUrl
          ? '<a class="research-source-link" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener">查看来源详情</a>'
          : '<span class="research-source-pending">来源详情入口待补</span>',
      '</article>',
    ].join("");
  }

  function selectableCandidates() {
    const board = view.board || {};
    if (Array.isArray(board.selectable_candidates)) return board.selectable_candidates;
    return Array.isArray(board.research_candidates) ? board.research_candidates : [];
  }

  function appendSelectableCandidatePool(target) {
    if (view.filter !== "all") return;
    const candidates = selectableCandidates();
    if (!candidates.length) return;
    target.insertAdjacentHTML(
      "beforeend",
      '<section class="research-queue"><div class="research-queue-head"><h3>可选品池 · 等待利润核验</h3><p>暂缺价格不降级：这批候选都会保留，只有同款新鲜价格与成本齐全后才进入正利润机会流。</p></div>'
        + candidates.map(selectableCandidateCard).join("") + "</section>",
    );
  }

  function opportunityCard(item) {
    const sourceUrl = safeHttpUrl(item.url || item.source_url);
    const xianyuUrl = safeHttpUrl(item.xianyu_url);
    const title = item.item_title || item.candidate_title || "未命名候选";
    const reference = Number(item.xianyu_price_cny || item.xianyu_reference_price || 0);
    const purchasePrice = Number(item.purchase_price_jpy || item.source_price || 0);
    const expectedProfit = Number(item.expected_profit || 0);
    const sampleCount = Number(item.valid_xianyu_sample_count || 0);
    const sampleRows = Number(item.xianyu_sample_rows || sampleCount);
    const xianyuTitle = item.xianyu_item_title || "闲鱼可售参考";
    const catalogNo = item.catalog_no || item.jan || "待人工确认";
    const detailVerified = item.detail_verified === true || Number(item.detail_verified) === 1;
    const candidateId = Number(item.candidate_id);
    const directionMarkup = candidateDirectionMarkup(candidateId);
    const xianyuSide = [
      '<div class="thumb xianyu-thumb">',
        thumbMarkup(item.xianyu_image_url, "闲鱼可售样本", "有效样本 " + sampleCount + " 条"),
      '</div>',
      '<div class="product">',
        '<span class="tag xianyu-tag">闲鱼 · 销售侧</span>',
        liquidityChip(item.liquidity_status),
        '<h4>' + esc(xianyuTitle) + '</h4>',
        '<div class="price">' + esc(cny(reference)) + '</div>',
        '<div class="desc">可比样本 ' + esc(sampleCount) + ' 条 · 已记录样本 ' + esc(sampleRows) + ' 条</div>',
      '</div>',
    ].join("");
    const wameijiSide = [
      '<div class="thumb market-thumb">',
        thumbMarkup(item.image_url, "挖煤姬详情页", detailVerified ? "价格已核验" : "等待核验"),
      '</div>',
      '<div class="product">',
        '<span class="tag hot">挖煤姬 · 进货侧</span>',
        '<span class="tag source-tag">' + esc(typeLabel(item.media_type)) + '</span>',
        '<h4>' + esc(title) + '</h4>',
        '<div class="price">' + esc(jpy(purchasePrice, displayJpyCnyRate())) + '</div>',
        '<div class="desc">品番 / JAN：' + esc(catalogNo) + (sourceUrl ? ' · 点击进入商品详情' : '') + '</div>',
      '</div>',
    ].join("");
    const reason = detailVerified
      ? "右侧挖煤姬价格来自已核验的商品详情页；左侧是 " + sampleCount + " 条有效闲鱼可比样本的参考价。下单前仍需人工核对版本、特典和品相。"
      : "来源详情仍待核验，当前价格不应作为进货依据。";
    const currentOutcome = currentFeedbackOutcome(candidateId);
    const feedbackMarkup = Number.isSafeInteger(candidateId) && candidateId > 0
      ? [
        '<div class="selection-feedback-actions" data-selection-feedback-candidate="' + esc(candidateId) + '">',
          '<span>产品方向：' + esc({ keep: "保留", source_pending: "供应待找", not_fit: "手动排除" }[currentOutcome] || "未标注") + '</span>',
          '<button type="button" data-selection-feedback="keep" data-candidate-id="' + esc(candidateId) + '">保留</button>',
          '<button type="button" data-selection-feedback="source_pending" data-candidate-id="' + esc(candidateId) + '">供应待找</button>',
          '<button type="button" data-selection-feedback="not_fit" data-candidate-id="' + esc(candidateId) + '">手动排除</button>',
        '</div>',
      ].join("")
      : "";
    return [
      '<article class="op-card discovery-op-card" data-discovery-opportunity-id="' + esc(item.id || "") + '">',
        sideMarkup("xianyu", xianyuUrl, xianyuSide),
        '<div class="analysis">',
          '<div class="comparison-rail"><span>挖煤姬进货（JPY） → 闲鱼国内销售（CNY）</span></div>',
          '<div class="grid2">',
            '<div class="metric"><small>闲鱼预计净利（CNY）</small><strong>' + esc(cny(expectedProfit)) + '</strong></div>',
            '<div class="metric"><small>闲鱼销售利润率</small><strong>' + esc(percent(item.net_margin)) + '</strong></div>',
            '<div class="metric"><small>匹配度</small><strong>' + esc(percent(item.match_confidence)) + '</strong></div>',
            '<div class="metric"><small>利润状态</small><strong><span class="status ok">净利润为正</span></strong></div>',
          '</div>',
           '<p class="comparison-catalog">品番 / JAN：' + esc(catalogNo) + (item.edition ? ' · ' + esc(item.edition) : '') + '</p>',
           directionMarkup,
           '<div class="reason">' + esc(reason) + '</div>',
           feedbackMarkup,
         '</div>',
        sideMarkup("market", sourceUrl, wameijiSide),
      '</article>',
    ].join("");
  }

  function dualEvidenceLabel(item) {
    if (!item) return "等待另一侧证据";
    return item.evidence_level === "detail_verified" ? "详情已核验" : "搜索挂牌价样本";
  }

  function dualSide(kind, item, exchangeRate) {
    const isXianyu = kind === "xianyu";
    const tag = isXianyu ? "闲鱼 · 销售侧" : "挖煤姬 · 进货侧";
    const image = item && item.image_url;
    const price = isXianyu
      ? cny(item && item.price)
      : jpy(item && item.price, exchangeRate || displayJpyCnyRate());
    const body = [
      '<div class="thumb ' + (isXianyu ? "xianyu-thumb" : "market-thumb") + '">',
        thumbMarkup(image, tag, dualEvidenceLabel(item)),
      '</div>',
      '<div class="product">',
        '<span class="tag ' + (isXianyu ? "xianyu-tag" : "hot") + '">' + esc(tag) + '</span>',
        '<span class="status ' + (item && item.evidence_level === "detail_verified" ? "ok" : "warn") + '">' + esc(dualEvidenceLabel(item)) + '</span>',
        '<h4>' + esc(item && item.title || "未获取商品") + '</h4>',
        '<div class="price">' + esc(price) + '</div>',
        '<div class="desc">' + esc(item && item.condition_group || "品相待确认") + ' · ' + esc(timeLabel(item && item.captured_at)) + '</div>',
      '</div>',
    ].join("");
    return sideMarkup(isXianyu ? "xianyu" : "market", item && item.url, body);
  }

  function costBreakdownMarkup(cost_breakdown) {
    const exchangeRate = Number(cost_breakdown.wameiji_exchange_rate_cny_per_jpy);
    const rateText = Number.isFinite(exchangeRate) && exchangeRate > 0
      ? "1 JPY ≈ " + exchangeRate.toFixed(4) + " CNY"
      : "汇率证据缺失";
    return [
      '<details class="cost-breakdown">',
        '<summary>展开完整成本 · 默认成本：头程 15 CNY · 国内包邮 5 CNY · 包材 2 CNY</summary>',
        '<div class="cost-breakdown-grid">',
          '<span>挖煤姬商品</span><b>' + esc(jpy(cost_breakdown.wameiji_item_jpy, exchangeRate)) + '</b>',
          '<span>日本国内运费</span><b>' + esc(jpy(cost_breakdown.wameiji_domestic_shipping_jpy, exchangeRate)) + '</b>',
          '<span>挖煤姬代购费</span><b>' + esc(jpy(cost_breakdown.wameiji_proxy_fee_jpy, exchangeRate)) + '</b>',
          '<span>换算汇率</span><b>' + esc(rateText) + '</b>',
          '<span>挖煤姬采购折合</span><b>' + esc(cny(cost_breakdown.wameiji_purchase_cny)) + '</b>',
          '<span>日本至国内头程</span><b>' + esc(cny(cost_breakdown.international_shipping_cny)) + '</b>',
          '<span>国内包邮运费</span><b>' + esc(cny(cost_breakdown.china_postage_cny)) + '</b>',
          '<span>包装材料</span><b>' + esc(cny(cost_breakdown.packaging_cny)) + '</b>',
          '<span>税费 / 售后 / 风险预留</span><b>' + esc(cny(
            (Number(cost_breakdown.tax_cny) || 0)
            + (Number(cost_breakdown.after_sale_reserve_cny) || 0)
            + (Number(cost_breakdown.risk_reserve_cny) || 0),
          )) + '</b>',
          '<span>闲鱼手续费（1.6%）</span><b>' + esc(cny(cost_breakdown.xianyu_seller_fee_cny)) + '</b>',
          '<span>挖煤姬落地成本</span><b>' + esc(cny(cost_breakdown.landed_cost_cny)) + '</b>',
          '<span>闲鱼到手净利</span><b>' + esc(cny(cost_breakdown.net_profit_cny)) + '</b>',
        '</div>',
      '</details>',
    ].join("");
  }

  function eligibleComparisonCard(item) {
    const calculation = item.calculation || {};
    const cost_breakdown = calculation.cost_breakdown || {};
    const exchangeRate = cost_breakdown.wameiji_exchange_rate_cny_per_jpy;
    const xianyuSide = dualSide("xianyu", {
      ...item.xianyu,
      image_url: item.xianyu.image_url,
    }, exchangeRate);
    const wameijiSide = dualSide("wameiji", {
      ...item.wameiji,
      image_url: item.wameiji.image_url,
    }, exchangeRate);
    const evidence = "匹配证据：同款键完全一致；两侧品相均为 "
      + String(item.xianyu.condition_group || "待核验")
      + "；闲鱼为搜索挂牌样本，挖煤姬为商品详情已核验。";
    return [
      '<article class="op-card discovery-op-card dual-market-card" data-dual-market-comparison-id="' + esc(item.comparison_id || "") + '">',
        xianyuSide,
        '<div class="analysis">',
          '<div class="comparison-rail"><span>挖煤姬进货（JPY） → 闲鱼国内销售（CNY）</span></div>',
          '<span class="status ok qualified-chip">净利润为正</span>',
          '<div class="grid2">',
            '<div class="metric"><small>闲鱼销售价（CNY）</small><strong>' + esc(cny(calculation.sale_price_cny)) + '</strong></div>',
            '<div class="metric"><small>挖煤姬落地成本（CNY）</small><strong>' + esc(cny(calculation.landed_cost_cny)) + '</strong></div>',
            '<div class="metric"><small>闲鱼预计净利（CNY）</small><strong>' + esc(cny(calculation.expected_profit_cny)) + '</strong></div>',
            '<div class="metric"><small>闲鱼销售利润率</small><strong>' + esc(percent(calculation.net_margin)) + '</strong></div>',
          '</div>',
          '<p class="comparison-catalog">同款键：' + esc(item.canonical_product_key) + '</p>',
          '<div class="match-evidence"><b>匹配证据</b><span>' + esc(evidence) + '</span></div>',
          costBreakdownMarkup(cost_breakdown),
          '<div class="reason recheck-warning">利润 = 闲鱼销售价（CNY） - 闲鱼销售费用 - 挖煤姬采购与落地成本（折合 CNY）。这是保存证据快照，不代表当前仍可买；下单前重新核验价格、库存、版本、特典、品相与闲鱼真实可售价。</div>',
        '</div>',
        wameijiSide,
      '</article>',
    ].join("");
  }

  function dualMarketSearchText(item) {
    return [
      item.canonical_product_key,
      item.xianyu && item.xianyu.title,
      item.wameiji && item.wameiji.title,
    ].filter(Boolean).join(" ").toLowerCase();
  }

  function renderDualMarketFeed(target) {
    const board = view.dualMarketBoard || {};
    if (board.unavailable) {
      target.innerHTML = '<div class="empty-state">双边证据流暂不可用；为避免把旧的全局样本误当成同款，本页不展示旧机会卡。</div>';
      appendSelectableCandidatePool(target);
      return;
    }
    const query = String(view.query || view.advancedFilter && view.advancedFilter.q || "").trim().toLowerCase();
    let eligible = Array.isArray(board.eligible) ? board.eligible.slice() : [];
    if (query) eligible = eligible.filter((item) => dualMarketSearchText(item).includes(query));
    eligible.sort((left, right) => (
      (Number(right.calculation && right.calculation.expected_profit_cny) || 0)
      - (Number(left.calculation && left.calculation.expected_profit_cny) || 0)
    ));
    if (eligible.length) {
      target.innerHTML = eligible.map(eligibleComparisonCard).join("");
      appendSelectableCandidatePool(target);
      return;
    }
    const summary = board.summary || {};
    const evaluated = Number(summary.evaluated_count) || 0;
    const pending = Number(summary.cost_pending_count) || 0;
    const below = Number(summary.below_margin_count) || 0;
    target.innerHTML = '<div class="empty-state">已评估 ' + esc(evaluated)
      + ' 条：成本待补 ' + esc(pending) + ' 条，当前未形成正利润 ' + esc(below)
      + ' 条；当前没有净利润为正且成本证据完整的机会。页面不会用旧样本补卡。</div>';
    appendSelectableCandidatePool(target);
  }

  function itemSearchText(item) {
    return [
      item.item_title,
      item.candidate_title,
      item.xianyu_item_title,
      item.catalog_no,
      item.jan,
      item.edition,
    ].filter(Boolean).join(" ").toLowerCase();
  }

  function applyAdvancedFilter(items) {
    const filter = view.advancedFilter || {};
    const query = String(view.query || filter.q || "").trim().toLowerCase();
    return items.filter((item) => {
      if (query && !itemSearchText(item).includes(query)) return false;
      if (filter.decision && filter.decision !== "all" && item.decision !== filter.decision) return false;
      if (filter.min_margin && (Number(item.net_margin) || 0) * 100 < Number(filter.min_margin)) return false;
      if (filter.min_diff && (Number(item.expected_profit) || 0) < Number(filter.min_diff)) return false;
      return true;
    });
  }

  function emptyFeedMessage(summary, allItemCount) {
    if (allItemCount > 0) {
      return "这个筛选暂时没有通过门槛的机会。调整筛选条件后可查看其他已核验卡片。";
    }
    const xianyuLoginState = String(summary.xianyu_login_state || "").trim();
    const resaleReady = Number(summary.resale_ready_candidates) || 0;
    if (xianyuLoginState === "login_required") {
      const detailCount = resaleReady || Number(summary.fresh_source_details) || 0;
      return detailCount > 0
        ? "闲鱼需要扫码登录；已核验 " + detailCount + " 条挖煤姬商品详情，登录后才会采集价格样本并计算利润。"
        : "闲鱼需要扫码登录；登录后采集器才会开始价格比对。";
    }
    if (resaleReady > 0) {
      return "已核验 " + resaleReady + " 条挖煤姬商品详情，等待闲鱼价格样本后才计算并展示利润。";
    }
    const freshDetails = Number(summary.fresh_source_details) || 0;
    if (freshDetails > 0) {
      return "已核验 " + freshDetails + " 条挖煤姬商品详情，等待下一轮闲鱼价格复查后再展示利润卡。";
    }
    const staleDetails = Number(summary.stale_source_details) || 0;
    if (staleDetails > 0) {
      return staleDetails + " 条挖煤姬采购详情已过期，正在等待重新打开详情页核验；当前不显示过期利润。";
    }
    return "暂时没有通过两侧详情核验和利润门槛的机会。";
  }

  function renderFeed() {
    const target = document.getElementById("homeFeed");
    if (!target || !view.board) return;
    if (view.dualMarketBoard) {
      renderDualMarketFeed(target);
      return;
    }
    const allItems = Array.isArray(view.board.opportunities) ? view.board.opportunities.slice() : [];
    let items = allItems;
    if (view.filter === "match") {
      items = items.filter((item) => Number(item.match_confidence) >= 0.8);
    } else if (view.filter === "low-risk") {
      items = items.filter((item) => (
        (item.detail_verified === true || Number(item.detail_verified) === 1)
        && item.liquidity_status === "normal"
      ));
    } else if (view.filter !== "all") {
      items = items.filter((item) => item.media_type === view.filter);
    }
    items = applyAdvancedFilter(items);
    items.sort((left, right) => (
      (Number(right.expected_profit) || 0) - (Number(left.expected_profit) || 0)
      || (Number(right.match_confidence) || 0) - (Number(left.match_confidence) || 0)
    ));
    const poolCandidates = view.filter === "all"
      ? selectableCandidates()
      : [];
    if (!items.length && !poolCandidates.length) {
      const summary = view.board.summary || {};
      target.innerHTML = '<div class="empty-state">' + esc(emptyFeedMessage(summary, allItems.length)) + '</div>';
      return;
    }
    target.innerHTML = [
      items.map(opportunityCard).join(""),
      poolCandidates.length
        ? '<section class="research-queue"><div class="research-queue-head"><h3>可选品池 · 等待利润核验</h3><p>暂缺价格不降级：这批候选都会保留，只有同款新鲜价格与成本齐全后才进入正利润机会流。</p></div>'
          + poolCandidates.map(selectableCandidateCard).join("") + '</section>'
        : "",
    ].join("");
  }

  function setSelectionBoardQuery(query) {
    view.query = String(query || "").trim();
    renderFeed();
  }

  function applySelectionBoardFilters(filter) {
    view.advancedFilter = {
      q: String(filter && filter.q || "").trim(),
      decision: String(filter && filter.decision || "all"),
      min_margin: String(filter && filter.min_margin || "").trim(),
      min_diff: String(filter && filter.min_diff || "").trim(),
    };
    view.query = view.advancedFilter.q;
    renderFeed();
  }

  window.CD_MONITOR_API.setSelectionBoardQuery = setSelectionBoardQuery;
  window.CD_MONITOR_API.applySelectionBoardFilters = applySelectionBoardFilters;

  function poolCard(pool) {
    const id = Number(pool.id);
    const enabledKeywords = (Array.isArray(pool.keywords) ? pool.keywords : [])
      .filter((keyword) => keyword.enabled)
      .map((keyword) => keyword.keyword)
      .join("\n");
    const enabled = Boolean(pool.enabled);
    const paused = enabled && pool.capture_state === "paused_quality";
    const stateClass = paused ? "bad" : (enabled ? "ok" : "idle");
    const stateLabel = paused ? "已自动暂停" : (enabled ? "运行中" : "已停用");
    const stateDetail = paused ? " · 原因：" + pauseReasonLabel(pool.pause_reason) : "";
    const marginPercent = Math.round((Number(pool.min_margin) || 0) * 100);
    return [
      '<article class="discovery-pool" data-pool-id="' + esc(id) + '">',
        '<div class="discovery-pool-title">',
          '<div><h4>' + esc(pool.name) + '</h4><p>' + esc(typeLabel(pool.media_type)) + ' · 上次扫描：' + esc(timeLabel(pool.last_scanned_at)) + esc(stateDetail) + '</p></div>',
          '<span class="status ' + stateClass + '">' + stateLabel + "</span>",
        '</div>',
        '<div class="discovery-pool-actions">',
          (paused
            ? '<button class="primary" type="button" data-discovery-action="resume-pool">恢复此池</button>'
            : '<button class="primary" type="button" data-discovery-action="scan" data-pool-id="' + esc(id) + '">立即扫描此池</button>'),
          '<button type="button" data-discovery-action="save-pool">保存采集规则</button>',
        '</div>',
        '<div class="discovery-rule-grid">',
          '<label><span>启用采集</span><input data-discovery-field="enabled" type="checkbox" ' + (enabled ? "checked" : "") + ' /></label>',
          '<label><span>扫描间隔（分钟）</span><input data-discovery-field="scan_interval_minutes" type="number" min="5" max="1440" value="' + esc(pool.scan_interval_minutes) + '" /></label>',
          '<label><span>每轮关键词数</span><input data-discovery-field="keyword_budget" type="number" min="1" max="10" value="' + esc(pool.keyword_budget) + '" /></label>',
          '<label><span>每轮详情页上限</span><input data-discovery-field="detail_budget" type="number" min="0" max="10" value="' + esc(pool.detail_budget ?? 4) + '" /></label>',
          '<label><span>每轮闲鱼商品上限</span><input data-discovery-field="xianyu_query_budget" type="number" min="0" max="10" value="' + esc(pool.xianyu_query_budget ?? 3) + '" /></label>',
          '<label><span>最低预估净利（CNY）</span><input data-discovery-field="min_profit_cny" type="number" min="0" value="' + esc(pool.min_profit_cny) + '" /></label>',
          '<label><span>最低利润率（%）</span><input data-discovery-field="min_margin_percent" type="number" min="0" max="100" value="' + esc(marginPercent) + '" /></label>',
        '</div>',
        '<label class="discovery-keywords"><span>发现关键词（每行一个；保存后替换当前启用词）</span><textarea data-discovery-keywords rows="4" placeholder="例如：初回限定盤">' + esc(enabledKeywords) + '</textarea></label>',
        '<button type="button" data-discovery-action="save-keywords">保存关键词</button>',
      '</article>',
    ].join("");
  }

  function renderPools() {
    const target = document.getElementById("discoveryPoolControls");
    if (!target || !view.board) return;
    const pools = Array.isArray(view.board.pools) ? view.board.pools : [];
    target.innerHTML = pools.length ? pools.map(poolCard).join("") : '<div class="empty-state">尚未建立选品池。</div>';
  }

  function commandLabel(commands) {
    const command = Array.isArray(commands) ? commands[0] : null;
    if (!command) return "采集电脑每分钟接收一次命令";
    const labels = { pending: "等待本机接收", accepted: "本机已接收", running: "本机执行中", completed: "已完成", human_required: "需要本机人工处理", failed: "执行失败" };
    return "最近命令：" + (labels[command.status] || command.status || "未知") + " · " + timeLabel(command.updated_at || command.created_at);
  }

  async function refreshBoard() {
    if (view.refreshing) return;
    view.refreshing = true;
    try {
      const emptyBoard = { summary: {}, pools: [], opportunities: [], selectable_candidates: [], research_candidates: [] };
      const [board, commandPayload, dualMarketBoard, referenceStatus, referenceObservationPayload, referenceProfilePayload, referenceDirectionPayload, candidateDirectionPayload, selectionFeedbackStatus, selectionFeedbackPayload] = await Promise.all([
        apiGet("/api/discovery/board").catch(() => emptyBoard),
        apiGet("/api/discovery/commands").catch(() => ({ items: [] })),
        window.DualMarketData.load({ apiGet, live: view.liveMode }),
        apiGet("/api/reference-memory/status").catch(() => null),
        apiGet("/api/reference-memory/observations?limit=3").catch(() => null),
        apiGet("/api/reference-memory/profiles?limit=3").catch(() => null),
        apiGet("/api/reference-memory/directions").catch(() => null),
        apiGet("/api/reference-memory/candidate-directions?limit=200").catch(() => null),
        apiGet("/api/selection-feedback/status").catch(() => null),
        apiGet("/api/selection-feedback?current=1&limit=200").catch(() => null),
      ]);
      view.board = board || { summary: {}, pools: [], opportunities: [], selectable_candidates: [], research_candidates: [] };
      view.dualMarketBoard = dualMarketBoard;
      view.commands = (commandPayload && commandPayload.items) || [];
      view.referenceStatus = referenceStatus;
      view.referenceObservations = (referenceObservationPayload && referenceObservationPayload.items) || [];
      view.referenceProfiles = referenceProfilePayload && Array.isArray(referenceProfilePayload.items)
        ? referenceProfilePayload.items
        : null;
      view.referenceDirections = referenceDirectionPayload;
      view.candidateDirections = candidateDirectionPayload && Array.isArray(candidateDirectionPayload.items)
        ? candidateDirectionPayload.items
        : [];
      view.selectionFeedbackStatus = selectionFeedbackStatus;
      view.selectionFeedback = (selectionFeedbackPayload && selectionFeedbackPayload.items) || [];
      if (window.state) {
        window.state.opportunities = (view.board.opportunities || []).map(toLegacyOpportunity);
        window.state.totalOpportunities = window.state.opportunities.length;
      }
      renderKpis(view.board.summary || {});
      renderReferenceMemory();
      renderSelectionFeedback();
      renderFeed();
      renderPools();
      setCommandMessage(
        view.liveApiBlocked
          ? "Pages 公开快照模式 · 无需访问令牌 · 实时操作仅限已配置的本机"
          : commandLabel(view.commands),
      );
    } catch (error) {
      setCommandMessage("读取选品广场失败：" + error.message, true);
      const target = document.getElementById("homeFeed");
      if (target) target.innerHTML = '<div class="empty-state">无法连接选品广场：' + esc(error.message) + '</div>';
    } finally {
      view.refreshing = false;
    }
  }

  async function queueScan(poolId) {
    if (view.dualMarketBoard && view.dualMarketBoard.collector
      && view.dualMarketBoard.collector.state === "paused") {
      setCommandMessage("采集已暂停，未提交扫描命令。", true);
      return;
    }
    const button = document.querySelector('[data-discovery-action="scan"][data-pool-id="' + String(poolId || "") + '"]')
      || document.getElementById("scanDiscoveryNowBtn")
      || document.getElementById("sideScanDiscoveryBtn");
    if (button) button.disabled = true;
    try {
      const payload = { command_type: "scan_now" };
      if (poolId !== null && poolId !== undefined) payload.pool_id = Number(poolId);
      await apiPost("/api/discovery/commands", payload);
      setCommandMessage("扫描命令已提交；采集电脑会在下一分钟内接收。结果同步后会自动出现在上方。", false);
      setTimeout(refreshBoard, 1200);
    } catch (error) {
      setCommandMessage("提交扫描失败：" + error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function numberValue(root, field, fallback, minimum, maximum) {
    const input = root.querySelector('[data-discovery-field="' + field + '"]');
    const number = Number(input && input.value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(maximum, Math.max(minimum, number));
  }

  async function savePool(root) {
    const poolId = Number(root.getAttribute("data-pool-id"));
    const updates = {
      enabled: Boolean(root.querySelector('[data-discovery-field="enabled"]')?.checked),
      scan_interval_minutes: numberValue(root, "scan_interval_minutes", 30, 5, 1440),
      keyword_budget: numberValue(root, "keyword_budget", 2, 1, 10),
      detail_budget: numberValue(root, "detail_budget", 4, 0, 10),
      xianyu_query_budget: numberValue(root, "xianyu_query_budget", 3, 0, 10),
      min_profit_cny: numberValue(root, "min_profit_cny", 35, 0, 100000),
      min_margin: numberValue(root, "min_margin_percent", 25, 0, 100) / 100,
    };
    try {
      await apiPost("/api/discovery/commands", { command_type: "set_pool", pool_id: poolId, updates });
      setCommandMessage("采集规则已提交；采集电脑收到后会应用。", false);
      setTimeout(refreshBoard, 1200);
    } catch (error) {
      setCommandMessage("保存采集规则失败：" + error.message, true);
    }
  }

  async function resumePool(root) {
    if (view.dualMarketBoard && view.dualMarketBoard.collector
      && view.dualMarketBoard.collector.state === "paused") {
      setCommandMessage("采集已暂停，未提交恢复命令。", true);
      return;
    }
    const poolId = Number(root.getAttribute("data-pool-id"));
    try {
      await apiPost("/api/discovery/commands", {
        command_type: "set_pool",
        pool_id: poolId,
        updates: { capture_state: "active" },
      });
      setCommandMessage("恢复命令已提交；采集电脑收到后会先按新的质量门槛运行。", false);
      setTimeout(refreshBoard, 1200);
    } catch (error) {
      setCommandMessage("恢复采集失败：" + error.message, true);
    }
  }

  async function saveKeywords(root) {
    const poolId = Number(root.getAttribute("data-pool-id"));
    const area = root.querySelector("[data-discovery-keywords]");
    const keywords = String(area && area.value || "")
      .split(/\r?\n/)
      .map((keyword) => keyword.trim())
      .filter(Boolean)
      .slice(0, 30)
      .map((keyword) => ({ keyword, weight: 1, enabled: true }));
    try {
      await apiPost("/api/discovery/commands", { command_type: "set_keywords", pool_id: poolId, keywords });
      setCommandMessage("关键词已提交；它们会替换此池当前启用的发现词。", false);
      setTimeout(refreshBoard, 1200);
    } catch (error) {
      setCommandMessage("保存关键词失败：" + error.message, true);
    }
  }

  function bindControls() {
    document.querySelectorAll("[data-discovery-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll("[data-discovery-filter]").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        view.filter = button.getAttribute("data-discovery-filter") || "all";
        renderFeed();
      });
    });
    document.getElementById("scanDiscoveryNowBtn")?.addEventListener("click", () => queueScan(null));
    document.getElementById("sideScanDiscoveryBtn")?.addEventListener("click", () => queueScan(null));
    document.getElementById("refreshDiscoveryBoardBtn")?.addEventListener("click", refreshBoard);
    document.getElementById("discoveryRefreshFeedBtn")?.addEventListener("click", refreshBoard);
    document.getElementById("sidePoolSettingsBtn")?.addEventListener("click", () => {
      document.getElementById("discoveryPoolSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    document.getElementById("poolSettingsTopBtn")?.addEventListener("click", () => {
      document.getElementById("discoveryPoolSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    document.getElementById("legacyTaskBtn")?.addEventListener("click", () => {
      document.querySelector('[data-page="tasks"]')?.click();
    });
    document.getElementById("sideLegacyToolsBtn")?.addEventListener("click", () => {
      document.querySelector('[data-page="tasks"]')?.click();
    });
    document.getElementById("homeFeed")?.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-selection-feedback]");
      if (!button) return;
      const candidateId = Number(button.dataset.candidateId);
      const outcome = button.dataset.selectionFeedback;
      if (!Number.isSafeInteger(candidateId) || candidateId <= 0 || !outcome) return;
      button.disabled = true;
      try {
        await apiPost("/api/selection-feedback", {
          candidate_id: Number(button.dataset.candidateId),
          outcome: button.dataset.selectionFeedback,
        });
        setCommandMessage("产品偏好已记录；它不会改变当前采购价格或市场状态。", false);
        await refreshBoard();
      } catch (error) {
        setCommandMessage("记录产品偏好失败：" + error.message, true);
      } finally {
        button.disabled = false;
      }
    });
    document.getElementById("discoveryPoolControls")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-discovery-action]");
      if (!button) return;
      const root = button.closest("[data-pool-id]");
      if (!root) return;
      const action = button.getAttribute("data-discovery-action");
      if (action === "scan") queueScan(button.getAttribute("data-pool-id"));
      if (action === "resume-pool") resumePool(root);
      if (action === "save-pool") savePool(root);
      if (action === "save-keywords") saveKeywords(root);
    });
  }

  function keepBoardKpisVisible() {
    const stats = document.querySelector(".hero .stats");
    if (!stats || !window.MutationObserver) return;
    const observer = new MutationObserver(() => {
      if (view.board) renderKpis(view.board.summary || {});
    });
    observer.observe(stats, { childList: true, characterData: true, subtree: true });
  }

  document.addEventListener("DOMContentLoaded", () => {
    // app.js exposes its authenticated API helpers in its own DOM-ready
    // listener. Queue one tick so this module always uses the same Page token.
    setTimeout(async () => {
      const api = window.CD_MONITOR_API;
      const separateCollector = Boolean(
        api && typeof api.isSeparateCollectorApi === "function" && api.isSeparateCollectorApi(),
      );
      const explicitLive = new URLSearchParams(window.location.search).get("live") === "1";
      view.liveMode = !separateCollector || explicitLive;
      if (separateCollector && !explicitLive) {
        view.liveApiBlocked = true;
      } else if (api && typeof api.ensureViewerAccessToken === "function") {
        const accessReady = await api.ensureViewerAccessToken();
        if (!accessReady) {
          view.liveApiBlocked = true;
          setCommandMessage("需要 Render 访问令牌才能读取选品广场；填写后刷新此页即可继续。", true);
        }
      }
      bindControls();
      keepBoardKpisVisible();
      refreshBoard();
      // The legacy dashboard also renders its old KPIs during bootstrap.
      // Repaint after it settles so the first visible numbers always belong
      // to the automatic selection board.
      setTimeout(refreshBoard, 900);
      setInterval(refreshBoard, 60000);
    }, 0);
  });
})();
