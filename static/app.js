"use strict";

(() => {
  const METRICS = [
    { key: "cpu", label: "CPU", color: "#3b82f6" },
    { key: "gpu", label: "GPU 利用率", color: "#14b8a6" },
    { key: "memory", label: "内存", color: "#8b5cf6" },
    { key: "gpu_memory", label: "显存占用", color: "#d99816" },
  ];
  const byId = (id) => document.getElementById(id);
  const restored = window.MonitorTaskContext?.read() || {};
  const model = { snapshot: null, kind: restored.kind || "all", search: restored.search || "", selected: restored.taskId || restored.selected || null, hours: restored.hours || 1, unavailable: false, fetching: false, historyRequest: 0, chart: null };
  let taskCards = new Map(), taskEmptySignature = null;
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
  const timeFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const chartClock = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const chartDay = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" });

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
  function clusters() {
    const list = Array.isArray(model.snapshot?.clusters) ? model.snapshot.clusters : [];
    return list.filter((cluster) => !(cluster.id === "public" || cluster.id?.startsWith("/subscriptions/public/") || /^public aec2$/i.test(cluster.name || "")));
  }
  function visibleClusters() {
    return clusters().filter((cluster) => cluster.available_gpus !== 0 || cluster.inventory?.total_gpus > 0 || cluster.inventory_error);
  }
  function visibleTasks() {
    return tasks().filter((task) => (model.kind === "all" || task.kind === model.kind) && `${task.name || ""} ${task.cluster || ""} ${task.id || ""}`.toLocaleLowerCase().includes(model.search));
  }

  async function request(path, method = "GET", signal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await (window.MonitorSite?.fetch || fetch)(path, { method, headers: method === "POST" ? { "X-Monitor-Request": "1" } : {}, signal: controller.signal, cache: "no-store" });
      let data;
      try { data = await response.json(); } catch { throw new Error("本地服务返回了无法读取的数据。"); }
      if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : `本地服务暂不可用（${response.status}）。`);
      return data;
    } finally { clearTimeout(timeout); signal?.removeEventListener("abort", abort); }
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
        title = "正在重连"; description = "正在等待当前采集结束或使用本机 IAM 配置重新登录；超时后会结束本次重连并提示重试。";
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
    const queueKnown = hasSnapshot && ["CCI_queue", "ACP_queue"].every((key) => model.snapshot?.source_status?.[key]?.status === "ok");
    byId("summary-waiting").textContent = queueKnown && Array.isArray(model.snapshot?.waiting_tasks) ? count(model.snapshot.waiting_tasks.length) : "—";
    byId("summary-queue-note").textContent = queueKnown ? "CCI / ACP · 工作空间全部用户" : "等待队列尚未完整采集";
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

    const recent = document.createDocumentFragment();
    for (const task of list.slice().sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)).slice(0, 6)) {
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
        const meter = element("span", `preview-meter${value === null ? " metric-unknown" : ""}`);
        const fill = element("span");
        fill.style.width = `${value ?? 0}%`;
        meter.append(fill); metric.append(meter);
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
      const signature = JSON.stringify([title, description]);
      if (signature !== taskEmptySignature) container.replaceChildren(emptyState(title, description));
      taskEmptySignature = signature; taskCards.clear();
      model.selected = null;
      byId("detail-panel").hidden = true;
      document.dispatchEvent(new CustomEvent("monitor-selection", { detail: null }));
      cancelHistory();
      return;
    }
    if (!list.some((task) => task.id === model.selected)) model.selected = list[0].id;
    const nextCards = new Map();
    for (const task of list) {
      const signature = JSON.stringify([task, model.snapshot?.source_status?.[task.kind]?.status]);
      let cached = taskCards.get(task.id);
      if (!cached || cached.signature !== signature) cached = { ...createTaskCard(task), signature };
      const selected = task.id === model.selected;
      if (cached.selected !== selected) {
        cached.card.classList.toggle("selected", selected);
        cached.card.setAttribute("aria-pressed", String(selected));
        cached.hint.textContent = selected ? "正在查看历史" : "查看使用历史 →";
        cached.selected = selected;
      }
      nextCards.set(task.id, cached);
    }
    const cards = [...nextCards.values()].map(entry => entry.root || entry.card), retained = new Set(cards);
    for (const child of [...container.children]) if (!retained.has(child)) container.removeChild(child);
    cards.forEach((card, index) => { if (container.children[index] !== card) container.insertBefore(card, container.children[index] || null); });
    taskCards = nextCards; taskEmptySignature = null;
  }

  function createTaskCard(task) {
    const card = element("button", "task-card");
    card.type = "button";
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
    const hint = element("span", "task-detail-hint");
    bottom.append(element("span", "", `最新采样 ${time(task.updated_at)}`), hint);
    card.append(top, meta, metrics, bottom);
    const id = task.id;
    card.addEventListener("click", () => { model.selected = id; renderTasks(); renderDetail(); loadHistory(); });
    let root;
    if (window.MonitorTaskContext && task.kind === "CCI" && task.state === "running" && task.name?.toLowerCase().includes("charles")) {
      root = element("div", "task-card-item");
      const open = element("a", "mobile-cci-terminal", "打开终端 →");
      open.href = `/terminal?${new URLSearchParams({site:window.MonitorSite?.id || "maoshanwang",task_id:id,task_view:"monitor"})}`;
      open.setAttribute("aria-label", `打开 ${task.name || id} 的 CCI 终端`);
      open.addEventListener("click", () => window.MonitorTaskContext.remember({selected:id,search:model.search,kind:model.kind,hours:model.hours,scroll:window.scrollY}));
      root.append(card, open);
    }
    return { card, hint, root };
  }

  function renderClusters() {
    const body = byId("cluster-body"), fragment = document.createDocumentFragment();
    const notices = [];
    for (const cluster of visibleClusters()) {
      const inventory = cluster.inventory;
      if (cluster.inventory_error) notices.push(`${cluster.name}：${cluster.inventory_error}`);
      else if (known(inventory?.abnormal_nodes) && inventory.abnormal_nodes > 0) notices.push(`${cluster.name}：${count(inventory.abnormal_nodes)} 个异常节点，涉及 ${count(inventory.abnormal_node_gpus)} 张 GPU`);
      if (inventory?.allocation?.error && !cluster.inventory_error) notices.push(`${cluster.name}：${inventory.allocation.error}`);
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
        if (key === "available_gpus" && inventory?.allocation) {
          const allocation = inventory.allocation;
          const fresh = !cluster.inventory_error && allocation.status === "ok";
          const groups = fresh && Array.isArray(allocation.idle_gpu_groups) ? allocation.idle_gpu_groups : null;
          const text = groups ? groups.length ? groups.map(group => `${count(group.gpus_per_node)} 卡 × ${count(group.nodes)} 组`).join(" · ") : "0 组" : "未知";
          cell.append(element("small", groups ? "inventory-caption" : "inventory-warning", `整组空闲 GPU：${text}`));
        }
        row.append(cell);
      }
      const health = element("td", "node-health");
      if (inventory) {
        health.append(element("div", "", `${count(inventory.node_count)} 节点 · ${count(inventory.disabled_nodes)} 禁用`));
        health.append(element("span", inventory.abnormal_nodes > 0 ? "node-alert" : "node-normal", `${count(inventory.abnormal_nodes)} 异常`));
        if (cluster.inventory_error) health.append(element("small", "inventory-warning", "旧状态"));
      } else health.textContent = "—";
      row.append(health);
      fragment.append(row);
    }
    if (!fragment.childNodes.length) {
      const row = element("tr"), cell = element("td", "table-empty", clusters().length ? "暂无有 GPU 的标准资源" : model.snapshot?.connected ? "尚未获取到标准集群余量" : "连接云会话后，集群可用资源将显示在这里");
      cell.colSpan = 6;
      row.append(cell);
      fragment.append(row);
    }
    body.replaceChildren(fragment);
    const notice = byId("inventory-notice");
    notice.hidden = notices.length === 0;
    notice.textContent = notices.join("；");
  }

  function renderDetail() {
    window.MonitorTaskContext?.remember({selected:model.selected,search:model.search,kind:model.kind,hours:model.hours});
    const task = tasks().find((item) => item.id === model.selected);
    byId("detail-panel").hidden = !task;
    document.dispatchEvent(new CustomEvent("monitor-selection", { detail: task || null }));
    if (!task) return;
    byId("detail-title").textContent = task.name || task.id;
    const workers = Array.isArray(task.workers) ? task.workers : [];
    byId("detail-meta").textContent = `${task.kind} · ${task.cluster || "集群未知"}${workers.length ? ` · ${workers.length} Workers` : ""} · ${task.kind === "CCI" ? "应用平均值" : "卡片为各 Worker 最新值均值"}`;
    byId("detail-meta").title = workers.length ? workers.join("\n") : "应用平均值";
    const link = byId("task-console-link");
    link.hidden = true;
    link.removeAttribute("href");
    try {
      const url = new URL(task.url);
      if (url.origin === new URL(model.snapshot?.site?.console || "https://console.ms-sc-01.maoshanwangtech.com").origin && !url.username && !url.password) { link.href = url.href; link.hidden = false; }
    } catch { /* A task without a trusted console URL simply has no link. */ }
    if (historyVisible()) {
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
    if (!model.chart) { chartMessage("图表组件暂不可用，请确认本地 ECharts 文件已就绪。"); return false; }
    const theme = getComputedStyle(document.documentElement);
    const chartInk = theme.getPropertyValue("--ink").trim(), chartMuted = theme.getPropertyValue("--muted").trim();
    const chartLine = theme.getPropertyValue("--line").trim(), chartSurface = theme.getPropertyValue("--surface").trim();
    const selectedSeries = Array.isArray(series) ? series.filter((item) => METRICS.some((metric) => metric.key === item.metric)) : [];
    const hasValues = selectedSeries.some((item) => Array.isArray(item.points) && item.points.some((point) => Array.isArray(point) && percent(point[1]) !== null));
    model.chartSeries = series;
    model.chartTaskId = model.selected;
    // All metrics use the same observed time window, including gaps in a worker's samples.
    let firstSample = Infinity, lastSample = -Infinity;
    for (const item of selectedSeries) {
      for (const point of Array.isArray(item.points) ? item.points : []) {
        if (!Array.isArray(point) || !Number.isFinite(point[0])) continue;
        firstSample = Math.min(firstSample, point[0] * 1000);
        lastSample = Math.max(lastSample, point[0] * 1000);
      }
    }
    const singleSample = firstSample === lastSample;
    const names = selectedSeries.map((item) => item.label ? item.label.replace(/^GPU(?= ·|$)/, "GPU 利用率").replace(/^显存(?= ·|$)/, "显存占用") : METRICS.find((metric) => metric.key === item.metric).label);
    const shortNames = names.map((name) => name.replace(/^(.*?) · .+-(master|worker)-(\d+)$/, "$1 · $2-$3"));
    // Keep ambiguous labels intact. Shortening never changes a series' source identity.
    const shortNameCounts = new Map();
    for (const name of shortNames) shortNameCounts.set(name, (shortNameCounts.get(name) || 0) + 1);
    const legendNames = new Map(names.map((name, index) => [name, shortNameCounts.get(shortNames[index]) === 1 ? shortNames[index] : name]));
    const workerCounts = new Map();
    const columns = model.chart.getWidth() < 420 || window.matchMedia("(max-width: 620px)").matches ? 1 : 2;
    const rows = 4 / columns;
    const rowGap = model.hours > 1 ? 64 : 54;
    const plotHeight = Math.max(32, (model.chart.getHeight() - 32 - 52 - (rows - 1) * rowGap) / rows);
    const grids = METRICS.map((metric, index) => ({
      left: columns === 1 || index % 2 === 0 ? 40 : "56%",
      right: columns === 1 || index % 2 === 1 ? 14 : "54%",
      top: 32 + Math.floor(index / columns) * (plotHeight + rowGap),
      height: plotHeight,
    }));
    model.chart.setOption({
      animation: false,
      aria: { enabled: true },
      textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
      title: METRICS.map((metric, index) => ({
        text: metric.label, left: grids[index].left, top: grids[index].top - 28,
        textStyle: { fontSize: 13, fontWeight: 600, color: metric.color },
      })),
      color: selectedSeries.map((item) => METRICS.find((metric) => metric.key === item.metric).color),
      tooltip: { trigger: "axis", renderMode: "richText", confine: true, backgroundColor: chartSurface, borderColor: chartLine, textStyle: { color: chartInk }, valueFormatter: (value) => value === null || value === undefined ? "—" : `${numberFormat.format(value)}%` },
      legend: { type: "scroll", bottom: 0, left: 16, right: 16, icon: "roundRect", itemWidth: 12, itemHeight: 3, itemGap: 15, formatter: (name) => legendNames.get(name) || name, textStyle: { color: chartMuted, fontSize: 11 }, pageTextStyle: { color: chartMuted } },
      axisPointer: { link: [{ xAxisIndex: "all" }], lineStyle: { color: "#97b4aa", type: "dashed" } },
      grid: grids,
      xAxis: METRICS.map((metric, index) => ({
        gridIndex: index, type: "time", boundaryGap: false, splitNumber: columns === 1 ? 4 : 3,
        min: Number.isFinite(firstSample) ? firstSample - (singleSample ? 30000 : 0) : undefined,
        max: Number.isFinite(lastSample) ? lastSample + (singleSample ? 30000 : 0) : undefined,
        axisLine: { lineStyle: { color: chartLine } }, axisTick: { show: false },
        axisLabel: { color: chartMuted, fontSize: 11, hideOverlap: true, formatter: (value) => model.hours > 1 ? `${chartDay.format(value)}\n${chartClock.format(value)}` : chartClock.format(value) }, splitLine: { show: false },
      })),
      yAxis: METRICS.map((metric, index) => ({
        gridIndex: index, type: "value", min: 0, max: 100, interval: 50,
        axisLabel: { formatter: "{value}%", color: chartMuted, fontSize: 11 },
        splitLine: { lineStyle: { color: chartLine, type: "dashed" } },
      })),
      series: selectedSeries.map((item, index) => {
        const metricIndex = METRICS.findIndex((metric) => metric.key === item.metric);
        const workerIndex = workerCounts.get(item.metric) || 0;
        workerCounts.set(item.metric, workerIndex + 1);
        return {
          id: `${item.metric}-${index}`,
          name: names[index],
          type: "line", xAxisIndex: metricIndex, yAxisIndex: metricIndex,
          showSymbol: false, connectNulls: false, smooth: false,
          lineStyle: { width: 2, type: ["solid", "dashed", "dotted"][workerIndex % 3], opacity: 1 - (workerIndex % 3) * 0.15 },
          emphasis: { focus: "series" },
          data: (Array.isArray(item.points) ? item.points : []).filter((point) => Array.isArray(point) && Number.isFinite(point[0])).map((point) => [point[0] * 1000, percent(point[1])]),
        };
      }),
    }, true);
    chartMessage(hasValues ? "" : "所选时间范围还没有有效采样。采集完成后，历史曲线将在这里出现。");
    return true;
  }

  function historyVisible() {
    return !document.hidden && !byId("tasks-view").hidden && !byId("detail-monitor").hidden
      && (!(window.MonitorTaskContext?.mobile.matches ?? window.matchMedia("(max-width: 760px)").matches) || byId("tasks-view").classList.contains("mobile-show-detail"));
  }

  function cancelHistory() {
    model.historyRequest += 1;
    model.historyController?.abort(); model.historyController = null;
    model.historyPendingKey = null; model.historyPromise = null;
  }

  function loadHistory() {
    if (!model.selected || !historyVisible()) { cancelHistory(); return; }
    const taskId = model.selected, hours = model.hours;
    const key = JSON.stringify([taskId, hours, model.snapshot?.updated_at]);
    if (model.historyPendingKey === key) return model.historyPromise;
    if (model.historyKey === key) { cancelHistory(); chartMessage(model.historyMessage); return; }
    cancelHistory();
    const requestId = model.historyRequest, controller = new AbortController();
    model.historyController = controller; model.historyPendingKey = key;
    chartMessage("正在读取历史采样…");
    model.historyPromise = (async () => {
      try {
        const history = await request(`/api/history?task_id=${encodeURIComponent(taskId)}&hours=${hours}`, "GET", controller.signal);
        if (requestId !== model.historyRequest) return;
        if (!renderChart(history.series)) return;
        model.historyKey = key;
        model.historyMessage = byId("chart-message").textContent;
      } catch {
        if (requestId !== model.historyRequest) return;
        model.historyKey = null; model.chartSeries = null;
        model.chart?.clear();
        chartMessage("历史采样暂时无法读取，请稍后重试。任务卡片保留最近快照。");
      } finally {
        if (requestId === model.historyRequest) {
          model.historyController = null; model.historyPendingKey = null; model.historyPromise = null;
        }
      }
    })();
    return model.historyPromise;
  }

  async function poll() {
    if (model.fetching) return;
    model.fetching = true;
    try {
      const snapshot = await request("/api/state");
      if (!snapshot || typeof snapshot.status !== "string" || !Array.isArray(snapshot.tasks) || !Array.isArray(snapshot.clusters)) throw new Error("监控快照格式无效");
      const changed = snapshot.updated_at !== model.snapshot?.updated_at;
      const taskDataChanged = JSON.stringify(snapshot.tasks) !== JSON.stringify(model.snapshot?.tasks) || snapshot.status !== model.snapshot?.status || snapshot.connected !== model.snapshot?.connected
        || ["CCI", "ACP"].some(kind => snapshot.source_status?.[kind]?.status !== model.snapshot?.source_status?.[kind]?.status);
      const clusterDataChanged = JSON.stringify(snapshot.clusters) !== JSON.stringify(model.snapshot?.clusters) || snapshot.connected !== model.snapshot?.connected;
      const previousSelection = model.selected;
      model.snapshot = snapshot;
      model.unavailable = false;
      renderStatus(); renderSummary();
      document.dispatchEvent(new CustomEvent("monitor-snapshot", { detail: { snapshot, unavailable: false } }));
      if (taskDataChanged) { renderTasks(); renderDetail(); }
      if (clusterDataChanged) renderClusters();
      // History owns its cancellation/errors; a slow chart must not hold state polling.
      if (changed || previousSelection !== model.selected) loadHistory();
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
    window.MonitorTaskContext?.remember({hours:model.hours});
    document.querySelectorAll(".range-button").forEach((item) => { const selected = item === button; item.classList.toggle("selected", selected); item.setAttribute("aria-pressed", String(selected)); });
    loadHistory();
  }));
  byId("task-search").addEventListener("input", (event) => { model.search = event.target.value.trim().toLocaleLowerCase(); renderTasks(); renderDetail(); loadHistory(); });
  byId("refresh-button").addEventListener("click", () => {
    if (location.hash !== "#tokenhub") action("refresh-button", "/api/refresh", "已请求刷新。采集完成后，页面会自动更新。");
  });
  byId("connect-button").addEventListener("click", () => action("connect-button", "/api/connect", "自动登录已开始，连接状态将自动更新。"));
  document.addEventListener("monitor-refresh", () => poll());
  document.addEventListener("appearance-change", () => {
    if (model.chartSeries && model.chartTaskId === model.selected) renderChart(model.chartSeries);
  });
  document.addEventListener("workspace-page", (event) => {
    if (event.detail === "tasks") { renderDetail(); loadHistory(); }
    else cancelHistory();
  });
  document.addEventListener("monitor-detail-tab", (event) => {
    if (event.detail === "monitor") { renderDetail(); loadHistory(); }
    else cancelHistory();
  });
  document.addEventListener("monitor-detail-visibility", () => {
    if (historyVisible()) { renderDetail(); loadHistory(); } else cancelHistory();
  });
  let resizePending = false;
  window.addEventListener("resize", () => {
    if (resizePending) return;
    resizePending = true;
    requestAnimationFrame(() => {
      resizePending = false;
      model.chart?.resize();
      if (model.chartSeries && model.chartTaskId === model.selected) renderChart(model.chartSeries);
    });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) cancelHistory();
    else { poll(); renderDetail(); loadHistory(); }
  });
  document.addEventListener("monitor-task-request", event => {
    const id = event.detail;
    if (!tasks().some(task => task.id === id)) return;
    if (!visibleTasks().some(task => task.id === id)) {
      model.search = ""; model.kind = "all"; restoreFilters();
    }
    model.selected = id; renderTasks(); renderDetail(); loadHistory();
  });
  function restoreFilters() {
    byId("task-search").value = model.search;
    document.querySelectorAll(".filter-button").forEach(button => {
      const selected = button.dataset.kind === model.kind;
      button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected));
    });
    document.querySelectorAll(".range-button").forEach(button => {
      const selected = Number(button.dataset.hours) === model.hours;
      button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected));
    });
  }
  if (window.MonitorTaskContext) restoreFilters();
  poll();
  setInterval(() => { if (!document.hidden) poll(); }, 10000);
})();
