"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  const taskId = new URL(location.href).searchParams.get("task_id");
  const mobileMedia = matchMedia("(max-width:760px), (pointer:coarse)");
  let targets = [], generation = 0, controller = null, busy = false;
  $("ide-back").href = `/?site=${encodeURIComponent(site.id)}#tasks`;
  $("ide-task").textContent = taskId || "未指定任务";

  function status(message, state = "loading") { $("ide-status").textContent = message; $("ide-status").dataset.state = state; }
  function unsupported() {
    if (/Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) || mobileMedia.matches) return "VS Code 仅支持桌面浏览器，请在电脑上打开此任务。";
    if (location.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(location.hostname)) return "请在本机通过 http://127.0.0.1 或 http://localhost 打开桌面监控，再进入 VS Code。";
    if (!taskId?.startsWith("CCI:") || taskId.length <= 4) return "VS Code 仅支持运行中的 charles CCI 任务，请返回监控选择任务。";
    return "";
  }
  function cancel() { generation += 1; controller?.abort(); controller = null; busy = false; }
  function resetFrame() { $("ide-frame").hidden = true; $("ide-frame").removeAttribute("src"); }
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
        if (result.error) throw new Error(result.error);
        return result.result;
      }
      if (result.pending !== true) throw new Error("准备状态无效，请重试。");
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
    busy = false; controls(); resetFrame(); status(error.message || "无法打开编辑器，请重试。", "error"); $("ide-retry").hidden = false;
  }
  async function open(target, gen) {
    busy = true; controls(); resetFrame();
    status("正在准备编辑器；首次安装可能需要 15 分钟…");
    const result = await prepare("open", { task_id: taskId, instance: target.instance, container: target.container }, gen);
    identity(result);
    if (result.instance !== target.instance || result.container !== target.container || !Number.isInteger(result.port) || result.port < 1024 || result.port > 65535 || !/^[A-Za-z0-9_-]{40,100}$/.test(result.ticket || "") || typeof result.ticket !== "string") throw new Error("编辑器连接信息无效，请重试。");
    const destination = new URL(`${location.protocol}//${location.hostname}:${result.port}/_ilab_open/${result.ticket}`);
    if (destination.origin === location.origin) throw new Error("编辑器必须使用独立端口，请重试。");
    active(gen);
    $("ide-frame").src = destination.href;
    $("ide-frame").hidden = false;
    busy = false; controls();
    const codexNotice = result.codex?.configured && result.codex.installed === false ? " · Codex 插件准备失败，可稍后重新连接重试" : "";
    status(`${target.instance} / ${target.container} · 编辑器已就绪${codexNotice}`, "ready");
  }
  async function load() {
    cancel(); resetFrame(); $("ide-picker").hidden = true;
    const unavailable = unsupported();
    if (unavailable) { status(unavailable, "error"); $("ide-retry").hidden = true; return; }
    const gen = generation; controller = new AbortController(); busy = true; controls();
    $("ide-retry").hidden = false; status("正在读取最新实例与容器…");
    try {
      const result = await prepare("targets", { task_id: taskId }, gen);
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
  $("ide-retry").addEventListener("click", load);
  mobileMedia.addEventListener("change", load);
  window.addEventListener("pagehide", cancel);
  load();
})();
