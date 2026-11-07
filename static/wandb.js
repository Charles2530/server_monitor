"use strict";

(() => {
  const normalizeSeries = (series) => (Array.isArray(series) ? series : []).filter((item) => item && typeof item.metric === "string" && Array.isArray(item.points));
  const formatStatus = (value) => ({running: "运行中", finished: "已完成", failed: "失败", crashed: "崩溃", killed: "已停止", preempted: "已抢占"}[value] || value || "未知");
  const groupHistoryByMetric = (entries) => {
    const grouped = new Map();
    for (const entry of Array.isArray(entries) ? entries : []) {
      const label = entry?.label || entry?.runId || "Run";
      for (const series of normalizeSeries(entry?.history)) {
        if (!grouped.has(series.metric)) grouped.set(series.metric, []);
        grouped.get(series.metric).push({name: label, runId: entry?.runId || label, data: series.points});
      }
    }
    return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([metric, series]) => ({metric, series}));
  };
  window.MonitorWandb = { normalizeSeries, formatStatus, groupHistoryByMetric };
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const site = window.MonitorSite;
  const state = { entity: "", project: "", runs: [], selected: null, detail: null, request: 0, charts: [], historyByRun: new Map(), compareIds: new Set() };
  const text = (tag, value, className = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? "—" : String(value);
    return node;
  };

  async function api(path, options = {}) {
    const response = await (site?.fetch || fetch)(path, {
      ...options,
      headers: {
        ...(options.method && options.method !== "GET" ? {"X-Monitor-Request": "1"} : {}),
        ...(options.headers || {}),
      },
      cache: "no-store",
    });
    let data = null;
    try { data = await response.json(); } catch { throw new Error("W&B 返回了无法读取的数据。"); }
    if (!response.ok) throw new Error(data?.error || `W&B 服务暂不可用（${response.status}）。`);
    return data;
  }

  async function apiWithTimeout(path, timeout = 16000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try {
      return await api(path, {signal: controller.signal});
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("日志读取超时，Run 详情仍可用。", {cause: error});
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function setStatus(message, error = false) {
    $("wandb-status").textContent = message;
    $("wandb-status").classList.toggle("wandb-error", error);
  }

  function query() { return new URLSearchParams({entity: state.entity, project: state.project}); }

  function updateContextMeta(message, tone = "") {
    const meta = $("wandb-context-meta-text");
    const dot = document.querySelector(".wandb-context-dot");
    if (meta) meta.textContent = message;
    if (dot) dot.dataset.tone = tone;
  }

  function renderProjects(items) {
    const select = $("wandb-project");
    select.replaceChildren(text("option", "选择项目"));
    for (const item of items || []) {
      const option = text("option", item.name);
      option.value = item.name;
      select.append(option);
    }
    select.disabled = !(items || []).length;
    if (state.project && [...select.options].some((item) => item.value === state.project)) select.value = state.project;
  }

  function filteredRuns() {
    const search = $("wandb-search").value.trim().toLowerCase();
    const filter = $("wandb-state").value;
    return state.runs.filter((run) => {
      const matchesState = !filter || run.state === filter;
      const haystack = `${run.name || ""} ${run.display_name || ""} ${run.id || ""}`.toLowerCase();
      return matchesState && (!search || haystack.includes(search));
    });
  }

  function renderRuns() {
    const list = $("wandb-runs");
    const runs = filteredRuns();
    list.replaceChildren();
    $("wandb-run-count").textContent = String(runs.length);
    $("wandb-visible-count").textContent = `${runs.length} 条`;
    $("wandb-compare-count").textContent = `${state.compareIds.size} 个已选`;
    $("wandb-compare-apply").disabled = state.compareIds.size < 2;
    if (!runs.length) {
      list.append(text("p", state.runs.length ? "没有符合条件的 Run。" : "选择项目后读取 Runs。", "wandb-empty"));
      return;
    }
    for (const run of runs) {
      const item = document.createElement("div");
      item.className = "wandb-run-item";
      item.setAttribute("role", "listitem");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "wandb-run-check";
      check.checked = state.compareIds.has(run.id);
      check.setAttribute("aria-label", `将 ${run.display_name || run.name || run.id} 加入对比`);
      check.addEventListener("click", (event) => event.stopPropagation());
      check.addEventListener("change", () => {
        if (!check.checked && state.selected?.id === run.id) {
          check.checked = true;
          return;
        }
        if (check.checked) state.compareIds.add(run.id);
        else state.compareIds.delete(run.id);
        renderRuns();
      });
      const button = text("button", "", "wandb-run");
      button.type = "button";
      button.dataset.selected = String(state.selected?.id === run.id);
      button.dataset.state = run.state || "unknown";
      button.title = run.display_name || run.name || run.id;
      const row = document.createElement("span");
      row.className = "wandb-run-main";
      row.append(text("strong", run.display_name || run.name || run.id));
      row.append(text("small", run.id || "无 ID"));
      const stateLabel = text("span", formatStatus(run.state), "wandb-run-state");
      button.append(row, stateLabel);
      button.addEventListener("click", () => selectRun(run));
      item.append(check, button);
      list.append(item);
    }
  }

  function clearCharts() {
    for (const chart of state.charts) {
      try { chart.dispose(); } catch { /* chart may already be detached */ }
    }
    state.charts = [];
  }

  function renderMetricChart(element, series, request) {
    if (!window.MonitorCharts || !element) return;
    window.MonitorCharts.load().then((echarts) => {
      if (request !== state.request || !element.isConnected) return;
      const chart = echarts.init(element);
      state.charts.push(chart);
      const colors = ["#55c59a", "#6da9ff", "#c995ff", "#e2b35f", "#ee7b88"];
      chart.setOption({
        animation: false,
        tooltip: {trigger: "axis"},
        legend: {type: "scroll", textStyle: {color: "#9aada5"}},
        grid: {left: 45, right: 18, top: 35, bottom: 35},
        xAxis: {type: "value"},
        yAxis: {type: "value"},
        series: series.map((item, index) => ({name: item.name, type: "line", showSymbol: false, data: item.data, lineStyle: {color: colors[index % colors.length]}})),
      }, true);
    }).catch((error) => {
      const message = element.parentElement?.querySelector(".wandb-chart-message");
      if (message && request === state.request) message.textContent = error.message;
    });
  }

  function formatLogs(logs) {
    return (logs || []).map((line) => `${line.timestamp ? `[${line.timestamp}] ` : ""}${line.content}`).join("\n") || "暂无日志";
  }

  function renderLogs(detail, logSection, request) {
    const heading = document.createElement("div");
    heading.className = "wandb-detail-section-heading";
    heading.append(text("h4", "最近日志"), text("span", "按需读取", "wandb-section-note"));
    const hint = text("p", "日志文件可能较大，首次打开 Run 时不会阻塞指标和配置。", "wandb-message");
    const pre = text("pre", "点击“读取最近日志”加载输出。", "wandb-log-output wandb-log-placeholder");
    const button = text("button", "读取最近日志", "button button-secondary wandb-log-button");
    button.type = "button";
    const status = text("p", "", "wandb-message");
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "正在读取…";
      status.textContent = "日志读取中，最长等待 16 秒。";
      try {
        const params = new URLSearchParams({entity: detail.entity, project: detail.project, run_id: detail.id});
        const logs = await apiWithTimeout(`/api/wandb/logs?${params}`);
        if (request !== state.request) return;
        pre.classList.remove("wandb-log-placeholder");
        pre.textContent = formatLogs(logs);
        button.remove();
        status.textContent = `${logs.length || 0} 行日志`;
      } catch (error) {
        if (request !== state.request) return;
        status.textContent = error.message;
        status.classList.add("wandb-error");
        button.disabled = false;
        button.textContent = "重试读取日志";
      }
    });
    logSection.append(heading, hint, pre, button, status);
  }

  function renderDetail(detail, historyByRun, request) {
    clearCharts();
    const root = $("wandb-detail");
    root.replaceChildren();
    const heading = document.createElement("div");
    heading.className = "wandb-detail-heading";
    const title = document.createElement("div");
    title.append(text("span", "RUN DETAIL", "wandb-panel-kicker"));
    title.append(text("h3", detail.display_name || detail.name || detail.id));
    title.append(text("p", `${detail.entity}/${detail.project}/${detail.id} · ${formatStatus(detail.state)}`));
    const open = document.createElement("a");
    open.className = "button button-secondary";
    open.href = detail.url || "#";
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    open.innerHTML = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-9 9"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>打开 W&B';
    heading.append(title, open);
    root.append(heading);

    const summary = document.createElement("div");
    summary.className = "wandb-summary-grid";
    for (const [key, value] of Object.entries(detail.summary || {}).slice(0, 12)) {
      const card = document.createElement("div");
      card.className = "wandb-summary-card";
      card.append(text("small", key), text("strong", typeof value === "object" ? JSON.stringify(value) : value));
      summary.append(card);
    }
    if (!summary.children.length) summary.append(text("p", "此 Run 暂无摘要指标。", "wandb-message"));
    root.append(summary);

    const comparisonRuns = state.runs.filter((run) => state.compareIds.has(run.id));
    const historyEntries = comparisonRuns.map((run) => ({runId: run.id, label: run.display_name || run.name || run.id, history: historyByRun.get(run.id) || []}));
    const metricGroups = groupHistoryByMetric(historyEntries);
    const metrics = document.createElement("section");
    metrics.className = "wandb-detail-section";
    const metricHeading = document.createElement("div");
    metricHeading.className = "wandb-detail-section-heading";
    metricHeading.append(text("h4", comparisonRuns.length > 1 ? `指标对比 · ${comparisonRuns.length} 个 Run` : "指标 History"), text("span", `${metricGroups.length} 张图表`, "wandb-section-note"));
    const chartGrid = document.createElement("div");
    chartGrid.className = "wandb-charts-grid";
    if (!metricGroups.length) {
      chartGrid.append(text("p", "没有可展示的数值指标。", "wandb-message"));
    } else {
      metricGroups.forEach((group, index) => {
        const card = document.createElement("section");
        card.className = "wandb-metric-card";
        const cardHeading = document.createElement("div");
        cardHeading.className = "wandb-metric-heading";
        cardHeading.append(text("strong", group.metric), text("span", `${group.series.length} 条曲线`, "wandb-section-note"));
        const chart = document.createElement("div");
        chart.id = index === 0 ? "wandb-chart" : `wandb-chart-${index}`;
        chart.className = "wandb-chart";
        const chartMessage = text("p", "", "wandb-message wandb-chart-message");
        card.append(cardHeading, chart, chartMessage);
        chartGrid.append(card);
        renderMetricChart(chart, group.series, request);
      });
    }
    metrics.append(metricHeading, chartGrid);
    root.append(metrics);

    const config = document.createElement("section");
    config.className = "wandb-detail-section";
    const configHeading = document.createElement("div");
    configHeading.className = "wandb-detail-section-heading";
    configHeading.append(text("h4", "配置"), text("span", `${Object.keys(detail.config || {}).length} 项`, "wandb-section-note"));
    const dl = document.createElement("dl");
    dl.className = "wandb-config";
    for (const [key, value] of Object.entries(detail.config || {}).slice(0, 80)) dl.append(text("dt", key), text("dd", typeof value === "object" ? JSON.stringify(value) : value));
    config.append(configHeading, dl.children.length ? dl : text("p", "此 Run 没有公开配置。", "wandb-message"));
    root.append(config);

    const logSection = document.createElement("section");
    logSection.className = "wandb-detail-section";
    renderLogs(detail, logSection, request);
    root.append(logSection);

    const linkSection = document.createElement("section");
    linkSection.className = "wandb-detail-section";
    linkSection.append(text("h4", "关联服务器任务"));
    const form = document.createElement("form");
    form.className = "wandb-link-form";
    const label = document.createElement("label");
    label.append(text("span", "任务 ID"));
    const input = document.createElement("input");
    input.placeholder = "ACP:... 或 CCI:...";
    input.required = true;
    label.append(input);
    const button = text("button", "关联任务", "button button-secondary");
    button.type = "submit";
    form.append(label, button);
    const message = text("p", "", "wandb-message");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      message.classList.remove("wandb-error");
      try {
        await api("/api/wandb/task-links", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({task_id: input.value.trim(), entity: detail.entity, project: detail.project, run_id: detail.id})});
        message.textContent = "已关联当前站点任务。";
      } catch (error) {
        message.textContent = error.message;
        message.classList.add("wandb-error");
      } finally { button.disabled = false; }
    });
    linkSection.append(form, message);
    root.append(linkSection);
  }

  async function selectRun(run) {
    state.selected = run;
    if (state.compareIds.size <= 1) state.compareIds = new Set([run.id]);
    renderRuns();
    const request = ++state.request;
    clearCharts();
    $("wandb-detail").replaceChildren(text("p", "正在读取 Run 详情和指标…", "wandb-empty"));
    try {
      const params = new URLSearchParams({entity: run.entity, project: run.project, run_id: run.id});
      const [detail, history] = await Promise.all([
        api(`/api/wandb/run?${params}`),
        api(`/api/wandb/history?${params}`),
      ]);
      if (request !== state.request) return;
      state.detail = detail;
      state.historyByRun.set(run.id, history);
      renderDetail(detail, state.historyByRun, request);
    } catch (error) {
      if (request === state.request) $("wandb-detail").replaceChildren(text("p", error.message, "wandb-empty wandb-error"));
    }
  }

  async function compareSelectedRuns() {
    const selectedRuns = state.runs.filter((run) => state.compareIds.has(run.id));
    if (selectedRuns.length < 2) return;
    const primaryRun = state.selected || selectedRuns[0];
    state.selected = primaryRun;
    renderRuns();
    const request = ++state.request;
    const button = $("wandb-compare-apply");
    button.disabled = true;
    button.textContent = "正在读取指标…";
    setStatus(`正在读取 ${selectedRuns.length} 个 Run 的指标…`);
    $("wandb-detail").replaceChildren(text("p", `正在读取 ${selectedRuns.length} 个 Run 的详情和指标…`, "wandb-empty"));
    try {
      const detailPromise = state.detail ? Promise.resolve(state.detail) : (() => {
        const params = new URLSearchParams({entity: primaryRun.entity, project: primaryRun.project, run_id: primaryRun.id});
        return api(`/api/wandb/run?${params}`);
      })();
      const [detail] = await Promise.all([detailPromise, ...selectedRuns.map(async (run) => {
        if (state.historyByRun.has(run.id)) return;
        const params = new URLSearchParams({entity: run.entity, project: run.project, run_id: run.id});
        state.historyByRun.set(run.id, await api(`/api/wandb/history?${params}`));
      })]);
      if (request !== state.request) return;
      state.detail = detail;
      state.historyByRun.set(primaryRun.id, state.historyByRun.get(primaryRun.id) || []);
      renderDetail(detail, state.historyByRun, request);
      setStatus(`${selectedRuns.length} 个 Run · 指标对比已更新`);
    } catch (error) {
      if (request === state.request) setStatus(error.message, true);
    } finally {
      if (request === state.request) {
        button.disabled = false;
        button.textContent = "比较选中 Runs";
        renderRuns();
      }
    }
  }

  async function loadRuns() {
    if (!state.entity || !state.project) return;
    updateContextMeta(`正在同步 ${state.project} · Runs`, "loading");
    setStatus("正在读取 Runs…");
    try {
      state.runs = await api(`/api/wandb/runs?${query()}`);
      renderRuns();
      updateContextMeta(`${state.runs.length} 个 Run · ${state.project}`, "ready");
      setStatus(`${state.runs.length} 个 Run · ${state.entity}/${state.project}`);
    } catch (error) {
      state.runs = [];
      renderRuns();
      updateContextMeta("项目读取失败", "error");
      setStatus(error.message, true);
    }
  }

  async function loadPage() {
    try {
      const status = await api("/api/wandb/status");
      if (!status.configured) {
        setStatus("未配置 WANDB_API_KEY。请在本机环境变量中设置后重启服务。", true);
        updateContextMeta("W&B 未连接", "error");
        return;
      }
      state.entity = status.entity || "";
      const entity = $("wandb-entity");
      entity.replaceChildren(text("option", state.entity || "未配置 Entity"));
      entity.value = state.entity;
      if (!state.entity) {
        setStatus("未配置 WANDB_ENTITY。", true);
        updateContextMeta("等待 Entity", "error");
        return;
      }
      const projects = await api(`/api/wandb/projects?entity=${encodeURIComponent(state.entity)}`);
      renderProjects(projects);
      if (!state.project && projects.length) {
        state.project = projects[0].name;
        $("wandb-project").value = state.project;
      }
      updateContextMeta(`${projects.length} 个项目可用`, "ready");
      setStatus(`${projects.length} 个项目 · ${state.entity}`);
    } catch (error) {
      setStatus(error.message, true);
      updateContextMeta("W&B 连接失败", "error");
    }
  }

  $("wandb-entity").addEventListener("change", () => {
    state.entity = $("wandb-entity").value;
    state.project = "";
    state.runs = [];
    state.selected = null;
    state.detail = null;
    state.historyByRun = new Map();
    state.compareIds = new Set();
    state.request += 1;
    clearCharts();
    renderProjects([]);
    renderRuns();
    updateContextMeta("选择项目以继续", "");
  });
  $("wandb-project").addEventListener("change", () => {
    state.project = $("wandb-project").value;
    state.selected = null;
    state.detail = null;
    state.historyByRun = new Map();
    state.compareIds = new Set();
    state.request += 1;
    clearCharts();
    $("wandb-detail").replaceChildren(text("p", "选择一个 Run 查看指标、配置和日志。", "wandb-empty"));
    loadRuns();
  });
  $("wandb-state").addEventListener("change", renderRuns);
  $("wandb-search").addEventListener("input", renderRuns);
  $("wandb-compare-apply").addEventListener("click", compareSelectedRuns);
  $("wandb-refresh").addEventListener("click", () => { loadPage().then(loadRuns); });
  document.addEventListener("workspace-page", (event) => { if (event.detail === "experiments") loadPage().then(loadRuns); });
  document.addEventListener("monitor-refresh", () => { if (location.hash === "#experiments") loadPage().then(loadRuns); });
  if (location.hash === "#experiments") loadPage().then(loadRuns);
})();
