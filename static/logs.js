"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const state = { task: null, tab: "monitor", generation: 0, controller: null, timer: null, workers: [], updated: null, entries: 0, dataTarget: null };
  const output = byId("logs-output"), workerSelect = byId("logs-worker"), containerSelect = byId("logs-container");
  let matchIndex = -1;
  function findLog(next = false) {
    const query = byId("logs-search").value, text = output.textContent;
    if (!query) { matchIndex = -1; byId("logs-search-status").textContent = ""; return; }
    const search = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    search.lastIndex = next ? matchIndex + 1 : 0;
    let match = search.exec(text);
    if (!match && next) match = search.exec(text);
    const index = match?.index ?? -1;
    matchIndex = index;
    byId("logs-search-status").textContent = index < 0 ? "未找到" : "已定位 · 暂停跟随";
    if (index < 0) return;
    byId("logs-follow").checked = false;
    const range = document.createRange(), selection = window.getSelection();
    range.setStart(output.firstChild, index); range.setEnd(output.firstChild, index + query.length);
    selection.removeAllRanges(); selection.addRange(range);
    const rect = range.getBoundingClientRect(), bounds = output.getBoundingClientRect();
    output.scrollTop += rect.top - bounds.top - output.clientHeight / 2;
    output.scrollLeft += rect.left - bounds.left - output.clientWidth / 2;
  }
  const errors = { not_connected: "请先连接云会话。", auth_required: "云会话已失效或没有日志权限，请重新连接后刷新。", target_mismatch: "任务或容器身份已变化，请重新选择任务。", logs_unavailable: "日志暂不可用，请稍后刷新。", invalid_request: "日志目标无效，请重新选择任务和容器。" };
  const active = () => state.tab === "logs" && Boolean(state.task) && !byId("tasks-view").hidden && !byId("detail-panel").hidden && !document.hidden;
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric", hour12: false });
  function message(text) { byId("logs-message").textContent = text; byId("logs-message").hidden = !text; }
  function status(text) { byId("logs-status").textContent = text; }
  function timestamp() {
    const parsed = new Date(state.updated);
    return state.updated && Number.isFinite(parsed.getTime()) ? dateFormat.format(parsed) : "时间未知";
  }
  function invalidate() {
    state.generation += 1;
    clearTimeout(state.timer); state.timer = null;
    state.controller?.abort(); state.controller = null;
    byId("logs-refresh").disabled = false;
  }
  function schedule() {
    clearTimeout(state.timer);
    if (active() && byId("logs-auto").checked) state.timer = setTimeout(() => refresh(), 10000);
  }
  function options(select, values) {
    select.replaceChildren(...values.map((value) => { const option = document.createElement("option"); option.value = value; option.textContent = value; return option; }));
    select.disabled = !values.length;
  }
  function clearLog() { output.textContent = ""; matchIndex = -1; byId("logs-search-status").textContent = ""; state.updated = null; state.entries = 0; state.dataTarget = null; message(""); status("正在读取日志…"); }
  function containers() {
    const worker = state.workers.find((item) => item.name === workerSelect.value);
    options(containerSelect, worker?.containers || []);
  }
  async function request(path, signal) {
    const response = await (window.MonitorSite?.fetch || fetch)(path, { cache: "no-store", signal });
    let data;
    try { data = await response.json(); } catch { throw new Error("本地服务返回的日志数据无法读取。"); }
    if (!response.ok) throw Object.assign(new Error(errors[data.code] || "日志读取失败，请稍后刷新。"), { code: data.code });
    return data;
  }
  async function refresh() {
    if (!active() || state.controller) return;
    clearTimeout(state.timer);
    const controller = new AbortController(), generation = state.generation, taskId = state.task.id;
    state.controller = controller;
    byId("logs-refresh").disabled = true;
    status(state.updated ? `正在刷新 · 保留上次日志 ${timestamp()} · 云端读取可能需要约 1 分钟` : "正在读取日志…云端首次读取可能需要约 1 分钟");
    const timeout = setTimeout(() => controller.abort(), 120000);
    const current = () => generation === state.generation && active() && state.task?.id === taskId;
    try {
      if (!state.workers.length || !containerSelect.value) {
        const previousWorker = workerSelect.value, previousContainer = containerSelect.value;
        const targets = await request(`/api/logs/targets?${new URLSearchParams({ task_id: taskId })}`, controller.signal);
        if (!current()) return;
        if (targets.task_id !== taskId || !Array.isArray(targets.workers)) throw new Error("日志目标返回不一致，请重新选择任务。");
        state.workers = targets.workers.filter((worker) => typeof worker.name === "string" && Array.isArray(worker.containers)).map((worker) => ({ ...worker, containers: worker.containers.filter((name) => typeof name === "string") }));
        options(workerSelect, state.workers.map((worker) => worker.name));
        workerSelect.value = state.workers.find((worker) => worker.name === previousWorker)?.name || state.workers.find((worker) => worker.containers.length)?.name || state.workers[0]?.name || "";
        containers();
        if (workerSelect.value === previousWorker && state.workers.find((worker) => worker.name === previousWorker)?.containers.includes(previousContainer)) containerSelect.value = previousContainer;
      }
      const worker = workerSelect.value, container = containerSelect.value;
      if (!worker || !container) { status("当前 worker 暂无可读取日志的 container；可选择其他 worker 或刷新重试。"); return; }
      const target = JSON.stringify([taskId, worker, container]);
      if (state.dataTarget && state.dataTarget !== target) clearLog();
      const query = new URLSearchParams({ task_id: taskId, worker, container, tail: "1000" });
      const data = await request(`/api/logs?${query}`, controller.signal);
      if (!current() || worker !== workerSelect.value || container !== containerSelect.value) return;
      if (data.task_id !== taskId || data.worker !== worker || data.container !== container || typeof data.text !== "string") throw new Error("日志目标返回不一致，未显示此次响应。");
      // A bounded snapshot only; never interpret remote log text as HTML.
      const bounded = data.text.length > 1024 * 1024;
      const text = data.text.slice(-1024 * 1024), changed = output.textContent !== text;
      if (changed) { output.textContent = text; matchIndex = -1; byId("logs-search-status").textContent = byId("logs-search").value ? "日志已更新，按下一处查找" : ""; }
      state.updated = data.updated_at; state.entries = data.entries; state.dataTarget = target;
      if (changed && byId("logs-follow").checked) output.scrollTop = output.scrollHeight;
      message(typeof data.warning === "string" ? data.warning : "");
      status(`${data.text ? `最新 ${Math.min(Number(data.entries) || 0, 1000)} 条` : "暂无日志"} · 更新 ${timestamp()}${data.truncated || bounded ? " · 已截断" : ""} · 最多 1000 条快照`);
    } catch (error) {
      if (!current()) return;
      if (error.code === "target_mismatch") state.workers = [];
      message(error.name === "AbortError" ? "读取日志超时，请稍后刷新。" : error instanceof TypeError ? "本地服务连接中断，请稍后刷新。" : error.message);
      status(state.updated ? `保留旧日志 · 上次成功 ${timestamp()} · ${state.entries} 条` : "日志尚未读取成功");
    } finally {
      clearTimeout(timeout);
      if (generation === state.generation) { state.controller = null; byId("logs-refresh").disabled = false; schedule(); }
    }
  }
  function selectTab(tab) {
    if (state.tab === tab) return;
    invalidate(); state.tab = tab;
    byId("detail-panel").classList.toggle("showing-logs", tab === "logs");
    for (const name of ["monitor", "logs"]) {
      const selected = name === tab, button = byId(`detail-tab-${name}`);
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
      byId(`detail-${name}`).hidden = !selected;
    }
    byId("history-ranges").hidden = tab !== "monitor";
    byId("history-note").hidden = tab !== "monitor";
    document.dispatchEvent(new CustomEvent("monitor-detail-tab", { detail: tab }));
    if (active()) refresh();
  }
  for (const name of ["monitor", "logs"]) {
    const button = byId(`detail-tab-${name}`);
    button.addEventListener("click", () => selectTab(name));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? "monitor" : event.key === "End" ? "logs" : name === "logs" ? "monitor" : "logs";
      selectTab(next); byId(`detail-tab-${next}`).focus();
    });
  }
  workerSelect.addEventListener("change", () => { invalidate(); containers(); clearLog(); refresh(); });
  containerSelect.addEventListener("change", () => { invalidate(); clearLog(); refresh(); });
  byId("logs-refresh").addEventListener("click", refresh);
  byId("logs-auto").addEventListener("change", () => { clearTimeout(state.timer); if (byId("logs-auto").checked) schedule(); });
  byId("logs-follow").addEventListener("change", () => {
    byId("logs-search-status").textContent = byId("logs-follow").checked ? "正在跟随底部" : "已暂停跟随";
    if (byId("logs-follow").checked) output.scrollTop = output.scrollHeight;
  });
  byId("logs-search").addEventListener("input", () => findLog());
  byId("logs-search").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); findLog(true); } });
  byId("logs-search-next").addEventListener("click", () => findLog(true));
  byId("logs-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(output.textContent); message("当前显示的日志已复制。"); }
    catch {
      const selection = window.getSelection(), range = document.createRange(); range.selectNodeContents(output); selection.removeAllRanges(); selection.addRange(range); output.focus();
      message("浏览器未允许复制，已选中日志，请按 Ctrl+C 或 ⌘C 复制。");
    }
  });
  document.addEventListener("monitor-selection", (event) => {
    const task = event.detail;
    if (state.task?.id === task?.id) { state.task = task; return; }
    invalidate(); state.task = task; state.workers = [];
    options(workerSelect, []); options(containerSelect, []); clearLog();
    if (active()) refresh();
  });
  document.addEventListener("workspace-page", () => { invalidate(); if (active()) refresh(); });
  document.addEventListener("visibilitychange", () => { invalidate(); if (active()) refresh(); });
})();
