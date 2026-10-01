"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  const taskId = new URL(location.href).searchParams.get("task_id");
  const isACP = taskId?.startsWith("ACP:"), kind = isACP ? "ACP" : "CCI";
  const consoleBase = site.id === "sensecore" ? "https://console.sensecore.cn/cn-sh-01" : "https://console.ms-sc-01.maoshanwangtech.com";
  let targets = [], loading = false, controller = null;
  $("terminal-back").href = `/?site=${encodeURIComponent(site.id)}#tasks`;
  $("terminal-title").textContent = `${kind} Web Terminal`;
  document.title = `${kind} Web Terminal · iLab Server Monitor`;
  $("terminal-instance-label").textContent = isACP ? "Worker" : "实例";
  $("terminal-container-label").hidden = $("terminal-container").hidden = isACP;
  function safeURL(target) {
    const url = new URL(target.url), base = new URL(consoleBase), query = url.searchParams;
    const matches = isACP
      ? query.get("job_id") === taskId.slice(4) && query.get("pod_name") === target.instance && [...query.keys()].sort().join(",") === "job_id,pod_name,workspace"
      : query.get("app_name") === taskId.slice(4) && query.get("container_instance_name") === target.instance && query.get("container_name") === target.container && [...query.keys()].sort().join(",") === "app_name,container_instance_name,container_name,workspace";
    if (url.origin !== base.origin || url.pathname !== `${base.pathname.replace(/\/$/, "")}/${kind.toLowerCase()}/terminal` || url.username || url.password || url.hash
      || !matches || !query.get("workspace")) throw new Error("终端链接与所选任务不一致，请重新读取。");
    return url.href;
  }
  function option(value) { const node = document.createElement("option"); node.value = value; node.textContent = value; return node; }
  function selectContainer() {
    const target = targets.find(t => t.instance === $("terminal-instance").value && (isACP || t.container === $("terminal-container").value));
    $("terminal-open").removeAttribute("href");
    if (target) $("terminal-open").href = target.url;
  }
  function selectInstance() {
    if (!isACP) $("terminal-container").replaceChildren(...targets.filter(t => t.instance === $("terminal-instance").value).map(t => option(t.container)));
    selectContainer();
  }
  async function load() {
    if (loading) return;
    loading = true; targets = []; $("terminal-picker").hidden = true; $("terminal-retry").hidden = true; $("terminal-open").removeAttribute("href");
    $("terminal-status").dataset.state = "loading";
    $("terminal-picker").setAttribute("aria-busy", "true");
    $("terminal-task").textContent = taskId || "未选择任务";
    $("terminal-status").textContent = isACP ? "正在读取最新 Worker…" : "正在读取最新实例与容器…";
    controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 60000);
    try {
      if (!/^(CCI|ACP):[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/.test(taskId || "")) throw new Error("请选择有效的 CCI 或 ACP 任务。");
      const response = await site.fetch(`/api/${kind.toLowerCase()}/terminal-targets?task_id=${encodeURIComponent(taskId)}`, { signal: controller.signal, cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "暂时无法读取终端配置，请重试。");
      if (data.site !== site.id || data.task_id !== taskId || !Array.isArray(data.targets)) throw new Error("终端配置与当前站点或任务不一致。");
      targets = data.targets.map(target => ({ ...target, url: safeURL(target) }));
      $("terminal-task").textContent = `${data.name} · ${taskId}`;
      if (!targets.length) throw new Error(`当前没有可连接的运行中${isACP ? " Worker" : "主容器"}，请稍后重试或返回监控查看任务状态。`);
      if (targets.length === 1) {
        $("terminal-status").dataset.state = "ready";
        $("terminal-status").textContent = "已找到运行容器，正在进入云控制台终端…";
        location.replace(targets[0].url); return;
      }
      $("terminal-instance").replaceChildren(...[...new Set(targets.map(t => t.instance))].map(option));
      selectInstance(); $("terminal-picker").hidden = false;
      $("terminal-status").dataset.state = "ready";
      $("terminal-status").textContent = isACP ? "有多个运行中的 Worker，请选择要进入的 Worker。" : "有多个运行容器，请选择要进入的实例和容器。";
    } catch (error) {
      $("terminal-status").dataset.state = "error";
      $("terminal-status").textContent = error.name === "AbortError" ? "读取超时，请重试。" : error instanceof TypeError ? "连接中断或终端配置异常，请重试。" : error.message;
    } finally { clearTimeout(timer); loading = false; $("terminal-picker").setAttribute("aria-busy", "false"); $("terminal-retry").hidden = false; }
  }
  $("terminal-instance").addEventListener("change", selectInstance);
  $("terminal-container").addEventListener("change", selectContainer);
  $("terminal-retry").addEventListener("click", load);
  window.addEventListener("pagehide", () => controller?.abort());
  load();
})();
