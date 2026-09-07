// Automatic selection-board controls. This is deliberately separate from the
// legacy per-item watch UI so GitHub Pages can operate the collector through
// Render without needing a browser profile on the viewing computer.
(function () {
  "use strict";

  const view = { board: null, commands: [], filter: "all", refreshing: false };

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function cny(value) {
    const number = Number(value);
    return Number.isFinite(number) ? "¥" + number.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) : "--";
  }

  function jpy(value) {
    const number = Number(value);
    return Number.isFinite(number) ? "¥" + number.toLocaleString("ja-JP", { maximumFractionDigits: 0 }) : "--";
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

  function setCommandMessage(text, error) {
    const element = document.getElementById("discoveryCommandStatus");
    if (!element) return;
    element.textContent = text;
    element.classList.toggle("error", Boolean(error));
  }

  function apiGet(path) {
    if (typeof window.getJson === "function") return window.getJson(path);
    return fetch(path, { cache: "no-store", credentials: "include" }).then((response) => {
      if (!response.ok) throw new Error("GET " + path + " -> " + response.status);
      return response.json();
    });
  }

  function apiPost(path, payload) {
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
    setText("kpiToday", String(Number(summary.active_opportunities) || 0));
    setText("kpiProfit", cny(summary.highest_expected_profit));
    setText("kpiMax", String(Number(summary.active_candidates) || 0));
    setText("kpiHitRate", timeLabel(summary.last_scan_at));
    setText("discoveryStatusText", summary.last_scan_at ? "已同步 · " + timeLabel(summary.last_scan_at) : "等待本机采集");
  }

  function opportunityCard(item) {
    const sourceUrl = String(item.url || item.source_url || "").trim();
    const safeSourceUrl = /^https?:\/\//i.test(sourceUrl) ? sourceUrl : "";
    const title = item.item_title || item.candidate_title || "未命名候选";
    const reference = Number(item.xianyu_price_cny || item.xianyu_reference_price || 0);
    const detailVerified = item.detail_verified === true || Number(item.detail_verified) === 1;
    const card = [
      '<article class="discovery-card">',
        '<div class="discovery-card-head">',
          '<span class="tag hot">挖煤姬 · ' + esc(typeLabel(item.media_type)) + '</span>',
          '<span class="discovery-verification ' + (detailVerified ? "verified" : "pending") + '">' + (detailVerified ? "详情已核验" : "详情待核验") + '</span>',
          '<span class="discovery-confidence">匹配 ' + esc(percent(item.match_confidence)) + '</span>',
        '</div>',
        '<h4>' + esc(title) + '</h4>',
        '<p class="discovery-edition">品番 / JAN：' + esc(item.catalog_no || item.jan || "待人工确认") + (item.edition ? " · " + esc(item.edition) : "") + '</p>',
        '<div class="discovery-price-row">',
          '<div><small>挖煤姬购入</small><b>' + esc(jpy(item.purchase_price_jpy || item.source_price)) + '</b></div>',
          '<div><small>闲鱼参考</small><b>' + esc(cny(reference)) + '</b></div>',
          '<div class="profit"><small>预估净利</small><b>' + esc(cny(item.expected_profit)) + '</b></div>',
        '</div>',
        '<div class="discovery-metrics">',
          '<span>利润率 ' + esc(percent(item.net_margin)) + '</span>',
          '<span>有效样本 ' + esc(item.valid_xianyu_sample_count || 0) + '</span>',
          '<span>' + esc(item.liquidity_status === "normal" ? "流动性正常" : (item.liquidity_status || "流动性待验证")) + '</span>',
        '</div>',
        safeSourceUrl
          ? '<a class="discovery-source-link" href="' + esc(safeSourceUrl) + '" target="_blank" rel="noopener">打开挖煤姬商品</a>'
          : '<span class="discovery-source-link disabled">商品链接待采集</span>',
      '</article>',
    ];
    return card.join("");
  }

  function renderFeed() {
    const target = document.getElementById("homeFeed");
    if (!target || !view.board) return;
    const allItems = Array.isArray(view.board.opportunities) ? view.board.opportunities : [];
    const items = view.filter === "all" ? allItems : allItems.filter((item) => item.media_type === view.filter);
    if (!items.length) {
      target.innerHTML = '<div class="empty-state">暂时没有通过门槛的机会。点击「让采集电脑立即扫描」即可开始自动发现。</div>';
      return;
    }
    target.innerHTML = '<div class="discovery-feed">' + items.map(opportunityCard).join("") + "</div>";
  }

  function poolCard(pool) {
    const id = Number(pool.id);
    const enabledKeywords = (Array.isArray(pool.keywords) ? pool.keywords : [])
      .filter((keyword) => keyword.enabled)
      .map((keyword) => keyword.keyword)
      .join("\n");
    const enabled = Boolean(pool.enabled);
    const marginPercent = Math.round((Number(pool.min_margin) || 0) * 100);
    return [
      '<article class="discovery-pool" data-pool-id="' + esc(id) + '">',
        '<div class="discovery-pool-title">',
          '<div><h4>' + esc(pool.name) + '</h4><p>' + esc(typeLabel(pool.media_type)) + ' · 上次扫描：' + esc(timeLabel(pool.last_scanned_at)) + '</p></div>',
          '<span class="status ' + (enabled ? "ok" : "idle") + '">' + (enabled ? "已启用" : "已暂停") + "</span>",
        '</div>',
        '<div class="discovery-pool-actions">',
          '<button class="primary" type="button" data-discovery-action="scan" data-pool-id="' + esc(id) + '">立即扫描此池</button>',
          '<button type="button" data-discovery-action="save-pool">保存采集规则</button>',
        '</div>',
        '<div class="discovery-rule-grid">',
          '<label><span>启用采集</span><input data-discovery-field="enabled" type="checkbox" ' + (enabled ? "checked" : "") + ' /></label>',
          '<label><span>扫描间隔（分钟）</span><input data-discovery-field="scan_interval_minutes" type="number" min="5" max="1440" value="' + esc(pool.scan_interval_minutes) + '" /></label>',
          '<label><span>每轮关键词数</span><input data-discovery-field="keyword_budget" type="number" min="1" max="10" value="' + esc(pool.keyword_budget) + '" /></label>',
          '<label><span>每词候选上限</span><input data-discovery-field="candidate_budget" type="number" min="1" max="50" value="' + esc(pool.candidate_budget) + '" /></label>',
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
      const [board, commandPayload] = await Promise.all([
        apiGet("/api/discovery/board"),
        apiGet("/api/discovery/commands"),
      ]);
      view.board = board || { summary: {}, pools: [], opportunities: [] };
      view.commands = (commandPayload && commandPayload.items) || [];
      if (window.state) {
        window.state.opportunities = (view.board.opportunities || []).map(toLegacyOpportunity);
        window.state.totalOpportunities = window.state.opportunities.length;
      }
      renderKpis(view.board.summary || {});
      renderFeed();
      renderPools();
      setCommandMessage(commandLabel(view.commands));
    } catch (error) {
      setCommandMessage("读取选品广场失败：" + error.message, true);
      const target = document.getElementById("homeFeed");
      if (target) target.innerHTML = '<div class="empty-state">无法连接选品广场：' + esc(error.message) + '</div>';
    } finally {
      view.refreshing = false;
    }
  }

  async function queueScan(poolId) {
    const button = document.querySelector('[data-discovery-action="scan"][data-pool-id="' + String(poolId || "") + '"]') || document.getElementById("scanDiscoveryNowBtn");
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
      candidate_budget: numberValue(root, "candidate_budget", 2, 1, 50),
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
    document.getElementById("discoveryPoolControls")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-discovery-action]");
      if (!button) return;
      const root = button.closest("[data-pool-id]");
      if (!root) return;
      const action = button.getAttribute("data-discovery-action");
      if (action === "scan") queueScan(button.getAttribute("data-pool-id"));
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
      if (api && typeof api.ensureViewerAccessToken === "function") {
        const accessReady = await api.ensureViewerAccessToken();
        if (!accessReady) {
          setCommandMessage("需要 Render 访问令牌才能读取选品广场；填写后刷新此页即可继续。", true);
          return;
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
