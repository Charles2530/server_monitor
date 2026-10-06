"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const known = (value) => typeof value === "number" && Number.isFinite(value);
  const count = (value) => known(value) ? value.toLocaleString("zh-CN") : "—";
  const money = (value) => known(value) ? `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}` : "—";
  const date = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "—";
  const platformName = (value) => ({ openai: "OpenAI", anthropic: "Claude", gemini: "Gemini", antigravity: "Antigravity", grok: "Grok", kimi: "Kimi", deepseek: "DeepSeek", zhipu: "智谱", minimax: "MiniMax" })[value] || value || "未知平台";
  const statusName = (value) => ({ active: "启用", inactive: "停用", expired: "已到期", quota_exhausted: "额度耗尽" })[value] || value || "未知";
  const today = (offset = 0) => {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(Date.now() + offset * 86400000));
    const get = (type) => parts.find((part) => part.type === type).value;
    return `${get("year")}-${get("month")}-${get("day")}`;
  };
  let snapshot = null, route = "", selectedTab = "dashboard", timer = null, stateController = null, stateSerial = 0;
  let trendDays = 7, trendData = null, trendSerial = 0, trendController = null, chart = null;
  const lists = { keys: { data: null, controller: null, serial: 0 }, usage: { data: null, controller: null, serial: 0 } };
  let keyCopy = null;
  $("tokenhub-start").value = today(-6);
  $("tokenhub-end").value = today();
  const section = (name) => snapshot?.sections?.[name];
  const sectionData = (name) => section(name)?.data;
  const fresh = (name) => section(name)?.status === "ok";
  const relevant = () => route === "overview" || route === "tokenhub";

  async function request(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options });
    let data;
    try { data = await response.json(); } catch { throw new Error("本地服务返回了无法读取的数据。"); }
    if (!response.ok) throw new Error(data.error || `读取失败（${response.status}）`);
    return data;
  }

  const metricIcons = [
    "M3 6.5h16a2 2 0 0 1 2 2V18H5a2 2 0 0 1-2-2V6.5Zm0 0V5a2 2 0 0 1 2-2h13v3.5M16 11h5v4h-5a2 2 0 0 1 0-4Z",
    "M4 17 10 11l4 4 6-9M15 6h5v5M4 4v16h16",
    "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
    "m13 2-9 12h7l-1 8 10-12h-7l1-8Z"
  ];
  function metric(label, value, note, stale = false, index = 0) {
    const card = node("div", undefined, `th-metric${stale ? " is-stale" : ""}`);
    const heading = node("div", undefined, "th-metric-heading");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24"); icon.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path"); path.setAttribute("d", metricIcons[index]); icon.append(path);
    heading.append(node("span", label), icon);
    const amount = node("strong");
    if (value.startsWith("$") && value.includes(".")) {
      const [integer, fraction] = value.slice(1).split(".");
      amount.append(node("span", "$", "th-currency"), document.createTextNode(integer), node("span", "." + fraction, "th-decimals"));
    } else amount.textContent = value;
    card.append(heading, amount, node("small", `${stale ? "旧数据 · " : ""}${note}`));
    return card;
  }

  function metrics(container) {
    const stats = sectionData("stats") || {}, account = sectionData("account") || {};
    container.replaceChildren(
      metric("账户余额 · USD", money(account.balance), `余额更新 ${date(section("account")?.updated_at)}`, !!section("account") && !fresh("account")),
      metric("今日消费 · USD", money(stats.today_actual_cost), "今日实际扣费 · 北京时间", !!section("stats") && !fresh("stats"), 1),
      metric("今日 Token", count(stats.today_tokens), "含输入、输出与缓存", !!section("stats") && !fresh("stats"), 2),
      metric("今日请求", count(stats.today_requests), `用量更新 ${date(section("stats")?.updated_at)}`, !!section("stats") && !fresh("stats"), 3));
  }

  function sectionStatus(name) {
    const part = section(name);
    return part ? `${part.status === "ok" ? "已更新" : "未更新 · " + (part.error || "数据可能过期")} · ${date(part.updated_at)}` : "连接后自动读取";
  }

  const windowName = (value) => ({ five_hour: "5 小时", seven_day: "7 天", seven_day_sonnet: "Sonnet · 7 天", seven_day_opus: "Opus · 7 天", daily: "每日", weekly: "每周", monthly: "每月" })[value] || value || "额度窗口";

  function quotaCard(row, compact = false) {
    const card = node("article", undefined, "th-quota");
    const heading = node("div", undefined, "th-quota-heading");
    heading.append(node("h3", row.name || platformName(row.platform)), node("span", row.available === true ? "可用" : row.available === false ? "不可用" : "状态未知", `th-badge${row.available === false ? " is-warning" : row.available === true ? "" : " is-unknown"}`));
    card.append(heading, node("small", [platformName(row.platform), row.subscription_tier].filter(Boolean).join(" · ")));
    const windows = [...(row.windows || []).map((entry) => ({ ...entry, label: windowName(entry.key) })), ...(row.model_quotas || []).map((entry) => ({ ...entry, label: entry.model || "模型额度" }))];
    const windowGrid = node("div", undefined, "th-quota-windows");
    for (const window of compact ? windows.slice(0, 2) : windows) {
      const item = node("div", undefined, `th-quota-window${window.utilization >= 90 ? " is-danger" : window.utilization >= 70 ? " is-warning" : ""}`);
      const line = node("div", undefined, "th-quota-line");
      line.append(node("span", window.label), node("strong", known(window.utilization) ? `${count(window.utilization)}% 已用` : "使用率未知")); item.append(line);
      if (known(window.utilization)) {
        const progress = node("progress"); progress.max = 100; progress.value = Math.max(0, Math.min(100, window.utilization));
        progress.setAttribute("aria-label", `${row.name || platformName(row.platform)} ${window.label}使用率`); item.append(progress);
      }
      if (window.unit && (known(window.used) || known(window.limit))) item.append(node("small", `已用 ${count(window.used)} / 上限 ${count(window.limit)} ${window.unit}`));
      item.append(node("small", `重置 ${date(window.resets_at)}`)); windowGrid.append(item);
    }
    card.append(windowGrid);
    if (!windows.length) card.append(node("p", "暂无周期额度数据", "th-empty"));
    if (!compact) for (const credit of row.credits || []) card.append(node("p", `${credit.type || "信用额度"} · ${count(credit.amount)}${known(credit.minimum_balance) ? ` · 最低余额 ${count(credit.minimum_balance)}` : ""}`, "th-credit"));
    card.append(node("small", `${fresh("account_quotas") ? "来源更新" : "旧数据 · 来源更新"} ${date(row.updated_at)}`));
    return card;
  }

  function renderAccountQuotas() {
    $("tokenhub-account-quotas-status").textContent = sectionStatus("account_quotas");
    const rows = sectionData("account_quotas");
    for (const [id, compact] of [["tokenhub-account-quotas", false], ["tokenhub-quota-preview", true]]) {
      const target = $(id); target.replaceChildren();
      if (!Array.isArray(rows) || !rows.length) target.append(node("p", rows ? "暂无可查看的账号额度" : "账号额度尚未获取", "th-empty"));
      else (compact ? rows.slice(0, 2) : rows).forEach((row) => target.append(quotaCard(row, compact)));
    }
  }

  function drawTrend() {
    if (!trendData || selectedTab !== "dashboard" || route !== "tokenhub") return;
    const rows = trendData.trend;
    const complete = rows.length === trendData.days && rows.every((row) => known(row.actual_cost));
    const sum = rows.reduce((value, row) => value + (known(row.actual_cost) ? row.actual_cost : 0), 0);
    const total = rows.some((row) => known(row.actual_cost)) ? money(sum) : "—";
    const main = node("div", undefined, "th-spend-main"), amount = node("strong");
    if (total.startsWith("$")) {
      const [integer, fraction] = total.slice(1).split(".");
      amount.append(node("span", "$", "th-spend-currency"), document.createTextNode(integer), node("span", "." + fraction, "th-spend-decimals"));
    } else amount.textContent = total;
    main.append(node("span", complete ? "区间实际花费" : "已返回记录花费", "th-spend-label"), amount);
    const period = node("div", undefined, "th-spend-period");
    period.append(node("span", `${trendData.days} 天 · USD`, "th-spend-range"), node("small", `${trendData.start_date} 至 ${trendData.end_date}`));
    $("tokenhub-trend-summary").replaceChildren(main, period);
    const byDate = new Map(rows.map((row) => [row.date, row]));
    const dates = Array.from({ length: trendData.days }, (_, index) => new Date(Date.parse(trendData.start_date + "T00:00:00Z") + index * 86400000).toISOString().slice(0, 10));
    if (!window.echarts) { $("tokenhub-trend-status").textContent = "图表组件不可用，请刷新页面。"; return; }
    chart = chart || echarts.init($("tokenhub-trend-chart"));
    const theme = getComputedStyle(document.documentElement);
    const chartInk = theme.getPropertyValue("--ink").trim(), chartMuted = theme.getPropertyValue("--muted").trim();
    const chartLine = theme.getPropertyValue("--line").trim(), chartSurface = theme.getPropertyValue("--surface").trim();
    const chartAccent = document.documentElement.dataset.theme === "dark" ? theme.getPropertyValue("--accent").trim() : "#159a76";
    chart.setOption({
      textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
      animation: false, color: [chartAccent],
      tooltip: { trigger: "axis", confine: true, renderMode: "richText", backgroundColor: chartSurface, borderColor: chartLine, borderWidth: 1, padding: [12, 16], textStyle: { color: chartInk, fontSize: 13 },
        axisPointer: { type: "line", lineStyle: { color: chartMuted, type: "dashed" } }, valueFormatter: (value) => known(value) ? money(value) + " USD" : "未知" },
      grid: { left: 20, right: 26, top: 26, bottom: 24, containLabel: true },
      xAxis: { type: "category", boundaryGap: false, data: dates, axisLabel: { formatter: (value) => value.slice(5), color: chartMuted, fontSize: 12, margin: 12, hideOverlap: true }, axisLine: { lineStyle: { color: chartLine } }, axisTick: { show: false } },
      yAxis: { type: "value", min: 0, splitNumber: 4, axisLabel: { color: chartMuted, fontSize: 12, margin: 12, formatter: (value) => money(value) }, splitLine: { lineStyle: { color: chartLine, type: "dashed" } } },
      series: [{ name: "实际花费", type: "line", smooth: false, showSymbol: dates.length <= 7, symbol: "circle", symbolSize: 7, connectNulls: false,
        itemStyle: { color: chartAccent, borderColor: chartSurface, borderWidth: 2 },
        lineStyle: { width: 2.5 }, areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: echarts.color.modifyAlpha(chartAccent, .22) }, { offset: 1, color: echarts.color.modifyAlpha(chartAccent, .01) }] } },
        data: dates.map((day) => known(byDate.get(day)?.actual_cost) ? byDate.get(day).actual_cost : null) }]
    }, true);
    chart.resize();
    $("tokenhub-trend-chart").setAttribute("aria-label", `每日实际花费趋势，单位 USD，${trendData.start_date} 至 ${trendData.end_date}，已返回记录花费合计 ${rows.some((row) => known(row.actual_cost)) ? money(sum) : "未知"}，${rows.length} 天有记录`);
  }

  async function loadTrend() {
    if (route !== "tokenhub" || selectedTab !== "dashboard") return;
    if (!snapshot?.connected) { $("tokenhub-trend-status").textContent = trendData ? "连接已失效，当前为旧数据。" : "请先连接 TokenHub。"; return; }
    trendController?.abort();
    const controller = new AbortController(), serial = ++trendSerial, days = trendDays; trendController = controller;
    const timeout = setTimeout(() => controller.abort(), 65000);
    $("tokenhub-trend-status").textContent = `正在读取近 ${days} 天趋势…`;
    try {
      const data = await request(`/api/tokenhub/trend?days=${days}`, { signal: controller.signal });
      if (serial !== trendSerial) return;
      if (!Array.isArray(data.trend) || data.days !== days) throw new Error("趋势数据格式无法识别。");
      trendData = data; drawTrend();
      $("tokenhub-trend-status").textContent = `更新 ${date(data.updated_at)} · 今日尚未结束；缺失日期不补零`;
    } catch (error) {
      if (serial !== trendSerial) return;
      $("tokenhub-trend-status").textContent = `${error.name === "AbortError" ? "读取超时" : error.message}。${trendData ? `保留旧数据 · ${trendData.start_date} 至 ${trendData.end_date} · 更新 ${date(trendData.updated_at)}` : "尚未取得趋势数据。"}`;
    } finally { clearTimeout(timeout); if (serial === trendSerial) trendController = null; }
  }

  function render() {
    metrics($("tokenhub-summary-metrics")); metrics($("tokenhub-metrics"));
    const quotaSummary = $("tokenhub-summary-quotas"); quotaSummary.replaceChildren();
    for (const row of sectionData("account_quotas") || []) {
      const values = (row.windows || []).map((window) => `${windowName(window.key)} ${known(window.utilization) ? `已用 ${count(window.utilization)}%` : "未知"}`);
      quotaSummary.append(node("span", `${row.name || platformName(row.platform)} · ${values.join(" / ") || "暂无周期额度"}${fresh("account_quotas") ? "" : " · 旧数据"}`));
    }
    const connecting = snapshot?.connecting, connected = snapshot?.connected, account = sectionData("account");
    const hasData = !!account;
    const text = connecting ? "等待你在本机浏览器完成 GitHub 登录…" : !snapshot ? "正在读取 TokenHub…" : snapshot.status === "auth_required" ? "登录已过期，请重新连接；当前为旧数据" : snapshot.status === "disconnected" ? "尚未连接 · 在本机完成一次 GitHub 登录即可同步" : snapshot.status === "ok" ? `已连接 · 余额更新 ${date(section("account")?.updated_at)}` : "部分数据未更新，请留意各项更新时间";
    $("tokenhub-status").textContent = text;
    $("tokenhub-status").dataset.state = snapshot?.status || "loading";
    $("tokenhub-summary-status").textContent = text;
    $("tokenhub-account").textContent = account?.display_name || account?.username || "我的 API 资源";
    const canConnect = snapshot?.can_connect !== false;
    $("tokenhub-disconnect").hidden = !canConnect || (!connected && !hasData && !connecting);
    for (const button of document.querySelectorAll("[data-tokenhub-connect]")) {
      button.hidden = !canConnect || (connected && !connecting);
      button.disabled = !!connecting;
      button.textContent = connecting ? "等待登录…" : "连接 TokenHub";
    }
    $("tokenhub-notice").textContent = snapshot?.login_error || (!connected ? "请在运行监控服务的电脑上连接。GitHub 登录完成后自动同步；若出现授权确认，请本人核对并完成。" : "");
    $("tokenhub-notice").hidden = !$("tokenhub-notice").textContent;
    renderAccountQuotas();
  }

  function emptyList(kind, text) {
    const row = node("tr"), cell = node("td", text, "th-empty"); cell.colSpan = kind === "keys" ? 6 : 5; row.append(cell);
    $(`tokenhub-${kind}-body`).replaceChildren(row);
  }

  let groupOptions = null, groupSerial = 0, groupController = null, groupSaving = false;
  function closeGroupDialog() {
    groupSerial += 1; groupController?.abort(); groupController = null; groupOptions = null;
    $("tokenhub-group-dialog").close();
  }
  async function openGroupDialog(item) {
    if (groupSaving) return;
    clearKeyCopy();
    closeGroupDialog();
    const serial = groupSerial, controller = new AbortController(); groupController = controller;
    const timeout = setTimeout(() => controller.abort(), 65000);
    const select = $("tokenhub-group-select");
    select.replaceChildren(new Option("正在读取可用分组…", "")); select.disabled = true;
    $("tokenhub-group-save").disabled = true;
    $("tokenhub-group-key").textContent = item.name || "未命名";
    $("tokenhub-group-status").textContent = "";
    $("tokenhub-group-dialog").showModal();
    try {
      const data = await request(`/api/tokenhub/key-groups?key_id=${item.id}`, { signal: controller.signal });
      if (serial !== groupSerial) return;
      if (!Array.isArray(data.items) || data.key?.id !== item.id || !Number.isInteger(data.account_id)) throw new Error("可用分组数据不完整。");
      groupOptions = data;
      select.replaceChildren(new Option("选择目标分组", ""));
      for (const group of data.items) {
        const name = group.name || "未命名", platform = platformName(group.platform);
        select.append(new Option(`${name}${name === platform ? "" : " · " + platform}${group.id === data.key.group_id ? "（当前）" : ""}`, String(group.id)));
      }
      if (data.items.some((group) => group.id === data.key.group_id)) select.value = String(data.key.group_id);
      select.disabled = !data.items.length;
      $("tokenhub-group-status").textContent = data.items.length ? "" : "暂无可切换的分组。";
    } catch (error) {
      if (serial === groupSerial) {
        select.replaceChildren(new Option("读取失败", ""));
        $("tokenhub-group-status").textContent = error.name === "AbortError" ? "读取超时，请关闭后重试。" : error.message;
      }
    } finally { clearTimeout(timeout); if (serial === groupSerial) groupController = null; }
  }
  $("tokenhub-group-select").addEventListener("change", () => {
    const target = Number($("tokenhub-group-select").value);
    $("tokenhub-group-save").disabled = groupSaving || !groupOptions || target === groupOptions.key.group_id || !groupOptions.items.some((group) => group.id === target);
  });
  $("tokenhub-group-cancel").addEventListener("click", closeGroupDialog);
  $("tokenhub-group-dialog").addEventListener("cancel", (event) => {
    event.preventDefault(); if (!groupSaving) closeGroupDialog();
  });
  $("tokenhub-group-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const options = groupOptions, groupId = Number($("tokenhub-group-select").value);
    if (groupSaving || !options || groupId === options.key.group_id || !options.items.some((group) => group.id === groupId)) return;
    groupSaving = true;
    const serial = groupSerial, controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 65000);
    for (const id of ["select", "save", "cancel"]) $(`tokenhub-group-${id}`).disabled = true;
    $("tokenhub-group-save").textContent = "正在保存…";
    $("tokenhub-group-status").textContent = "";
    try {
      await request("/api/tokenhub/key-group", { method: "POST", signal: controller.signal, headers: { "X-Monitor-Request": "1", "Content-Type": "application/json" }, body: JSON.stringify({ key_id: options.key.id, group_id: groupId, expected_group_id: options.key.group_id, account_id: options.account_id }) });
      if (serial !== groupSerial) return;
      closeGroupDialog();
      lists.keys.serial += 1; lists.keys.controller?.abort(); lists.keys.controller = null;
      await loadList("keys");
    } catch (error) {
      if (serial === groupSerial) {
        // A transport error can happen after the upstream write; re-read before retrying.
        groupOptions = null;
        $("tokenhub-group-status").textContent = `${error.name === "AbortError" ? "保存超时。" : error.message} 请关闭窗口，刷新后确认当前分组。`;
      }
    } finally {
      clearTimeout(timeout); groupSaving = false;
      $("tokenhub-group-cancel").disabled = false;
      $("tokenhub-group-save").textContent = "保存分组";
    }
  });

  function resetLists() {
    clearKeyCopy();
    closeGroupDialog();
    trendSerial += 1; trendController?.abort(); trendController = null; trendData = null; chart?.clear();
    $("tokenhub-trend-summary").textContent = "连接后查看每日花费趋势";
    $("tokenhub-trend-status").textContent = "";
    for (const [kind, list] of Object.entries(lists)) {
      list.serial += 1; list.controller?.abort(); list.controller = null; list.data = null;
      emptyList(kind, "连接后查看详情");
      $(`tokenhub-${kind}-status`).textContent = "";
      $(`tokenhub-${kind}-page`).textContent = "—";
      for (const direction of ["prev", "next"]) $(`tokenhub-${kind}-${direction}`).disabled = true;
    }
  }

  async function loadState(force = false) {
    if (!relevant() || stateController) return;
    clearTimeout(timer);
    const controller = new AbortController(), serial = ++stateSerial;
    stateController = controller;
    const timeout = setTimeout(() => controller.abort(), 75000);
    $("tokenhub-refresh").disabled = true;
    try {
      const data = await request(`/api/tokenhub/state${force ? "?refresh=true" : ""}`, { signal: controller.signal });
      if (serial !== stateSerial) return;
      if (!data.sections || typeof data.sections !== "object" || typeof data.status !== "string") throw new Error("TokenHub 状态格式无法识别。");
      const before = sectionData("account")?.id, after = data.sections.account?.data?.id;
      if (before !== after || data.status === "disconnected") resetLists();
      snapshot = data;
      if (!snapshot.connected) clearKeyCopy();
      render();
      if (route === "tokenhub") {
        if (selectedTab === "dashboard") { await loadTrend(); if ($("tokenhub-usage-details").open) await loadList("usage"); }
        if (selectedTab === "keys") await loadList("keys");
      }
    } catch (error) {
      if (serial !== stateSerial) return;
      const text = `${error.name === "AbortError" ? "读取超时" : error.message}。${snapshot ? "保留上次成功数据，当前可能过期。" : "尚未获取数据。"}`;
      $("tokenhub-status").textContent = text; $("tokenhub-summary-status").textContent = text;
      $("tokenhub-status").dataset.state = "error";
      $("tokenhub-notice").textContent = text; $("tokenhub-notice").hidden = false;
      for (const card of document.querySelectorAll(".th-metric")) card.classList.add("is-stale");
    } finally {
      clearTimeout(timeout);
      if (serial === stateSerial) {
        stateController = null; $("tokenhub-refresh").disabled = false;
        if (relevant()) timer = setTimeout(loadState, snapshot?.connecting ? 2000 : 60000);
      }
    }
  }

  function cell(main, sub) {
    const item = node("td"); item.append(node("strong", main));
    if (sub) item.append(node("small", sub));
    return item;
  }

  function dateCell(value, empty = "—") {
    if (!value) return cell(empty);
    const full = date(value), [day, ...time] = full.split(/\s+/);
    const item = cell(day, time.join(" "));
    item.className = "th-key-date";
    return item;
  }

  function clearKeyCopy() {
    const operation = keyCopy; keyCopy = null;
    if (operation) {
      operation.controller.abort(); clearTimeout(operation.timeout); operation.secret = "";
      operation.button.disabled = false; operation.button.textContent = "复制密钥";
    }
    $("tokenhub-key-copy-status").textContent = "";
  }

  function currentKeyCopy(operation) {
    return keyCopy === operation && route === "tokenhub" && selectedTab === "keys"
      && snapshot?.connected && sectionData("account")?.id === operation.accountId
      && operation.button.isConnected && !document.hidden;
  }

  function legacyCopy(secret) {
    const input = node("textarea", undefined, "th-copy-buffer");
    input.value = secret; input.setAttribute("aria-hidden", "true"); input.tabIndex = -1;
    const focused = document.activeElement;
    document.body.append(input);
    try { input.select(); return document.execCommand("copy"); }
    finally { input.value = ""; input.remove(); focused?.focus({ preventScroll: true }); }
  }

  async function finishKeyCopy(operation) {
    if (!currentKeyCopy(operation) || !operation.secret) return;
    operation.button.disabled = true;
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(operation.secret);
      else if (!legacyCopy(operation.secret)) throw new Error("clipboard_unavailable");
      if (!currentKeyCopy(operation)) return;
      clearKeyCopy();
      operation.button.textContent = "已复制";
      $("tokenhub-key-copy-status").textContent = "密钥已复制到剪贴板。";
      setTimeout(() => { if (operation.button.isConnected && operation.button.textContent === "已复制") operation.button.textContent = "复制密钥"; }, 2500);
    } catch {
      if (!currentKeyCopy(operation)) return;
      operation.button.disabled = false; operation.button.textContent = "再次点击复制";
      $("tokenhub-key-copy-status").textContent = "浏览器未允许复制，请再点一次；仍失败可前往原站复制。";
    }
  }

  async function copyKey(item, button) {
    if (keyCopy?.button === button) { await finishKeyCopy(keyCopy); return; }
    clearKeyCopy();
    if (!snapshot?.connected) { $("tokenhub-key-copy-status").textContent = "请先连接 TokenHub。"; return; }
    const operation = { button, accountId: sectionData("account")?.id, controller: new AbortController(), secret: "", timeout: null };
    keyCopy = operation; button.disabled = true; button.textContent = "正在读取…";
    operation.timeout = setTimeout(() => operation.controller.abort(), 65000);
    try {
      const data = await request("/api/tokenhub/key-copy", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Monitor-Request": "1" },
        body: JSON.stringify({ key_id: item.id, account_id: operation.accountId }), signal: operation.controller.signal
      });
      if (!currentKeyCopy(operation)) return;
      if (typeof data.key !== "string" || !data.key) throw new Error("未读取到完整密钥，请前往原站复制。");
      operation.secret = data.key; delete data.key;
      clearTimeout(operation.timeout);
      operation.timeout = setTimeout(() => {
        if (keyCopy !== operation) return;
        clearKeyCopy(); $("tokenhub-key-copy-status").textContent = "复制等待已结束，请重新点击复制密钥。";
      }, 30000);
      await finishKeyCopy(operation);
    } catch (error) {
      if (keyCopy !== operation) return;
      clearKeyCopy();
      $("tokenhub-key-copy-status").textContent = error.name === "AbortError" ? "读取超时，请重试。" : error.message;
    }
  }

  function renderList(kind, data) {
    if (kind === "keys") clearKeyCopy();
    const body = $(`tokenhub-${kind}-body`); body.replaceChildren();
    if (!data.items.length) emptyList(kind, kind === "keys" ? "暂无 API Key" : "该日期范围内暂无用量记录");
    for (const item of data.items) {
      const row = node("tr");
      if (kind === "keys") {
        const identity = cell(item.name || "未命名"); identity.className = "th-key-identity";
        const meta = node("div", undefined, "th-key-meta");
        meta.append(node("span", item.group_name || "未分组", "th-key-group"));
        const edit = node("button", "调整分组", "th-group-edit"); edit.type = "button";
        edit.setAttribute("aria-label", `调整 ${item.name || "未命名"} 的分组`);
        edit.disabled = !Number.isInteger(item.id) || item.id <= 0;
        edit.addEventListener("click", () => openGroupDialog(item)); meta.append(edit); identity.append(meta);
        const copy = node("button", "复制密钥", "th-key-copy"); copy.type = "button";
        copy.setAttribute("aria-label", `复制 ${item.name || "未命名"} 的密钥`);
        copy.disabled = !Number.isInteger(item.id) || item.id <= 0;
        copy.addEventListener("click", () => copyKey(item, copy)); meta.append(copy);
        const usage = node("td", undefined, "th-key-usage");
        for (const [label, value] of [["今日", item.today_actual_cost], ["近30天", item.last_30_days_actual_cost]]) {
          const line = node("div", undefined, "th-key-usage-line");
          line.append(node("span", label), node("strong", known(value) ? `$${value.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 })}` : "—")); usage.append(line);
        }
        const concurrency = cell(Number.isInteger(item.current_concurrency) && item.current_concurrency >= 0 ? count(item.current_concurrency) : "—");
        concurrency.firstElementChild.className = "th-concurrency";
        concurrency.firstElementChild.classList.toggle("is-busy", item.current_concurrency > 0);
        row.append(identity, cell(statusName(item.status)), concurrency, usage, dateCell(item.expires_at, item.expires_at === null ? "未设到期时间" : "—"), dateCell(item.last_used_at));
      }
      else {
        const tokens = node("td", undefined, "th-token-pair");
        for (const [label, value] of [["输入", item.input_tokens], ["输出", item.output_tokens]]) {
          const line = node("div"); line.append(node("span", label), node("strong", count(value))); tokens.append(line);
        }
        const amount = cell(money(item.actual_cost)); amount.className = "th-usage-cost";
        row.append(cell(item.model || "未知模型", date(item.created_at)), cell(item.key_name || "—"), tokens, cell(known(item.cache_creation_tokens) && known(item.cache_read_tokens) ? count(item.cache_creation_tokens + item.cache_read_tokens) : "—"), amount);
      }
      if (kind === "keys") {
        const badge = row.children[1].firstElementChild;
        badge.className = "th-key-status"; badge.dataset.status = item.status || "unknown";
      }
      const labels = kind === "keys" ? ["名称 / 分组", "状态", "当前并发", "用量 USD", "到期时间", "最近使用"] : ["时间 / 模型", "API 密钥名称", "输入 / 输出 Token", "缓存 Token", "实际扣费 USD"];
      Array.from(row.children).forEach((item, index) => { item.dataset.label = labels[index]; });
      body.append(row);
    }
    $(`tokenhub-${kind}-page`).textContent = `第 ${data.page} / ${data.pages} 页 · ${count(data.total)} 条`;
    $(`tokenhub-${kind}-prev`).disabled = data.page <= 1;
    $(`tokenhub-${kind}-next`).disabled = data.page >= data.pages;
  }

  async function loadList(kind, page) {
    if (route !== "tokenhub" || !lists[kind]) return;
    const list = lists[kind];
    if (!snapshot?.connected) {
      $(`tokenhub-${kind}-status`).textContent = list.data ? "连接已失效，当前为旧数据。" : "请先连接 TokenHub。"; return;
    }
    if (list.controller) return;
    const serial = ++list.serial, controller = new AbortController(); list.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 65000);
    page = page ?? list.data?.page ?? 1;
    const params = new URLSearchParams({ page });
    const range = kind === "usage" ? { start_date: $("tokenhub-start").value, end_date: $("tokenhub-end").value } : null;
    if (range) Object.entries(range).forEach(([key, value]) => params.set(key, value));
    $(`tokenhub-${kind}-status`).textContent = "正在读取…";
    try {
      const data = await request(`/api/tokenhub/${kind}?${params}`, { signal: controller.signal });
      if (serial !== list.serial) return;
      if (!Array.isArray(data.items) || !known(data.total) || !known(data.page) || !known(data.pages)) throw new Error("列表格式无法识别。");
      list.data = { ...data, range }; renderList(kind, data);
      $(`tokenhub-${kind}-status`).textContent = `已更新 ${date(data.updated_at)}${range ? ` · ${range.start_date} 至 ${range.end_date}` : ""}${data.usage_error ? ` · ${data.usage_error}` : ""}`;
    } catch (error) {
      if (serial !== list.serial) return;
      const oldRange = list.data?.range;
      $(`tokenhub-${kind}-status`).textContent = `${error.name === "AbortError" ? "读取超时" : error.message}。${list.data ? `保留上次结果 · ${date(list.data.updated_at)}${oldRange ? ` · ${oldRange.start_date} 至 ${oldRange.end_date}` : ""}` : "尚未取得数据。"}`;
      if (!list.data) emptyList(kind, "数据读取失败，可刷新重试");
    } finally {
      clearTimeout(timeout);
      if (serial === list.serial) list.controller = null;
    }
  }

  function activate(nextRoute) {
    clearKeyCopy();
    route = nextRoute; clearTimeout(timer);
    closeGroupDialog();
    trendSerial += 1; trendController?.abort(); trendController = null;
    stateSerial += 1; stateController?.abort(); stateController = null; $("tokenhub-refresh").disabled = false;
    for (const list of Object.values(lists)) { list.serial += 1; list.controller?.abort(); list.controller = null; }
    if (relevant()) loadState();
  }
  for (const button of document.querySelectorAll("[data-tokenhub-tab]")) button.addEventListener("click", () => {
    clearKeyCopy();
    selectedTab = button.dataset.tokenhubTab;
    for (const item of document.querySelectorAll("[data-tokenhub-tab]")) {
      const active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active));
    }
    for (const panel of document.querySelectorAll("[data-tokenhub-panel]")) panel.hidden = panel.dataset.tokenhubPanel !== selectedTab;
    if (selectedTab === "keys") loadList("keys");
    if (selectedTab === "dashboard") { drawTrend(); loadTrend(); }
  });
  for (const kind of Object.keys(lists)) for (const [direction, delta] of [["prev", -1], ["next", 1]]) {
    $(`tokenhub-${kind}-${direction}`).addEventListener("click", () => loadList(kind, (lists[kind].data?.page || 1) + delta));
  }
  $("tokenhub-usage-filter").addEventListener("submit", (event) => { event.preventDefault(); loadList("usage", 1); });
  $("tokenhub-refresh").addEventListener("click", () => loadState(true));
  $("refresh-button").addEventListener("click", () => { if (relevant()) loadState(true); });
  for (const button of document.querySelectorAll("[data-tokenhub-connect]")) button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await request("/api/tokenhub/connect", { method: "POST", headers: { "X-Monitor-Request": "1" } });
      snapshot = { ...(snapshot || { sections: {}, connected: false }), connecting: true }; render();
      clearTimeout(timer); timer = setTimeout(loadState, 1500);
    } catch (error) { $("tokenhub-notice").hidden = false; $("tokenhub-notice").textContent = error.message; $("tokenhub-summary-status").textContent = error.message; }
    finally { button.disabled = !!snapshot?.connecting; }
  });
  $("tokenhub-disconnect").addEventListener("click", async () => {
    if (!confirm("断开本机 TokenHub 连接？将清除本机保存的本站会话，原站账号和 API Key 不受影响。")) return;
    try {
      stateSerial += 1; stateController?.abort(); stateController = null;
      resetLists();
      snapshot = await request("/api/tokenhub/disconnect", { method: "POST", headers: { "X-Monitor-Request": "1" } });
      render();
    } catch (error) { $("tokenhub-notice").hidden = false; $("tokenhub-notice").textContent = error.message; }
  });
  for (const button of document.querySelectorAll("[data-tokenhub-days]")) button.addEventListener("click", () => {
    trendDays = Number(button.dataset.tokenhubDays);
    for (const item of document.querySelectorAll("[data-tokenhub-days]")) item.setAttribute("aria-pressed", String(item === button));
    loadTrend();
  });
  $("tokenhub-all-quotas").addEventListener("click", () => document.querySelector('[data-tokenhub-tab="quotas"]').click());
  $("tokenhub-usage-details").addEventListener("toggle", () => { if ($("tokenhub-usage-details").open) loadList("usage"); });
  window.addEventListener("resize", () => chart?.resize());
  // Account quota cards can resize the chart row without a window resize.
  new ResizeObserver(([entry]) => {
    if (entry.contentRect.width && entry.contentRect.height) chart?.resize();
  }).observe($("tokenhub-trend-chart"));
  document.addEventListener("appearance-change", () => { if (chart) drawTrend(); });
  for (const link of document.querySelectorAll('.th-page a[href^="https://tokenhub.ilab.beer/"]')) link.addEventListener("click", (event) => {
    if (snapshot?.can_connect !== false) return;
    event.preventDefault();
    $("tokenhub-original-url").value = link.href;
    $("tokenhub-copy-status").textContent = "也可以长按地址复制。";
    $("tokenhub-original-dialog").showModal();
  });
  $("tokenhub-copy-url").addEventListener("click", async () => {
    const input = $("tokenhub-original-url"); input.focus(); input.select();
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(input.value);
      else if (!document.execCommand("copy")) throw new Error("copy");
      $("tokenhub-copy-status").textContent = "地址已复制，请在手机浏览器打开。";
    } catch { $("tokenhub-copy-status").textContent = "请长按上方地址，选择复制。"; }
  });
  $("tokenhub-close-original").addEventListener("click", () => $("tokenhub-original-dialog").close());
  document.addEventListener("workspace-page", (event) => activate(event.detail));
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearKeyCopy(); });
  window.addEventListener("pagehide", clearKeyCopy);
  render(); activate(location.hash.slice(1) || "overview");
})();
