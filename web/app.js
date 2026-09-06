// Kuro Atelier Edition v8 - frontend for WAMEIJI-XIANYU
// 6 pages: home / ranking / tasks / accounts / logs / settings
(function () {
  "use strict";

  // ---------- helpers ----------
  const fmtJpy = new Intl.NumberFormat("ja-JP", { style: "currency", currency: "JPY", maximumFractionDigits: 0 });
  const fmtCny = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 });
  function money(value, currency) {
    const num = Number(value);
    if (!Number.isFinite(num)) return "--";
    if (currency === "JPY") return fmtJpy.format(num);
    return fmtCny.format(num);
  }
  function pct01(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return "--";
    return (num * 100).toFixed(1) + "%";
  }
  function pctRaw(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return "--";
    return num.toFixed(0) + "%";
  }
  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
  }
  function timeShort(value) {
    if (!value) return "--";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return hh + ":" + mm;
  }
  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  function setEmpty(target) {
    if (!target) return;
    const empty = target.getAttribute("data-empty") || "暂无数据";
    target.innerHTML = '<div class="empty-state">' + esc(empty) + "</div>";
  }
  function chipForDecision(decision) {
    if (decision === "strong_alert") return '<span class="status ok">强提醒</span>';
    if (decision === "weak_alert") return '<span class="status blue">弱提醒</span>';
    if (decision === "skip") return '<span class="status idle">跳过</span>';
    return '<span class="status warn">' + esc(decision || "未知") + "</span>";
  }
  function chipForLiquidity(s) {
    if (s === "normal") return '<span class="status ok">高流动性</span>';
    if (s === "thin") return '<span class="status warn">低流动性</span>';
    if (s === "dead") return '<span class="status bad">无成交</span>';
    return '<span class="status idle">' + esc(s || "--") + "</span>";
  }
  function chipForLoginStatus(s) {
    if (s === "ready") return { cls: "ok", label: "已登录" };
    if (s === "missing") return { cls: "warn", label: "未配置" };
    return { cls: "bad", label: "异常" };
  }

  // The browser UI can be hosted separately from the collector API (for
  // example GitHub Pages -> Render).  Keep the default same-origin behavior,
  // while allowing an operator to set the API origin without rebuilding the
  // page.  Priority: URL override, localStorage, runtime-config.js.
  function configuredApiBase() {
    const runtime = window.CD_MONITOR_CONFIG || {};
    const query = new URLSearchParams(window.location.search).get("api_base");
    const saved = (() => {
      try { return window.localStorage.getItem("cd_monitor_api_base") || ""; } catch (_) { return ""; }
    })();
    return String(query || saved || runtime.apiBase || "").trim().replace(/\/+$/, "");
  }

  function configuredApiToken() {
    const runtime = window.CD_MONITOR_CONFIG || {};
    const query = new URLSearchParams(window.location.search).get("access_token");
    const saved = (() => {
      try { return window.localStorage.getItem("cd_monitor_access_token") || ""; } catch (_) { return ""; }
    })();
    return String(query || saved || runtime.accessToken || "").trim();
  }

  function apiUrl(path) {
    if (/^https?:\/\//i.test(path)) return path;
    const base = configuredApiBase();
    const relative = String(path || "").startsWith("/") ? String(path) : "/" + String(path);
    const url = base + relative;
    const token = configuredApiToken();
    if (!token || /[?&]access_token=/.test(url)) return url;
    return url + (url.includes("?") ? "&" : "?") + "access_token=" + encodeURIComponent(token);
  }

  async function getJson(path) {
    const resp = await fetch(apiUrl(path), { cache: "no-store", credentials: "include" });
    if (!resp.ok) throw new Error("GET " + path + " -> " + resp.status);
    return await resp.json();
  }
  async function postJson(path, body) {
    const resp = await fetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      credentials: "include",
    });
    const text = await resp.text();
    let data; try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { raw: text }; }
    if (!resp.ok) throw new Error((data && data.error) || ("POST " + path + " -> " + resp.status));
    return data;
  }
  async function deleteReq(path) {
    const resp = await fetch(apiUrl(path), { method: "DELETE", credentials: "include" });
    if (!resp.ok) throw new Error("DELETE " + path + " -> " + resp.status);
    return await resp.json().catch(() => ({}));
  }
  async function putJson(path, body) {
    const resp = await fetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      credentials: "include",
    });
    const text = await resp.text();
    let data; try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { raw: text }; }
    if (!resp.ok) throw new Error((data && data.error) || ("PUT " + path + " -> " + resp.status));
    return data;
  }

  // ---------- data state ----------
  const state = {
    summary: null,
    opportunities: [],
    watchlist: [],
    reviews: [],
    alerts: [],
    rechecks: [],
    searchRuns: [],
    config: null,
    browser: null,
    doctor: null,
    xianyuLogin: null,
    wameijiLogin: null,
    userSettings: {},
    feedFilter: "all",
    homeFeedObserver: null,
    homeFeedPageSize: 12,
    homeFeedOffset: 0,
    homeFeedHasMore: true,
    homeFeedLoading: false,
    taskFilter: "all",
    logFilter: "all",
    rankSort: "score",
    accountFilter: "all",
    lastByKeyword: {},
  };

  // ---------- page navigation ----------
  function switchPage(name) {
    document.querySelectorAll(".nav button").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-page") === name);
    });
    document.querySelectorAll(".page").forEach((p) => {
      p.classList.toggle("active", p.getAttribute("data-page") === name);
    });
    if (name === "ranking") renderRanking();
    if (name === "tasks") renderTasks();
    if (name === "accounts") renderAccounts();
    if (name === "logs") renderLogs();
    if (name === "settings") renderSettings();
  }
  function bindNav() {
    document.querySelectorAll(".nav button").forEach((btn) => {
      btn.addEventListener("click", () => switchPage(btn.getAttribute("data-page")));
    });
  }

  // ---------- home page ----------
  function sourceSiteLabel(s) {
    const m = { mercari_jp: 'Mercari', yahoo_auctions: '雅虎拍卖', suruga_ya: '骏河屋' };
    return m[s] || (s || '日本市场');
}

function buildProductUrl(opp) {
    // Only return a real wameiji item URL. If the scraper did not record one,
    // return null so the card renders as a non-link (no fabricated jump).
    const raw = String(opp.url || "").trim();
    if (raw && !/example\.invalid|localhost|127\.0\.0\.1/i.test(raw)) return raw;
    return null;
  }

  function buildXianyuUrl(opp) {
    // Same rule: only return a real xianyu item URL. No fake search fallback.
    const raw = String(opp.xianyu_url || "").trim();
    if (raw && !/example\.invalid|localhost|127\.0\.0\.1/i.test(raw)) return raw;
    return null;
  }

  function renderOpportunityCard(opp) {
    const id = opp.id;
    const wTitle = opp.item_title || ("机会 #" + (opp.catalog_no || id));
    const xTitle = opp.xianyu_item_title || ("闲鱼标本 " + (opp.catalog_no || id));
    const profitCny = Number(opp.expected_profit || 0);
    const margin = Number(opp.net_margin || 0);
    const conf = Number(opp.match_confidence || 0);
    const purchaseJpy = Number(opp.purchase_price_jpy || 0);
    const sampleCount = Number(opp.valid_xianyu_sample_count || 0);
    const xSampleRows = Number(opp.xianyu_sample_rows || 0);
    const decision = opp.decision || "skip";
    const liquidity = opp.liquidity_status || "--";
    const risks = Array.isArray(opp.risk_labels) ? opp.risk_labels : [];
    const createdAt = opp.created_at;
    const imageUrl = opp.image_url;
    const xianyuImg = opp.xianyu_image_url;
    const xPriceCny = Number(opp.xianyu_price_cny || opp.xianyu_reference_price || 0);
    const wameijiHref = buildProductUrl(opp);
    const xianyuHref = buildXianyuUrl(opp);
    const reason = risks.length
      ? "AI 判断：需注意 " + risks.join("、")
      : "AI 判断：首图与市场标本高度接近，建议加入监控。";
    const xThumb = xianyuImg
      ? '<img src="' + esc(xianyuImg) + '" alt="" loading="lazy" />'
      : '<span>闲鱼标本<br>尚未采集</span>';
    const wThumb = imageUrl
      ? '<img src="' + esc(imageUrl) + '" alt="" loading="lazy" />'
      : '<span>挖煤姬市场图<br>等待抓取</span>';
    // Each side panel is independently linkable to its own platform.
    // No real URL => render as a plain <div>, no click.
    const xianyuOpen = xianyuHref
      ? '<a class="side-product xianyu side-link" href="' + esc(xianyuHref) + '" target="_blank" rel="noopener" title="点击跳转到闲鱼商品">'
      : '<div class="side-product xianyu side-nolink"><span class="nolink-badge">无链接</span>';
    const xianyuClose = xianyuHref ? '</a>' : '</div>';
    const wameijiOpen = wameijiHref
      ? '<a class="side-product market side-link" href="' + esc(wameijiHref) + '" target="_blank" rel="noopener" title="点击跳转到挖煤姬商品">'
      : '<div class="side-product market side-nolink"><span class="nolink-badge">无链接</span>';
    const wameijiClose = wameijiHref ? '</a>' : '</div>';
    return [
      '<div class="op-card" data-opp-id="' + esc(id) + '">',
        xianyuOpen,
          '<div class="thumb xianyu-thumb">', xThumb, '</div>',
          '<div class="product">',
            '<span class="tag xianyu-tag">闲鱼</span>', chipForLiquidity(liquidity),
            '<h4>' + esc(xTitle) + '</h4>',
            '<div class="price">' + money(xPriceCny, "CNY") + '</div>',
            '<div class="desc">标本数 ' + esc(sampleCount) + ' · 历史抓取 ' + esc(xSampleRows) + ' · ' + esc(timeShort(createdAt)) + ' 前</div>',
          '</div>',
        xianyuClose,
        '<div class="analysis">',
          '<div class="grid2">',
            '<div class="metric"><small>预计利润</small><strong>' + esc(money(profitCny, "CNY")) + '</strong></div>',
            '<div class="metric"><small>利润率</small><strong>' + esc(pct01(margin)) + '</strong></div>',
            '<div class="metric"><small>置信度</small><strong>' + esc(pct01(conf)) + '</strong></div>',
            '<div class="metric"><small>决策</small><strong>' + chipForDecision(decision) + '</strong></div>',
          '</div>',
          '<div class="reason">' + esc(reason) + '</div>',
        '</div>',
        wameijiOpen,
          '<div class="thumb market-thumb">', wThumb, '</div>',
          '<div class="product">',
            '<span class="tag hot">挖煤姬</span><span class="tag source-tag">' + sourceSiteLabel(opp.wameiji_source_site || opp.source_site) + '</span><span class="tag">' + esc(opp.availability || "日版") + '</span>',
            '<h4>' + esc(wTitle) + '</h4>',
            '<div class="price">' + esc(money(purchaseJpy, "JPY")) + '</div>',
            '<div class="price-sub cny-equiv">\u2248 \u00a5' + Math.round(purchaseJpy * (window.JPY_RATE || 0.046)).toLocaleString("zh-CN") + ' CNY (\u6309\u5f53\u524d\u6c47\u7387\u8f6c\u6362)</div>',
            '<div class="desc">品番 ' + esc(opp.catalog_no || "--") + (wameijiHref ? '</div>' : ' · 点击直达挖煤姬商品</div>'),
          '</div>',
        wameijiClose,
      '</div>',
    ].join("");
  }


  function buildOppsUrl(limit, offset, randomize) {
    let q = "/api/opportunities?limit=" + limit + "&offset=" + offset;
    if (randomize) {
      q += "&random=1&seed=" + Date.now();
    }
    return q;
  }

  function applyFeedFilter(items) {
    let arr = items.slice();
    const f = state.feedFilter;
    if (f === "match") arr.sort((a, b) => (Number(b.match_confidence) || 0) - (Number(a.match_confidence) || 0));
    else if (f === "low-risk") arr = arr.filter((o) => (Number(o.match_confidence) || 0) > 0.75);
    return arr;
  }


  // ---- home feed infinite scroll ----
  function setupInfiniteScroll(target, onHit) {
    const sentinel = target.querySelector("[data-home-sentinel]");
    if (!sentinel) return null;
    if (state.homeFeedObserver) {
      try { state.homeFeedObserver.disconnect(); } catch (_) {}
    }
    const obs = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting && state.homeFeedHasMore && !state.homeFeedLoading) {
          onHit && onHit();
        }
      }
    }, { rootMargin: "200px" });
    obs.observe(sentinel);
    state.homeFeedObserver = obs;
    return obs;
  }

  async function loadMoreHome() {
    if (state.homeFeedLoading || !state.homeFeedHasMore) return;
    state.homeFeedLoading = true;
    const target = document.getElementById("homeFeed");
    const sentinel = target ? target.querySelector("[data-home-sentinel]") : null;
    if (sentinel) sentinel.textContent = "加载中…";
    try {
      // Random shuffle when the feed is exhausted so the user keeps seeing fresh
      // batches instead of an empty stream. The dedupe-by-id below keeps each
      // card from showing up twice.
      const isExhausted = state.homeFeedOffset >= state.totalOpportunities;
      const next = await getJson(buildOppsUrl(state.homeFeedPageSize, state.homeFeedOffset, isExhausted));
      const items = (next && next.items) || [];
      const seenIds = new Set(state.opportunities.map((o) => o.id));
      const fresh = items.filter((it) => !seenIds.has(it.id));
      if (fresh.length && target) {
        state.opportunities = state.opportunities.concat(fresh);
        state.homeFeedOffset += fresh.length;
        state.homeFeedHasMore = !!next.has_more && items.length >= state.homeFeedPageSize;
        const tmp = document.createElement("div");
        tmp.innerHTML = fresh.map(renderOpportunityCard).join("");
        const newCards = Array.from(tmp.children);
        if (sentinel) {
          newCards.forEach((c) => target.insertBefore(c, sentinel));
        } else {
          newCards.forEach((c) => target.appendChild(c));
        }
        // Cards are <a> elements; browser handles click navigation.
        if (sentinel) sentinel.textContent = state.homeFeedHasMore ? "加载更多…" : "已经到底啦";
      } else {
        state.homeFeedHasMore = false;
        if (sentinel) sentinel.textContent = "已经到底啦";
      }
    } catch (e) {
      if (sentinel) sentinel.textContent = "加载失败：" + e.message;
    } finally {
      state.homeFeedLoading = false;
    }
  }

  function renderHome(extraFilter) {
    const target = document.getElementById("homeFeed");
    if (!target) return;
    let items = state.opportunities.slice();
    if (extraFilter) {
      if (extraFilter.q) {
        const q = extraFilter.q.toLowerCase();
        items = items.filter((o) => (o.catalog_no || "").toLowerCase().includes(q) || (o.item_title || "").toLowerCase().includes(q));
      }
      if (extraFilter.decision && extraFilter.decision !== "all") items = items.filter((o) => o.decision === extraFilter.decision);
      if (extraFilter.platform && extraFilter.platform !== "all") items = items.filter((o) => String(o.platform || "both") === extraFilter.platform || String(o.platform || "both") === "both");
      if (extraFilter.min_margin) items = items.filter((o) => (Number(o.net_margin) || 0) * 100 >= Number(extraFilter.min_margin));
      if (extraFilter.min_diff) items = items.filter((o) => (Number(o.expected_profit) || 0) >= Number(extraFilter.min_diff));
    } else {
      items = applyFeedFilter(items);
    }
    if (!items.length) { setEmpty(target); return; }
    target.innerHTML = items.map(renderOpportunityCard).join("") + '<div class="home-sentinel" data-home-sentinel>' + (state.homeFeedHasMore ? "加载更多…" : "已经到底啦") + '</div>';
    state.homeFeedObserver = setupInfiniteScroll(target, () => loadMoreHome());
    // Cards are now <a> elements; browser handles the click navigation directly.
  }

  function renderHomeKpi() {
    const s = state.summary || {};
    const today = s.opportunity_count ?? state.opportunities.length;
    const profit = s.top_expected_profit ?? 0;
    const allProfits = state.opportunities.map((o) => Number(o.expected_profit) || 0);
    const max = allProfits.length ? Math.max(...allProfits) : 0;
    const avgConf = s.avg_match_confidence ?? 0;
    setText("kpiToday", String(today || 0));
    setText("kpiProfit", money(profit, "CNY"));
    setText("kpiMax", money(max, "CNY"));
    setText("kpiHitRate", pct01(avgConf));
  }

  async function showOpportunityDetail(id) {
    const modal = document.getElementById("detailModal");
    const body = document.getElementById("detailBody");
    const title = document.getElementById("detailTitle");
    if (!modal || !body) return;
    title.textContent = "机会详情 #" + id;
    body.innerHTML = '<div class="empty-state">加载中…</div>';
    modal.hidden = false;
    try {
      const d = await getJson("/api/opportunities/" + encodeURIComponent(id));
      const opp = d.opportunity || d;
      const market = d.market_items || [];
      const xianyu = d.xianyu_samples || [];
      const reviews = d.review_decisions || [];
      const alerts = d.sent_alerts || [];
      const rechecks = d.candidate_rechecks || [];
      const html = [
        '<div class="detail-section">',
          '<h4>核心指标</h4>',
          '<div class="detail-grid">',
            '<div class="metric"><small>预计利润</small><strong>' + esc(money(opp.expected_profit, "CNY")) + '</strong></div>',
            '<div class="metric"><small>利润率</small><strong>' + esc(pct01(opp.net_margin)) + '</strong></div>',
            '<div class="metric"><small>置信度</small><strong>' + esc(pct01(opp.match_confidence)) + '</strong></div>',
            '<div class="metric"><small>决策</small><strong>' + chipForDecision(opp.decision) + '</strong></div>',
            '<div class="metric"><small>挖煤姬购入</small><strong>' + esc(money(opp.purchase_price_jpy, "JPY")) + '</strong></div>',
            '<div class="metric"><small>预期售出</small><strong>' + esc(money(opp.expected_sale_price, "CNY")) + '</strong></div>',
            '<div class="metric"><small>落地成本</small><strong>' + esc(money(opp.landed_cost, "CNY")) + '</strong></div>',
            '<div class="metric"><small>闲鱼样本</small><strong>' + esc(opp.valid_xianyu_sample_count || 0) + ' 条</strong></div>',
          '</div>',
        '</div>',
        '<div class="detail-section">',
          '<h4>挖煤姬市场样本（' + market.length + '）</h4>',
          market.length
            ? '<div class="detail-list">' + market.slice(0, 10).map((m) => '<div class="row"><b>' + esc(m.title || m.external_item_id || "--") + '</b> · ' + esc(money(m.price, m.currency || "JPY")) + (m.url ? ' · <a href="' + esc(m.url) + '" target="_blank" rel="noopener">链接</a>' : '') + '</div>').join("") + '</div>'
            : '<div class="detail-empty">暂无样本</div>',
        '</div>',
        '<div class="detail-section">',
          '<h4>闲鱼价格样本（' + xianyu.length + '）</h4>',
          xianyu.length
            ? '<div class="detail-list">' + xianyu.slice(0, 10).map((x) => '<div class="row"><b>' + esc(x.title || "--") + '</b> · ' + esc(money(x.price_cny, "CNY")) + (x.is_valid ? '' : ' · ⚠️ ' + esc(x.invalid_reason || "无效")) + '</div>').join("") + '</div>'
            : '<div class="detail-empty">暂无样本</div>',
        '</div>',
        '<div class="detail-section">',
          '<h4>审核历史（' + reviews.length + '）</h4>',
          reviews.length
            ? '<div class="detail-list">' + reviews.map((r) => '<div class="row">' + esc(r.result || "--") + ' · ' + esc(r.note || "") + ' · ' + esc(r.reviewed_at || "") + '</div>').join("") + '</div>'
            : '<div class="detail-empty">暂无审核</div>',
        '</div>',
        '<div class="detail-section">',
          '<h4>推送历史（' + alerts.length + '）</h4>',
          alerts.length
            ? '<div class="detail-list">' + alerts.map((a) => '<div class="row">' + esc(a.channel || "--") + ' · ' + esc(a.status || "--") + ' · ' + esc(a.sent_at || "") + '</div>').join("") + '</div>'
            : '<div class="detail-empty">暂无推送</div>',
        '</div>',
        '<div class="detail-section">',
          '<h4>复检记录（' + rechecks.length + '）</h4>',
          rechecks.length
            ? '<div class="detail-list">' + rechecks.map((r) => '<div class="row">' + esc(r.status || "--") + ' · ' + esc(r.reason || "") + ' · ' + esc(r.scheduled_at || "") + '</div>').join("") + '</div>'
            : '<div class="detail-empty">暂无复检</div>',
        '</div>',
      ].join("");
      body.innerHTML = html;
      // Add the action buttons at end of body
      const actions = document.createElement("div");
      actions.className = "detail-section";
      actions.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;padding-top:8px;border-top:1px solid var(--kuro-line);";
      actions.innerHTML =
        '<button class="primary" data-detail-action="approve" data-opp-id="' + esc(id) + '">通过</button>' +
        '<button data-detail-action="reject" data-opp-id="' + esc(id) + '">拒绝</button>' +
        '<button data-detail-action="recheck" data-opp-id="' + esc(id) + '">30 分钟后复检</button>' +
        '<button data-detail-action="notify" data-opp-id="' + esc(id) + '">立即通知</button>' +
        '<button data-detail-action="backtest" data-catalog="' + esc(opp.catalog_no || "") + '">回测该品番</button>';
      body.appendChild(actions);
      // Attach listeners AFTER buttons are in the DOM
      body.querySelectorAll("[data-detail-action]").forEach((btn) => {
        btn.addEventListener("click", () => onDetailAction(btn.getAttribute("data-detail-action"), btn));
      });
    } catch (err) {
      body.innerHTML = '<div class="empty-state">加载失败：' + esc(err.message) + "</div>";
    }
  }

  // ---------- ranking page ----------
  function renderRanking() {
    const tbody = document.querySelector("#rankingTable tbody");
    if (!tbody) return;
    const sortKey = state.rankSort || "score";
    let items = state.opportunities.slice();
    if (sortKey === "profit") {
      items.sort((a, b) => (Number(b.expected_profit) || 0) - (Number(a.expected_profit) || 0));
    } else if (sortKey === "confidence") {
      items.sort((a, b) => (Number(b.match_confidence) || 0) - (Number(a.match_confidence) || 0));
    } else {
      items.sort((a, b) => {
        const sa = (Number(a.expected_profit) || 0) * (Number(a.match_confidence) || 0);
        const sb = (Number(b.expected_profit) || 0) * (Number(b.match_confidence) || 0);
        return sb - sa;
      });
    }
    items = items.slice(0, 20);
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-row">暂无机会数据</td></tr>';
    } else {
      tbody.innerHTML = items.map((opp, i) => {
        const title = opp.item_title || ("机会 #" + opp.catalog_no);
        const profit = Number(opp.expected_profit || 0);
        const margin = Number(opp.net_margin || 0);
        const conf = Number(opp.match_confidence || 0);
        const status = conf > 0.85 ? '<span class="status ok">强匹配</span>' : conf > 0.7 ? '<span class="status blue">可关注</span>' : '<span class="status warn">待验证</span>';
        return [
          '<tr><td><b>#' + (i + 1) + '</b></td>',
          '<td>' + esc(title) + '</td>',
          '<td>' + esc(money(profit, "CNY")) + '</td>',
          '<td>' + esc(pct01(margin)) + '</td>',
          '<td>' + esc(pct01(conf)) + '</td>',
          '<td>' + status + '</td></tr>',
        ].join("");
      }).join("");
    }
    const total = state.opportunities.length;
    const hits = state.opportunities.filter((o) => (Number(o.match_confidence) || 0) > 0.7).length;
    const profits = state.opportunities.map((o) => Number(o.expected_profit) || 0).filter((n) => n > 0);
    const avg = profits.length ? profits.reduce((a, b) => a + b, 0) / profits.length : 0;
    const top = profits.length ? Math.max(...profits) : 0;
    setText("rankScans", String(state.searchRuns.length || 0));
    setText("rankHits", String(hits));
    setText("rankAvg", money(avg, "CNY"));
    setText("rankTop", money(top, "CNY"));
    const pick = items[0];
    const pickEl = document.getElementById("kuroPick");
    if (pickEl) {
      if (!pick) pickEl.textContent = "暂无数据，等候扫描。";
      else {
        pickEl.innerHTML =
          '<b>' + esc(pick.item_title || "未命名") + '</b><br/>' +
          '挖煤姬 ' + esc(money(pick.purchase_price_jpy || 0, "JPY")) + ' (≈ ¥' + Math.round((pick.purchase_price_jpy || 0) * (window.JPY_RATE || 0.046)).toLocaleString("zh-CN") + ' CNY) → 闲鱼预期 ' + money(pick.xianyu_reference_price, "CNY") + '<br/>' +
          '净利润 ' + esc(money(pick.expected_profit || 0, "CNY")) +
          ' · 利润率 ' + esc(pct01(pick.net_margin || 0)) +
          ' · 置信度 ' + esc(pct01(pick.match_confidence || 0));
      }
    }
  }


  // ---------- tasks page ----------
  function renderTasks() {
    const list = document.getElementById("taskList");
    if (!list) return;
    const items = state.watchlist;
    if (!items.length) { setEmpty(list); }
    else {
      let viewItems = items;
      const tf = state.taskFilter || "all";
      if (tf === "active") viewItems = viewItems.filter((w) => w.enabled === true || w.enabled === 1 || w.enabled === "1");
      else if (tf === "paused") viewItems = viewItems.filter((w) => w.enabled === false || w.enabled === 0 || w.enabled === "0");
      else if (tf === "error") viewItems = viewItems.filter((w) => {
        const run = state.lastByKeyword && state.lastByKeyword[String(w.catalog_no || "").trim()];
        return run && run.status && run.status !== "ok";
      });
      if (!viewItems.length) { setEmpty(list); return; }
      list.innerHTML = viewItems.map((w) => {
        const id = w.id;
        const title = (w.artist ? w.artist + " · " : "") + (w.catalog_no || ("任务 #" + id));
        const prio = "P" + (w.priority ?? 3);
        const holding = w.expected_holding_days ? "持有 " + w.expected_holding_days + " 天" : "";
        const kw = (w.required_keywords || []).join(" / ") || "无关键词";
        const margin = pct01(w.min_margin || 0);
        const diff = money(w.min_diff || 0, "CNY");
        const notify = w.notify_channel || "none";
        const platform = w.platform || "both";
        const strategy = w.account_strategy || "auto";
        const stateFile = w.account_state_file || "";
        const strategyLabel = strategy === "fixed" ? "固定账号"
          : (strategy === "rotate" ? "轮询账号" : "自动");
        const strategyBadge = stateFile
          ? strategyLabel + " · " + stateFile
          : strategyLabel;
        // P5.4: surface decision_mode + description preview so the user
        // can tell at a glance whether a task is configured for AI or
        // keyword judgment, plus a glimpse of the requirement context.
        const decisionMode = w.decision_mode || "ai";
        const modeBadge = decisionMode === "keyword"
          ? '<span class="badge mode-keyword">关键词判断</span>' + (stateFile ? '' : '')
          : '<span class="badge mode-ai">AI 判断</span>';
        const descText = (w.description || "").trim();
        const descPreview = descText
          ? '<span class="desc-preview" title="' + esc(descText) + '">' + esc(descText.length > 60 ? descText.slice(0, 60) + "…" : descText) + '</span>'
          : (decisionMode === "ai" ? '<span class="desc-preview missing">未填写 description</span>' : '');
        const enabled = w.enabled === true || w.enabled === 1 || w.enabled === undefined;
        const run = state.lastByKeyword && state.lastByKeyword[String(w.catalog_no || "").trim()];
        const hasError = run && run.status && run.status !== "ok";
        let statusCls = enabled ? "ok" : "warn";
        let statusLabel = enabled ? "监控中" : "已暂停";
        if (enabled && hasError) { statusCls = "bad"; statusLabel = "异常"; }
        const toggleBtn = enabled
          ? '<button data-task-action="pause" title="暂停任务">暂停</button>'
          : '<button data-task-action="resume" title="恢复任务">恢复</button>';
        return [
          '<div class="task-card" data-task-id="' + esc(id) + '">',
            '<div class="meta"><h4>' + esc(title) + '' + ' ' + modeBadge + '</h4>',
            '<p>品番 ' + esc(w.catalog_no || "--") + ' · 平台 ' + esc(platform) + ' · 优先级 ' + esc(prio) + ' · ' + esc(holding) + ' · 利润率门槛 ' + margin + ' · 价格差门槛 ' + diff + ' · 通知 ' + esc(notify) + ' · 账号策略 ' + esc(strategyBadge) + (hasError ? ' · 上次扫描 ' + esc(run.status) : '') + '</p>',
            (descPreview ? '<p class="task-mode-line">' + descPreview + '</p>' : ''),
            '</div>',
            '<div class="actions">',
            '<span class="status ' + statusCls + '">' + statusLabel + '</span>',
            '<button class="primary" data-task-action="scan">扫描</button>',
            '<button data-task-action="start" title="立即运行一次（P0 #3+#9）">立即运行</button>',
            '<button data-task-action="regen" title="重新生成 AI 判定标准">重新生成 AI</button>',
            '<button data-task-action="blacklist" title="编辑结果黑名单关键词">黑名单</button>',
            '<button data-task-action="edit" title="编辑任务设置">编辑</button>',
            toggleBtn,
            '<button data-task-action="delete" title="硬删除">删除</button>',
            '</div>',
          '</div>',
        ].join("");
      }).join("");
      list.querySelectorAll("[data-task-action]").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const card = btn.closest("[data-task-id]");
          const id = card && card.getAttribute("data-task-id");
          const action = btn.getAttribute("data-task-action");
          if (!id) return;
          if (action === "scan") onTaskScan(id);
          else if (action === "edit") onTaskEdit(id);
          else if (action === "delete") onTaskDelete(id);
          else if (action === "pause") onTaskToggle(id, false);
          else if (action === "resume") onTaskToggle(id, true);
          else if (action === "start") onTaskRunNow(id);
          else if (action === "regen") onTaskRegenerate(id);
          else if (action === "blacklist") openBlacklistEditor(id);
        });
      });
    }
    const enabledCount = items.filter((w) => w.enabled === true || w.enabled === 1 || w.enabled === undefined).length;
    const pausedCount = items.filter((w) => !w.enabled).length;
    const errorCount = items.filter((w) => {
      const run = state.lastByKeyword && state.lastByKeyword[String(w.catalog_no || "").trim()];
      return run && run.status && run.status !== "ok";
    }).length;
    setText("tasksActive", String(enabledCount));
    setText("tasksPaused", String(pausedCount));
    setText("tasksError", String(errorCount));
    setText("tasksToday", String(items.filter((w) => isToday(w.created_at)).length));
  }
  function isToday(value) {
    if (!value) return false;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return false;
    const now = new Date();
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  }
  async function onTaskScan(watchId) {
    try {
      const result = await postJson("/api/scan/watchlist", { watch_id: watchId, notify: false });
      alert("扫描完成：新增 " + (result.new_count ?? result.opportunity_count ?? 0) + " 条机会。");
      await refreshAll();
    } catch (err) { alert("扫描失败：" + err.message); }
  }
  async function onTaskDelete(watchId) {
    if (!confirm("确认硬删除这条任务？关联数据不会被清除。")) return;
    try {
      await deleteReq("/api/watchlist/" + encodeURIComponent(watchId) + "/hard-delete");
      await refreshAll();
    } catch (err) { alert("删除失败：" + err.message); }
  }
  function onTaskEdit(watchId) {
    const t = state.watchlist.find((w) => String(w.id) === String(watchId));
    if (!t) { alert("未找到任务 #" + watchId); return; }
    const form = document.getElementById("editTaskForm");
    if (!form) return;
    form.dataset.editingId = String(watchId);
    form.elements["keyword"].value = t.catalog_no || t.artist || "";
    form.elements["artist"].value = t.artist || "";
    form.elements["jan"].value = t.jan || "";
    form.elements["edition"].value = t.edition || "";
    form.elements["min_margin"].value = t.min_margin != null ? t.min_margin : 0.30;
    form.elements["min_diff"].value = t.min_diff != null ? t.min_diff : 1500;
    form.elements["priority"].value = t.priority || 3;
    form.elements["expected_holding_days"].value = t.expected_holding_days || 30;
    form.elements["platform"].value = t.platform || "both";
    form.elements["notify_channel"].value = t.notify_channel || "none";
    form.elements["decision_mode"].value = t.decision_mode || "ai";
    form.elements["description"].value = t.description || "";
    form.elements["ai_prompt_base_file"].value = t.ai_prompt_base_file || "";
    form.elements["ai_prompt_criteria_file"].value = t.ai_prompt_criteria_file || "";
    syncDecisionModeVisibility(form);
    populateAccountOptions().then(() => {
      form.elements["account_strategy"].value = t.account_strategy || "auto";
      form.elements["account_state_file"].value = t.account_state_file || "";
      syncDecisionModeVisibility(form);
    });
    const m = document.getElementById("editTaskModal");
    if (m) m.hidden = false;
  }
  async function onTaskToggle(watchId, enable) {
    try {
      const path = enable
        ? "/api/watchlist/" + encodeURIComponent(watchId) + "/enable"
        : "/api/watchlist/" + encodeURIComponent(watchId) + "/disable";
      await postJson(path, {});
      await refreshAll();
    } catch (err) { alert((enable ? "恢复" : "暂停") + "失败：" + err.message); }
  }
  function bindEditTaskForm() {
    populateAccountOptions().catch((e) => console.error('populateAccountOptions', e));
    const form = document.getElementById("editTaskForm");
    if (!form) return;
    wireDecisionModeToggle(form);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const id = form.dataset.editingId;
      if (!id) { alert("未选择任务"); return; }
      const fd = new FormData(form);
      const descRaw = String(fd.get("description") || "").trim();
      const decisionMode = String(fd.get("decision_mode") || "ai");
      const payload = {
        catalog_no: String(fd.get("keyword") || "").trim() || undefined,
        artist: String(fd.get("artist") || "").trim() || null,
        jan: String(fd.get("jan") || "").trim() || null,
        edition: String(fd.get("edition") || "").trim() || null,
        min_margin: Number(fd.get("min_margin") || 0.30),
        min_diff: Number(fd.get("min_diff") || 1500),
        priority: Number(fd.get("priority") || 3),
        expected_holding_days: Number(fd.get("expected_holding_days") || 30),
        platform: String(fd.get("platform") || "both"),
        notify_channel: String(fd.get("notify_channel") || "none"),
        account_strategy: String(fd.get("account_strategy") || "auto"),
        account_state_file: String(fd.get("account_state_file") || "") || null,
        decision_mode: decisionMode,
        description: descRaw || null,
        ai_prompt_base_file: String(fd.get("ai_prompt_base_file") || "").trim() || null,
        ai_prompt_criteria_file: String(fd.get("ai_prompt_criteria_file") || "").trim() || null,
      };
      Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
      try {
        await postJson("/api/watchlist/" + encodeURIComponent(id), payload);
        const m = document.getElementById("editTaskModal");
        if (m) m.hidden = true;
        delete form.dataset.editingId;
        await refreshAll();
      } catch (err) { alert("保存失败：" + err.message); }
    });
  }
  function bindImportForm() {
    const form = document.getElementById("importForm");
    if (!form) return;
    // Show/hide the right textareas depending on the chosen kind.
    const updateImportFields = () => {
      const k = String(form.elements["kind"]?.value || "html-text");
      form.querySelectorAll("[data-import-show]").forEach((el) => {
        el.style.display = el.getAttribute("data-import-show") === k ? "" : "none";
      });
    };
    updateImportFields();
    form.elements["kind"]?.addEventListener("change", updateImportFields);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const r = document.getElementById("importResult");
      const fd = new FormData(form);
      const kind = String(fd.get("kind") || "html-text");
      const catalog = String(fd.get("catalog_no") || "").trim();
      if (!catalog) { r.textContent = "请填写关键词 (catalog_no)"; return; }
      r.textContent = "评估中…";
      try {
        let resp;
        if (kind === "json-files") {
          const wj = String(fd.get("wameiji_json") || "[]");
          const xj = String(fd.get("xianyu_json") || "[]");
          resp = await postJson("/api/import/evaluate-json", { catalog_no: catalog, wameiji_json: wj, xianyu_json: xj });
        } else if (kind === "html-text") {
          const html = String(fd.get("html_text") || "");
          if (!html.trim()) { r.textContent = "请粘贴 HTML 文本"; return; }
          resp = await postJson("/api/import/evaluate-html-text", { catalog_no: catalog, html_text: html });
        } else if (kind === "files") {
          const wh = String(fd.get("wameiji_html") || "");
          const xc = String(fd.get("xianyu_csv") || "");
          if (!wh.trim() || !xc.trim()) { r.textContent = "请粘贴 Wameiji HTML 和 Xianyu CSV"; return; }
          resp = await postJson("/api/import/evaluate-files", { catalog_no: catalog, wameiji_html: wh, xianyu_csv: xc });
        }
        const lines = [];
        lines.push("catalog_no: " + (resp.catalog_no || catalog));
        lines.push("snapshot_path: " + (resp.snapshot_path || "--"));
        lines.push("opportunity_count: " + (resp.opportunity_count || 0));
        if (Array.isArray(resp.opportunities) && resp.opportunities.length) {
          lines.push("opportunities:");
          resp.opportunities.slice(0, 5).forEach((o) => lines.push("  - " + (o.item_title || ("#" + o.id)) + " · profit=" + money(o.expected_profit || 0, "CNY") + " · margin=" + pct01(o.net_margin || 0) + " · conf=" + pct01(o.match_confidence || 0)));
        }
        r.textContent = lines.join("\n");
        await refreshAll();
      } catch (err) {
        r.textContent = "评估失败：" + err.message;
      }
    });
  }
  // P5.4: when decision_mode == "ai" we want the description textarea
  // visible and the user warned when it is empty. When "keyword" we
  // relax — description is allowed to stay empty. Mirrors the backend
  // _validate_decision_mode_update rule (web_server.py).
  function wireDecisionModeToggle(form) {
    if (!form) return;
    const select = form.querySelector('[data-decision-mode-select]');
    if (!select) return;
    select.addEventListener("change", () => syncDecisionModeVisibility(form));
    syncDecisionModeVisibility(form);
  }
  function syncDecisionModeVisibility(form) {
    if (!form) return;
    const select = form.querySelector('[data-decision-mode-select]');
    if (!select) return;
    const scope = select.getAttribute("data-decision-mode-select") || "";
    const mode = select.value || "ai";
    form.querySelectorAll('[data-decision-mode-target]').forEach((label) => {
      const targetScope = label.getAttribute("data-decision-mode-target");
      if (scope && targetScope && targetScope !== scope) return;
      const onlyMode = label.getAttribute("data-description-only");
      const shouldShow = !onlyMode || onlyMode === mode;
      if (shouldShow) {
        label.hidden = false;
        label.style.display = "";
      } else {
        label.hidden = true;
        label.style.display = "none";
      }
    });
  }
  function bindTaskForm() {
    const form = document.getElementById("newTaskForm");
    if (!form) return;
    wireDecisionModeToggle(form);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const margin = Number(fd.get("min_margin") || 30) / 100;
      const descRaw = String(fd.get("description") || "").trim();
      const decisionMode = String(fd.get("decision_mode") || "ai");
      const payload = {
        catalog_no: String(fd.get("keyword") || "").trim(),
        artist: String(fd.get("artist") || "").trim() || String(fd.get("keyword") || "").trim(),
        jan: String(fd.get("jan") || "").trim() || null,
        edition: String(fd.get("edition") || "").trim() || null,
        priority: Math.max(1, Math.min(5, Number(fd.get("priority") || 3))),
        expected_holding_days: Math.max(1, Number(fd.get("holding_days") || 30)),
        min_margin: margin,
        min_diff: Number(fd.get("min_diff") || 1500),
        notify_channel: String(fd.get("notify") || "none"),
        platform: String(fd.get("platform") || "both"),
        account_strategy: String(fd.get("account_strategy") || "auto"),
        account_state_file: String(fd.get("account_state_file") || "") || null,
        decision_mode: decisionMode,
        description: descRaw || null,
        ai_prompt_base_file: String(fd.get("ai_prompt_base_file") || "").trim() || null,
        ai_prompt_criteria_file: String(fd.get("ai_prompt_criteria_file") || "").trim() || null,
      };
      if (!payload.catalog_no) { alert("请填写关键词或品番"); return; }
      if (payload.decision_mode === "ai" && !descRaw) {
        alert("AI 判断模式下，详细需求(description)不能为空。请填写后重试。");
        const el = form.elements["description"]; if (el) el.focus();
        return;
      }
      try {
        await postJson("/api/watchlist", payload);
        form.reset();
        await refreshAll();
        switchPage("tasks");
      } catch (err) { alert("创建失败：" + err.message); }
    });
  }

  // ---------- ai generate modal (P5.4+) ----------
  // Wires up the topbar "AI 生成任务" button + the modal form. Submits
  // POST /api/watchlist/generate, then polls /api/watchlist/generate/{job_id}
  // every ~750ms to surface the 6-step live progress (prepare / reference /
  // prompt / llm / persist / task). Mirrors the CLI task-generate behavior.
  function bindAiGenerateButton() {
    const btn = document.getElementById("aiGenerateBtn");
    if (!btn) return;
    btn.addEventListener("click", () => openAiGenerateModal());
  }
  function openAiGenerateModal() {
    const modal = document.getElementById("aiGenerateModal");
    if (!modal) return;
    modal.hidden = false;
    const form = document.getElementById("aiGenerateForm");
    if (form) form.reset();
    const prog = document.getElementById("aiGenerateProgress");
    if (prog) prog.hidden = true;
    const actions = document.getElementById("aiGenerateActions");
    if (actions) actions.hidden = true;
    const stepsEl = document.getElementById("aiGenerateSteps");
    if (stepsEl) stepsEl.innerHTML = "";
    const foot = document.getElementById("aiGenerateFoot");
    if (foot) foot.textContent = "";
    syncAiGenerateModeVisibility();
  }
  function syncAiGenerateModeVisibility() {
    const form = document.getElementById("aiGenerateForm");
    if (!form) return;
    const mode = form.elements["decision_mode"].value;
    form.querySelectorAll('[data-decision-mode-target="ai-generate"]').forEach((label) => {
      const onlyMode = label.getAttribute("data-description-only") || label.getAttribute("data-keywords-only");
      const shouldShow = !onlyMode || onlyMode === mode;
      if (shouldShow) label.style.display = ""; else label.style.display = "none";
    });
  }
  function bindAiGenerateForm() {
    const form = document.getElementById("aiGenerateForm");
    if (!form) return;
    form.elements["decision_mode"].addEventListener("change", syncAiGenerateModeVisibility);
    syncAiGenerateModeVisibility();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const taskName = String(fd.get("task_name") || "").trim();
      const keyword = String(fd.get("keyword") || "").trim() || taskName;
      const description = String(fd.get("description") || "").trim();
      const decisionMode = String(fd.get("decision_mode") || "ai");
      const requiredKwRaw = String(fd.get("required_keywords") || "").trim();
      if (!taskName) { alert("请填写任务名 / 艺人"); return; }
      if (decisionMode === "ai" && !description) {
        alert("AI 判断模式下，详细需求(description)不能为空。");
        form.elements["description"].focus();
        return;
      }
      const submitBtn = document.getElementById("aiGenerateSubmitBtn");
      if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "提交中…"; }
      const payload = {
        task_name: taskName,
        keyword: keyword,
        description: description,
        decision_mode: decisionMode,
      };
      if (decisionMode === "keyword" && requiredKwRaw) {
        payload.required_keywords = requiredKwRaw.split(/[,,]/).map(s => s.trim()).filter(Boolean);
      }
      const prog = document.getElementById("aiGenerateProgress");
      const stepsEl = document.getElementById("aiGenerateSteps");
      const footEl = document.getElementById("aiGenerateFoot");
      const actions = document.getElementById("aiGenerateActions");
      if (prog) prog.hidden = false;
      if (actions) actions.hidden = true;
      try {
        const result = await postJson("/api/watchlist/generate", payload);
        if (decisionMode === "keyword") {
          if (stepsEl) stepsEl.innerHTML = '<li class="step done"><b>✅ 任务已创建</b><p>关键词模式直接落库，watch_id = ' + (result.id || "?") + '</p></li>';
          if (footEl) footEl.innerHTML = '<p style="color:#15803d;">关键词模式已秒级创建任务，跳到 <a href="#" id="aiGoTasks">任务管理</a> 页面查看。</p>';
          if (actions) actions.hidden = false;
          await refreshAll();
          return;
        }
        if (stepsEl) stepsEl.innerHTML = '<li class="step pending"><b>🚀 提交成功，后台任务已启动…</b><p>job_id: ' + result.job_id + '</p></li>';
        await pollAiGenerateJob(result.job_id);
      } catch (err) {
        if (stepsEl) stepsEl.innerHTML = '<li class="step failed"><b>❌ 提交失败</b><p>' + esc(String(err.message || err)) + '</p></li>';
        if (footEl) footEl.innerHTML = '<p style="color:#b91c1c;">请检查 .env 中的 AI_PROVIDER / MINIMAX_API_KEY 是否配置。</p>';
      } finally {
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "开始 AI 生成"; }
      }
    });
  }
  async function pollAiGenerateJob(jobId) {
    const stepsEl = document.getElementById("aiGenerateSteps");
    const footEl = document.getElementById("aiGenerateFoot");
    const actions = document.getElementById("aiGenerateActions");
    let lastSnapshot = null;
    for (let i = 0; i < 240; i++) {
      let snapshot;
      try {
        snapshot = await getJson("/api/watchlist/generate/" + encodeURIComponent(jobId));
      } catch (e) {
        await new Promise(r => setTimeout(r, 1500));
        continue;
      }
      if (snapshot && Array.isArray(snapshot.steps)) {
        const html = snapshot.steps.map(s => {
          const cls = s.status === "completed" ? "done" :
                      s.status === "running" ? "active" :
                      s.status === "failed" ? "failed" : "pending";
          return '<li class="step ' + cls + '"><b>' + esc(s.label) + '</b><p>' + esc(s.message || "") + '</p></li>';
        }).join("");
        if (stepsEl) stepsEl.innerHTML = html;
      }
      lastSnapshot = snapshot;
      if (snapshot.status === "completed") {
        if (footEl) {
          footEl.innerHTML =
            '<p style="color:#15803d;">✅ 任务生成完成。' +
            'watch_id=' + (snapshot.watch_id || "?") + ' · ' +
            'criteria=' + esc(snapshot.criteria_path || "?") + '</p>' +
            '<p class="quiet">已自动创建监控任务，可以到任务管理页面查看。</p>';
        }
        if (actions) actions.hidden = false;
        await refreshAll();
        return;
      } else if (snapshot.status === "failed") {
        if (footEl) {
          footEl.innerHTML = '<p style="color:#b91c1c;">❌ 生成失败：' + esc(snapshot.error || snapshot.message || "未知错误") + '</p>';
        }
        if (actions) actions.hidden = false;
        return;
      }
      await new Promise(r => setTimeout(r, 750));
    }
    if (footEl) footEl.innerHTML = '<p style="color:#b91c1c;">⏱ 轮询超时，请稍后在任务列表查看是否生成成功。</p>';
    if (actions && lastSnapshot) actions.hidden = false;
  }
  function bindAiGenerateActions() {
    const openBtn = document.getElementById("aiGenerateOpenTaskBtn");
    if (openBtn) openBtn.addEventListener("click", async () => {
      const m = document.getElementById("aiGenerateModal");
      if (m) m.hidden = true;
      await refreshAll();
      switchPage("tasks");
    });
    const closeBtn = document.getElementById("aiGenerateCloseBtn");
    if (closeBtn) closeBtn.addEventListener("click", () => {
      const m = document.getElementById("aiGenerateModal");
      if (m) m.hidden = true;
    });
    document.addEventListener("click", (e) => {
      if (e.target && e.target.id === "aiGoTasks") {
        e.preventDefault();
        const m = document.getElementById("aiGenerateModal");
        if (m) m.hidden = true;
        switchPage("tasks");
      }
    });
  }

  // ---------- accounts page ----------
  function renderLoginSummary(s) {
    if (!s) return { cls: "warn", label: "检测中", text: "等待返回" };
    const chip = chipForLoginStatus(s.status);
    let text = s.error_message || (s.status === "ready" ? "登录态有效" : "需要扫码登录");
    if (s.cookie_domains && s.cookie_domains.length) text += " · " + s.cookie_domains.join(", ");
    // Cookie expiry warning (绿=健康,黄=近7天,红=已过期)
    if (s.status === "ready") {
      const exp = Number(s.expired || 0);
      const near = Number(s.expiring_7d || 0);
      const total = Number(s.cookies_total || 0);
      if (exp > 0) text += " · ⚠ " + exp + " cookie 已过期";
      else if (near > 0) text += " · ⚠ " + near + " cookie 近 7 天到期";
      else if (total) text += " · " + total + " cookie 有效";
    }
    // Downgrade chip when any cookie is already expired
    let cls = chip.cls;
    let label = chip.label;
    if (s.status === "ready" && Number(s.expired || 0) > 0) {
      cls = "bad";
      label = "需重新登录";
    } else if (s.status === "ready" && Number(s.expiring_7d || 0) > 0) {
      cls = "warn";
      label = "即将失效";
    }
    return { cls: cls, label: label, text: text };
  }

  async function populateAccountOptions() {
    let items = [];
    try {
      const r = await getJson('/api/accounts');
      items = (r && r.items) || [];
    } catch (e) { items = []; }
    document.querySelectorAll('select[id$="AccountFileSelect"]').forEach((sel) => {
      const cur = sel.value;
      const seen = new Set([cur]);
      const makeOpt = (val, label) => {
        const o = document.createElement('option');
        o.value = val;
        o.textContent = label;
        return o;
      };
      sel.innerHTML = '';
      sel.appendChild(makeOpt('', '系统自动'));
      items.forEach((a) => {
        const v = a.state_file || (a.platform + '/' + a.name + '.json');
        if (seen.has(v)) return;
        seen.add(v);
        sel.appendChild(makeOpt(v, '[' + a.platform + '] ' + (a.name || v)));
      });
      sel.value = cur;
    });
  }

  function renderAccounts() {
    const xList = document.getElementById("xianyuAccounts");
    const mList = document.getElementById("wameijiAccounts");
    const xLine = document.getElementById("xianyuStatusLine");
    const mLine = document.getElementById("wameijiStatusLine");
    const xCard = xList ? xList.closest(".content-card") : null;
    const mCard = mList ? mList.closest(".content-card") : null;
    const filter = state.accountFilter || "all";
    if (xCard) xCard.style.display = (filter === "wameiji") ? "none" : "";
    if (mCard) mCard.style.display = (filter === "xianyu") ? "none" : "";
    if (xLine) {
      const x = renderLoginSummary(state.xianyuLogin);
      xLine.innerHTML = '<span class="status ' + x.cls + '">' + esc(x.label) + '</span> ' + esc(x.text);
    }
    if (mLine) {
      const w = renderLoginSummary(state.wameijiLogin);
      mLine.innerHTML = '<span class="status ' + w.cls + '">' + esc(w.label) + '</span> ' + esc(w.text);
    }
    if (xList) {
      const x = state.xianyuLogin;
      if (x && x.status === "ready") {
        xList.innerHTML =
          '<div class="account-card"><div><h4>闲鱼主号</h4><div class="task-meta">登录态有效 · ' + esc((x.cookie_domains || []).join(", ") || "已就绪") + '</div></div><span class="status ok">已登录</span></div>' +
          '<div class="account-card"><div><h4>闲鱼备用号</h4><div class="task-meta">空闲 · 等待调度</div></div><span class="status idle">空闲</span></div>';
      } else if (x && x.error_message) {
        xList.innerHTML = '<div class="account-card"><div><h4>闲鱼主号</h4><div class="task-meta">' + esc(x.error_message) + '</div></div><span class="status bad">异常</span></div>';
      } else {
        setEmpty(xList);
      }
    }
    if (mList) {
      const w = state.wameijiLogin;
      if (w && w.status === "ready") {
        mList.innerHTML =
          '<div class="account-card"><div><h4>挖煤姬主号</h4><div class="task-meta">登录态有效 · ' + esc((w.cookie_domains || []).join(", ") || "已就绪") + '</div></div><span class="status ok">采集中</span></div>' +
          '<div class="account-card"><div><h4>挖煤姬备用号</h4><div class="task-meta">自动调度可用</div></div><span class="status idle">空闲</span></div>';
      } else if (w && w.error_message) {
        mList.innerHTML = '<div class="account-card"><div><h4>挖煤姬主号</h4><div class="task-meta">' + esc(w.error_message) + '</div></div><span class="status bad">异常</span></div>';
      } else {
        setEmpty(mList);
      }
    }
    const allOnline = (state.xianyuLogin && state.xianyuLogin.status === "ready" ? 1 : 0) + (state.wameijiLogin && state.wameijiLogin.status === "ready" ? 1 : 0);
    const error = (state.xianyuLogin && state.xianyuLogin.error_message ? 1 : 0) + (state.wameijiLogin && state.wameijiLogin.error_message ? 1 : 0);
    setText("acctOnline", String(allOnline));
    setText("acctIdle", String(allOnline > 0 ? 1 : 0));
    setText("acctRunning", String(allOnline));
    setText("acctError", String(error));
  }

  async function refreshAccounts() {
    try {
      const [x, m, br] = await Promise.all([
        getJson("/api/login-state/xianyu").catch((e) => ({ status: "error", error_message: e.message })),
        getJson("/api/login-state/wameiji").catch((e) => ({ status: "error", error_message: e.message })),
        getJson("/api/browser/status").catch(() => null),
      ]);
      state.xianyuLogin = x;
      state.wameijiLogin = m;
      state.browser = br;
    } catch (e) {
      state.xianyuLogin = { status: "error", error_message: e.message };
      state.wameijiLogin = { status: "error", error_message: e.message };
    }
    renderAccounts();
  }

  async function onDetailAction(action, btn) {
    const id = btn.getAttribute("data-opp-id");
    const catalog = btn.getAttribute("data-catalog") || "";
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "处理中…";
    try {
      if (action === "approve") {
        await postJson("/api/review", { opportunity_id: Number(id), result: "accepted_for_personal_collection", note: "UI 端通过" });
        alert("机会 #" + id + " 已通过。");
      } else if (action === "reject") {
        await postJson("/api/review", { opportunity_id: Number(id), result: "rejected_other", note: "UI 端拒绝" });
        alert("机会 #" + id + " 已拒绝。");
      } else if (action === "recheck") {
        await postJson("/api/rechecks", { opportunity_id: Number(id), delay_seconds: 1800, reason: "ui_manual_recheck" });
        alert("已调度 30 分钟后复检。");
      } else if (action === "notify") {
        const r = await postJson("/api/notify", { opportunity_id: Number(id), dry_run: true, channel: "feishu" });
        alert("通知已发送（dry-run）。结果：" + JSON.stringify(r).slice(0, 300));
      } else if (action === "backtest") {
        if (!catalog) { alert("无品番"); return; }
        const r = await postJson("/api/backtest", { catalog_no: catalog });
        alert("回测 " + catalog + " 完成：\n" + JSON.stringify(r).slice(0, 600));
      }
      await refreshAll();
      const stillExists = state.opportunities.some((o) => String(o.id) === String(id));
      if (stillExists) showOpportunityDetail(id);
      else document.getElementById("detailModal").hidden = true;
    } catch (e) { alert("操作失败：" + e.message); }
    finally { btn.disabled = false; btn.textContent = original; }
  }

  function bindAccountActions() {
    document.getElementById("xianyuRefreshBtn")?.addEventListener("click", () => refreshAccounts());
    document.getElementById("wameijiRefreshBtn")?.addEventListener("click", () => refreshAccounts());
    document.getElementById("xianyuDeleteBtn")?.addEventListener("click", async () => {
      if (!confirm("确认清除闲鱼登录态？")) return;
      try { await deleteReq("/api/login-state/xianyu"); await refreshAccounts(); }
      catch (e) { alert("清除失败：" + e.message); }
    });
    document.getElementById("wameijiDeleteBtn")?.addEventListener("click", async () => {
      if (!confirm("确认清除挖煤姬登录态？")) return;
      try { await deleteReq("/api/login-state/wameiji"); await refreshAccounts(); }
      catch (e) { alert("清除失败：" + e.message); }
    });
  }

  // ---------- logs page ----------
  function renderLogs() {
    const list = document.getElementById("logList");
    if (!list) return;
    const items = [];
    state.searchRuns.forEach((r) => items.push({ at: r.started_at || r.created_at, kind: r.status === "ok" ? "task" : "error", status: r.status === "ok" ? "ok" : "bad", text: "扫描 " + (r.keyword || r.platform || "watchlist") + " 完成 · 发现 " + (r.found ?? r.count ?? 0) + " 条" + (r.status !== "ok" ? " · 异常: " + (r.error_type || r.error_message || "未知") : ""), action: "查看结果" }));
    state.reviews.forEach((r) => items.push({ at: r.reviewed_at || r.created_at, kind: "alert", status: r.result && String(r.result).startsWith("rejected") ? "warn" : "blue", text: "审核：品番 " + (r.catalog_no || r.opportunity_id || "--") + " → " + (r.result || "pending"), action: "查看证据" }));
    state.alerts.forEach((r) => items.push({ at: r.sent_at || r.created_at, kind: r.status === "failed" ? "error" : "alert", status: r.status === "failed" ? "bad" : "ok", text: "推送 " + (r.channel || "通知") + (r.status === "failed" ? " 失败" : " · " + r.status), action: "查看消息" }));
    state.rechecks.forEach((r) => items.push({ at: r.scheduled_at || r.created_at, kind: "task", status: r.status === "done" ? "ok" : "blue", text: "复检 #" + (r.id || "--") + " · 品番 " + (r.catalog_no || r.opportunity_id || "--") + " · " + (r.status || "queued"), action: r.status === "done" || r.status === "resolved" ? "已处理" : "标记解决", _action: r.status === "done" || r.status === "resolved" ? null : "resolve-recheck", _id: r.id }));
    if (state.xianyuLogin && state.xianyuLogin.status === "missing") items.push({ at: new Date().toISOString(), kind: "account", status: "warn", text: "闲鱼登录态未配置：请用浏览器扩展采集并粘贴到号池页。", action: "去配置" });
    if (state.wameijiLogin && state.wameijiLogin.status === "missing") items.push({ at: new Date().toISOString(), kind: "account", status: "warn", text: "挖煤姬登录态未配置：请用浏览器扩展采集并粘贴到号池页。", action: "去配置" });
    items.sort((a, b) => new Date(b.at || 0) - new Date(a.at || 0));
    const lf = state.logFilter || "all";
    const filtered = lf === "all" ? items : items.filter((it) => it.kind === lf);
    if (!filtered.length) { setEmpty(list); return; }
    list.innerHTML = filtered.slice(0, 80).map((it) => {
      const status = '<span class="status ' + esc(it.status || "blue") + '">' + esc(labelForStatus(it.status)) + '</span>';
      const actionHtml = it._action ? '<button class="action-btn" data-action="' + esc(it._action) + '" data-id="' + esc(it._id) + '">' + esc(it.action || "") + '</button>' : '<span class="action">' + esc(it.action || "") + '</span>';
      return [
        '<div class="log-line" data-kind="' + esc(it.kind) + '">',
        '<b>' + esc(timeShort(it.at)) + '</b>',
        status,
        '<span>' + esc(it.text) + '</span>',
        actionHtml,
        '</div>',
      ].join("");
    }).join("");
    list.querySelectorAll(".action-btn[data-action=resolve-recheck]").forEach((btn) => {
      btn.addEventListener("click", () => resolveRecheck(Number(btn.getAttribute("data-id"))));
    });
  }
  async function resolveRecheck(recheckId) {
    try {
      await postJson("/api/rechecks/" + encodeURIComponent(recheckId) + "/resolve", { status: "confirmed" });
      await refreshAll();
    } catch (e) { alert("解决复检失败：" + e.message); }
  }
  function labelForStatus(s) {
    return s === "ok" ? "成功" : s === "bad" ? "异常" : s === "warn" ? "风险" : s === "blue" ? "AI" : s === "failed" ? "失败" : "信息";
  }

  // ---------- settings page ----------
  function renderSettings() {
    const cfg = state.config || {};
    const br = state.browser || {};
    const cost = cfg.cost || {};
    const evaluation = cfg.evaluation || {};
    const jpyRate = Number(cost.wameiji_exchange_rate || 0.046);
    setText("settingsFx", "1 JPY = " + jpyRate.toFixed(4) + " CNY · 国际运费 ¥" + (cost.international_shipping_per_cd_cny ?? "--") + " · 二次中转 ¥" + (cost.china_reship_cost_cny ?? "--") + " · 风险准备金 ¥" + (cost.risk_reserve_min_cny ?? "--"));
    setText("settingsMargin", "强提醒阈值：利润 ≥¥" + (evaluation.strong_profit_min_cny ?? "--") + " 且利润率 ≥" + pctRaw((evaluation.strong_margin_min || 0) * 100) + " · 弱提醒阈值：利润 ≥¥" + (evaluation.weak_profit_min_cny ?? "--") + " 且利润率 ≥" + pctRaw((evaluation.weak_margin_min || 0) * 100));
    setText("settingsRisk", "置信度阈值（强/弱）: " + pctRaw((evaluation.min_match_confidence_strong || 0) * 100) + " / " + pctRaw((evaluation.min_match_confidence_weak || 0) * 100) + " · 议价折扣 " + pctRaw((evaluation.negotiation_discount || 0) * 100));
    setText("settingsMatch", "数据源：mock " + (cfg.data_sources?.mock?.ok ? "✓" : "✗") + " · 人工快照 " + (cfg.data_sources?.manual_snapshots?.ok ? "✓" : "✗") + " · 浏览器 " + (cfg.data_sources?.wameiji_live_browser?.status || "--") + " / " + (cfg.data_sources?.xianyu_live_browser?.status || "--"));
    setText("settingsFeed", "前端：信息流图片优先、置信度门槛 " + pctRaw((evaluation.min_match_confidence_strong || 0) * 100) + "、低置信度自动隐藏、风险商品默认折叠");
    const apiOk = !!state.summary;
    setText("settingsBackend", (apiOk ? "API 在线" : "API 离线") + " · 浏览器 " + (br.enabled ? "启用" : "禁用") + " · 风控 stop_on_captcha=" + (br.safety?.stop_on_captcha ? "true" : "false") + " · 通知 " + ((cfg.notify?.feishu_configured || cfg.notify?.dingtalk_configured) ? "已配置" : "未配置"));
    if (state.doctor) setText("doctorOutput", JSON.stringify(state.doctor, null, 2));
    else setText("doctorOutput", "运行中…");
    // user settings feedback
    const us = state.userSettings || {};
    const usLine = document.getElementById("settingsUserOverrides");
    if (usLine) {
      const keys = Object.keys(us);
      usLine.textContent = keys.length ? "用户覆盖： " + keys.map((k) => k + "=" + us[k]).join(" · ") : "用户覆盖：未设置";
    }
    // populate user-overrides form fields from saved values
    const uof = document.getElementById("userOverridesForm");
    if (uof) {
      if (uof.elements["kuro_theme"]) uof.elements["kuro_theme"].value = us.kuro_theme || "rose";
      const marginPct = Math.round(Number(us.min_margin || 0.30) * 100);
      if (uof.elements["min_margin"]) uof.elements["min_margin"].value = String(Number.isFinite(marginPct) ? marginPct : 30);
      if (uof.elements["min_diff"]) uof.elements["min_diff"].value = String(Number(us.min_diff || 1500));
    }
  }
  async function refreshSettings() {
    if (!state.config) { try { state.config = await getJson("/api/config"); } catch (e) { state.config = { error: e.message }; } }
    if (!state.browser) { try { state.browser = await getJson("/api/browser/status"); } catch (e) { state.browser = {}; } }
    try { state.doctor = await getJson("/api/doctor"); } catch (e) { state.doctor = { error: e.message }; }
    renderSettings();
  }

  // ---------- main bootstrap ----------
  setInterval(pollScraperStatus, 30000);
  async function pollScraperStatus() {
  try {
    const r = await getJson("/api/scraper-status");
    const el = document.getElementById("scraperStatus");
    const txt = document.getElementById("scraperStatusText");
    if (!el || !txt) return;
    const s = r.status || "unknown";
    el.className = "scraper-status " + s;
    let msg = "待机";
    if (s === "idle") msg = "实时采集中";
    else if (s === "starting") msg = "启动中";
    else if (s === "error") msg = "采集异常";
    else if (s === "not_running") msg = "未运行";
    if (r.last_run) msg += " · " + r.last_run.split(" ")[1];
    if (r.count != null && r.count > 0) msg += " · +" + r.count;
    txt.textContent = msg;
  } catch (e) { /* silent */ }
}

async function refreshAll() {
    const safe = async (p) => { try { return await p; } catch (e) { console.warn("api fail", e.message); return null; } };
    const [summary, opps, wl, rev, alerts, rechecks, runs, cfg, br, us, xyLogin, wmLogin, doctor] = await Promise.all([
      safe(getJson("/api/summary")),
      safe(getJson(buildOppsUrl(state.homeFeedPageSize, 0, false))),
      safe(getJson("/api/watchlist/all")),
      safe(getJson("/api/reviews")),
      safe(getJson("/api/alerts")),
      safe(getJson("/api/rechecks")),
      safe(getJson("/api/search-runs")),
      safe(getJson("/api/config")),
      safe(getJson("/api/browser/status")),
      safe(getJson("/api/user-settings")),
        safe(getJson("/api/login-state/xianyu")),
        safe(getJson("/api/login-state/wameiji")),
        safe(getJson("/api/doctor")),
    ]);
    state.summary = summary;
    state.opportunities = (opps && opps.items) || [];
    state.totalOpportunities = (opps && opps.total) || 0;
      state.homeFeedOffset = (opps && opps.items) ? opps.items.length : 0;
    state.homeFeedHasMore = !!(opps && opps.has_more);
    state.homeFeedLoading = false;
    state.watchlist = (wl && wl.items) || [];
    state.reviews = (rev && rev.items) || [];
    state.alerts = (alerts && alerts.items) || [];
    state.rechecks = (rechecks && rechecks.items) || [];
    state.searchRuns = (runs && runs.items) || [];
    const _kw = {};
    (state.searchRuns || []).forEach((r) => {
      const k = String(r.keyword || "");
      if (k && !_kw[k]) _kw[k] = r;
    });
    state.lastByKeyword = _kw;
    if (cfg) state.config = cfg;
    if (br) state.browser = br;
    // Expose the live JPY -> CNY rate so the card subtitle (uses window.JPY_RATE)
    // matches the actual config instead of the hardcoded 0.046 fallback.
    const _costCfg = (cfg && cfg.cost) || {};
    window.JPY_RATE = Number(_costCfg.wameiji_exchange_rate || 0.046);
    window.JPY_TO_CNY = window.JPY_RATE;
    state.userSettings = (us && us.items) || {};
      if (xyLogin) state.xianyuLogin = xyLogin;
      if (wmLogin) state.wameijiLogin = wmLogin;
      if (doctor) state.doctor = doctor;
    renderHomeKpi();
    renderHome();
    renderRanking();
    renderTasks();
    renderAccounts();
    renderLogs();
    renderSettings();
    updateHealth();
    updateLastUpdated();
    pollScraperStatus();
  }

  function updateHealth() {
    const line = document.getElementById("healthLine");
    if (!line) return;
    if (state.summary && !state.summary.error) {
      line.innerHTML = '<span class="status ok">在线</span> 数据库 ' + esc(state.summary.database || "已连接");
    } else if (state.summary && state.summary.error) {
      line.innerHTML = '<span class="status bad">离线</span> ' + esc(state.summary.error);
    } else {
      line.innerHTML = '<span class="status warn">检测中…</span>';
    }
  }
  function updateLastUpdated() {
    const el = document.getElementById("lastUpdated");
    if (!el) return;
    el.textContent = "最后更新：" + new Date().toLocaleTimeString("zh-CN", { hour12: false });
  }

    function bindFilterForm() {
    const form = document.getElementById("advancedFilterForm");
    if (!form) return;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const extra = {
        q: String(fd.get("q") || "").trim(),
        platform: String(fd.get("platform") || "all"),
        min_margin: String(fd.get("min_margin") || "").trim(),
        min_diff: String(fd.get("min_diff") || "").trim(),
        decision: String(fd.get("decision") || "all"),
      };
      state._lastAdvancedFilter = extra;
      renderHome(extra);
      const m = document.getElementById("filterModal");
      if (m) m.hidden = true;
    });
  }
  function bindGlobalButtons() {
    document.querySelectorAll("[data-feed-refresh]").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.preventDefault();
        b.disabled = true;
        const originalText = b.textContent;
        b.textContent = "抓取中…";
        // First trigger a fresh scrape to get new data, then dedupe & append
        try {
          await postJson("/api/scrape/full", {});
          for (let i = 0; i < 48; i++) {
            await new Promise((r) => setTimeout(r, 5000));
            try {
              const r = await getJson("/api/scrape/full-status");
              if (r && r.running === false && r.finished_at && r.started_at && r.finished_at >= r.started_at) {
                await new Promise((rr) => setTimeout(rr, 1500));
                break;
              }
            } catch (_) { /* ignore */ }
          }
        } catch (err) {
          console.warn("scrape/now failed", err);
        }
        // Fetch shuffled batches, dedupe by opp.id, append
        try {
          const seenIds = new Set(state.opportunities.map(o => o.id));
          let allNew = [];
          let tries = 0;
          while (allNew.length < state.homeFeedPageSize && tries < 6) {
            const opps = await getJson(buildOppsUrl(Math.max(state.homeFeedPageSize, 24), 0, true));
            const batch = (opps && opps.items) || [];
            for (const it of batch) {
              if (!seenIds.has(it.id) && !allNew.find(x => x.id === it.id)) {
                seenIds.add(it.id);
                allNew.push(it);
                if (allNew.length >= state.homeFeedPageSize) break;
              }
            }
            tries++;
            if (batch.length === 0) break;
          }
          if (allNew.length === 0) {
            state.opportunities = state.opportunities.slice().sort(() => Math.random() - 0.5);
            renderHome();
            const tip = document.createElement("div");
            tip.textContent = "\u5168\u90e8\u673a\u4f1a\u5df2\u663e\u793a (\u5171 " + state.opportunities.length + " \u5f20)";
            tip.style.cssText = "position:fixed;top:20px;right:20px;background:#5e8b5e;color:white;padding:10px 18px;border-radius:10px;z-index:9999;box-shadow:0 6px 20px rgba(0,0,0,0.2);font-weight:700;";
            document.body.appendChild(tip);
            setTimeout(() => tip.remove(), 1800);
            b.disabled = false;
            b.textContent = originalText;
            return;
          }
          state.opportunities = state.opportunities.concat(allNew);
          state.homeFeedOffset = state.opportunities.length;
          state.homeFeedHasMore = true;
          renderHome();
          b.disabled = false;
          b.textContent = originalText;
          const tip = document.createElement("div");
          tip.textContent = "\u65b0\u589e " + allNew.length + " \u5f20\u673a\u4f1a";
          tip.style.cssText = "position:fixed;top:20px;right:20px;background:#7c3aed;color:white;padding:10px 18px;border-radius:10px;z-index:9999;box-shadow:0 6px 20px rgba(0,0,0,0.2);font-weight:700;";
          document.body.appendChild(tip);
          window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
        } catch (err) {
          alert("\u52a0\u8f7d\u5931\u8d25\uff1a" + err.message);
        }
      });
    });
    document.getElementById("newTaskBtn")?.addEventListener("click", () => switchPage("tasks"));
    document.getElementById("sideNewTaskBtn")?.addEventListener("click", () => switchPage("tasks"));
    document.getElementById("advancedFilterBtn")?.addEventListener("click", () => {
      const m = document.getElementById("filterModal");
      if (m) m.hidden = false;
    });
    document.getElementById("proBtn")?.addEventListener("click", () => {
      const notes = [];
      notes.push("Pro 会员当前以本地数据库的 user_settings 覆盖实现，Kuro Atelier Edition 主题色可即时切换。");
      notes.push("支持：1) 多账号调度（账号池页）；2) 跨平台（platform 字段在新建任务表单）；3) AI 复核优先级（min_margin / match_confidence 阈值）。");
      notes.push("要解锁更多能力：在 .env 配置 notify 通道（feishu / dingtalk / bark / telegram / wecom），重启 web 服务即可推送真实通知。");
      alert(notes.join("\n\n"));
    });
    document.getElementById("settingsSaveBtn")?.addEventListener("click", onSettingsSave);
    document.getElementById("refreshAllBtn")?.addEventListener("click", async () => {
      const btn = document.getElementById("refreshAllBtn");
      if (btn) btn.disabled = true;
      const originalText = btn ? btn.textContent : "";
      if (btn) btn.textContent = "抓取中…";
      try {
        await postJson("/api/scrape/full", {});
      } catch (e) {
        console.warn("scrape/now failed", e);
      }
      // Poll until the /api/scrape/full cycle completes (running=false with started/finished_at set)
      for (let i = 0; i < 48; i++) {
        try {
          const r = await getJson("/api/scrape/full-status");
          if (r && r.running === false && r.finished_at && r.started_at && r.finished_at >= r.started_at) {
            await new Promise((rr) => setTimeout(rr, 1500));
            break;
          }
        } catch (e) { /* ignore */ }
        await new Promise((r) => setTimeout(r, 5000));
      }
      // Now refresh the feed
      await refreshAll();
      if (btn) {
        btn.textContent = "已刷新 · +新数据";
        setTimeout(() => { btn.textContent = originalText || "立即刷新"; btn.disabled = false; }, 1500);
      } else {
        alert("已触发立即刷新");
      }
    });
    document.getElementById("importDataBtn")?.addEventListener("click", openImportModal);
    document.getElementById("sideImportBtn")?.addEventListener("click", openImportModal);
    document.getElementById("runFullScanBtn")?.addEventListener("click", runFullScan);
    document.getElementById("sideFullScanBtn")?.addEventListener("click", runFullScan);
    document.getElementById("testNotifyBtn")?.addEventListener("click", testNotify);
    document.getElementById("generateReportBtn")?.addEventListener("click", generateReport);
  }
  async function generateReport() {
    try {
      const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const output = "data/snapshots/opportunity-report-" + ts + ".md";
      const r = await postJson("/api/report", { output: output, limit: 50 });
      alert("报告已生成：" + (r.output || output) + "\r\n机会总数：" + (r.opportunity_count || 0));
    } catch (e) { alert("生成报告失败：" + e.message); }
  }

  function openImportModal() {
    const m = document.getElementById("importModal");
    if (m) m.hidden = false;
    const r = document.getElementById("importResult");
    if (r) r.textContent = "选择类型，填写字段，点击「评估并入库」";
  }

  async function runFullScan() {
    try {
      const r = await postJson("/api/scan/watchlist", { notify: false });
      alert("全量扫描完成：扫描 " + (r.scanned_count || 0) + " 个任务，生成 " + (r.opportunity_count || 0) + " 条机会。");
      await refreshAll();
    } catch (e) { alert("扫描失败：" + e.message); }
  }

  async function testNotify() {
    try {
      const r = await postJson("/api/notify", { opportunity_id: 1, dry_run: true, channel: "feishu" });
      alert("通知测试（dry-run）已发送。响应：" + JSON.stringify(r).slice(0, 400));
    } catch (e) { alert("通知测试失败：" + e.message); }
  }

  async function onSettingsSave() {
    try {
      const overrides = {
        kuro_theme: state.userSettings?.kuro_theme || "rose",
        min_margin: state.userSettings?.min_margin || "0.30",
        min_diff: state.userSettings?.min_diff || "1500",
        feed_filter: state.feedFilter,
        advanced_filter: JSON.stringify(state._lastAdvancedFilter || {}),
      };
      const resp = await postJson("/api/user-settings", overrides);
      state.userSettings = { ...(state.userSettings || {}), ...overrides };
      alert("设置已保存：" + (resp.saved || 0) + " 项。");
      renderSettings();
    } catch (err) { alert("保存失败：" + err.message); }
  }

  function bindUserOverridesForm() {
    const form = document.getElementById("userOverridesForm");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const marginPct = Number(fd.get("min_margin") || 30);
      const overrides = {
        kuro_theme: String(fd.get("kuro_theme") || "rose"),
        min_margin: String((marginPct / 100).toFixed(2)),
        min_diff: String(Number(fd.get("min_diff") || 1500)),
      };
      try {
        const resp = await postJson("/api/user-settings", overrides);
        state.userSettings = { ...(state.userSettings || {}), ...overrides };
        applyKuroTheme(overrides.kuro_theme);
        alert("用户覆盖已保存：" + (resp.saved || 0) + " 项。");
        renderSettings();
      } catch (err) { alert("保存失败：" + err.message); }
    });
  }
  function applyKuroTheme(theme) {
    const t = String(theme || "rose").toLowerCase();
    // CSS selectors target body.kuro[data-kuro-theme=...], so apply to body.
    const target = document.body || document.documentElement;
    target.setAttribute("data-kuro-theme", t);
    try { localStorage.setItem("kuro_theme", t); } catch (_) {}
  }
  function bindFeedFilter() {
    document.querySelectorAll("[data-feed-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-feed-filter]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.feedFilter = btn.getAttribute("data-feed-filter");
        renderHome();
      });
    });
  }

  function bindGlobalSearch() {
    const input = document.getElementById("globalSearch");
    if (!input) return;
    let timer = null;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = input.value.trim();
        if (!q) { renderHome(); return; }
        try {
          const resp = await getJson("/api/search?q=" + encodeURIComponent(q));
          const items = (resp && resp.items) || [];
          const feed = document.getElementById("homeFeed");
          if (!feed) return;
          if (!items.length) { setEmpty(feed); return; }
          feed.innerHTML = items.map(renderOpportunityCard).join("");
          // Cards are <a> elements; browser handles click navigation.
        } catch (e) { /* ignore */ }
      }, 250);
    });
  }

  function bindModalCloses() {
    document.querySelectorAll("[data-modal-close]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-modal-close");
        const m = document.querySelector('[data-modal="' + key + '"]');
        if (m) m.hidden = true;
      });
    });
    document.querySelectorAll(".modal-mask").forEach((mask) => {
      mask.addEventListener("click", (e) => {
        if (e.target === mask) mask.hidden = true;
      });
    });
  }

  function bindAdvancedFilter() {
    const form = document.getElementById("advancedFilterForm");
    if (!form) return;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const filter = {
        q: String(fd.get("q") || "").trim(),
        platform: String(fd.get("platform") || "all"),
        min_margin: Number(fd.get("min_margin") || 0),
        min_diff: Number(fd.get("min_diff") || 0),
        decision: String(fd.get("decision") || "all"),
      };
      state._lastAdvancedFilter = filter;
      const m = document.getElementById("filterModal");
      if (m) m.hidden = true;
      // If we have a query, hit /api/search; otherwise filter local
      if (filter.q) {
        getJson("/api/search?q=" + encodeURIComponent(filter.q))
          .then((resp) => {
            let items = (resp && resp.items) || [];
            if (filter.decision !== "all") items = items.filter((o) => o.decision === filter.decision);
            if (filter.min_margin) items = items.filter((o) => (Number(o.net_margin) || 0) * 100 >= filter.min_margin);
            if (filter.min_diff) items = items.filter((o) => (Number(o.expected_profit) || 0) >= filter.min_diff);
            const feed = document.getElementById("homeFeed");
            if (!feed) return;
            if (!items.length) { setEmpty(feed); return; }
            feed.innerHTML = items.map(renderOpportunityCard).join("");
            // Cards are <a> elements; browser handles click navigation.
          })
          .catch(() => renderHome(filter));
      } else {
        renderHome(filter);
      }
    });
  }

  function bindTaskFilter() {
    document.querySelectorAll("[data-task-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-task-filter]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.taskFilter = btn.getAttribute("data-task-filter");
        renderTasks();
      });
    });
  }
  function bindLogFilter() {
    document.querySelectorAll("[data-log-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-log-filter]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.logFilter = btn.getAttribute("data-log-filter");
        renderLogs();
      });
    });
  }
  function bindRankSort() {
    document.querySelectorAll("[data-rank-sort]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-rank-sort]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.rankSort = btn.getAttribute("data-rank-sort");
        renderRanking();
      });
    });
  }
  function bindAccountFilter() {
    document.querySelectorAll("[data-account-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-account-filter]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.accountFilter = btn.getAttribute("data-account-filter");
        renderAccounts();
      });
    });
  }
  function bindPasteLoginState() {
    const xArea = document.getElementById("xianyuPasteArea");
    const xBtn = document.getElementById("xianyuPasteBtn");
    const mArea = document.getElementById("wameijiPasteArea");
    const mBtn = document.getElementById("wameijiPasteBtn");
    if (xBtn && xArea) {
      xBtn.addEventListener("click", async () => {
        const text = (xArea.value || "").trim();
        if (!text) { alert("请粘贴 Chrome 扩展采集的 JSON"); return; }
        xBtn.disabled = true;
        try {
          const r = await postJson("/api/login-state/xianyu", { snapshot: text });
          xArea.value = "";
          alert("闲鱼登录态已保存：" + JSON.stringify(r));
          await refreshAccounts();
        } catch (e) { alert("保存失败：" + e.message); }
        finally { xBtn.disabled = false; }
      });
    }
    if (mBtn && mArea) {
      mBtn.addEventListener("click", async () => {
        const text = (mArea.value || "").trim();
        if (!text) { alert("请粘贴 Chrome 扩展采集的 JSON"); return; }
        mBtn.disabled = true;
        try {
          const r = await postJson("/api/login-state/wameiji", { snapshot: text });
          mArea.value = "";
          alert("挖煤姬登录态已保存：" + JSON.stringify(r));
          await refreshAccounts();
        } catch (e) { alert("保存失败：" + e.message); }
        finally { mBtn.disabled = false; }
      });
    }
  }
  document.addEventListener("DOMContentLoaded", () => {
    bindNav();
    bindGlobalButtons();
    bindFeedFilter();
    bindGlobalSearch();
    bindModalCloses();
    bindAdvancedFilter();
    bindTaskForm();
    bindEditTaskForm();
    bindAiGenerateButton();
    bindAiGenerateForm();
    bindAiGenerateActions();
    bindImportForm();
    bindFilterForm();
    bindAccountActions();
    bindTaskFilter();
    bindLogFilter();
    bindRankSort();
    bindAccountFilter();
    bindPasteLoginState();
    bindUserOverridesForm();
    applyKuroTheme(state.userSettings?.kuro_theme || localStorage.getItem("kuro_theme"));
    refreshAll().catch((e) => console.error("refreshAll failed", e));
    setInterval(() => refreshAll().catch(() => {}), 60_000);
    // kuro_bridge: surface IIFE-private helpers to module-level code
    try {
      window.postJson = postJson;
      window.getJson = getJson;
      window.state = state;
      window.deleteReq = deleteReq;
      window.renderNotificationsAndAI = function(){ return renderNotificationsAndAI.apply(this, arguments); };
      window.flashToast = flashToast;
      window.refreshAll = refreshAll;
    } catch(e) { console.warn('kuro bridge failed', e); }
  });
})();

// =====================================================================
// P0 #3+#9+#10+#11+#5+#6 front-end wiring
// =====================================================================
async function onTaskRunNow(watchId) {
  try {
    const r = await postJson("/api/watchlist/" + encodeURIComponent(watchId) + "/start", {});
    if (r && r.queued) {
      flashToast("启动任务已提交，后台中运行。 job_id=" + (r.job_id || "--"));
    } else {
      flashToast("启动响应：" + JSON.stringify(r).slice(0, 240));
    }
  } catch (e) { flashToast("启动失败：" + e.message, true); }
}

async function onTaskRegenerate(watchId) {
  try {
    const r = await postJson("/api/watchlist/" + encodeURIComponent(watchId) + "/regenerate-criteria", {});
    if (r && r.queued) {
      flashToast("重新生成 AI 判定标准已提交，后台中运行。 job_id=" + (r.job_id || "--"));
    } else {
      flashToast("重新生成响应：" + JSON.stringify(r).slice(0, 240));
    }
  } catch (e) { flashToast("重新生成失败：" + e.message, true); }
}

function flashToast(msg, isError) {
  let t = document.getElementById("kuroToast");
  if (!t) {
    t = document.createElement("div");
    t.id = "kuroToast";
    t.style.cssText = "position:fixed;left:50%;bottom:32px;transform:translateX(-50%);background:rgba(28,28,32,.92);color:#fff;padding:10px 18px;border-radius:8px;font-size:13px;z-index:9999;box-shadow:0 4px 18px rgba(0,0,0,.25);max-width:80vw;";
    document.body.appendChild(t);
  }
  t.style.background = isError ? "rgba(176,32,32,.92)" : "rgba(28,28,32,.92)";
  t.textContent = String(msg);
  t.style.opacity = "1";
  clearTimeout(flashToast._timer);
  flashToast._timer = setTimeout(() => { t.style.opacity = "0"; }, 3500);
}

async function openBlacklistEditor(watchId) {
  const task = (state.watchlist || []).find((w) => String(w.id) === String(watchId));
  const title = task ? (task.catalog_no || ("任务 #" + watchId)) : ("任务 #" + watchId);
  let rule = { watch_id: Number(watchId), keywords: [], updated_at: null };
  try {
    rule = await getJson("/api/watchlist/" + encodeURIComponent(watchId) + "/blacklist-rules");
  } catch (e) {
    flashToast("加载黑名单失败：" + e.message, true);
    return;
  }
  const m = document.getElementById("blacklistModal");
  if (!m) return;
  document.getElementById("blacklistWatchLabel").textContent = title;
  document.getElementById("blacklistKeywords").value = (rule.keywords || []).join("\n");
  document.getElementById("blacklistUpdated").textContent = rule.updated_at ? ("最后更新：" + rule.updated_at) : "";
  m.dataset.watchId = String(watchId);
  m.hidden = false;
}

async function saveBlacklist() {
  const m = document.getElementById("blacklistModal");
  if (!m) return;
  const wid = m.dataset.watchId;
  const raw = document.getElementById("blacklistKeywords").value || "";
  const keywords = raw.split(/[\n,,\uFF0C]+/).map((s) => s.trim()).filter(Boolean);
  try {
    const r = await postJson("/api/watchlist/" + encodeURIComponent(wid) + "/blacklist-rules", { keywords: keywords });
    document.getElementById("blacklistUpdated").textContent = "最后更新：" + (r.updated_at || "");
    flashToast("黑名单已保存，共 " + (r.saved_count || 0) + " 条");
  } catch (e) { flashToast("保存黑名单失败：" + e.message, true); }
}

async function renderNotificationsAndAI() {
  try {
    const view = await getJson("/api/settings/notifications");
    const form = document.getElementById("notificationForm");
    if (form && view) {
      const fd = form.elements;
      if (fd["channel"]) fd["channel"].value = view.channel || "feishu";
      if (fd["feishu_webhook"]) fd["feishu_webhook"].value = view.feishu_webhook || "";
      if (fd["dingtalk_webhook"]) fd["dingtalk_webhook"].value = view.dingtalk_webhook || "";
      if (fd["bark_url"]) fd["bark_url"].value = view.bark_url || "";
      if (fd["telegram_bot_token"]) fd["telegram_bot_token"].value = view.telegram_bot_token || "";
      if (fd["telegram_chat_id"]) fd["telegram_chat_id"].value = view.telegram_chat_id || "";
      if (fd["wecom_webhook"]) fd["wecom_webhook"].value = view.wecom_webhook || "";
      if (fd["ntfy_url"]) fd["ntfy_url"].value = view.ntfy_url || "";
      if (fd["gotify_url"]) fd["gotify_url"].value = view.gotify_url || "";
    }
  } catch (e) {}
  try {
    const view = await getJson("/api/settings/ai");
    const form = document.getElementById("aiSettingsForm");
    const hint = document.getElementById("aiSourceHint");
    if (form && view) {
      const fd = form.elements;
      if (fd["provider"]) fd["provider"].value = view.provider || "openai";
      if (fd["base_url"]) fd["base_url"].value = view.base_url || "";
      if (fd["model_name"]) fd["model_name"].value = view.model_name || "";
      if (fd["proxy_url"]) fd["proxy_url"].value = view.proxy_url || "";
    }
    if (hint && view) {
      hint.textContent = "当前来源：" + (view.api_key_source || "env")
        + " · 是否配置：" + (view.is_configured ? "是" : "否")
        + " · model: " + (view.model_name || "--");
    }
  } catch (e) {}
}

async function onNotificationSubmit(e) {
  if (e) e.preventDefault();
  const form = document.getElementById("notificationForm");
  if (!form) return;
  const fd = new FormData(form);
  const payload = {
    channel: String(fd.get("channel") || "feishu"),
    feishu_webhook: String(fd.get("feishu_webhook") || "").trim(),
    dingtalk_webhook: String(fd.get("dingtalk_webhook") || "").trim(),
    bark_url: String(fd.get("bark_url") || "").trim(),
    telegram_bot_token: String(fd.get("telegram_bot_token") || "").trim(),
    telegram_chat_id: String(fd.get("telegram_chat_id") || "").trim(),
    wecom_webhook: String(fd.get("wecom_webhook") || "").trim(),
    ntfy_url: String(fd.get("ntfy_url") || "").trim(),
    gotify_url: String(fd.get("gotify_url") || "").trim(),
  };
  const fb = document.getElementById("notifyFeedback");
  try {
    const r = await postJson("/api/settings/notifications", payload);
    fb.textContent = "已保存，渠道=" + (r.view ? r.view.channel : "?");
    flashToast("通知配置已保存");
  } catch (err) {
    fb.textContent = "保存失败：" + err.message;
    flashToast("保存通知配置失败：" + err.message, true);
  }
}

async function onNotificationTest() {
  const form = document.getElementById("notificationForm");
  if (!form) return;
  const fd = new FormData(form);
  const channel = String(fd.get("channel") || "feishu");
  const payload = {
    channel: channel,
    title: "Kuro Atelier 测试通知",
    body: "这是从网页设置面板发出的测试消息。渠道=" + channel,
    dry_run: true,
  };
  try {
    const r = await postJson("/api/settings/notifications/test", payload);
    flashToast("测试响应：" + JSON.stringify(r).slice(0, 240));
  } catch (e) {
    flashToast("测试失败：" + e.message, true);
  }
}

async function onAISubmit(e) {
  if (e) e.preventDefault();
  const form = document.getElementById("aiSettingsForm");
  if (!form) return;
  const fd = new FormData(form);
  const payload = {
    provider: String(fd.get("provider") || "openai"),
    base_url: String(fd.get("base_url") || "").trim(),
    model_name: String(fd.get("model_name") || "").trim(),
    proxy_url: String(fd.get("proxy_url") || "").trim(),
    api_key: String(fd.get("api_key") || "").trim(),
  };
  const fb = document.getElementById("aiFeedback");
  try {
    const r = await postJson("/api/settings/ai", payload);
    fb.textContent = "已保存，写入 " + (r.saved || 0) + " 项。is_configured=" + (r.view ? r.view.is_configured : "?");
    flashToast("AI 配置已保存");
    await renderNotificationsAndAI();
  } catch (err) {
    fb.textContent = "保存失败：" + err.message;
    flashToast("保存 AI 配置失败：" + err.message, true);
  }
}

async function onAITest() {
  try {
    const r = await postJson("/api/settings/ai/test", { live: false });
    flashToast("AI 连通性：" + (r.reason || "--") + " configured=" + (r.view ? r.view.is_configured : "?"));
  } catch (e) {
    flashToast("AI 测试失败：" + e.message, true);
  }
}

function bindNewSettingsForms() {
  const nf = document.getElementById("notificationForm");
  if (nf && !nf.dataset.bound) {
    nf.dataset.bound = "1";
    nf.addEventListener("submit", onNotificationSubmit);
    const tb = nf.querySelector("[data-notify-test=\"true\"]");
    if (tb) tb.addEventListener("click", onNotificationTest);
  }
  const af = document.getElementById("aiSettingsForm");
  if (af && !af.dataset.bound) {
    af.dataset.bound = "1";
    af.addEventListener("submit", onAISubmit);
    const tb = af.querySelector("[data-ai-test=\"true\"]");
    if (tb) tb.addEventListener("click", onAITest);
  }
  const bm = document.getElementById("blacklistModal");
  if (bm && !bm.dataset.bound) {
    bm.dataset.bound = "1";
    const closeBtn = bm.querySelector("[data-modal-close=\"blacklist\"]");
    if (closeBtn) closeBtn.addEventListener("click", () => { bm.hidden = true; });
    const saveBtn = bm.querySelector("[data-blacklist-save]");
    if (saveBtn) saveBtn.addEventListener("click", saveBlacklist);
  }
}

function connectLiveFeed() {
  if (window._kuroLiveFeed && window._kuroLiveFeed.ws && window._kuroLiveFeed.ws.readyState <= 1) return;
  const wsOrigin = configuredApiBase() || window.location.origin;
  let wsUrl;
  try {
    const parsed = new URL(wsOrigin, window.location.href);
    parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
    parsed.pathname = "/ws";
    parsed.search = configuredApiToken() ? "?access_token=" + encodeURIComponent(configuredApiToken()) : "";
    parsed.hash = "";
    wsUrl = parsed.toString();
  } catch (_) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    wsUrl = proto + "//" + window.location.host + "/ws";
  }
  const url = wsUrl;
  let ws = null;
  let backoff = 1000;
  const setStatus = (cls, label) => {
    const el = document.getElementById("liveFeedStatus");
    if (!el) return;
    el.className = "status " + cls;
    el.textContent = label;
  };
  const connect = () => {
    try { ws = new WebSocket(url); } catch (e) {
      setStatus("bad", "实时连接失败");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
      return;
    }
    window._kuroLiveFeed = window._kuroLiveFeed || {};
    window._kuroLiveFeed.ws = ws;
    ws.addEventListener("open", () => { setStatus("ok", "实时连接已打开"); backoff = 1000; });
    ws.addEventListener("close", () => {
      setStatus("warn", "实时连接已断开，重连中");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    });
    ws.addEventListener("error", () => { setStatus("bad", "实时连接错误"); });
    ws.addEventListener("message", (evt) => {
      let msg = null;
      try { msg = JSON.parse(evt.data); } catch (e) { return; }
      if (!msg) return;
      const t = msg.type || "";
      if (t === "hello") return;
      if (t.indexOf("watch_action") === 0) {
        try { refreshAll(); } catch (e) {}
        const p = msg.payload || {};
        if (t === "watch_action_terminal") {
          flashToast("任务 #" + p.watch_id + " · " + (p.action || "") + " → " + (p.status || ""));
        }
      }
    });
  };
  connect();
}

(function _kuroBootstrap() {
  const wire = () => {
    bindNewSettingsForms();
    renderNotificationsAndAI();
    connectLiveFeed();
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire, { once: true });
  } else {
    wire();
  }
})();

// Patch V3: bridge IIFE-private helpers to module-level functions via window.
(function _kuroBridge() {
  try {
    if (typeof postJson === "function") window.postJson = postJson;
    if (typeof getJson === "function") window.getJson = getJson;
    if (typeof deleteReq === "function") window.deleteReq = deleteReq;
    if (typeof state !== "undefined") window.state = state;
    if (typeof refreshAll === "function") window.refreshAll = refreshAll;
    if (typeof renderNotificationsAndAI === "function") window.renderNotificationsAndAI = renderNotificationsAndAI;
    if (typeof flashToast === "function") window.flashToast = flashToast;
  } catch (e) {
    console.warn("kuro bridge failed", e);
  }
})();
// openBlacklistEditor_PATCH_V3


