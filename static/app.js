"use strict";

(() => {
  const METRICS = [
    { key: "cpu", label: "CPU", color: "#3b82f6" },
    { key: "gpu", label: "GPU 利用率", color: "#14b8a6" },
    { key: "memory", label: "内存", color: "#8b5cf6" },
    { key: "gpu_memory", label: "显存占用", color: "#d99816" },
  ];
  const byId = (id) => document.getElementById(id);
  const model = { snapshot: null, kind: "all", search: "", selected: null, hours: 1, unavailable: false, fetching: false, historyRequest: 0, chart: null };
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
  const timeFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function known(value) { return typeof value === "number" && Number.isFinite(value) && value >= 0; }
  function count(value) { return known(value) ? numberFormat.format(value) : "—"; }
  function percent(value) { return known(value) && value <= 100 ? value : null; }
  function time(value) {
    const date = value ? new Date(value) : null;
    return date && Number.isFinite(date.getTime()) ? timeFormat.format(date) : "尚未采集";
  }

  function sumAvailable(items, key) {
    if (!items.length || items.some((item) => !known(item[key]))) return null;
    return items.reduce((sum, item) => sum + item[key], 0);
  }

  function tasks() { return Array.isArray(model.snapshot?.tasks) ? model.snapshot.tasks : []; }
  function clusters() { return Array.isArray(model.snapshot?.clusters) ? model.snapshot.clusters : []; }
  function visibleClusters() {
    return clusters().filter((cluster) => cluster.available_gpus !== 0 || cluster.inventory?.total_gpus > 0 || cluster.inventory_error);
  }
  function visibleTasks() {
    return tasks().filter((task) => (model.kind === "all" || task.kind === model.kind) && `${task.name || ""} ${task.cluster || ""} ${task.id || ""}`.toLocaleLowerCase().includes(model.search));
  }

  async function request(path, method = "GET") {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await (window.MonitorSite?.fetch || fetch)(path, { method, headers: method === "POST" ? { "X-Monitor-Request": "1" } : {}, signal: controller.signal, cache: "no-store" });
      let data;
      try { data = await response.json(); } catch { throw new Error("本地服务返回了无法读取的数据。"); }
      if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : `本地服务暂不可用（${response.status}）。`);
      return data;
    } finally { clearTimeout(timeout); }
  }

  function renderStatus() {
    const snapshot = model.snapshot;
    let style = "loading", symbol = "◌", title = "正在读取监控状态", description = "首次连接后，将开始采集你的任务与资源数据。";
    const stale = snapshot?.updated_at && Date.now() - new Date(snapshot.updated_at).getTime() > Math.max(180, (snapshot.interval_seconds || 60) * 3) * 1000;
    if (model.unavailable) {
      style = "error"; symbol = "!"; title = "本地服务连接中断";
      description = snapshot ? "正在展示上次快照，数据可能已过期。请确认本地服务仍在运行。" : "请确认本地服务正在运行；页面将自动重试。";
    } else if (snapshot) {
      if (snapshot.connecting) {
        title = "正在自动登录"; description = "正在使用本机保存的 IAM 凭据连接云平台，无需手动输入。";
      } else if (snapshot.status === "collecting") {
        title = "正在采集"; description = snapshot.updated_at ? "新的快照准备中，当前仍显示上次采集结果。" : "首次采集可能需要一些时间。";
      } else if (snapshot.status === "disconnected" || !snapshot.connected) {
        style = "disconnected"; symbol = "↗"; title = "等待连接云会话";
        description = snapshot.error || "系统会使用本地 IAM 配置自动连接，也可点击“连接会话”立即重试。";
        if (snapshot.updated_at) description += "当前展示历史快照。";
      } else if (snapshot.status === "error") {
        style = "error"; symbol = "!"; title = "采集遇到问题"; description = snapshot.error || "暂时无法读取云端数据，请稍后刷新。";
      } else if (snapshot.status === "partial" || stale) {
        style = "warning"; symbol = "!"; title = stale ? "数据已超过预期更新时间" : "部分数据暂不可用";
        description = snapshot.error || "已保留可用快照；缺失指标以“—”显示，请留意各项采集时间。";
      } else {
        style = "ok"; symbol = "✓"; title = "采集正常"; description = "监控连接已就绪，资源数据自动更新。";
      }
    }
    byId("status-banner").className = `status-banner status-${style}`;
    byId("status-symbol").textContent = symbol;
    byId("status-title").textContent = title;
    byId("status-description").textContent = description;
    byId("last-updated").textContent = snapshot?.updated_at ? `最近采集 ${time(snapshot.updated_at)}` : "等待首次采集";
    if (snapshot?.updated_at) byId("last-updated").dateTime = snapshot.updated_at;
    byId("connect-button").firstChild.textContent = snapshot?.connected ? "重新连接 " : "连接会话 ";
  }

  function renderSummary() {
    const hasSnapshot = Boolean(model.snapshot?.updated_at);
    const listsKnown = hasSnapshot && ["CCI", "ACP"].every((kind) => model.snapshot?.source_status?.[kind]?.status === "ok");
    const list = tasks(), clusterList = visibleClusters();
    byId("summary-tasks").textContent = listsKnown ? count(list.length) : "—";
    byId("task-count").textContent = listsKnown ? count(list.length) : "—";
    const services = byId("summary-services");
    services.replaceChildren(document.createTextNode(`CCI ${model.snapshot?.source_status?.CCI?.status === "ok" ? list.filter((task) => task.kind === "CCI").length : "—"}`), element("span", "", "·"), document.createTextNode(`ACP ${model.snapshot?.source_status?.ACP?.status === "ok" ? list.filter((task) => task.kind === "ACP").length : "—"}`));
    byId("summary-gpus").textContent = !listsKnown ? "—" : list.length === 0 ? "0" : count(sumAvailable(list, "gpu_count"));
    byId("summary-available").textContent = count(sumAvailable(clusters().filter((cluster) => known(cluster.available_gpus)), "available_gpus"));
    byId("summary-clusters").textContent = clusterList.length ? `${clusterList.filter((cluster) => known(cluster.available_gpus)).length} 个显示池的已知余量合计 · ${clusterList.filter((cluster) => !known(cluster.available_gpus)).length} 个未知` : clusters().length ? "暂无有 GPU 的标准资源" : "等待标准集群资源数据";
    renderOverview(listsKnown);
  }

  function renderOverview(listsKnown) {
    const list = tasks();
    const signature = JSON.stringify([list, visibleClusters(), listsKnown, model.unavailable, model.snapshot?.connected, model.snapshot?.source_status]);
    if (signature === model.overviewSignature) return;
    model.overviewSignature = signature;
    const totals = ["CCI", "ACP"].map((kind) => {
      const group = list.filter((task) => task.kind === kind);
      const sourceKnown = Boolean(model.snapshot?.updated_at) && model.snapshot?.source_status?.[kind]?.status === "ok";
      const total = sourceKnown ? (group.length ? sumAvailable(group, "gpu_count") : 0) : null;
      const label = byId(`allocation-${kind.toLowerCase()}`);
      label.replaceChildren(document.createTextNode(count(total)), element("small", "", "张"));
      byId(`allocation-${kind.toLowerCase()}-tasks`).textContent = `${sourceKnown ? count(group.length) : "—"} 个运行任务`;
      return total;
    });
    const total = listsKnown && totals.every(known) ? totals[0] + totals[1] : null;
    byId("allocation-total").textContent = count(total);
    const ring = byId("allocation-ring");
    ring.style.background = total > 0 ? `conic-gradient(#3b82f6 0 ${totals[0] / total * 100}%, #14b8a6 ${totals[0] / total * 100}% 100%)` : "var(--line)";
    ring.setAttribute("aria-label", total === null ? "配置 GPU 总量未知" : `配置 GPU 共 ${count(total)} 张，CCI ${count(totals[0])} 张，ACP ${count(totals[1])} 张`);
    byId("allocation-note").textContent = total === null ? "部分任务来源或配置未知，暂不计算分布。" : "配置数量不代表实时 GPU 利用率。";

    const resources = byId("overview-resources");
    const clusterList = visibleClusters().slice().sort((a, b) => (b.available_gpus ?? -1) - (a.available_gpus ?? -1));
    const fragment = document.createDocumentFragment();
    for (const cluster of clusterList.slice(0, 4)) {
      const row = element("div", "resource-preview");
      const name = element("span", "resource-preview-name", cluster.name || cluster.id || "未命名集群");
      const value = element("strong", "", count(cluster.available_gpus));
      value.append(element("small", "", "张可用"));
      row.append(element("span", "resource-preview-icon", "GPU"), name, value);
      fragment.append(row);
    }
    if (!clusterList.length) fragment.append(element("p", "overview-empty", model.snapshot?.connected ? "暂无可展示的 GPU 资源" : "连接后查看集群资源"));
    resources.replaceChildren(fragment);

    const recent = document.createDocumentFragment();
    for (const task of list.slice().sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)).slice(0, 4)) {
      const row = element("button", "overview-task");
      row.type = "button";
      row.setAttribute("aria-label", `查看 ${task.name || task.id} 的监控`);
      const info = element("span", "overview-task-info");
      const title = element("strong", "", task.name || task.id);
      title.title = task.name || task.id;
      info.append(title, element("small", "", `${task.id} · ${count(task.gpu_count)} GPU · ${count(task.replicas)} 副本`));
      const gpuMetrics = element("span", "overview-task-gpu");
      for (const key of ["gpu", "gpu_memory"]) {
        const metric = element("span", "overview-task-metric");
        metric.dataset.metric = key;
        const label = METRICS.find((item) => item.key === key).label;
        const value = percent(task.metrics?.[key]);
        const sampledAt = Date.parse(task.metric_updated_at?.[key] || task.updated_at);
        const stale = model.unavailable || !model.snapshot?.connected || task.failed_metrics?.includes(key) || model.snapshot?.source_status?.[task.kind]?.status !== "ok" || (Number.isFinite(sampledAt) && Date.now() - sampledAt > 300000);
        metric.append(element("strong", "", value === null ? "—" : `${count(value)}%`), element("small", "", `${label}${stale ? " · 旧快照" : ""}`));
        if (stale) metric.classList.add("is-stale");
        gpuMetrics.append(metric);
      }
      row.append(element("span", `service-icon ${task.kind === "CCI" ? "blue" : "teal"}`, task.kind), info, gpuMetrics, element("span", "row-arrow", "›"));
      row.addEventListener("click", () => {
        model.selected = task.id; model.search = ""; model.kind = "all";
        byId("task-search").value = "";
        document.querySelectorAll(".filter-button").forEach((button) => {
          const selected = button.dataset.kind === "all";
          button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected));
        });
        renderTasks(); renderDetail(); location.hash = "tasks";
      });
      recent.append(row);
    }
    if (!list.length) recent.append(element("p", "overview-empty", !model.snapshot?.connected ? "连接云会话后查看运行任务" : listsKnown ? "暂无运行任务，创建任务后将在此展示。" : "任务列表暂时未知，等待下一次采集。"));
    byId("overview-task-list").replaceChildren(recent);
  }

  function emptyState(title, description, icon = "▤") {
    const box = element("div", "empty-state");
    const symbol = element("span", "empty-icon", icon);
    symbol.setAttribute("aria-hidden", "true");
    box.append(symbol, element("h3", "", title), element("p", "", description));
    return box;
  }

  function renderTasks() {
    const list = visibleTasks(), container = byId("task-list");
    if (!list.length) {
      let title = "当前没有运行任务", description = "连接正常。CCI 与 ACP 的运行任务将在采集后显示在这里。";
      if (model.search || model.kind !== "all") { title = "没有符合条件的任务"; description = "尝试更换服务类型，或使用不同的任务、集群关键词。"; }
      else if (model.unavailable && !model.snapshot) { title = "暂时无法读取任务"; description = "恢复本地服务连接后，任务会自动显示。"; }
      else if (!model.snapshot?.connected) { title = "连接后，开始观察你的算力"; description = "系统将使用本地 IAM 配置自动登录，连接后即可读取 charles 的运行任务。"; }
      else if (["CCI", "ACP"].some((kind) => model.snapshot?.source_status?.[kind]?.status !== "ok")) { title = "任务数量暂时未知"; description = "任务列表尚未成功采集，请等待重试或重新连接云会话。"; }
      else if (!model.snapshot?.updated_at) { title = "等待首次任务快照"; description = "采集器正在准备数据，请稍候。"; }
      container.replaceChildren(emptyState(title, description));
      model.selected = null;
      byId("detail-panel").hidden = true;
      document.dispatchEvent(new CustomEvent("monitor-selection", { detail: null }));
      model.historyRequest += 1;
      return;
    }
    if (!list.some((task) => task.id === model.selected)) model.selected = list[0].id;
    const fragment = document.createDocumentFragment();
    for (const task of list) {
      const card = element("button", `task-card${task.id === model.selected ? " selected" : ""}`);
      card.type = "button";
      card.setAttribute("aria-pressed", String(task.id === model.selected));
      card.setAttribute("aria-label", `查看 ${task.name || task.id} 的资源使用历史`);
      const top = element("div", "task-top");
      const identity = element("div", "task-identity");
      identity.append(element("div", "task-name", task.name || task.id), element("div", "task-cluster", `${task.cluster || "集群信息暂不可用"} · ${task.id}`));
      top.append(element("span", `service-icon ${task.kind === "ACP" ? "purple" : "blue"}`, task.kind || "—"), identity, element("span", "running-badge", task.state === "running" ? "运行中" : (task.state || "状态未知")));
      const meta = element("div", "task-meta");
      meta.append(element("span", "meta-pill", `${count(task.gpu_count)} GPU`), element("span", "meta-pill", `${count(task.replicas)} 副本`));
      if (Array.isArray(task.workers) && task.workers.length) meta.append(element("span", "meta-pill", `${task.workers.length} workers`));
      const metrics = element("div", "metric-grid");
      for (const metric of METRICS) {
        const cell = element("div", "metric-cell"), value = percent(task.metrics?.[metric.key]);
        const display = element("div", "metric-value", value === null ? "—" : numberFormat.format(value));
        if (value !== null) display.append(element("small", "", "%"));
        const track = element("div", `metric-track${value === null ? " metric-unknown" : ""}`);
        const fill = element("div", `metric-fill ${metric.key}`);
        fill.style.width = `${value ?? 0}%`;
        track.append(fill);
        cell.append(element("div", "metric-label", metric.label), display, track);
        if (task.failed_metrics?.includes(metric.key)) cell.append(element("small", "metric-age", `旧采样 ${time(task.metric_updated_at?.[metric.key])}`));
        metrics.append(cell);
      }
      if (task.metric_error || model.snapshot?.source_status?.[task.kind]?.status === "error") card.append(element("div", "task-warning", task.metric_error || "任务列表采集失败 · 旧快照"));
      const bottom = element("div", "task-bottom");
      bottom.append(element("span", "", `最新采样 ${time(task.updated_at)}`), element("span", "task-detail-hint", task.id === model.selected ? "正在查看历史" : "查看使用历史 →"));
      card.append(top, meta, metrics, bottom);
      card.addEventListener("click", () => { model.selected = task.id; renderTasks(); renderDetail(); loadHistory(); });
      fragment.append(card);
    }
    container.replaceChildren(fragment);
  }

  function renderClusters() {
    const body = byId("cluster-body"), fragment = document.createDocumentFragment();
    const notices = [];
    for (const cluster of visibleClusters()) {
      const inventory = cluster.inventory;
      if (cluster.inventory_error) notices.push(`${cluster.name}：${cluster.inventory_error}`);
      else if (known(inventory?.abnormal_nodes) && inventory.abnormal_nodes > 0) notices.push(`${cluster.name}：${count(inventory.abnormal_nodes)} 个异常节点，涉及 ${count(inventory.abnormal_node_gpus)} 张 GPU`);
      const row = element("tr"), nameCell = element("td"), name = element("div", "cluster-name-cell");
      name.append(element("span", "cluster-dot"), document.createTextNode(cluster.name || cluster.id));
      nameCell.append(name);
      row.append(nameCell);
      const totalCell = element("td");
      totalCell.append(element("span", "resource-value", count(inventory?.total_gpus)));
      if (inventory && known(inventory.abnormal_node_gpus) && inventory.abnormal_node_gpus > 0) totalCell.append(element("small", "inventory-caption", `含异常节点 ${count(inventory.abnormal_node_gpus)} 张`));
      if (cluster.inventory_error) totalCell.append(element("small", "inventory-warning", inventory ? "旧总量" : "采集失败"));
      row.append(totalCell);
      for (const key of ["available_gpus", "available_vcpu", "available_memory_gib"]) {
        const cell = element("td");
        cell.append(element("span", `resource-value${key === "available_gpus" ? " gpu-value" : ""}`, count(cluster[key])));
        row.append(cell);
      }
      const health = element("td", "node-health");
      if (inventory) {
        health.append(element("div", "", `${count(inventory.node_count)} 节点 · ${count(inventory.disabled_nodes)} 禁用`));
        health.append(element("span", inventory.abnormal_nodes > 0 ? "node-alert" : "node-normal", `${count(inventory.abnormal_nodes)} 异常`));
        if (cluster.inventory_error) health.append(element("small", "inventory-warning", "旧状态"));
      } else health.textContent = "—";
      row.append(health);
      const collected = element("td", "inventory-time");
      collected.append(element("div", "", `余量 ${time(cluster.updated_at)}`));
      if (inventory) collected.append(element("div", "", `总量 ${time(inventory.updated_at)}`));
      row.append(collected);
      fragment.append(row);
    }
    if (!fragment.childNodes.length) {
      const row = element("tr"), cell = element("td", "table-empty", clusters().length ? "暂无有 GPU 的标准资源" : model.snapshot?.connected ? "尚未获取到标准集群余量" : "连接云会话后，集群可用资源将显示在这里");
      cell.colSpan = 7;
      row.append(cell);
      fragment.append(row);
    }
    body.replaceChildren(fragment);
    const notice = byId("inventory-notice");
    notice.hidden = notices.length === 0;
    notice.textContent = notices.join("；");
  }

  function renderDetail() {
    const task = tasks().find((item) => item.id === model.selected);
    byId("detail-panel").hidden = !task;
    document.dispatchEvent(new CustomEvent("monitor-selection", { detail: task || null }));
    if (!task) return;
    byId("detail-title").textContent = task.name || task.id;
    const workers = Array.isArray(task.workers) ? task.workers : [];
    byId("detail-meta").textContent = `${task.kind} · ${task.cluster || "集群未知"}${workers.length ? ` · Worker：${workers.join("、")}` : ""} · ${task.kind === "CCI" ? "卡片为应用平均值" : "卡片为各 worker 最新值的算术平均"}`;
    const link = byId("task-console-link");
    link.hidden = true;
    link.removeAttribute("href");
    try {
      const url = new URL(task.url);
      if (url.origin === new URL(model.snapshot?.site?.console || "https://console.ms-sc-01.maoshanwangtech.com").origin && !url.username && !url.password) { link.href = url.href; link.hidden = false; }
    } catch { /* A task without a trusted console URL simply has no link. */ }
    if (!byId("tasks-view").hidden && !byId("detail-monitor").hidden) {
      if (!model.chart && window.echarts) model.chart = window.echarts.init(byId("history-chart"), null, { renderer: "canvas" });
      model.chart?.resize();
    }
  }

  function chartMessage(text) {
    const message = byId("chart-message");
    message.textContent = text || "";
    message.hidden = !text;
  }

  function renderChart(series) {
    if (!model.chart) { chartMessage("图表组件暂不可用，请确认本地 ECharts 文件已就绪。"); return; }
    const selectedSeries = Array.isArray(series) ? series.filter((item) => METRICS.some((metric) => metric.key === item.metric)) : [];
    const hasValues = selectedSeries.some((item) => Array.isArray(item.points) && item.points.some((point) => Array.isArray(point) && percent(point[1]) !== null));
    model.chart.setOption({
      animation: false,
      aria: { enabled: true },
      textStyle: { fontFamily: 'Inter, -apple-system, "PingFang SC", sans-serif' },
      color: selectedSeries.map((item) => METRICS.find((metric) => metric.key === item.metric).color),
      tooltip: { trigger: "axis", renderMode: "richText", confine: true, valueFormatter: (value) => value === null || value === undefined ? "—" : `${numberFormat.format(value)}%` },
      legend: { type: "scroll", bottom: 5, left: "center", icon: "roundRect", itemWidth: 12, itemHeight: 3, itemGap: 20, textStyle: { color: "#758598", fontSize: 10 } },
      grid: { left: 40, right: 16, top: 25, bottom: 64 },
      xAxis: { type: "time", boundaryGap: false, axisLine: { lineStyle: { color: "#e5edeb" } }, axisTick: { show: false }, axisLabel: { color: "#85959f", fontSize: 10, hideOverlap: true }, splitLine: { show: false } },
      yAxis: { type: "value", min: 0, max: 100, interval: 25, axisLabel: { formatter: "{value}%", color: "#85959f", fontSize: 10 }, splitLine: { lineStyle: { color: "#edf2f0", type: "dashed" } } },
      series: selectedSeries.map((item, index) => ({
        id: `${item.metric}-${index}`,
        name: item.label ? item.label.replace(/^GPU(?= ·|$)/, "GPU 利用率").replace(/^显存(?= ·|$)/, "显存占用") : METRICS.find((metric) => metric.key === item.metric).label,
        type: "line", showSymbol: false, connectNulls: false, smooth: false,
        lineStyle: { width: 2 }, emphasis: { focus: "series" },
        data: (Array.isArray(item.points) ? item.points : []).filter((point) => Array.isArray(point) && Number.isFinite(point[0])).map((point) => [point[0] * 1000, percent(point[1])]),
      })),
    }, true);
    chartMessage(hasValues ? "" : "所选时间范围还没有有效采样。采集完成后，历史曲线将在这里出现。");
  }

  async function loadHistory() {
    if (!model.selected || byId("tasks-view").hidden || byId("detail-monitor").hidden) return;
    const requestId = ++model.historyRequest, taskId = model.selected, hours = model.hours;
    chartMessage("正在读取历史采样…");
    try {
      const history = await request(`/api/history?task_id=${encodeURIComponent(taskId)}&hours=${hours}`);
      if (requestId !== model.historyRequest) return;
      renderChart(history.series);
    } catch {
      if (requestId !== model.historyRequest) return;
      model.chart?.clear();
      chartMessage("历史采样暂时无法读取，请稍后重试。任务卡片保留最近快照。");
    }
  }

  async function poll() {
    if (model.fetching) return;
    model.fetching = true;
    try {
      const snapshot = await request("/api/state");
      if (!snapshot || typeof snapshot.status !== "string" || !Array.isArray(snapshot.tasks) || !Array.isArray(snapshot.clusters)) throw new Error("监控快照格式无效");
      const changed = snapshot.updated_at !== model.snapshot?.updated_at;
      const taskDataChanged = JSON.stringify(snapshot.tasks) !== JSON.stringify(model.snapshot?.tasks) || snapshot.status !== model.snapshot?.status || snapshot.connected !== model.snapshot?.connected;
      const clusterDataChanged = JSON.stringify(snapshot.clusters) !== JSON.stringify(model.snapshot?.clusters) || snapshot.connected !== model.snapshot?.connected;
      const previousSelection = model.selected;
      model.snapshot = snapshot;
      model.unavailable = false;
      renderStatus(); renderSummary();
      document.dispatchEvent(new CustomEvent("monitor-snapshot", { detail: { snapshot, unavailable: false } }));
      if (taskDataChanged) { renderTasks(); renderDetail(); }
      if (clusterDataChanged) renderClusters();
      if (changed || previousSelection !== model.selected) await loadHistory();
    } catch {
      model.unavailable = true;
      document.dispatchEvent(new CustomEvent("monitor-snapshot", { detail: { snapshot: model.snapshot, unavailable: true } }));
      renderStatus();
      renderSummary();
      if (!model.snapshot) { renderTasks(); renderClusters(); }
    } finally { model.fetching = false; }
  }

  async function action(buttonId, endpoint, message) {
    const button = byId(buttonId), notice = byId("action-message");
    button.disabled = true;
    notice.hidden = false;
    notice.textContent = endpoint === "/api/connect" ? "正在使用本地 IAM 配置自动连接，请稍候…" : "正在请求新的采集…";
    try {
      const result = await request(endpoint, "POST");
      notice.textContent = result.message || message;
      await poll();
    } catch (error) {
      notice.textContent = error.name === "AbortError" ? "请求暂未得到响应，请稍后重试。" : error.message;
    } finally { button.disabled = false; }
  }

  document.querySelectorAll(".filter-button").forEach((button) => button.addEventListener("click", () => {
    model.kind = button.dataset.kind;
    document.querySelectorAll(".filter-button").forEach((item) => { const selected = item === button; item.classList.toggle("selected", selected); item.setAttribute("aria-pressed", String(selected)); });
    renderTasks(); renderDetail(); loadHistory();
  }));
  document.querySelectorAll(".range-button").forEach((button) => button.addEventListener("click", () => {
    model.hours = Number(button.dataset.hours);
    document.querySelectorAll(".range-button").forEach((item) => { const selected = item === button; item.classList.toggle("selected", selected); item.setAttribute("aria-pressed", String(selected)); });
    loadHistory();
  }));
  byId("task-search").addEventListener("input", (event) => { model.search = event.target.value.trim().toLocaleLowerCase(); renderTasks(); renderDetail(); loadHistory(); });
  byId("refresh-button").addEventListener("click", () => action("refresh-button", "/api/refresh", "已请求刷新。采集完成后，页面会自动更新。"));
  byId("connect-button").addEventListener("click", () => action("connect-button", "/api/connect", "自动登录已开始，连接状态将自动更新。"));
  document.addEventListener("monitor-refresh", () => poll());
  document.addEventListener("workspace-page", (event) => {
    model.historyRequest += 1;
    if (event.detail === "tasks") { renderDetail(); loadHistory(); }
  });
  document.addEventListener("monitor-detail-tab", (event) => {
    model.historyRequest += 1;
    if (event.detail === "monitor") { model.chart?.resize(); loadHistory(); }
  });
  window.addEventListener("resize", () => model.chart?.resize());
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { poll(); model.chart?.resize(); } });
  poll();
  setInterval(() => { if (!document.hidden) poll(); }, 10000);
})();
