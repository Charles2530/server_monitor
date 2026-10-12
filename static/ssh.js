"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let timer = null, busy = false, signature = "";
  function render(state) {
    $("ssh-refresh").disabled = state.checking;
    $("ssh-refresh").textContent = state.checking ? "检测中…" : "重新检测";
    $("ssh-count").textContent = state.available.length;
    $("ssh-status").textContent = state.error || (state.checking
      ? `正在检测 ${state.checked} / ${state.total} · 已找到 ${state.available.length} 台可用服务器`
      : `检测完成 · ${state.available.length} / ${state.total} 台可用${state.checked_at ? " · " + new Date(state.checked_at * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : ""}`);
    $("ssh-empty").hidden = state.available.length > 0;
    $("ssh-empty").textContent = state.error || (state.checking ? "正在验证 SSH 连接，可用服务器会陆续显示…" : state.total ? "当前没有可连接的服务器，切换网络后可重新检测。" : "未找到服务器，请检查本机 ~/.ssh/config。" );
    const next = JSON.stringify(state.available);
    if (signature === next) return;
    signature = next;
    $("ssh-servers").replaceChildren(...state.available.map(host => {
      const card = document.createElement("article"), top = document.createElement("div"), name = document.createElement("h3"), badge = document.createElement("span"), endpoint = document.createElement("p"), actions = document.createElement("div");
      card.className = "ssh-card"; top.className = "ssh-card-heading"; badge.className = "ssh-online"; actions.className = "ssh-actions";
      name.textContent = host.alias; badge.textContent = "可连接";
      endpoint.textContent = `${host.user}@${host.hostname}:${host.port}`; endpoint.title = endpoint.textContent;
      for (const [page, label] of [["terminal", "进入 Terminal"], ["ide", "进入 VS Code"]]) {
        const link = document.createElement("a");
        link.className = `button ${page === "terminal" ? "button-primary" : "button-secondary"}`;
        link.textContent = label + " ↗"; link.target = "_blank"; link.rel = "noopener noreferrer";
        link.href = `/${page}?${new URLSearchParams({site: "ssh", task_id: "SSH:" + host.alias})}`;
        if (page === "ide") link.title = "首次使用会在服务器用户目录准备 VS Code（Linux x86_64）";
        actions.append(link);
      }
      top.append(name, badge); card.append(top, endpoint, actions); return card;
    }));
  }
  async function update(refresh = false) {
    if (busy) return;
    clearTimeout(timer); busy = true;
    try {
      const response = await fetch(refresh ? "/api/ssh/refresh" : "/api/ssh", {
        method: refresh ? "POST" : "GET", headers: {"X-Monitor-Request": "1"}, cache: "no-store", signal: AbortSignal.timeout(15000)
      });
      if (!response.ok) throw new Error("无法读取 SSH 状态，请重试。");
      const state = await response.json(); render(state);
      if (state.checking && location.hash === "#ssh") timer = setTimeout(() => update(), 1000);
    } catch (error) {
      $("ssh-status").textContent = error.name === "TimeoutError" ? "读取超时，请重新检测。" : error.message;
      $("ssh-refresh").disabled = false; $("ssh-refresh").textContent = "重新检测";
    } finally { busy = false; }
  }
  $("ssh-refresh").addEventListener("click", () => update(true));
  $("refresh-button").addEventListener("click", () => { if (location.hash === "#ssh") update(true); });
  function enter() {
    if (location.hash !== "#ssh") { clearTimeout(timer); return; }
    update(true);
  }
  document.addEventListener("workspace-page", enter);
  enter();
})();
