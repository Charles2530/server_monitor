"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const button = $("task-rename-open"), dialog = $("task-rename-dialog"), form = $("task-rename-form");
  const input = $("task-rename-name"), save = $("task-rename-save"), cancel = $("task-rename-cancel"), message = $("task-rename-message");
  const site = window.MonitorSite?.id || "maoshanwang";
  let selected = null, target = null, busy = false, pending = null, unavailable = false;
  const key = taskId => `monitor.task-rename.${site}.${taskId}`;
  const validName = value => /^[\u4e00-\u9fa5a-zA-Z0-9][\u4e00-\u9fa5a-zA-Z0-9_\-.:]{0,255}$/.test(value) && /charles/i.test(value);
  function notice(text) { message.textContent = text; message.hidden = !text; }
  function controls() {
    button.hidden = !selected || !["CCI", "ACP"].includes(selected.kind);
    button.disabled = busy || unavailable;
    input.disabled = busy || Boolean(pending);
    save.disabled = busy;
    cancel.disabled = busy;
    save.textContent = busy ? (pending?.checking ? "正在核对…" : "正在保存…") : pending ? "核对结果" : "保存名称";
    form.setAttribute("aria-busy", String(busy));
  }
  function restore(taskId) {
    try {
      const data = JSON.parse(sessionStorage.getItem(key(taskId)));
      if (data?.task_id === taskId && typeof data.expected_name === "string" && validName(data.display_name)
          && /^[0-9a-f-]{36}$/i.test(data.request_id)) return { data, checking: true };
    } catch { /* Storage may be disabled; the current page still retains its request. */ }
    return null;
  }
  function remember() {
    try { sessionStorage.setItem(key(target.id), JSON.stringify(pending.data)); } catch { /* In-memory recovery remains available. */ }
  }
  function forget(taskId) {
    try { sessionStorage.removeItem(key(taskId)); } catch { /* No stored state available. */ }
    pending = null;
  }
  function close() { if (!busy && dialog.open) dialog.close(); }
  button.addEventListener("click", () => {
    if (!selected || busy || unavailable) return;
    const retained = target?.id === selected.id ? pending : null;
    target = { ...selected };
    pending = retained || restore(target.id);
    input.value = pending?.data.display_name || target.name;
    $("task-rename-current").textContent = `当前任务：${target.name} · ${target.id}`;
    notice(pending ? "上次提交尚未确认，点击“核对结果”读取云端名称，不会重复修改。" : "");
    controls(); dialog.showModal();
    if (pending) save.focus(); else { input.focus(); input.select(); }
  });
  cancel.addEventListener("click", close);
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !target) return;
    if (!pending) {
      const name = input.value.trim();
      if (!validName(name)) {
        notice("名称须包含 charles，最多 256 字符，以中英文或数字开头，仅支持中英文、数字、下划线、连字符、点和冒号。");
        input.focus(); return;
      }
      if (name === target.name) { close(); return; }
      pending = { data: { task_id: target.id, expected_name: target.name, display_name: name, request_id: crypto.randomUUID() }, checking: false };
    }
    const submission = { ...pending.data, check_only: pending.checking };
    remember(); busy = true; notice(""); controls();
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 85000);
    try {
      const response = await (window.MonitorSite?.fetch || fetch)("/api/tasks/rename", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Monitor-Request": "1" },
        body: JSON.stringify(submission), signal: controller.signal, cache: "no-store",
      });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(typeof data.error === "string" ? data.error : "重命名暂不可用。");
        error.code = data.code; throw error;
      }
      if (data.site !== site || data.task_id !== submission.task_id || data.name !== submission.display_name
          || !Number.isFinite(Date.parse(data.verified_at))) throw new Error("返回结果无法确认。");
      forget(submission.task_id);
      document.dispatchEvent(new CustomEvent("monitor-task-renamed", { detail: data }));
      dialog.close();
      $("task-rename-status").textContent = `已同步云端名称：${data.name}`;
    } catch (error) {
      const rejected = ["not_submitted", "invalid_request", "name_changed", "target_mismatch", "request_conflict", "auth_required", "cloud_rejected"].includes(error.code);
      if (rejected) forget(submission.task_id);
      else if (pending) pending.checking = true;
      notice(rejected ? error.message : "改名结果尚未确认，请点击“核对结果”或在云控制台查看。核对不会重复发送修改。");
      if (error.code === "name_changed") document.dispatchEvent(new CustomEvent("monitor-refresh"));
    } finally {
      clearTimeout(timer); busy = false; controls();
    }
  });
  document.addEventListener("monitor-selection", event => {
    const previousId = selected?.id;
    selected = event.detail;
    if (previousId !== selected?.id) {
      $("task-rename-status").textContent = "";
      if (!busy) close();
    }
    controls();
  });
  document.addEventListener("monitor-snapshot", event => {
    unavailable = Boolean(event.detail.unavailable) || !event.detail.snapshot?.connected;
    controls();
  });
  window.addEventListener("beforeunload", event => {
    if (busy) { event.preventDefault(); event.returnValue = ""; }
  });
  controls();
})();
