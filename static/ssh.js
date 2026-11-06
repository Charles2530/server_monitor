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
    path.setAttribute("d", {server: "M5 3h14v7H5zM5 14h14v7H5zM8 6.5h.01M8 17.5h.01M12 6.5h4M12 17.5h4", terminal: "m5 6 6 6-6 6m9 0h5", ide: "m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18", copy: "M9 9h11v11H9zM15 5V3H3v12h2", check: "m5 12 4 4L19 6", close: "m6 6 12 12M6 18 18 6", download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5", upload: "M12 15V3m-5 5 5-5 5 5M4 16v5h16v-5", folder: "M3 5h6l2 2h10v13H3z", file: "M5 3h9l5 5v13H5zM14 3v6h5",
      activity: "M3 12h4l3-8 4 16 3-8h4", refresh: "M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 2M4 16l2 2a7 7 0 0 0 12-1",
      cpu: "M7 7h10v10H7zM10 10h4v4h-4zM9 3v4m6-4v4M9 17v4m6-4v4M3 9h4m-4 6h4m10-6h4m-4 6h4",
      memory: "M3 6h18v11H3zM7 10v3m5-3v3m5-3v3M6 17v3m4-3v3m4-3v3m4-3v3",
      swap: "M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4", disk: "M5 4h14l3 10v6H2v-6zM2 14h20M6 17h.01M10 17h.01",
      gpu: "M3 6h17v12H3zM20 9h2m-2 6h2M6 18v3m4-3v3m4-3v3M13 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6M6 9v6",
      network: "M9 3h6v6H9zM2 16h6v5H2zM16 16h6v5h-6zM12 9v4M5 16v-3h14v3",
      globe: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18",
      shield: "m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6zM8 12l3 3 5-6",
      key: "M21 8a5 5 0 1 1-10 0 5 5 0 0 1 10 0M12 12l-9 9H7v-4h4v-4M17 7h.01", user: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M4 21v-2a8 8 0 0 1 16 0v2",
      settings: "M4 7h16M4 17h16M8 4v6m8 4v6", clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2",
      info: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 11v6m0-10h.01", alert: "m12 3 10 18H2zM12 9v5m0 3h.01",
      chevron: "m9 5 7 7-7 7"}[name]);
    svg.append(path); return svg;
  }
  const quote = value => /^[A-Za-z0-9_][A-Za-z0-9_.@:\/-]*$/.test(value) ? value : "'" + value.replaceAll("'", "'\"'\"'") + "'";
  function command(host) {
    const target = host.user ? `${host.user}@${host.hostname}` : host.hostname;
    return `ssh -p ${host.port} ${target.startsWith("-") ? "-- " : ""}${quote(target)}`;
  }
  function createCard(host) {
    const card = element("article", "ssh-card"), top = element("div", "ssh-card-heading"), identity = element("div", "ssh-identity"), mark = element("span", "ssh-server-icon"), name = element("h3", "", host.alias), badge = element("span", "ssh-online"), endpoint = element("p", "ssh-endpoint"), metadata = element("dl", "ssh-meta"), actions = element("div", "ssh-actions"), tools = element("div", "ssh-card-tools");
    const details = element("div", "ssh-card-details"), utilities = element("div", "ssh-card-utilities");
    utilities.setAttribute("role", "group"); utilities.setAttribute("aria-label", "服务器操作");
    const record = {card, badge, endpoint, host, details: [], copy: null};
    card.dataset.alias = host.alias; name.title = host.alias;
    mark.append(icon("server")); identity.append(name, endpoint); tools.append(badge);
    {
      const control = element("div", "ssh-copy-control"), copy = element("button", "ssh-copy"), feedback = element("span", "ssh-copy-feedback");
      copy.type = "button"; copy.append(icon("copy"));
      feedback.setAttribute("role", "status");
      const item = record.copy = {copy, reset: null};
      function copyIcon(name) {
        copy.replaceChildren(icon(name));
      }
      copy.addEventListener("click", async () => {
        clearTimeout(item.reset); copy.disabled = true;
        try {
          await navigator.clipboard.writeText(command(record.host));
          copyIcon("check"); copy.dataset.state = "success"; feedback.textContent = "已复制";
        } catch (_) {
          copyIcon("copy"); copy.dataset.state = "error"; feedback.textContent = "复制失败，请重试";
        } finally {
          copy.disabled = false;
          item.reset = setTimeout(() => { copyIcon("copy"); delete copy.dataset.state; feedback.textContent = ""; }, 2500);
        }
      });
      control.append(copy, feedback); utilities.append(control);
    }
    for (const label of ["用户", "端口"]) {
      const item = element("div", ""), detail = element("dd", "");
      const term = element("dt", "", label); term.prepend(icon(label === "用户" ? "user" : "network"));
      item.append(term, detail); metadata.append(item); record.details.push(detail);
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
    const transfers = element("div", "ssh-transfer-actions");
    transfers.setAttribute("role", "group"); transfers.setAttribute("aria-label", "文件传输");
    for (const [direction, label] of [["download", "下载"], ["upload", "上传"]]) {
      const button = element("button", "ssh-copy ssh-transfer-launch");
      button.type = "button"; button.title = `${label}文件或文件夹`;
      button.setAttribute("aria-label", `${label}文件或文件夹 · ${host.alias}`);
      button.append(icon(direction)); button.addEventListener("click", () => openTransfer(record.host.alias, direction, button));
      transfers.append(button);
    }
    utilities.append(transfers); details.append(metadata, utilities);
    const health = element("button", "ssh-health-launch", "体检与诊断"); health.type = "button";
    health.prepend(icon("activity")); health.append(icon("chevron"));
    health.setAttribute("aria-label", `体检与诊断 · ${host.alias}`);
    health.addEventListener("click", () => openHealth(record.host, health));
    top.append(mark, identity, tools); card.append(top, details, actions, health); return record;
  }
  let healthDialog;
  function openHealth(host, opener) {
    if (!healthDialog) healthDialog = createHealthDialog();
    healthDialog.open(host, opener);
  }
  function createHealthDialog() {
    const dialog = element("dialog", "ssh-transfer-dialog ssh-health-dialog");
    dialog.setAttribute("aria-labelledby", "ssh-health-title");
    dialog.innerHTML = `<header class="ssh-transfer-header"><span class="ssh-health-mark" aria-hidden="true"></span><div><h2 id="ssh-health-title"></h2><p data-endpoint></p></div><button type="button" class="ssh-copy" data-close aria-label="关闭体检窗口"></button></header>
      <div class="ssh-health-toolbar"><span class="ssh-health-status-icon" aria-hidden="true" data-status-icon></span><div class="ssh-health-summary"><strong data-summary>尚未体检</strong><p class="ssh-health-time"><span aria-hidden="true" data-time-icon></span><span data-time></span></p></div><button type="button" class="button button-primary" data-run>开始体检</button></div>
      <p class="ssh-health-message" role="status" data-message></p><div data-results></div>
      <details class="ssh-health-help"><summary>采集范围与筛查阈值</summary><p>通过现有 SSH 配置执行固定只读脚本，读取 Linux 系统可见的数据；需要远端 python3，不安装 agent。系统指标不等于容器配额。CPU 与磁盘 IO 采样约 0.5 秒，本地文件系统空间与 inode 来自 df；GPU 数据来自可用的 nvidia-smi。</p><p>本轮阈值：内存 ≥90%、Swap ≥75%、磁盘空间或 inode ≥90%、GPU 温度 ≥85°C。单次快照用于初筛，不自动停止任务或修改服务器配置。诊断沿用本机 SSH 身份与主机校验设置；跳板路径不使用本机直连目标的结果判定故障。</p></details>`;
    document.body.append(dialog);
    const find = selector => dialog.querySelector(selector), run = find("[data-run]"), summary = find("[data-summary]"), message = find("[data-message]"), body = find("[data-results]");
    find(".ssh-health-mark").append(icon("activity")); find("[data-close]").append(icon("close"));
    find("[data-time-icon]").append(icon("clock")); run.prepend(icon("activity"));
    const scope = find(".ssh-health-help summary"); scope.prepend(icon("info")); scope.append(icon("chevron"));
    function setSummary(text, state) {
      summary.textContent = text; summary.dataset.state = state; find(".ssh-health-toolbar").dataset.state = state;
      find("[data-status-icon]").replaceChildren(icon({healthy:"shield", warning:"alert", unavailable:"alert", checking:"refresh"}[state] || "info"));
    }
    let host, opener, version = 0, controller, poll, signature = "", checking = false;
    const known = value => typeof value === "number" && Number.isFinite(value);
    const value = (number, unit = "") => known(number) ? `${Number(number.toFixed(1))}${unit}` : "未获取";
    const bytes = number => {
      if (!known(number)) return "未获取";
      const units = ["B", "KiB", "MiB", "GiB", "TiB"]; let index = 0;
      while (number >= 1024 && index < units.length-1) { number /= 1024; index++; }
      return `${Number(number.toFixed(1))} ${units[index]}`;
    };
    function heading(name, text, caption) {
      const h = element("h3", "ssh-health-heading"); h.append(icon(name), element("span", "", text));
      if (caption) h.append(element("small", "", caption));
      return h;
    }
    function meter(percent, threshold) {
      const track = element("span", "ssh-health-meter"), fill = element("span", ""); track.setAttribute("aria-hidden", "true");
      if (known(percent)) { fill.style.width = `${Math.min(100, Math.max(0, percent))}%`; track.append(fill); }
      if (known(percent) && threshold && percent >= threshold) track.dataset.state = "warning";
      return track;
    }
    function percentCell(percent, threshold) {
      const cell = element("div", "ssh-health-percent"); cell.append(element("span", "", value(percent, "%")));
      if (known(percent)) cell.append(meter(percent, threshold));
      return cell;
    }
    function table(name, title, headings, rows, empty) {
      const section = element("section", "ssh-health-section"), scroll = element("div", "ssh-health-table-scroll"), table = element("table", "ssh-health-table"), head = element("thead", ""), row = element("tr", "");
      section.append(heading(name, title));
      if (!rows.length) { section.append(element("p", "ssh-health-muted", empty)); return section; }
      for (const label of headings) { const th = element("th", "", label); th.scope = "col"; row.append(th); }
      head.append(row); const tbody = element("tbody", "");
      for (const values of rows) {
        const tr = element("tr", "");
        for (const text of values) { const td = element("td", ""); td.append(text instanceof Node ? text : document.createTextNode(String(text))); tr.append(td); }
        tbody.append(tr);
      }
      table.append(head, tbody); scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", `${title}，可滚动查看`);
      scroll.append(table); section.append(scroll); return section;
    }
    function emptyState(title, text) {
      const state = element("div", "ssh-health-empty"); state.append(icon("activity"), element("strong", "", title), element("p", "", text)); return state;
    }
    function results(result) {
      const next = JSON.stringify(result);
      if (next === signature) return;
      signature = next; body.replaceChildren();
      if (!result) {
        const empty = emptyState("开始一次服务器体检", "通过已配置的 SSH 连接，检查资源用量与连接各层。点击开始后采集，打开窗口不会自动执行。");
        const scope = element("div", "ssh-health-scope");
        for (const [name, text] of [["cpu","CPU 与负载"],["memory","内存与 Swap"],["disk","磁盘与 inode"],["gpu","GPU 状态"],["network","分层连接"]]) {
          const item = element("span", ""); item.append(icon(name), document.createTextNode(text)); scope.append(item);
        }
        empty.append(scope); body.append(empty); return;
      }
      const layout = element("div", "ssh-health-layout"), diagnostics = element("section", "ssh-health-diagnostics"), steps = element("ol", "ssh-health-steps"), metrics = element("div", "ssh-health-metrics");
      diagnostics.append(heading("network", "分层连接诊断", `${(result.steps || []).filter(step => step.status === "ok").length} / ${(result.steps || []).length} 通过`));
      for (const step of result.steps || []) {
        const li = element("li", ""), indicator = element("span", "ssh-health-indicator"), content = element("div", "ssh-health-step-content"), title = element("div", "ssh-health-step-title");
        li.dataset.state = step.status; indicator.append(icon({config:"settings",dns:"globe",tcp:"network",ssh:"shield",auth:"key",exec:"terminal"}[step.key] || "info"));
        const status = element("span", "ssh-health-step-status", {ok:"通过",error:"失败",skipped:"跳过"}[step.status] || "未知");
        title.append(element("strong", "", step.label), status); content.append(title, element("p", "", step.message));
        if (known(step.latency_ms)) status.append(element("small", "", `${step.latency_ms} ms`));
        li.append(indicator, content); steps.append(li);
      }
      diagnostics.append(steps); layout.append(diagnostics, metrics); body.append(layout);
      if (result.issues?.length) {
        const notice = element("div", "ssh-health-alert"), issues = element("ul", "ssh-health-issues");
        for (const issue of result.issues) issues.append(element("li", "", issue)); notice.append(icon("alert"), issues); metrics.append(notice);
      }
      const health = result.health;
      if (!health) { metrics.append(emptyState("主机指标暂未取得", result.message || "请根据连接诊断中的失败阶段检查配置与网络，再重新体检。")); return; }
      const uptime = known(health.uptime_seconds) ? `${Math.floor(health.uptime_seconds / 86400)} 天 ${Math.floor(health.uptime_seconds % 86400 / 3600)} 小时` : "未获取";
      const system = element("div", "ssh-health-system");
      for (const [name, text] of [["server",`${health.system} ${health.release} · ${health.architecture}`],["cpu",value(health.cpu_count, " 核")],["clock",`已运行 ${uptime}`]]) {
        const item = element("span", ""); item.append(icon(name), document.createTextNode(text)); system.append(item);
      }
      metrics.append(system);
      if (health.notes?.includes("unsupported_os")) metrics.append(element("p", "ssh-health-message", "当前系统暂不支持 Linux 指标采集；连接诊断仍可使用。"));
      const cards = element("div", "ssh-health-stats"), memory = health.memory || {};
      const load = health.load?.map(number => value(number)).join(" / ") || "未获取";
      for (const [name, label, main, caption, percent, threshold] of [
        ["cpu","CPU 使用率",value(health.cpu_percent,"%"),"约 0.5 秒采样窗口",health.cpu_percent],
        ["memory","内存使用率",value(memory.used_percent,"%"),`${bytes(memory.available_bytes)} 可用 / ${bytes(memory.total_bytes)}`,memory.used_percent,90],
        ["activity","系统负载",value(health.load?.[0]),`1 / 5 / 15 分钟：${load}`],
        ["swap","Swap 使用率",memory.swap_total_bytes === 0 ? "未配置" : value(memory.swap_percent,"%"),memory.swap_total_bytes === 0 ? "系统未配置 Swap" : `${bytes(memory.swap_free_bytes)} 可用 / ${bytes(memory.swap_total_bytes)}`,memory.swap_percent,75]
      ]) {
        const card = element("div", ""), title = element("div", "ssh-health-stat-label"); title.append(icon(name), element("span", "", label));
        card.append(title, element("strong", "", main)); if (known(percent)) card.append(meter(percent, threshold));
        card.append(element("small", "", caption)); if (known(percent) && threshold && percent >= threshold) card.dataset.state = "warning"; cards.append(card);
      }
      metrics.append(cards,
        table("disk", "本地文件系统", ["挂载点", "空间使用", "可用", "inode"], (health.disks || []).map(disk => {
          const used = percentCell(disk.used_percent, 90); used.append(element("small", "", `${bytes(disk.used_bytes)} / ${bytes(disk.total_bytes)}`));
          return [disk.mount,used,bytes(disk.available_bytes),percentCell(disk.inode_percent,90)];
        }), "文件系统信息未获取，请检查 df 与读取权限。"),
        table("gpu", "GPU 状态", ["设备", "利用率", "温度", "显存 MiB", "功耗 W"], (health.gpus || []).map(gpu => [`${value(gpu.index)} · ${gpu.name}`,percentCell(gpu.utilization_percent),value(gpu.temperature_c,"°C"),`${value(gpu.memory_used_mib)} / ${value(gpu.memory_total_mib)}`,`${value(gpu.power_w)} / ${value(gpu.power_limit_w)}`]), "未取得 GPU 信息：nvidia-smi 可能缺失或不可用，不代表机器没有 GPU。"));
      const io = element("details", "ssh-health-io"), ioTitle = element("summary", "", "磁盘 IO · 设备读写速率"); ioTitle.prepend(icon("disk")); ioTitle.append(icon("chevron"));
      io.append(ioTitle, table("activity", "采样窗口内的平均速率", ["设备", "读取", "写入"], (health.io || []).map(disk => [disk.device,`${bytes(disk.read_bps)}/s`,`${bytes(disk.write_bps)}/s`]), "磁盘 IO 数据未获取。")); metrics.append(io);
    }
    async function load(refresh = false) {
      if (!dialog.open || !active() || controller) return;
      clearTimeout(poll); const current = version, request = controller = new AbortController();
      const timeout = setTimeout(() => request.abort(), 20000); run.disabled = true; message.textContent = refresh ? "正在启动只读体检…" : "正在读取检测记录…";
      try {
        const response = await fetch(`/api/ssh/health${refresh ? "" : "?" + new URLSearchParams({alias: host.alias})}`, {method:refresh ? "POST" : "GET", headers:{"Content-Type":"application/json","X-Monitor-Request":"1"}, body:refresh ? JSON.stringify({alias:host.alias}) : undefined, cache:"no-store", signal:request.signal});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "体检暂不可用，请重试。");
        if (current !== version || !dialog.open || !active()) return;
        checking = !!data.checking;
        setSummary(checking ? "体检进行中…" : ({healthy:"本轮未见阈值异常",warning:`${data.result?.issues?.length || 0} 项需要关注`,partial:"部分指标或诊断未完成",unavailable:"SSH 连接未建立"}[data.result?.status] || "尚未体检"), checking ? "checking" : data.result?.status || "unknown");
        find("[data-time]").textContent = data.result?.checked_at ? `最近结果 · ${new Date(data.result.checked_at * 1000).toLocaleString("zh-CN",{hour12:false})}${Date.now()-data.result.checked_at*1000>300000 ? " · 超过 5 分钟，建议重新体检" : ""}` : "按需检测 · 最多同时检测两台服务器";
        message.textContent = checking ? "正在验证连接并读取指标，通常需要数秒；已有结果保留至新检测完成。" : "";
        run.replaceChildren(icon(data.result ? "refresh" : "activity"), element("span", "", data.result ? "重新体检" : "开始体检")); results(data.result);
        if (checking) poll = setTimeout(() => load(), 1000);
      } catch (error) {
        if (current === version && dialog.open && active()) {
          checking = false; setSummary("检测记录读取失败", "unavailable");
          message.textContent = error.name === "AbortError" ? "读取超时，检测可能仍在进行；可点击按钮重试。" : error.message;
        }
      } finally {
        clearTimeout(timeout);
        if (current === version) { controller = null; run.disabled = checking; }
      }
    }
    function pause() { version++; clearTimeout(poll); controller?.abort(); controller = null; }
    run.addEventListener("click", () => { checking = false; void load(true); });
    find("[data-close]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => { pause(); if (opener?.isConnected && active()) opener.focus(); });
    document.addEventListener("workspace-page", () => { if (!active() && dialog.open) dialog.close(); });
    document.addEventListener("visibilitychange", () => { if (!dialog.open) return; pause(); if (active()) void load(); });
    return {open(nextHost, button) { pause(); host = nextHost; opener = button; checking = false; signature = ""; body.replaceChildren();
      setSummary("正在读取记录…", "unknown"); find("[data-time]").textContent = "";
      find("h2").textContent = `体检与诊断 · ${host.alias}`; find("[data-endpoint]").textContent = `${host.user ? host.user + "@" : ""}${host.hostname || host.alias}:${host.port || 22}`;
      dialog.showModal(); void load(); }};
  }
  let transferDialog;
  function openTransfer(alias, direction, opener) {
    if (!transferDialog) transferDialog = createTransferDialog();
    transferDialog.open(alias, direction, opener);
  }
  window.openMonitorDirectoryPicker = options => {
    if (!transferDialog) transferDialog = createTransferDialog();
    transferDialog.openDirectory(options);
  };
  function createTransferDialog() {
    const dialog = element("dialog", "ssh-transfer-dialog");
    dialog.setAttribute("aria-labelledby", "ssh-transfer-title");
    dialog.innerHTML = `<header class="ssh-transfer-header"><span class="ssh-transfer-mark" aria-hidden="true"></span><div><h2 id="ssh-transfer-title"></h2><p data-step></p></div><button type="button" class="ssh-copy" data-close aria-label="关闭传输窗口"></button></header>
      <ol class="ssh-transfer-steps" aria-label="传输步骤"><li data-stage="source"><span>1</span>选择文件</li><li data-stage="destination"><span>2</span>选择保存目录</li></ol>
      <p class="ssh-transfer-summary" data-summary></p>
      <section data-browser><form class="ssh-transfer-path"><button type="button" class="button button-secondary" data-home>主目录</button><button type="button" class="button button-secondary" data-up aria-label="上一级目录">↑</button><input aria-label="文件或目录路径" maxlength="4096" placeholder="输入绝对路径"><button class="button button-secondary" type="submit">前往</button></form>
      <div class="ssh-transfer-filter"><input type="search" aria-label="筛选当前目录" placeholder="筛选当前目录"><label><input type="checkbox" data-hidden> 显示隐藏文件</label></div>
      <div class="ssh-transfer-list-heading"><span data-location></span><span data-count></span></div><div class="ssh-transfer-files" role="group" aria-label="文件和文件夹"></div><p class="ssh-transfer-hint" data-hint></p></section>
      <p class="ssh-transfer-message" role="status" data-message></p>
      <footer class="ssh-transfer-footer"><button type="button" class="button button-secondary" data-back>上一步</button><button type="button" class="button button-secondary" data-folder>选择此文件夹</button><button type="button" class="button button-primary" data-next>下一步</button></footer>
      <section class="ssh-transfer-jobs" aria-label="传输记录"><h3>传输记录</h3><div data-jobs></div><p class="ssh-transfer-hint">关闭窗口后继续传输；重新打开可查看结果。</p></section>`;
    document.body.append(dialog);
    dialog.querySelector("[data-close]").append(icon("close"));
    const find = selector => dialog.querySelector(selector), title = find("h2"), step = find("[data-step]"), summary = find("[data-summary]"), browser = find("[data-browser]"), form = find("form"), path = find("input[aria-label='文件或目录路径']"), filter = find("input[type=search]"), hidden = find("[data-hidden]"), list = find(".ssh-transfer-files"), hint = find("[data-hint]"), message = find("[data-message]"), back = find("[data-back]"), folder = find("[data-folder]"), next = find("[data-next]"), jobs = find("[data-jobs]");
    let alias, direction, phase, source, listing, selected, picker, busy = false, version = 0, pollVersion = 0, request, pollTimer, opener, sourceDirectory = "";
    const label = () => direction === "download" ? "下载" : "上传";
    const side = () => (direction === "download") === (phase === "source") ? "remote" : "local";
    const formatBytes = value => {
      if (!Number.isFinite(value) || value < 0) return "—";
      if (value < 1000) return `${Math.round(value)} B`;
      const units = ["KB", "MB", "GB", "TB"]; let amount = value, index = -1;
      while (amount >= 1000 && index < units.length - 1) { amount /= 1000; index++; }
      return `${amount >= 100 || Number.isInteger(amount) ? Math.round(amount) : amount.toFixed(1)} ${units[index]}`;
    };
    const formatDuration = seconds => {
      if (!Number.isFinite(seconds) || seconds < 0) return "—";
      seconds = Math.round(seconds);
      if (seconds < 60) return `${seconds} 秒`;
      const minutes = Math.floor(seconds / 60), remainder = seconds % 60;
      if (minutes < 60) return `${minutes} 分 ${String(remainder).padStart(2, "0")} 秒`;
      return `${Math.floor(minutes / 60)} 小时 ${String(minutes % 60).padStart(2, "0")} 分`;
    };
    const formatSpeed = value => Number.isFinite(value) && value > 0 ? `${formatBytes(value)}/秒` : "等待速度";
    const parentPath = () => listing?.parent || (path.value.startsWith("/") ? path.value.replace(/\/+$/, "").replace(/\/[^/]*$/, "") || "/" : "");
    function notice(text, kind = "info") { message.textContent = text; message.dataset.kind = kind; }
    async function api(url, data, signal) {
      const response = await fetch(url, {method: data ? "POST" : "GET", headers: {"Content-Type": "application/json", "X-Monitor-Request": "1"}, body: data ? JSON.stringify(data) : undefined, cache: "no-store", signal});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "请求失败，请重试。");
      return result;
    }
    function controls() {
      dialog.dataset.phase = phase;
      dialog.querySelectorAll("[data-stage]").forEach(node => {
        const current = node.dataset.stage === phase, done = phase === "started" || (phase === "destination" && node.dataset.stage === "source");
        node.dataset.state = current ? "current" : done ? "done" : "pending";
        if (current) node.setAttribute("aria-current", "step"); else node.removeAttribute("aria-current");
      });
      browser.hidden = phase === "started";
      back.hidden = phase !== "destination"; back.disabled = busy;
      folder.hidden = phase !== "source"; folder.disabled = busy || !listing || listing.path === "/";
      next.textContent = phase === "source" ? "下一步：选择保存目录" : phase === "destination" ? `开始${label()}` : "继续选择文件";
      next.disabled = busy || (phase === "source" ? !selected : phase === "destination" ? !listing : false);
      form.querySelectorAll("button, input").forEach(node => { node.disabled = busy; });
      list.querySelectorAll("button, input").forEach(node => { node.disabled = busy; });
      find("[data-up]").disabled = busy || !parentPath() || path.value === parentPath();
      filter.disabled = hidden.disabled = busy;
      list.setAttribute("aria-busy", String(busy));
      path.placeholder = phase === "source" ? "输入文件或目录的绝对路径" : "输入保存目录的绝对路径";
      step.textContent = direction === "download" ? "远端服务器 → 本机" : "本机 → 远端服务器";
      find("[data-location]").textContent = side() === "local" ? "本机 · 运行服务的 Mac" : `服务器 · ${alias}`;
      find("[data-count]").textContent = busy ? "读取中…" : listing ? `${list.querySelectorAll(".ssh-transfer-row").length} 项` : "";
      summary.textContent = source ? `${label()}来源：${source}` : selected ? `已选：${selected}` : "选择一项；点击文件夹名称可进入目录。";
      summary.dataset.selected = String(Boolean(source || selected));
      find(".ssh-transfer-steps").hidden = find(".ssh-transfer-jobs").hidden = Boolean(picker);
      if (picker) {
        back.hidden = true;
        next.textContent = "使用此目录";
        step.textContent = picker.label;
        path.placeholder = "输入目录的绝对路径";
        find("[data-location]").textContent = "文件夹";
        summary.textContent = listing ? `当前目录：${listing.path}` : "选择文件夹后确认，路径将填入传输表单。";
        summary.dataset.selected = "false";
      }
    }
    function renderFiles() {
      const query = filter.value.toLocaleLowerCase();
      const entries = (listing?.entries || []).filter(entry => (phase === "source" || entry.kind === "directory") && (hidden.checked || !entry.name.startsWith(".")) && entry.name.toLocaleLowerCase().includes(query));
      list.replaceChildren();
      for (const entry of entries) {
        const row = element("div", "ssh-transfer-row");
        if (phase === "source") {
          const radio = element("input", ""); radio.type = "radio"; radio.name = "ssh-transfer-selection"; radio.checked = selected === entry.path;
          radio.setAttribute("aria-label", `选择${entry.kind === "directory" ? "文件夹" : "文件"} ${entry.name}`);
          radio.addEventListener("change", () => { selected = entry.path; controls(); }); row.append(radio);
        }
        const name = element("button", "ssh-transfer-entry"); name.type = "button";
        name.dataset.kind = entry.kind;
        name.append(icon(entry.kind === "directory" ? "folder" : "file"), element("span", "", entry.name));
        name.title = entry.path;
        name.addEventListener("click", () => {
          if (entry.kind === "directory") browse(entry.path);
          else { selected = entry.path; row.querySelector("input").checked = true; controls(); }
        });
        row.append(name, element("span", "ssh-transfer-kind", entry.kind === "directory" ? "文件夹" : "文件")); list.append(row);
      }
      if (!entries.length) {
        const empty = element("div", "ssh-transfer-empty"); empty.append(icon("folder"), element("p", "", query ? "没有匹配的文件或文件夹" : "当前没有可显示的项目")); list.append(empty);
      }
      find("[data-count]").textContent = `${entries.length} 项`;
      hint.textContent = listing?.truncated ? "此目录项目较多，列表已截断。可输入文件或目录的完整路径。" : picker ? "点击文件夹进入；确认后使用当前目录。" : phase === "destination" ? "文件将保存到当前目录；同名目标不会覆盖。" : "可选择文件，也可选择整个文件夹。";
    }
    async function browse(target) {
      request?.abort(); const controller = new AbortController(); request = controller;
      const current = ++version, locationSide = side(); busy = true; listing = null; selected = null; path.value = target; notice("正在读取目录…"); list.replaceChildren(); controls();
      const timeout = setTimeout(() => controller.abort(), 35000);
      try {
        let result = picker ? await picker.browse(target, controller.signal) : await api("/api/ssh/files", {side: locationSide, alias, path: target}, controller.signal), file = null;
        if (current !== version) return;
        if (result.kind === "file") {
          if (phase !== "source") throw new Error("选择的位置必须是文件夹，请输入目录路径。");
          file = result;
          result = await api("/api/ssh/files", {side: locationSide, alias, path: file.parent}, controller.signal);
          if (!(result.entries || []).some(entry => entry.path === file.path)) result.entries.push(file);
        }
        if (current !== version) return;
        listing = result; selected = file?.path || null; path.value = result.path; filter.value = file?.name || "";
        if (file?.name.startsWith(".")) hidden.checked = true;
        notice(""); renderFiles();
      } catch (error) {
        if (current === version) notice(error.name === "AbortError" ? "读取超时，请重试。" : error.message, "error");
      } finally {
        clearTimeout(timeout);
        if (current === version) { busy = false; controls(); }
      }
    }
    async function poll() {
      clearTimeout(pollTimer); if (!dialog.open) return;
      const host = alias, current = version, cycle = ++pollVersion;
      try {
        const result = await api(`/api/ssh/transfers?${new URLSearchParams({alias: host})}`, null, AbortSignal.timeout(10000));
        if (!dialog.open || cycle !== pollVersion) return;
        jobs.replaceChildren();
        const activeJob = job => ["running", "finalizing", "cancelling"].includes(job.state);
        for (const job of [...result.jobs.filter(activeJob), ...result.jobs.filter(job => !activeJob(job)).slice(0, 10)]) {
          const row = element("div", "ssh-transfer-job"), text = element("div", "");
          row.dataset.state = job.state;
          const status = {running: "传输中…", cancelling: "正在取消…", finalizing: "正在保存…", completed: "已完成", cancelled: "已取消", failed: "失败"}[job.state] || job.state;
          const heading = element("div", "ssh-transfer-job-heading"), symbol = element("span", "ssh-transfer-job-icon");
          symbol.append(icon(job.direction === "upload" ? "upload" : "download"));
          heading.append(symbol, element("strong", "", job.source.split("/").pop()), element("span", "ssh-transfer-job-state", `${job.direction === "upload" ? "上传" : "下载"} · ${status}`));
          text.append(heading);
          const progress = element("div", "ssh-transfer-progress");
          const bar = element("progress", "");
          const knownProgress = Number.isFinite(job.progress_percent);
          bar.max = 100; bar.value = knownProgress ? Math.max(0, Math.min(100, job.progress_percent)) : 0;
          if (!knownProgress && activeJob(job)) bar.removeAttribute("value");
          bar.setAttribute("aria-label", knownProgress ? `传输进度 ${Math.round(job.progress_percent)}%` : "传输进度读取中");
          const stats = element("div", "ssh-transfer-progress-stats");
          const transferred = Number.isFinite(job.total_bytes) ? `${formatBytes(job.transferred_bytes)} / ${formatBytes(job.total_bytes)}` : `已传 ${formatBytes(job.transferred_bytes)}`;
          const percent = knownProgress ? `${Math.round(job.progress_percent)}%` : "计算中";
          stats.append(element("span", "", `${transferred} · ${percent}`), element("span", "", formatSpeed(job.speed_bps)));
          const estimate = element("span", "ssh-transfer-progress-eta", job.state === "completed" ? "已完成" : `预计剩余 ${formatDuration(job.eta_seconds)}`);
          stats.append(estimate); progress.append(bar, stats);
          if (job.current_file && (!knownProgress || job.current_file !== job.source.split("/").pop())) {
            progress.append(element("p", "ssh-transfer-current-file", `当前文件：${job.current_file}`));
          }
          text.append(progress);
          for (const [label, value] of [["来源", job.source], ["保存至", job.destination]]) {
            const path = element("p", "ssh-transfer-job-path"); path.append(element("span", "", label), element("span", "", value)); text.append(path);
          }
          if (job.error || job.warning) text.append(element("p", "ssh-transfer-message", [job.error, job.warning].filter(Boolean).join("；")));
          row.append(text);
          if (job.state === "running") {
            const cancel = element("button", "button button-secondary", "取消"); cancel.type = "button";
            cancel.addEventListener("click", async () => {
              cancel.disabled = true;
              try { await api(`/api/ssh/transfers/${encodeURIComponent(job.id)}/cancel`, {}); if (dialog.open && host === alias) poll(); }
              catch (error) { if (dialog.open && host === alias) { notice(error.message, "error"); cancel.disabled = false; } }
            }); row.append(cancel);
          }
          jobs.append(row);
        }
        if (!result.jobs.length) jobs.append(element("p", "ssh-transfer-hint", "暂无传输记录。"));
      } catch (_) {
        if (cycle === pollVersion && current === version && dialog.open) jobs.replaceChildren(element("p", "ssh-transfer-message", "传输状态暂不可用，正在重试…"));
      } finally { if (dialog.open && cycle === pollVersion) pollTimer = setTimeout(poll, 1000); }
    }
    async function advance() {
      if (busy) return;
      if (picker) {
        if (listing) { picker.onSelect(listing.path); dialog.close(); }
        return;
      }
      if (phase === "started") { phase = "source"; source = null; await browse(sourceDirectory); return; }
      if (phase === "source") {
        if (!selected) return;
        source = selected; sourceDirectory = listing.path; phase = "destination"; await browse(""); return;
      }
      if (!listing) return;
      busy = true; notice("正在提交传输…"); controls(); const current = version;
      try {
        await api("/api/ssh/transfers", {alias, direction, source, destination: listing.path});
        if (current !== version) return;
        phase = "started"; notice("传输已开始，可在下方查看进度。", "success"); poll();
      } catch (error) { if (current === version) notice(error.message, "error"); }
      finally { if (current === version) { busy = false; controls(); } }
    }
    form.addEventListener("submit", event => { event.preventDefault(); browse(path.value); });
    find("[data-home]").addEventListener("click", () => browse(""));
    find("[data-up]").addEventListener("click", () => { if (parentPath()) browse(parentPath()); });
    filter.addEventListener("input", renderFiles); hidden.addEventListener("change", renderFiles);
    folder.addEventListener("click", () => { selected = listing.path; advance(); });
    next.addEventListener("click", advance);
    back.addEventListener("click", () => { phase = "source"; source = null; browse(sourceDirectory); });
    find("[data-close]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => { version++; pollVersion++; request?.abort(); clearTimeout(pollTimer); opener?.focus(); });
    return {open(host, mode, button) {
      picker = null; alias = host; direction = mode; opener = button; phase = "source"; source = null; sourceDirectory = ""; hidden.checked = false;
      find(".ssh-transfer-mark").replaceChildren(icon(direction));
      title.textContent = `${label()} · ${alias}`; dialog.showModal(); browse(""); jobs.replaceChildren(); poll();
    }, openDirectory(options) {
      picker = options; opener = options.opener; phase = "destination"; source = null; hidden.checked = false;
      find(".ssh-transfer-mark").replaceChildren(icon("folder"));
      title.textContent = options.title; dialog.showModal(); browse(options.path || "");
    }};
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
      clearTimeout(record.copy.reset); record.card.remove(); cards.delete(alias);
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
      const copy = record.copy.copy; copy.title = `复制完整 SSH 指令：${command(host)}`; copy.setAttribute("aria-label", copy.title);
    }
    failures.hidden = unavailable.length === 0;
    failureSummary.textContent = `${state.error ? "上次检测" : "本次检测"} · ${unavailable.length} 台不可用`;
    const nextFailures = JSON.stringify(unavailable.map(host => [host.alias, host.reason, host.hostname, host.user, host.port]));
    if (nextFailures !== failureSignature) {
      failureSignature = nextFailures;
      failureList.replaceChildren(...unavailable.map(host => {
        const row = element("li", ""), diagnose = element("button", "button button-secondary ssh-health-failure", "诊断");
        diagnose.prepend(icon("activity")); diagnose.type = "button"; diagnose.setAttribute("aria-label", `诊断 · ${host.alias}`); diagnose.addEventListener("click", () => openHealth(host, diagnose));
        row.append(element("span", "ssh-failure-alias", host.alias), element("span", "ssh-failure-reason", reasons[host.reason] || reasons.connection), diagnose); return row;
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
