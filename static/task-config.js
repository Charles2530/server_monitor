"use strict";

(() => {
  const button = document.getElementById("task-config-open");
  const dialog = document.createElement("dialog");
  dialog.id = "task-config-dialog";
  dialog.className = "task-config-dialog";
  dialog.setAttribute("aria-labelledby", "task-config-title");
  // Only static markup here; all cloud values below are inserted as text.
  dialog.innerHTML = `<header class="task-config-header"><div><small>ACP · TASK CONFIGURATION</small><h2 id="task-config-title">任务启动配置</h2><p id="task-config-name"></p></div><button type="button" class="button button-secondary" id="task-config-close" aria-label="关闭任务配置">关闭</button></header><div id="task-config-body" class="task-config-body" aria-live="polite"></div>`;
  document.body.append(dialog);
  const body = document.getElementById("task-config-body");
  let selected = null, controller = null, generation = 0;
  const site = window.MonitorSite?.id || "maoshanwang";
  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function fields(items, className = "") {
    const list = node("dl", undefined, `task-config-fields ${className}`.trim());
    for (const [label, value, fieldClass] of items) {
      const field = node("div", undefined, fieldClass);
      if (fieldClass === "task-config-state") field.dataset.state = String(value ?? "").trim().toUpperCase();
      field.append(node("dt", label), node("dd", value === null || value === undefined || value === "" ? "—" : String(value)));
      list.append(field);
    }
    return list;
  }
  function invalidate() { generation++; controller?.abort(); controller = null; }
  function close() { invalidate(); if (dialog.open) dialog.close(); }
  function commandPane(command) {
    const section = node("section", undefined, "task-config-command");
    const toolbar = node("div", undefined, "task-config-command-toolbar");
    const copy = node("button", "复制命令"), wrap = node("button", "自动换行");
    copy.type = wrap.type = "button";
    wrap.setAttribute("aria-pressed", "true");
    toolbar.append(node("span", "启动命令 · Shell"), wrap, copy);
    const pre = node("pre", undefined, "task-config-code wrap");
    pre.tabIndex = 0;
    pre.setAttribute("aria-label", "启动命令原文");
    const text = typeof command === "string" ? command : "";
    const code = node("code", text);
    pre.append(code);
    // The separate gutter is decorative and never becomes part of the copied command.
    const gutter = node("pre", Array.from({length: text.split("\n").length}, (_, i) => i + 1).join("\n"), "task-config-gutter");
    gutter.setAttribute("aria-hidden", "true");
    gutter.hidden = true;
    const editor = node("div", undefined, "task-config-editor");
    editor.append(gutter, pre);
    pre.addEventListener("scroll", () => { gutter.scrollTop = pre.scrollTop; });
    const status = node("p", text ? "只读原文，复制时保留换行。" : (command == null ? "云端未返回启动命令。" : "此角色的启动命令为空。"), "task-config-copy-status");
    copy.disabled = !text;
    wrap.addEventListener("click", () => {
      const wrapped = pre.classList.toggle("wrap");
      wrap.setAttribute("aria-pressed", String(wrapped));
      gutter.hidden = wrapped;
    });
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(text); status.textContent = "已复制完整启动命令。"; }
      catch {
        const range = document.createRange(); range.selectNodeContents(code);
        const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        status.textContent = "浏览器未允许复制，已选中命令，请手动复制。";
      }
    });
    section.append(toolbar, editor, status);
    return section;
  }
  function render(data) {
    body.replaceChildren(fields([["站点", site], ["任务 ID", data.task_id], ["集群", data.cluster], ["分区", data.zone], ["框架", data.framework], ["状态", data.state, "task-config-state"]], "task-config-overview"));
    for (const role of data.roles) {
      const section = node("section", undefined, "task-config-section");
      section.append(node("h3", `${role.name || "未命名角色"} · 启动配置`));
      section.append(fields([["镜像", role.image, "task-config-field-wide"], ["副本数", role.replicas],
        ...(role.resources || []).map((r, i) => [`资源规格 ${i + 1}`, r.name])]));
      section.append(commandPane(role.command));
      body.append(section);
    }
    if (!data.roles.length) body.append(node("p", "云端未返回角色配置。"));
    const mounts = node("section", undefined, "task-config-section");
    mounts.append(node("h3", "AFS 挂载"));
    for (const mount of data.afs_mounts || []) {
      mounts.append(fields([["存储卷", mount.id], ["分区", mount.zone], ["源子目录", mount.subdir], ["容器挂载路径", mount.mount_path]], "task-config-mount"));
    }
    if (!data.afs_mounts?.length) mounts.append(node("p", "此任务没有 AFS 挂载记录。"));
    body.append(mounts);
  }
  async function load() {
    if (!selected || button.hidden) return;
    invalidate();
    const task = selected, version = generation, request = new AbortController();
    controller = request;
    document.getElementById("task-config-name").textContent = task.name || task.id;
    body.replaceChildren(node("p", "正在读取该任务的实际启动配置…", "task-config-status"));
    if (!dialog.open) dialog.showModal();
    const timer = setTimeout(() => request.abort(), 60000);
    try {
      const response = await window.MonitorSite.fetch(`/api/acp/task-config?${new URLSearchParams({task_id: task.id})}`, {signal: request.signal, cache: "no-store"});
      const data = await response.json();
      if (version !== generation || !dialog.open) return;
      if (!response.ok) throw new Error(data.code === "auth_required" ? "云认证已失效，请重新连接后重试。" : "读取配置失败，请稍后重试。");
      if (data.task_id !== task.id || data.site !== site || !Array.isArray(data.roles)) throw new Error("返回的任务配置不匹配，请重新选择任务。");
      render(data);
    } catch (error) {
      if (version !== generation || !dialog.open) return;
      const retry = node("button", "重新读取", "button button-secondary"); retry.type = "button";
      retry.addEventListener("click", load);
      body.replaceChildren(node("p", error.name === "AbortError" ? "读取超时，请重试。" : error.message, "task-config-status"), retry);
    } finally { clearTimeout(timer); if (version === generation) controller = null; }
  }
  document.addEventListener("monitor-selection", ({detail: task}) => {
    if (task?.id !== selected?.id) close();
    selected = task;
    button.hidden = !(task?.kind === "ACP" && /charles/i.test(task.name || ""));
  });
  button.addEventListener("click", load);
  document.getElementById("task-config-close").addEventListener("click", close);
  dialog.addEventListener("close", invalidate);
  dialog.addEventListener("cancel", invalidate);
  window.addEventListener("hashchange", close);
})();
