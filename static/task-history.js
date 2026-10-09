"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  let snapshot = null, page = 1, active = false, controller = null, timer = null, serial = 0;
  let tableSignature = null, readError = "";
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
    const body = $("task-history-body"); if (changed) { tableSignature = null; body.replaceChildren(); }
    if (changed && !rows.length) {
      const cell = node("td", emptyText, "table-empty");
      cell.colSpan = 6; const row = node("tr"); row.append(cell); body.append(row);
    }
    for (const task of changed ? pageRows : []) {
      const row = node("tr"), identity = node("td"), state = node("td"), resources = node("td"), created = node("td"), ended = node("td"), action = node("td");
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
      try {
        const url = new URL(task.url), origin = site.id === "sensecore" ? "https://console.sensecore.cn" : "https://console.ms-sc-01.maoshanwangtech.com";
        if (url.origin === origin && !url.username && !url.password) {
          const link = node("a", "跳转任务 ↗", "text-link"); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; action.append(link);
        }
      } catch { /* Unknown destinations remain plain text. */ }
      if (task.kind === "ACP") {
        const restart = node("button", "重新启动", "button button-secondary"); restart.type = "button";
        restart.addEventListener("click", () => document.dispatchEvent(new CustomEvent("acp-restart", { detail: { id: task.id, name: task.name } })));
        action.append(restart);
      }
      const cells = [identity, state, resources, created, ended, action];
      const labels = ["任务", "状态", "集群 / 资源", "创建 / 开始时间", "结束时间 / 运行时长", "操作"];
      cells.forEach((cell, i) => { cell.dataset.label = labels[i]; });
      row.append(...cells); body.append(row);
    }
    tableSignature = signature;
    if (resetScroll) $("task-history-section").querySelector(".table-scroll").scrollTop = 0;
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
  async function refresh() {
    if (!active || document.hidden || controller) return;
    clearTimeout(timer); const request = ++serial, current = new AbortController(); controller = current;
    const timeout = setTimeout(() => current.abort(), 15000); let delay = 60000;
    $("task-history-refresh").disabled = true;
    try {
      const response = await site.fetch("/api/task-history", { signal: current.signal });
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
      if (request === serial) { controller = null; $("task-history-refresh").disabled = false; if (active && !document.hidden) timer = setTimeout(refresh, delay); }
    }
  }
  function activate(route) {
    active = route === "history"; clearTimeout(timer);
    if (active && !document.hidden) refresh();
    else { serial += 1; controller?.abort(); controller = null; $("task-history-refresh").disabled = false; }
  }
  for (const id of ["task-history-search", "task-history-kind", "task-history-state", "task-history-size"]) $(id).addEventListener("input", () => { page = 1; render(true); });
  $("task-history-reset").addEventListener("click", () => {
    for (const id of ["task-history-search", "task-history-kind", "task-history-state"]) $(id).value = "";
    page = 1; render(true); $("task-history-search").focus();
  });
  $("task-history-prev").addEventListener("click", () => { page -= 1; render(true); });
  $("task-history-next").addEventListener("click", () => { page += 1; render(true); });
  $("task-history-refresh").addEventListener("click", refresh);
  $("refresh-button").addEventListener("click", () => { if (active) refresh(); });
  document.addEventListener("workspace-page", event => activate(event.detail));
  document.addEventListener("visibilitychange", () => activate(location.hash.slice(1)));
  activate(location.hash.slice(1));
})();
