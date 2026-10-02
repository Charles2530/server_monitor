"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  const taskId = new URL(location.href).searchParams.get("task_id"), isACP = taskId?.startsWith("ACP:"), kind = isACP ? "ACP" : "CCI";
  const consoleBase = site.id === "sensecore" ? "https://console.sensecore.cn/cn-sh-01" : "https://console.ms-sc-01.maoshanwangtech.com";
  let targets = [], loading = false, term, fit, session = null, socket = null, alive = false, sequence = 0, cursor = 0;
  let inputQueue = [], sendingInput = false, inputTimer = null, outputQueue = Promise.resolve(), pending = null, ping = null, polling = null, generation = 0;
  $("terminal-back").href = `/?site=${encodeURIComponent(site.id)}#tasks`;
  $("terminal-title").textContent = `${kind} Web Terminal`;
  document.title = `${kind} Web Terminal · iLab Server Monitor`;
  $("terminal-instance-label").textContent = isACP ? "Worker" : "实例";
  $("terminal-container-label").hidden = $("terminal-container").hidden = isACP;

  function status(text, state = "loading") { $("terminal-status").textContent = text; $("terminal-status").dataset.state = state; }
  async function api(path, data, timeout = 30000, signal = null) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
    const abort = () => controller.abort(); signal?.addEventListener("abort", abort, { once: true });
    try {
      const response = await site.fetch(path, { method: data === undefined ? "GET" : "POST", headers: { "X-Monitor-Request": "1", "Content-Type": "application/json" },
        body: data === undefined ? undefined : JSON.stringify(data), cache: "no-store", signal: controller.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "终端请求未完成，请重试。");
      return result;
    } catch (e) { if (e.name === "AbortError") throw new Error("连接超时，请重新连接终端。"); throw e; }
    finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }
  async function prepare(action, data) {
    const started = await api(`/api/terminal/${action}`, data);
    const deadline = Date.now() + 165000;
    while (Date.now() < deadline) {
      const state = await api("/api/terminal/status", { request_id: started.request_id });
      if (state.pending) continue;
      if (state.error) throw new Error(state.error);
      return state.result;
    }
    throw new Error("终端准备超时，请重新连接。");
  }
  function safeURL(target) {
    const url = new URL(target.url), base = new URL(consoleBase), query = url.searchParams;
    const matches = isACP
      ? query.get("job_id") === taskId.slice(4) && query.get("pod_name") === target.instance && [...query.keys()].sort().join(",") === "job_id,pod_name,workspace"
      : query.get("app_name") === taskId.slice(4) && query.get("container_instance_name") === target.instance && query.get("container_name") === target.container && [...query.keys()].sort().join(",") === "app_name,container_instance_name,container_name,workspace";
    if (url.origin !== base.origin || url.pathname !== `${base.pathname.replace(/\/$/, "")}/${kind.toLowerCase()}/terminal` || url.username || url.password || url.hash || !matches || !query.get("workspace")) throw new Error("终端链接与所选任务不一致，请重新读取。");
    return url.href;
  }
  function option(value) { const node = document.createElement("option"); node.value = value; node.textContent = value; return node; }
  function selected() { return targets.find(t => t.instance === $("terminal-instance").value && (isACP || t.container === $("terminal-container").value)); }
  function official() {
    const t = selected(); $("terminal-official").hidden = !t;
    if (t) $("terminal-official").href = t.url;
    $("terminal-open").disabled = !t || loading || alive;
  }
  function selectInstance() { if (!isACP) $("terminal-container").replaceChildren(...targets.filter(t => t.instance === $("terminal-instance").value).map(t => option(t.container))); official(); }
  function fitScreen() { if (term && !$("terminal-workspace").hidden) fit.fit(); }
  function initialize() {
    if (term) { term.reset(); return; }
    term = new Terminal({ cursorBlink: true, fontSize: matchMedia("(max-width:620px)").matches ? 12 : 14, lineHeight: 1.22, scrollback: 5000,
      fontFamily: '"SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace', allowProposedApi: false, disableStdin: true,
      theme: { background: "#101b19", foreground: "#dfebe4", cursor: "#97e9b6", selectionBackground: "#47695b99", black: "#182722", red: "#f28585", green: "#89d4a4", yellow: "#e4ce89", blue: "#89b7ec", magenta: "#c5a4e4", cyan: "#82d1cf", white: "#dfebe4", brightBlack: "#7c9588", brightRed: "#ffa3a3", brightGreen: "#aaf0bf", brightYellow: "#f8e5ae", brightBlue: "#b2d4ff", brightMagenta: "#e0c0ff", brightCyan: "#a8f0ed", brightWhite: "#ffffff" } });
    fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open($("terminal-screen"));
    term.onData(data => {
      // Preserve unicode codepoints while bounding each input frame, including long pastes.
      if (new TextEncoder().encode(data).length > 65536) { $("terminal-hint").textContent = "一次粘贴最多 64 KiB，请分段粘贴。"; return; }
      let chunk = "";
      for (const char of data) { chunk += char; if (chunk.length >= 2048) { enqueue(chunk); chunk = ""; } }
      if (chunk) enqueue(chunk);
    });
    term.onResize(size => { if (alive) enqueue("", size); });
    new ResizeObserver(fitScreen).observe($("terminal-screen"));
    window.visualViewport?.addEventListener("resize", fitScreen);
  }
  async function renderOutput(data, gen) {
    if (gen !== generation) return;
    for (const chunk of data.chunks || []) {
      const bytes = Uint8Array.from(atob(chunk), c => c.charCodeAt(0));
      await new Promise(resolve => term.write(bytes, resolve));
    }
    cursor = data.cursor;
    if (data.closed) await disconnect(data.error || "终端会话已结束。", gen);
  }
  function enqueue(data, size = null) {
    if (!alive) return;
    const last = inputQueue.at(-1);
    // Coalesce keystrokes while one input is in flight (especially on mobile).
    if (!size && last && !last.size && last.data.length + data.length <= 4096) last.data += data;
    else if (size && last?.size) last.size = size;
    else inputQueue.push({ data, size });
    if (!sendingInput && !inputTimer) inputTimer = setTimeout(() => { inputTimer = null; flushInput(generation); }, 8);
  }
  async function flushInput(gen) {
    sendingInput = true;
    try {
      while (alive && gen === generation && inputQueue.length) {
        const input = { sequence: ++sequence, ...inputQueue.shift() };
        if (socket?.readyState === WebSocket.OPEN) {
          await new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending = null; reject(new Error("输入结果无法确认，已停止发送；不会自动重发命令。")); }, 15000);
            pending = { sequence, resolve: () => { clearTimeout(timer); pending = null; resolve(); }, reject: () => { clearTimeout(timer); pending = null; reject(new Error("连接中断，输入不会自动重发。")); } };
            socket.send(JSON.stringify(input));
          });
        } else await api("/api/terminal/write", { session_id: session, ...input });
      }
    } catch (error) { if (gen === generation) await disconnect(error.message, gen); }
    finally { if (gen === generation) sendingInput = false; }
  }
  async function useSocket(gen) {
    return await new Promise(resolve => {
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/terminal/socket`);
      let ready = false, settled = false;
      const finish = ok => { if (settled) return; settled = true; clearTimeout(timer); resolve(ok); };
      const timer = setTimeout(() => { if (!ready) { ws.close(); finish(false); } }, 1500);
      ws.onopen = () => ws.send(JSON.stringify({ site: site.id, session_id: session }));
      ws.onmessage = event => {
        if (gen !== generation || (settled && !ready)) return;
        let data; try { data = JSON.parse(event.data); } catch { ws.close(); return; }
        if (data.type === "ready") {
          ws.send(JSON.stringify({ type: "attach" }));
          ready = true; socket = ws; finish(true);
          ping = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" })); }, 15000);
        } else if (data.type === "output") outputQueue = outputQueue.then(() => renderOutput(data, gen)).catch(() => disconnect("终端输出读取失败，请重新连接。", gen));
        else if (data.type === "ack" && pending?.sequence === data.sequence) pending.resolve();
        else if (data.type === "error") { if (ready) disconnect(data.error, gen); else { ws.close(); finish(false); } }
      };
      ws.onerror = () => { if (!ready) { ws.close(); finish(false); } };
      ws.onclose = () => { if (!ready) finish(false); else if (gen === generation) disconnect("实时连接已断开，请重新连接。", gen); };
    });
  }
  async function poll(gen) {
    polling = new AbortController(); const signal = polling.signal;
    try {
      while (alive && gen === generation) {
        const data = await api("/api/terminal/read", { session_id: session, cursor }, 30000, signal);
        await renderOutput(data, gen);
      }
    } catch (error) { if (alive && gen === generation) await disconnect(error.message, gen); }
  }
  async function disconnect(message = "已断开终端。训练任务保持原状态。", gen = generation) {
    if (gen !== generation) return;
    const old = session; session = null; alive = false; generation++;
    clearInterval(ping); clearTimeout(inputTimer); inputTimer = null; inputQueue = []; sendingInput = false; ping = null; polling?.abort(); polling = null;
    pending?.reject(); pending = null;
    const ws = socket; socket = null; ws?.close();
    if (term) term.options.disableStdin = true;
    $("terminal-led").classList.remove("connected"); $("terminal-transport").textContent = "已断开";
    $("terminal-hint").textContent = message; $("terminal-retry").hidden = false;
    $("terminal-instance").disabled = $("terminal-container").disabled = false;
    official();
    if (old) await api("/api/terminal/close", { session_id: old }, 5000).catch(() => {});
  }
  async function openTerminal() {
    const target = selected(); if (!target || loading || alive) return;
    loading = true; official(); status("正在建立终端连接；会话过期时会自动续登…");
    $("terminal-workspace").hidden = false; document.body.classList.add("terminal-active");
    initialize(); fitScreen();
    const gen = ++generation; sequence = cursor = 0; inputQueue = []; sendingInput = false; outputQueue = Promise.resolve();
    try {
      const result = await prepare("open", { task_id: taskId, instance: target.instance, container: target.container || "", cols: term.cols, rows: term.rows });
      if (gen !== generation) { api("/api/terminal/close", { session_id: result.session_id }).catch(() => {}); return; }
      session = result.session_id;
      if (typeof session !== "string" || result.site !== site.id || result.task_id !== taskId) throw new Error("终端会话与所选任务不一致。");
      alive = true;
      const realtime = await useSocket(gen);
      if (!alive || gen !== generation) return;
      $("terminal-transport").textContent = realtime ? "实时连接" : "兼容连接";
      if (!realtime) poll(gen);
      term.options.disableStdin = false; term.focus();
      $("terminal-target-name").textContent = target.instance + (target.container ? ` / ${target.container}` : "");
      $("terminal-led").classList.add("connected");
      $("terminal-hint").textContent = "已连接 · 关闭终端仅断开当前 Shell，不会停止训练任务。";
      $("terminal-instance").disabled = $("terminal-container").disabled = true;
      status("终端已连接，认证由后台管理。", "ready");
    } catch (error) { if (gen === generation) { await disconnect(error.message, gen); status(error.message, "error"); } }
    finally { loading = false; official(); $("terminal-retry").hidden = false; }
  }
  async function load() {
    if (loading) return;
    await disconnect();
    loading = true; targets = []; $("terminal-picker").hidden = true; $("terminal-retry").hidden = true;
    $("terminal-task").textContent = taskId || "未选择任务"; status(isACP ? "正在读取最新 Worker…" : "正在读取最新实例与容器…");
    try {
      if (!/^(CCI|ACP):[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/.test(taskId || "")) throw new Error("请选择有效的 CCI 或 ACP 任务。");
      const data = await prepare("targets", { task_id: taskId });
      if (data.site !== site.id || data.task_id !== taskId || !Array.isArray(data.targets)) throw new Error("终端配置与当前站点或任务不一致。");
      targets = data.targets.map(t => ({ ...t, url: safeURL(t) }));
      $("terminal-task").textContent = `${data.name} · ${taskId}`;
      if (!targets.length) throw new Error("当前没有可连接的运行容器，请稍后重新读取。");
      $("terminal-instance").replaceChildren(...[...new Set(targets.map(t => t.instance))].map(option));
      selectInstance(); $("terminal-picker").hidden = false;
      status(targets.length > 1 ? "请选择要连接的 Worker / 容器。" : "已找到运行容器。", "ready");
    } catch (error) { status(error.message || "连接失败，请重试。", "error"); }
    finally { loading = false; official(); $("terminal-retry").hidden = false; }
    if (targets.length === 1) await openTerminal();
  }
  $("terminal-instance").addEventListener("change", selectInstance);
  $("terminal-container").addEventListener("change", official);
  $("terminal-open").addEventListener("click", openTerminal);
  $("terminal-retry").addEventListener("click", load);
  $("terminal-disconnect").addEventListener("click", () => disconnect());
  for (const [id, delta] of [["terminal-font-down", -1], ["terminal-font-up", 1]]) $(id).addEventListener("click", () => { if (term) { term.options.fontSize = Math.max(10, Math.min(24, term.options.fontSize + delta)); fitScreen(); } });
  $("terminal-copy").addEventListener("click", async () => { try { await navigator.clipboard.writeText(term.getSelection()); } catch { $("terminal-hint").textContent = "无法访问剪贴板，请使用系统复制操作。"; } });
  $("terminal-paste").addEventListener("click", async () => { if (alive) { try { term.paste(await navigator.clipboard.readText()); term.focus(); } catch { $("terminal-hint").textContent = "无法访问剪贴板，请使用系统粘贴操作。"; } } });
  $("terminal-fullscreen").addEventListener("click", async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await $("terminal-workspace").requestFullscreen(); } catch { $("terminal-hint").textContent = "当前浏览器不支持全屏。"; } });
  document.addEventListener("fullscreenchange", fitScreen);
  const keys = { interrupt: "\x03", tab: "\t", escape: "\x1b", up: "\x1b[A", down: "\x1b[B", left: "\x1b[D", right: "\x1b[C", enter: "\r" };
  document.querySelectorAll("[data-terminal-key]").forEach(button => button.addEventListener("click", () => { enqueue(keys[button.dataset.terminalKey]); term?.focus(); }));
  window.addEventListener("pagehide", () => {
    const old = session; generation++; alive = false; clearInterval(ping); polling?.abort(); socket?.close();
    if (old) site.fetch("/api/terminal/close", { method: "POST", headers: { "X-Monitor-Request": "1", "Content-Type": "application/json" }, body: JSON.stringify({ session_id: old }), keepalive: true }).catch(() => {});
  });
  load();
})();
