"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const cards = new Map();
  let timer = null, request = null, generation = 0, lastState = null;
  const active = () => location.hash === "#ssh" && !document.hidden;
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
  const quote = value => /^[A-Za-z0-9_][A-Za-z0-9_.@:\/-]*$/.test(value) ? value : "'" + value.replaceAll("'", "'\"'\"'") + "'";
  function commands(host) {
    const target = host.user ? `${host.user}@${host.hostname}` : host.hostname;
    return [`ssh -p ${host.port} ${target.startsWith("-") ? "-- " : ""}${quote(target)}`, `ssh ${quote(host.alias)}`];
  }
  function createCard(host) {
    const card = element("article", "ssh-card"), top = element("div", "ssh-card-heading"), identity = element("div", "ssh-identity"), mark = element("span", "ssh-server-icon"), name = element("h3", "", host.alias), badge = element("span", "ssh-online"), endpoint = element("p", "ssh-endpoint"), metadata = element("dl", "ssh-meta"), actions = element("div", "ssh-actions"), tools = element("div", "ssh-card-tools");
    const record = {card, badge, endpoint, host, details: [], copies: []};
    card.dataset.alias = host.alias;
    mark.append(icon("server")); identity.append(name, endpoint); tools.append(badge);
    for (const [index, label] of ["复制完整地址指令", "复制本机别名指令"].entries()) {
      const control = element("div", "ssh-copy-control"), copy = element("button", `ssh-copy${index ? " ssh-copy-alias" : ""}`), feedback = element("span", "ssh-copy-feedback");
      copy.type = "button"; copy.append(icon("copy"));
      if (index) copy.append(element("span", "", "别名"));
      feedback.setAttribute("role", "status");
      const item = {copy, label, reset: null}; record.copies.push(item);
      function copyIcon(name) {
        copy.replaceChildren(icon(name));
        if (index) copy.append(element("span", "", "别名"));
      }
      copy.addEventListener("click", async () => {
        clearTimeout(item.reset); copy.disabled = true;
        try {
          await navigator.clipboard.writeText(commands(record.host)[index]);
          copyIcon("check"); copy.dataset.state = "success"; feedback.textContent = "已复制";
        } catch (_) {
          copyIcon("copy"); copy.dataset.state = "error"; feedback.textContent = "复制失败，请重试";
        } finally {
          copy.disabled = false;
          item.reset = setTimeout(() => { copyIcon("copy"); delete copy.dataset.state; feedback.textContent = ""; }, 2500);
        }
      });
      control.append(copy, feedback); tools.append(control);
    }
    for (const label of ["用户", "端口"]) {
      const item = element("div", ""), detail = element("dd", "");
      item.append(element("dt", "", label), detail); metadata.append(item); record.details.push(detail);
    }
    for (const [page, label] of [["terminal", "进入 Terminal"], ["ide", "进入 VS Code"]]) {
      const link = document.createElement("a");
      link.className = `button ${page === "terminal" ? "button-primary" : "button-secondary"}`;
      const arrow = element("span", "ssh-link-arrow", "↗"); arrow.setAttribute("aria-hidden", "true");
      link.setAttribute("aria-label", `${label} · ${host.alias}`);
      link.append(icon(page), element("span", "", page === "terminal" ? "Terminal" : "VS Code"), arrow); link.target = "_blank"; link.rel = "noopener noreferrer";
      link.href = `/${page}?${new URLSearchParams({site: "ssh", task_id: "SSH:" + host.alias})}`;
      if (page === "ide") link.title = "首次使用会在服务器用户目录准备 VS Code（Linux x86_64 / ARM64）";
      actions.append(link);
    }
    top.append(mark, identity, tools); card.append(top, metadata, actions); return record;
  }
  const failures = element("details", "ssh-unavailable"), failureSummary = element("summary", ""), failureList = element("ul", "ssh-unavailable-list");
  failures.id = "ssh-unavailable"; failures.hidden = true; failures.append(failureSummary, failureList);
  $("ssh-empty").after(failures);
  const reasons = {authentication: "认证失败", host_key: "主机密钥校验失败", dns: "主机名解析失败", timeout: "连接超时", network: "网络不可达", connection: "连接失败"};
  let failureSignature = "";
  function render(state) {
    const hosts = state.available || [], unavailable = state.unavailable || [];
    const verified = new Set(state.verified || (!state.checking && !state.error && !state.stale ? hosts.map(host => host.alias) : []));
    const failed = new Set(state.error ? [] : unavailable.map(host => host.alias));
    $("ssh-view").dataset.state = state.error ? "error" : state.checking ? "checking" : "ready";
    $("ssh-refresh").disabled = state.checking;
    $("ssh-refresh-label").textContent = state.checking ? "检测中…" : "重新检测";
    $("ssh-progress").hidden = !state.checking;
    $("ssh-progress").max = state.total || 1; $("ssh-progress").value = state.checked || 0;
    $("ssh-count").textContent = state.checking ? verified.size : hosts.length;
    const retained = hosts.filter(host => !verified.has(host.alias)).length;
    $("ssh-status").textContent = state.error ? `${state.error}${hosts.length ? " · 保留上次结果，尚未重新验证" : ""}` : state.checking
      ? `正在检测 ${state.checked} / ${state.total} · 本次已验证 ${verified.size} 台${retained ? ` · ${retained} 台保留上次结果` : ""}`
      : `检测完成 · ${hosts.length} / ${state.total} 台可用${unavailable.length ? ` · ${unavailable.length} 台不可用` : ""}${state.checked_at ? " · " + new Date(state.checked_at * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : ""}`;
    $("ssh-empty").hidden = hosts.length > 0;
    $("ssh-empty").textContent = state.error || (state.checking ? "正在验证 SSH 连接，可用服务器会陆续显示…" : state.total ? "当前没有可连接的服务器，可展开下方检测结果查看原因。" : "未找到服务器，请检查本机 ~/.ssh/config。");
    const aliases = new Set(hosts.map(host => host.alias));
    for (const [alias, record] of cards) {
      if (aliases.has(alias)) continue;
      record.copies.forEach(item => clearTimeout(item.reset)); record.card.remove(); cards.delete(alias);
    }
    for (const host of hosts) {
      let record = cards.get(host.alias);
      if (!record) { record = createCard(host); cards.set(host.alias, record); $("ssh-servers").append(record.card); }
      record.host = host;
      const status = verified.has(host.alias) ? "ready" : failed.has(host.alias) ? "unavailable" : state.checking ? "verifying" : "stale";
      record.card.dataset.state = status; record.badge.dataset.state = status;
      record.badge.textContent = {ready: "可连接", unavailable: "本次不可用", verifying: "待验证", stale: "上次可用"}[status];
      record.endpoint.textContent = host.hostname; record.endpoint.title = host.hostname;
      [host.user, host.port].forEach((value, index) => { record.details[index].textContent = value; record.details[index].title = String(value); });
      commands(host).forEach((command, index) => { const {copy, label} = record.copies[index]; copy.title = `${label}：${command}`; copy.setAttribute("aria-label", copy.title); });
    }
    failures.hidden = unavailable.length === 0;
    failureSummary.textContent = `${state.error ? "上次检测" : "本次检测"} · ${unavailable.length} 台不可用`;
    const nextFailures = JSON.stringify(unavailable.map(host => [host.alias, host.reason]));
    if (nextFailures !== failureSignature) {
      failureSignature = nextFailures;
      failureList.replaceChildren(...unavailable.map(host => {
        const row = element("li", ""); row.append(element("span", "ssh-failure-alias", host.alias), element("span", "ssh-failure-reason", reasons[host.reason] || reasons.connection)); return row;
      }));
    }
  }
  function cancel() {
    generation += 1; clearTimeout(timer); timer = null;
    if (request) request.abort(); request = null;
  }
  async function update(refresh = false, refreshStale = false) {
    if (!active() || request) return;
    clearTimeout(timer);
    const version = generation, controller = new AbortController(); request = controller;
    let timedOut = false, next = null;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    try {
      const response = await fetch(refresh ? "/api/ssh/refresh" : "/api/ssh", {
        method: refresh ? "POST" : "GET", headers: {"X-Monitor-Request": "1"}, cache: "no-store", signal: controller.signal
      });
      if (!response.ok) throw new Error("无法读取 SSH 状态，请重试。");
      const state = await response.json();
      if (version !== generation || !active()) return;
      lastState = state; render(state);
      if (state.checking) next = "poll";
      else if (refreshStale && (state.error || state.stale || !state.checked_at || Date.now() - state.checked_at * 1000 >= 60000)) next = "refresh";
    } catch (error) {
      if (version !== generation || !active()) return;
      render({...lastState, available: lastState?.available || [], checking: false, stale: true, verified: [],
        error: timedOut ? "读取超时，请重新检测。" : error.message});
    } finally {
      clearTimeout(timeout);
      if (request === controller) request = null;
    }
    if (version !== generation || !active()) return;
    if (next === "refresh") update(true);
    else if (next === "poll") timer = setTimeout(() => update(), 1000);
  }
  $("ssh-refresh").addEventListener("click", () => update(true));
  $("refresh-button").addEventListener("click", () => { if (active()) update(true); });
  function enter() { cancel(); if (active()) update(false, true); }
  document.addEventListener("workspace-page", enter);
  document.addEventListener("visibilitychange", enter);
  enter();
})();
