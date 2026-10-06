"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  let active = false, timer = null, controller = null, serial = 0, snapshot = null;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const known = value => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;
  const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric", hour12: false });
  const date = value => value && Number.isFinite(Date.parse(value)) ? dateFormat.format(new Date(value)) : "—";
  const duration = value => {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "未知";
    const minutes = Math.floor(value / 60), days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60);
    if (!minutes) return "不足 1 分钟";
    return `${days ? `${days} 天 ` : ""}${hours ? `${hours} 小时 ` : ""}${minutes % 60} 分钟`;
  };

  function render() {
    if (!snapshot) return;
    const data = snapshot;
    const mineOnly = $("utilization-mine").checked;
    const tasks = data.tasks.filter(task => !mineOnly || task.is_mine === true).sort((a, b) => Number(!known(a.gpu_utilization)) - Number(!known(b.gpu_utilization))
      || (known(a.gpu_utilization) && known(b.gpu_utilization) ? a.gpu_utilization - b.gpu_utilization : 0)
      || a.id.localeCompare(b.id));
    $("utilization-count").textContent = data.updated_at ? (mineOnly ? `${tasks.length} / ${data.tasks.length}` : tasks.length) : "—";
    const valid = tasks.filter(task => known(task.gpu_utilization)).length;
    $("utilization-status").textContent = `${data.loading ? "正在读取实时 GPU 利用率，已取得的结果先显示" : data.status === "error" ? "读取失败 · 当前为旧列表" : "实时 GPU 利用率从低到高"} · ${mineOnly ? "仅我的任务 · " : ""}${valid} 个有效值 / ${tasks.length} 个 ACP 任务 · 列表更新 ${date(data.updated_at)}`;
    const showWarnings = !mineOnly || data.status === "error" || tasks.some(task => !["ok", "no_gpu"].includes(task.metric_status));
    $("utilization-warning").hidden = !showWarnings || !data.warnings?.length;
    $("utilization-warning").textContent = (data.warnings || []).join(" ");
    const fragment = document.createDocumentFragment();
    for (const [index, task] of tasks.entries()) {
      const row = node("tr"), identity = node("td", undefined, "waiting-identity");
      row.dataset.taskId = task.id;
      const title = node("strong");
      title.append(node("span", known(task.gpu_utilization) ? String(index + 1).padStart(2, "0") : "—", "utilization-rank"), document.createTextNode(task.name || task.id));
      identity.append(title, node("small", task.id));
      const utilization = node("td");
      utilization.append(node("span", known(task.gpu_utilization) ? `${number.format(task.gpu_utilization)}%` : task.metric_status === "no_gpu" ? "未配置 GPU" : "未知",
        `utilization-value${known(task.gpu_utilization) ? "" : " utilization-unknown"}`));
      utilization.title = `GPU 采样 ${date(task.metric_updated_at)}`;
      if (task.metric_status === "stale") utilization.append(node("small", "旧采样，不参与排名"));
      const action = node("td", "只读");
      try {
        const url = new URL(task.url), origin = site.id === "sensecore" ? "https://console.sensecore.cn" : "https://console.ms-sc-01.maoshanwangtech.com";
        if (url.origin === origin && !url.username && !url.password) {
          const link = node("a", "跳转任务 ↗", "text-link");
          link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; action.append(link);
        }
      } catch { /* Invalid cloud links stay read-only text. */ }
      const timing = node("td", undefined, "utilization-time");
      timing.append(node("span", date(task.created_at)), node("small", `${data.status === "error" ? "上次观测已运行" : "已运行"} ${duration(task.duration_seconds)}`, "utilization-duration"));
      timing.title = `启动时间 ${date(task.started_at)}`;
      const cells = [identity, node("td", task.owner || "—"), node("td", task.cluster || "—"), utilization,
        node("td", `${task.gpu_count ?? "—"} GPU / ${task.replicas ?? "—"} 副本`), timing, action];
      const labels = ["任务 / ID", "提交者", "集群", "实时 GPU 利用率", "GPU / 副本", "创建时间 / 已运行", "操作"];
      cells.forEach((cell, i) => { cell.dataset.label = labels[i]; });
      row.append(...cells); fragment.append(row);
    }
    if (!tasks.length) {
      const cell = node("td", data.loading ? "正在读取运行中的 ACP 任务…" : data.status === "error" ? "ACP 列表暂不可用，请稍后重试" : mineOnly ? "当前没有我的运行中 ACP 任务（名称包含 charles）" : "当前没有运行中的 ACP 任务", "table-empty");
      cell.colSpan = 7; const row = node("tr"); row.append(cell); fragment.append(row);
    }
    $("utilization-body").replaceChildren(fragment);
  }

  async function refresh() {
    if (!active || document.hidden || controller) return;
    clearTimeout(timer);
    const request = ++serial, current = new AbortController(); controller = current;
    const timeout = setTimeout(() => current.abort(), 15000);
    let delay = 60000;
    try {
      const response = await site.fetch("/api/utilization-queue", { signal: current.signal, cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "利用率读取失败");
      if (data.site !== site.id || !Array.isArray(data.tasks)) throw new Error("利用率数据与站点不一致");
      if (!active || request !== serial) return;
      snapshot = data; render();
      if (data.loading) delay = 2000;
    } catch (error) {
      if (!active || request !== serial) return;
      if (snapshot) {
        snapshot = { ...snapshot, status: "error", loading: false,
          tasks: snapshot.tasks.map(task => ({ ...task, gpu_utilization: null, metric_status: "stale" })) };
        render();
      }
      $("utilization-warning").hidden = false;
      $("utilization-warning").textContent = `${error.name === "AbortError" ? "利用率读取超时" : error.message}。${snapshot ? "保留上次列表，旧利用率不参与排名。" : "尚未取得运行任务，请稍后重试。"}`;
      $("utilization-status").textContent = "读取失败，将自动重试";
    } finally {
      clearTimeout(timeout);
      if (request === serial) {
        controller = null;
        if (active && !document.hidden) timer = setTimeout(refresh, delay);
      }
    }
  }
  function activate(route) {
    active = route === "waiting";
    clearTimeout(timer); serial += 1; controller?.abort(); controller = null;
    if (active && !document.hidden) refresh();
  }
  document.addEventListener("workspace-page", event => activate(event.detail));
  document.addEventListener("visibilitychange", () => activate(location.hash.slice(1)));
  $("refresh-button").addEventListener("click", refresh);
  $("utilization-mine").addEventListener("change", render);
  window.addEventListener("pagehide", () => activate(""));
  activate(location.hash.slice(1));
})();
