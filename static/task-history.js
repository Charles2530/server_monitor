"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  let snapshot = null, page = 1, active = false, controller = null, timer = null, serial = 0;
  let tableSignature = null, readError = "";
  const taskRows = new Map();
  const refreshLabel = $("task-history-refresh").textContent;
  function setBusy(busy) {
    $("task-history-refresh").disabled = busy;
    $("task-history-refresh").textContent = busy ? "刷新中…" : refreshLabel;
    $("task-history-refresh").setAttribute("aria-busy", String(busy));
    $("task-history-section").setAttribute("aria-busy", String(busy));
  }
  const filterIds = ["task-history-search", "task-history-kind", "task-history-state", "task-history-size"];
  const filterKey = `ilab-history-filters:${site.id}`;
  try {
    const saved = JSON.parse(sessionStorage.getItem(filterKey) || "{}");
    for (const id of filterIds) if (typeof saved[id] === "string") {
      const input = $(id);
      if (input.tagName !== "SELECT" || [...input.options].some(option => option.value === saved[id])) input.value = saved[id].slice(0, 200);
    }
  } catch { /* Filters remain usable when session storage is unavailable. */ }
  function rememberFilters() {
    try { sessionStorage.setItem(filterKey, JSON.stringify(Object.fromEntries(filterIds.map(id => [id, $(id).value])))); } catch { /* Optional persistence. */ }
  }
  const node = (tag, text, className) => {
    const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
    if (className) el.className = className; return el;
  };
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric", hour12: false });
  const date = value => value && Number.isFinite(Date.parse(value)) ? dateFormat.format(new Date(value)) : "—";
  const duration = value => {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "—";
    const seconds = Math.floor(value), days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60);
    return `${days ? `${days} 天 ` : ""}${hours ? `${hours} 小时 ` : ""}${minutes ? `${minutes} 分 ` : ""}${seconds % 60} 秒`;
  };
  function render(resetScroll = false) {
    if (!snapshot) return;
    const query = $("task-history-search").value.trim().toLowerCase();
    const kind = $("task-history-kind").value, status = $("task-history-state").value;
    const rows = snapshot.tasks.filter(t => (!kind || t.kind === kind) && (!status || t.status === status)
      && (!query || [t.name, t.id, t.cluster].join(" ").toLowerCase().includes(query)));
    $("task-history-reset").hidden = !query && !kind && !status;
    $("history-total").textContent = snapshot.tasks.length.toLocaleString("zh-CN");
    for (const value of ["succeeded", "failed", "stopped"]) {
      $(`history-${value}`).textContent = snapshot.tasks.filter(t => t.status === value).length.toLocaleString("zh-CN");
    }
    const size = Number($("task-history-size").value), pages = Math.max(1, Math.ceil(rows.length / size));
    page = Math.min(page, pages);
    $("task-history-count").textContent = `${rows.length} / ${snapshot.tasks.length}`;
    $("task-history-page").textContent = `第 ${page} / ${pages} 页 · ${rows.length} 条${snapshot.loading ? "（读取中）" : ""}`;
    $("task-history-prev").disabled = page <= 1; $("task-history-next").disabled = page >= pages;
    const pageRows = rows.slice((page - 1) * size, page * size);
    const failed = Object.values(snapshot.source_status || {}).some(s => s.status === "error");
    const emptyText = rows.length ? null : snapshot.loading ? "正在读取云端历史任务…" : failed ? "历史数据未完整获取，可刷新重试" : snapshot.tasks.length ? "没有匹配的历史任务" : "暂无符合条件的历史任务";
    const signature = JSON.stringify([pageRows, emptyText]), changed = signature !== tableSignature;
    const body = $("task-history-body"), nextRows = [];
    const scroll = $("task-history-section").querySelector(".table-scroll"), scrollTop = scroll.scrollTop, scrollLeft = scroll.scrollLeft;
    const focused = document.activeElement;
    const liveKeys = new Set(snapshot.tasks.map(task => `${task.kind}:${task.id}`));
    for (const key of taskRows.keys()) if (!liveKeys.has(key)) taskRows.delete(key);
    if (changed && !rows.length) {
      const cell = node("td", emptyText, "table-empty");
      cell.colSpan = 6; const row = node("tr"); row.append(cell); nextRows.push(row);
    }
    for (const task of changed ? pageRows : []) {
      const key = `${task.kind}:${task.id}`, rowSignature = JSON.stringify(task);
      let record = taskRows.get(key);
      if (record?.signature === rowSignature) { nextRows.push(record.row); continue; }
      if (!record) { record = { row: node("tr"), action: node("td") }; taskRows.set(key, record); }
      record.task = task;
      const row = record.row, identity = node("td"), state = node("td"), resources = node("td"), created = node("td"), ended = node("td"), action = record.action;
      const identityText = node("div", undefined, "history-identity-text"), identityGroup = node("div", undefined, "history-identity");
      identityText.append(node("strong", task.name || task.id), node("small", task.id, "history-task-id"));
      identityGroup.append(node("span", task.kind || "—", `history-service ${task.kind === "ACP" ? "acp" : "cci"}`), identityText);
      identity.append(identityGroup);
      state.append(node("span", task.status_label, `history-state ${["succeeded", "failed", "stopped"].includes(task.status) ? task.status : ""}`));
      resources.append(node("span", task.cluster || "—", "history-cluster"), node("small", `${task.gpu_count ?? "—"} GPU · ${task.replicas ?? "—"} 副本`, "history-resources"));
      created.append(node("span", date(task.created_at)), node("small", `开始 ${date(task.started_at)}`));
      if (!task.started_at && task.run_evidence === "observed") created.append(node("small", "本机曾观测到运行"));
      ended.append(node("span", date(task.ended_at)), node("small", `运行 ${duration(task.duration_seconds)}`, "history-duration"));
      if (!task.ended_at) ended.append(node("small", `最近更新 ${date(task.updated_at)}`));
      let validUrl = null;
      try {
        const url = new URL(task.url), origin = site.id === "sensecore" ? "https://console.sensecore.cn" : "https://console.ms-sc-01.maoshanwangtech.com";
        if (url.origin === origin && !url.username && !url.password) {
          validUrl = url.href;
        }
      } catch { /* Unknown destinations remain plain text. */ }
      if (validUrl) {
        if (!record.link) {
          record.link = node("a", "跳转任务 ↗", "text-link"); record.link.target = "_blank"; record.link.rel = "noopener noreferrer";
          action.insertBefore(record.link, action.firstChild);
        }
        record.link.href = validUrl;
      } else if (record.link) { record.link.remove(); record.link = null; }
      if (task.kind === "ACP" && !record.restart) {
        const restart = node("button", "重新启动", "button button-secondary"); restart.type = "button";
        restart.addEventListener("click", () => document.dispatchEvent(new CustomEvent("acp-restart", { detail: { id: record.task.id, name: record.task.name } })));
        record.restart = restart; action.append(restart);
      }
      const cells = [identity, state, resources, created, ended, action];
      const labels = ["任务", "状态", "集群 / 资源", "创建 / 开始时间", "结束时间 / 运行时长", "操作"];
      cells.forEach((cell, i) => { cell.dataset.label = labels[i]; });
      cells.forEach((cell, index) => {
        if (!row.children[index]) row.append(cell);
        else if (row.children[index] !== cell) row.replaceChild(cell, row.children[index]);
      });
      record.signature = rowSignature; nextRows.push(row);
    }
    if (changed) {
      const wanted = new Set(nextRows);
      for (const row of [...body.children]) if (!wanted.has(row)) row.remove();
      nextRows.forEach((row, index) => { if (body.children[index] !== row) body.insertBefore(row, body.children[index] || null); });
      if (focused !== document.activeElement && focused?.isConnected && body.contains(focused)) focused.focus({ preventScroll: true });
    }
    tableSignature = signature;
    scroll.scrollTop = resetScroll ? 0 : scrollTop; scroll.scrollLeft = scrollLeft;
    const sources = Object.entries(snapshot.source_status || {}).map(([kind, s]) => `${kind} ${s.status === "error" ? "读取失败 · " : ""}${date(s.updated_at)}`).join("；");
    $("task-history-status").textContent = `${readError ? "读取失败 · 当前为旧结果" : snapshot.loading ? "正在更新，已取得的结果先显示" : "历史读取完成"} · ${sources}`;
    const excluded = snapshot.filtered_stopped || {};
    $("task-history-excluded").hidden = false;
    $("task-history-excluded").textContent = `已过滤无运行依据的已停止任务：ACP ${excluded.ACP || 0} 条、CCI ${excluded.CCI || 0} 条${snapshot.loading ? "（统计中）" : ""}。`;
    const warnings = [...(snapshot.warnings || [])];
    if (readError) warnings.unshift(readError);
    const failedSources = Object.entries(snapshot.source_status || {}).filter(([, source]) => source.status === "error").map(([kind]) => kind);
    if (failedSources.length && !warnings.length) warnings.unshift(`${failedSources.join(" / ")} 历史读取失败，当前结果可能不完整。`);
    $("task-history-warning").hidden = !warnings.length; $("task-history-warning").textContent = warnings.join(" ");
  }
  async function refresh(force = false) {
    if (!active || document.hidden || controller) return;
    clearTimeout(timer); const request = ++serial, current = new AbortController(); controller = current;
    const timeout = setTimeout(() => current.abort(), 15000); let delay = 60000;
    setBusy(true);
    try {
      const response = await site.fetch(`/api/task-history${force ? "?refresh=true" : ""}`, { signal: current.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "历史读取失败");
      if (!Array.isArray(data.tasks) || data.site !== site.id) throw new Error("历史数据与站点不一致");
      if (!active || request !== serial) return;
      snapshot = data; readError = ""; render(); if (data.loading) delay = 2000;
    } catch (error) {
      if (!active || request !== serial) return;
      readError = `${error.name === "AbortError" ? "历史读取超时" : error.message}。${snapshot ? "保留上次结果，数据可能过期。" : "尚未取得历史任务，可刷新重试。"}`;
      $("task-history-warning").hidden = false;
      $("task-history-warning").textContent = readError;
      $("task-history-status").textContent = "读取失败";
      if (!snapshot) { const cell = node("td", "历史数据未获取", "table-empty"), row = node("tr"); cell.colSpan = 6; row.append(cell); $("task-history-body").replaceChildren(row); }
    } finally {
      clearTimeout(timeout);
      if (request === serial) { controller = null; setBusy(false); if (active && !document.hidden) timer = setTimeout(refresh, delay); }
    }
  }
  function activate(route) {
    active = route === "history"; clearTimeout(timer);
    if (active && !document.hidden) refresh();
    else { serial += 1; controller?.abort(); controller = null; setBusy(false); }
  }
  for (const id of filterIds) $(id).addEventListener("input", () => { page = 1; rememberFilters(); render(true); });
  $("task-history-reset").addEventListener("click", () => {
    for (const id of ["task-history-search", "task-history-kind", "task-history-state"]) $(id).value = "";
    page = 1; rememberFilters(); render(true); $("task-history-search").focus();
  });
  $("task-history-prev").addEventListener("click", () => { page -= 1; render(true); });
  $("task-history-next").addEventListener("click", () => { page += 1; render(true); });
  $("task-history-refresh").addEventListener("click", () => refresh(true));
  $("refresh-button").addEventListener("click", () => { if (active) refresh(true); });
  document.addEventListener("workspace-page", event => activate(event.detail));
  document.addEventListener("visibilitychange", () => activate(location.hash.slice(1)));
  activate(location.hash.slice(1));
})();
