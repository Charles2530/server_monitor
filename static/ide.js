"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  const taskId = new URL(location.href).searchParams.get("task_id");
  const mobileMedia = matchMedia("(max-width:760px), (pointer:coarse)");
  let targets = [], generation = 0, controller = null, busy = false;
  let connection = null, nextCheck = 0, recoveryFailures = 0;
  const loadingStarted = performance.now();
  const recentKey = `ilab-ide-recent:${site.id}`;
  function recentTarget() {
    try {
      const saved = JSON.parse(localStorage.getItem(recentKey));
      if (saved?.task_id === taskId && Number.isFinite(saved.saved_at) && Date.now() - saved.saved_at >= 0 && Date.now() - saved.saved_at < 1800000
          && [saved.instance, saved.container].every(value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$/.test(value))) return saved;
    } catch (_) { /* Storage may be unavailable; normal discovery remains usable. */ }
    return null;
  }
  $("ide-back").href = `/?site=${encodeURIComponent(site.id)}#tasks`;
  $("ide-task").textContent = taskId || "未指定任务";

  function status(message, state = "loading") { $("ide-status").textContent = message; $("ide-status").dataset.state = state; }
  function progress(value) {
    const labels = {download_editor: "下载 VS Code 编辑器", verify_editor: "校验编辑器安装包", extract_editor: "解压编辑器", download_codex: "下载 Codex 插件", verify_codex: "校验 Codex 插件", install_codex: "安装 Codex 插件", start_editor: "启动编辑器"};
    const label = value && labels[value.stage];
    $("ide-progress").hidden = !label;
    $("ide-progress-bar").removeAttribute("value");
    $("ide-progress-value").textContent = "";
    $("ide-progress-detail").textContent = "";
    if (!label) return;
    $("ide-progress-stage").textContent = label;
    const bytes = value.downloaded_bytes, total = value.total_bytes;
    const download = value.stage.startsWith("download_");
    const validBytes = Number.isSafeInteger(bytes) && bytes >= 0;
    const knownTotal = validBytes && Number.isSafeInteger(total) && total > 0 && total >= bytes;
    const size = n => `${(n / 1048576).toFixed(1)} MiB`;
    if (download && knownTotal) {
      const percent = Math.floor(bytes / total * 100);
      $("ide-progress-bar").value = percent;
      $("ide-progress-value").textContent = `${percent}%`;
      $("ide-progress-detail").textContent = `${size(bytes)} / ${size(total)} · 当前安装包下载进度`;
    } else if (download) {
      $("ide-progress-detail").textContent = validBytes ? `已下载 ${size(bytes)} · 总大小未知` : "正在等待下载服务器响应…";
    } else {
      $("ide-progress-detail").textContent = "此阶段无法计算百分比，完成后会自动继续。";
    }
  }
  function unsupported() {
    if (/Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) || mobileMedia.matches) return "VS Code 仅支持桌面浏览器，请在电脑上打开此任务。";
    if (location.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(location.hostname)) return "请在本机通过 http://127.0.0.1 或 http://localhost 打开桌面监控，再进入 VS Code。";
    if (!taskId?.startsWith("CCI:") || taskId.length <= 4) return "VS Code 仅支持运行中的 charles CCI 任务，请返回监控选择任务。";
    return "";
  }
  function cancel() { progress(null); generation += 1; controller?.abort(); controller = null; busy = false; }
  function resetFrame() { progress(null); $("ide-frame").hidden = true; $("ide-frame").removeAttribute("src"); }
  function controls() { ["ide-instance", "ide-container", "ide-open"].forEach(id => { $(id).disabled = busy; }); }
  function active(gen) { if (gen !== generation) throw new DOMException("Cancelled", "AbortError"); }
  async function api(path, data, gen) {
    active(gen);
    const request = new AbortController(), outer = controller.signal;
    const abort = () => request.abort();
    outer.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, 15000);
    try {
      const response = await site.fetch(`/api/cci/ide/${path}`, { method: "POST", headers: { "X-Monitor-Request": "1", "Content-Type": "application/json" }, body: JSON.stringify(data), cache: "no-store", signal: request.signal });
      const result = await response.json();
      active(gen);
      if (!response.ok) throw new Error(result.error || "编辑器请求失败，请重试。");
      return result;
    } catch (error) {
      active(gen);
      if (error.name === "AbortError") throw new Error("连接超时，请重试。");
      throw error;
    } finally { clearTimeout(timer); outer.removeEventListener("abort", abort); }
  }
  async function prepare(action, data, gen) {
    const start = await api(action, data, gen), deadline = Date.now() + 15 * 60 * 1000;
    if (typeof start.request_id !== "string" || !start.request_id) throw new Error("准备请求无效，请重试。");
    while (Date.now() < deadline) {
      const result = await api("status", { request_id: start.request_id }, gen);
      if (result.pending === false) {
        progress(null);
        if (result.error) throw new Error(result.error);
        return result.result;
      }
      if (result.pending !== true) throw new Error("准备状态无效，请重试。");
      progress(result.progress);
      if (typeof result.message === "string" && result.message) status(result.message);
      await new Promise(resolve => setTimeout(resolve, 400));
      active(gen);
    }
    throw new Error("编辑器准备超过 15 分钟，请重试以核对当前状态。");
  }
  function identity(result) {
    if (!result || result.site !== site.id || result.task_id !== taskId) throw new Error("编辑器响应与所选站点或任务不一致，请重试。");
  }
  function option(value) { const node = document.createElement("option"); node.value = value; node.textContent = value; return node; }
  function containers() { $("ide-container").replaceChildren(...targets.filter(t => t.instance === $("ide-instance").value).map(t => option(t.container))); }
  function selected() { return targets.find(t => t.instance === $("ide-instance").value && t.container === $("ide-container").value); }
  function failure(error, gen) {
    if (gen !== generation) return;
    busy = false; controls(); if (!connection) resetFrame(); status(error.message || "无法打开编辑器，请重试。", "error"); $("ide-retry").hidden = false;
  }
  async function open(target, gen) {
    busy = true; controls(); if (!connection) resetFrame();
    status("正在检查已有编辑器并建立连接，请稍候…");
    const result = await prepare("open", { task_id: taskId, instance: target.instance, container: target.container }, gen);
    identity(result);
    if (result.instance !== target.instance || result.container !== target.container || !Number.isInteger(result.port) || result.port < 1024 || result.port > 65535 || !/^[A-Za-z0-9_-]{40,100}$/.test(result.ticket || "") || typeof result.ticket !== "string") throw new Error("编辑器连接信息无效，请重试。");
    const destination = new URL(`${location.protocol}//${location.hostname}:${result.port}/_ilab_open/${result.ticket}`);
    if (destination.origin === location.origin) throw new Error("编辑器必须使用独立端口，请重试。");
    active(gen);
    $("ide-status").dataset.connectionSeconds = ((performance.now() - loadingStarted) / 1000).toFixed(1);
    if (targets.length === 1 && !target.saved_at) {
      try { localStorage.setItem(recentKey, JSON.stringify({ task_id: taskId, instance: target.instance, container: target.container, saved_at: Date.now() })); } catch (_) {}
    }
    $("ide-task").textContent = `${result.name || taskId} · ${taskId}`;
    connection = { instance: target.instance, container: target.container, port: result.port, origin: destination.origin, ready: false };
    nextCheck = Date.now() + 30000;
    $("ide-frame").src = destination.href;
    $("ide-frame").hidden = false;
    busy = false; controls();
    const codexNotice = result.codex?.configured && result.codex.installed === false ? " · Codex 插件尚未准备完成" : "";
    const appearanceNotice = result.appearance?.installed === false ? " · 外观预设尚未准备完成" : "";
    connection.notice = result.optional_pending ? " · 插件与外观正在后台准备" : codexNotice + appearanceNotice;
    status("连接已建立，正在加载编辑器…");
  }
  async function recover(force = false) {
    if (!connection || busy || document.hidden || (!force && Date.now() < nextCheck)) return;
    if (navigator.onLine === false) {
      status("网络已离线；已保留编辑器，网络恢复后会重试连接。", "error");
      return;
    }
    cancel(); controller = new AbortController(); const gen = generation;
    busy = true; controls(); nextCheck = Date.now() + 30000;
    if (force) status("正在检查并恢复编辑器连接；已保留当前编辑内容…");
    try {
      const result = await prepare("recover", { task_id: taskId, instance: connection.instance, container: connection.container }, gen);
      identity(result);
      if (result.instance !== connection.instance || result.container !== connection.container || result.port !== connection.port) throw new Error("编辑器连接已变化；请保留未保存内容后重新打开页面。");
      active(gen);
      recoveryFailures = 0;
      busy = false; controls();
      status(connection.ready ? `${connection.instance} / ${connection.container} · 连接可用` : "连接可用，正在加载编辑器…", connection.ready ? "ready" : "loading");
    } catch (error) {
      if (gen !== generation) return;
      recoveryFailures += 1;
      nextCheck = Date.now() + Math.min(120000, 30000 * 2 ** Math.min(recoveryFailures, 2));
      failure(error, gen);
    }
  }
  async function load() {
    cancel(); resetFrame(); $("ide-picker").hidden = true;
    const unavailable = unsupported();
    if (unavailable) { status(unavailable, "error"); $("ide-retry").hidden = true; return; }
    const gen = generation; controller = new AbortController(); busy = true; controls();
    $("ide-retry").hidden = false; status("正在读取最新实例与容器…");
    try {
      const remembered = recentTarget();
      if (remembered) {
        // This is only a selection hint. The server checks the live session or
        // revalidates current ownership before creating any new transport.
        targets = [remembered];
        try { await open(remembered, gen); return; }
        catch (error) {
          active(gen);
          try { localStorage.removeItem(recentKey); } catch (_) {}
        }
      }
      const result = await prepare("targets", { task_id: taskId }, gen);
      $("ide-status").dataset.discoverySeconds = ((performance.now() - loadingStarted) / 1000).toFixed(1);
      identity(result);
      if (!Array.isArray(result.targets) || !result.targets.length) throw new Error("当前没有可用的运行实例，请稍后重试。");
      if (result.targets.some(t => !t || typeof t.instance !== "string" || !t.instance || typeof t.container !== "string" || !t.container)) throw new Error("实例或容器信息无效，请重试。");
      targets = result.targets;
      $("ide-task").textContent = `${result.name || taskId} · ${taskId}`;
      $("ide-instance").replaceChildren(...[...new Set(targets.map(t => t.instance))].map(option));
      containers();
      if (targets.length === 1) await open(targets[0], gen);
      else { busy = false; controls(); $("ide-picker").hidden = false; status("选择实例与容器后打开编辑器。"); }
    } catch (error) { failure(error, gen); }
  }
  $("ide-instance").addEventListener("change", containers);
  $("ide-picker").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || unsupported()) return;
    const target = selected(); if (!target) return;
    cancel(); controller = new AbortController(); const gen = generation;
    try { await open(target, gen); } catch (error) { failure(error, gen); }
  });
  $("ide-retry").addEventListener("click", () => connection ? recover(true) : load());
  window.addEventListener("message", event => {
    if (!connection || event.source !== $("ide-frame").contentWindow || event.origin !== connection.origin || event.data?.type !== "ilab-editor-ready") return;
    if (connection.ready) return;
    connection.ready = true;
    const seconds = ((performance.now() - loadingStarted) / 1000).toFixed(1);
    $("ide-status").dataset.readySeconds = seconds;
    status(`${connection.instance} / ${connection.container} · 编辑器已就绪 · ${seconds} 秒${connection.notice || ""}`, "ready");
  });
  // A desktop window crossing the layout breakpoint must not unload the editor.
  mobileMedia.addEventListener("change", () => { if (!connection) load(); });
  window.addEventListener("focus", () => recover());
  window.addEventListener("online", () => recover(true));
  window.addEventListener("offline", () => { if (connection) status("网络已离线；已保留编辑器，网络恢复后会重试连接。", "error"); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) recover(); });
  window.addEventListener("pageshow", () => recover());
  setInterval(() => recover(), 30000);
  window.addEventListener("pagehide", cancel);
  load();
})();
