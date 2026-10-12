"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let timer = null, busy = false, signature = "";
  function element(tag, className, text) {
    const node = document.createElement(tag); node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [key, value] of Object.entries({viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.7", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true"})) svg.setAttribute(key, value);
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", {server: "M5 3h14v7H5zM5 14h14v7H5zM8 6.5h.01M8 17.5h.01M12 6.5h4M12 17.5h4", terminal: "m5 6 6 6-6 6m9 0h5", ide: "m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18", copy: "M9 9h11v11H9zM15 5V3H3v12h2", check: "m5 12 4 4L19 6"}[name]);
    svg.append(path); return svg;
  }
  function render(state) {
    $("ssh-view").dataset.state = state.error ? "error" : state.checking ? "checking" : "ready";
    $("ssh-refresh").disabled = state.checking;
    $("ssh-refresh-label").textContent = state.checking ? "检测中…" : "重新检测";
    $("ssh-progress").hidden = !state.checking;
    $("ssh-progress").max = state.total || 1; $("ssh-progress").value = state.checked;
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
      const card = element("article", "ssh-card"), top = element("div", "ssh-card-heading"), identity = element("div", "ssh-identity"), mark = element("span", "ssh-server-icon"), name = element("h3", "", host.alias), badge = element("span", "ssh-online", "可连接"), endpoint = element("p", "ssh-endpoint", host.hostname), metadata = element("dl", "ssh-meta"), actions = element("div", "ssh-actions");
      mark.append(icon("server")); identity.append(name, endpoint);
      endpoint.title = host.hostname;
      const target = `${host.user}@${host.hostname}`;
      const escaped = /^[A-Za-z0-9_][A-Za-z0-9_.@:\/-]*$/.test(target) ? target : "'" + target.replaceAll("'", "'\"'\"'") + "'";
      const tools = element("div", "ssh-card-tools"), control = element("div", "ssh-copy-control"), copy = element("button", "ssh-copy"), feedback = element("span", "ssh-copy-feedback"), command = `ssh -p ${host.port} ${target.startsWith("-") ? "-- " : ""}${escaped}`;
      copy.type = "button"; copy.title = `复制登录指令：${command}`; copy.setAttribute("aria-label", copy.title); copy.append(icon("copy"));
      feedback.setAttribute("role", "status");
      let reset;
      copy.addEventListener("click", async () => {
        clearTimeout(reset); copy.disabled = true;
        try {
          await navigator.clipboard.writeText(command);
          copy.replaceChildren(icon("check")); copy.dataset.state = "success"; feedback.textContent = "已复制";
        } catch (_) {
          copy.replaceChildren(icon("copy")); copy.dataset.state = "error"; feedback.textContent = "复制失败，请重试";
        } finally {
          copy.disabled = false;
          reset = setTimeout(() => { copy.replaceChildren(icon("copy")); delete copy.dataset.state; feedback.textContent = ""; }, 2500);
        }
      });
      control.append(copy, feedback); tools.append(badge, control);
      for (const [label, value] of [["用户", host.user], ["端口", host.port]]) {
        const item = element("div", ""), detail = element("dd", "", value);
        detail.title = String(value); item.append(element("dt", "", label), detail); metadata.append(item);
      }
      for (const [page, label] of [["terminal", "进入 Terminal"], ["ide", "进入 VS Code"]]) {
        const link = document.createElement("a");
        link.className = `button ${page === "terminal" ? "button-primary" : "button-secondary"}`;
        const arrow = element("span", "ssh-link-arrow", "↗"); arrow.setAttribute("aria-hidden", "true");
        link.setAttribute("aria-label", label);
        link.append(icon(page), element("span", "", page === "terminal" ? "Terminal" : "VS Code"), arrow); link.target = "_blank"; link.rel = "noopener noreferrer";
        link.href = `/${page}?${new URLSearchParams({site: "ssh", task_id: "SSH:" + host.alias})}`;
        if (page === "ide") link.title = "首次使用会在服务器用户目录准备 VS Code（Linux x86_64 / ARM64）";
        actions.append(link);
      }
      top.append(mark, identity, tools); card.append(top, metadata, actions); return card;
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
      $("ssh-view").dataset.state = "error"; $("ssh-progress").hidden = true;
      $("ssh-refresh").disabled = false; $("ssh-refresh-label").textContent = "重新检测";
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
