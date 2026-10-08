"use strict";
(() => {
  const $ = id => document.getElementById(id), site = window.MonitorSite;
  const taskId = new URL(location.href).searchParams.get("task_id");
  const mobileMedia = matchMedia("(max-width:760px), (pointer:coarse)");
  let targets = [], generation = 0, controller = null, busy = false, historyRevision = 0;
  let connection = null, nextCheck = 0, recoveryFailures = 0, retryTimer = null, initializationFailed = false;
  const loadingStarted = performance.now();
  const recentKey = `ilab-ide-recent:${site.id}`;
  let setupBusy = false, setupFailures = 0, setupNextCheck = 0, focusMode = false;
  try { focusMode = localStorage.getItem('ilab-ide-focus') === '1'; } catch (_) {}
  function applyFocus() {
    document.body.classList.toggle('ide-focused', focusMode && !!connection);
    $("ide-focus").textContent = focusMode ? "退出专注" : "专注模式";
    $("ide-focus").setAttribute('aria-pressed', String(focusMode));
  }
  $("ide-focus").addEventListener('click', () => {
    focusMode = !focusMode; applyFocus();
    try { localStorage.setItem('ilab-ide-focus', focusMode ? '1' : '0'); } catch (_) {}
  });
  function setupStatus(result) {
    if (!connection) return;
    if (result) {
      connection.setup = { ...connection.setup, ...result };
      setupFailures = 0;
    }
    const setup = connection.setup || {}, parts = [];
    if (setup.optional_pending) parts.push('插件与外观正在后台准备');
    if (connection.codexPanel === 'ready') parts.push('Codex 面板已加载');
    else if (connection.codexPanel === 'loading') parts.push('Codex 面板加载中…');
    else if (setup.codex?.installed) parts.push('Codex 已安装，等待面板打开');
    else if (setup.codex?.configured === false) parts.push('此容器尚未配置 Codex');
    else if (setup.codex?.installed === false && !setup.optional_pending) parts.push('Codex 安装未完成，下次打开时重试');
    if (setup.appearance?.installed === false && !setup.optional_pending) parts.push('外观配置未完成，下次打开时重试');
    if (connection.historyState === 'ready') parts.push('当前项目聊天已过滤 · ' + (connection.historyCount || 0) + ' 条');
    else if (connection.historyState === 'empty') parts.push('当前项目暂无历史聊天');
    else if (connection.historyState === 'unavailable') parts.push('项目聊天过滤暂不可用，已保留 Codex 原列表');
    else if (connection.historyState === 'loading') parts.push('正在确认项目聊天过滤状态');
    if (setupFailures) parts.push('配置状态暂不可用，稍后重试');
    $("ide-setup-status").textContent = parts.join(' · ');
    $("ide-setup-status").hidden = !parts.length;
    $("ide-setup-status").dataset.attention = String(!!setupFailures || (!setup.optional_pending && (setup.appearance?.installed === false || (setup.codex?.configured && setup.codex.installed === false))));
  }
  async function refreshSetup() {
    const current = connection;
    if (!current?.setup?.optional_pending || setupBusy || document.hidden || navigator.onLine === false || current.entryExpired || Date.now() < setupNextCheck) return;
    setupBusy = true;
    const request = new AbortController(), timeout = setTimeout(() => request.abort(), 10000);
    try {
      const response = await site.fetch('/api/cci/ide/details', { method: 'POST', headers: { 'X-Monitor-Request': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({task_id: taskId, instance: current.instance, container: current.container}), cache: 'no-store', signal: request.signal });
      if (!response.ok) throw new Error('setup status unavailable');
      const result = await response.json(); identity(result);
      if (connection !== current) return;
      if (result.port !== current.port || result.instance !== current.instance || result.container !== current.container) throw new Error('setup identity changed');
      setupStatus(result);
    } catch (_) {
      if (connection === current) { setupFailures += 1; setupStatus(); }
    } finally {
      clearTimeout(timeout); setupBusy = false;
      setupNextCheck = Date.now() + Math.min(60000, 5000 * 2 ** Math.min(setupFailures, 4));
    }
  }
  function recentTarget() {
    try {
      const saved = JSON.parse(localStorage.getItem(recentKey));
      if (saved?.task_id === taskId && Number.isFinite(saved.saved_at) && Date.now() - saved.saved_at >= 0 && Date.now() - saved.saved_at < 1800000
          && [saved.instance, saved.container].every(value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$/.test(value))) return saved;
    } catch (_) { /* Storage may be unavailable; normal discovery remains usable. */ }
    return null;
  }
  function requestedTarget() {
    const query = new URL(location.href).searchParams;
    if (!query.has('instance') && !query.has('container')) return null;
    const instance = query.get('instance'), container = query.get('container');
    if (query.getAll('instance').length !== 1 || query.getAll('container').length !== 1
        || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$/.test(instance || '')
        || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/.test(container || '')) {
      throw new Error('终端链接的实例或容器参数无效，请返回终端重新打开。');
    }
    return { instance, container };
  }
  $("ide-back").href = window.MonitorTaskContext?.returnUrl(taskId, new URL(location.href).searchParams.get("task_view")) || `/?site=${encodeURIComponent(site.id)}#tasks`;
  $("ide-task").textContent = taskId || "未指定任务";

  function status(message, state = "loading") { $("ide-status").textContent = message; $("ide-status").dataset.state = state; window.MonitorConnectionLog?.update(navigator.onLine === false ? "offline" : state, message); }
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
  function cancel() { clearTimeout(retryTimer); retryTimer = null; progress(null); generation += 1; controller?.abort(); controller = null; busy = false; }
  function resetFrame() { progress(null); $("ide-frame").hidden = true; $("ide-frame").removeAttribute("src"); }
  function controls() { ["ide-instance", "ide-container", "ide-open"].forEach(id => { $(id).disabled = busy; }); }
  function active(gen) { if (gen !== generation) throw new DOMException("Cancelled", "AbortError"); }
  async function apiOnce(path, data, gen) {
    active(gen);
    if (navigator.onLine === false) throw new Error('网络已离线，网络恢复后会自动修复连接。');
    const started = performance.now();
    const request = new AbortController(), outer = controller.signal;
    const abort = () => request.abort();
    outer.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, 15000);
    try {
      const response = await site.fetch(`/api/cci/ide/${path}`, { method: "POST", headers: { "X-Monitor-Request": "1", "Content-Type": "application/json" }, body: JSON.stringify(data), cache: "no-store", signal: request.signal });
      const result = await response.json();
      active(gen);
      if (!response.ok || path === 'open' || path === 'recover') window.MonitorConnectionLog?.note(`API ${path} · HTTP ${response.status} · ${Math.min(999999, Math.round(performance.now() - started))}ms`, response.ok ? 'loading' : 'error');
      if (!response.ok) throw Object.assign(new Error(result.error || "编辑器请求失败，请重试。"), { code: result.code });
      return result;
    } catch (error) {
      active(gen);
      if (error.name === "AbortError") throw new Error("连接超时，请重试。");
      if (error instanceof TypeError) window.MonitorConnectionLog?.note('本机监控请求连接失败。', 'error');
      if (error instanceof TypeError) throw Object.assign(new Error("本机监控服务暂时无法连接，可能正在重启；请稍后点击“重新连接”。"), { code: "ide_network" });
      throw error;
    } finally { clearTimeout(timer); outer.removeEventListener("abort", abort); }
  }
  async function api(path, data, gen) {
    for (let attempt = 0; ; attempt += 1) {
      try { return await apiOnce(path, data, gen); }
      catch (error) {
        active(gen);
        if (error.code !== 'ide_network' || attempt >= 2 || navigator.onLine === false) throw error;
        const delay = (attempt + 1) * 1000;
        status(`本机监控连接中断，${delay / 1000} 秒后自动重试（${attempt + 1}/2）…`);
        await new Promise(resolve => setTimeout(resolve, delay));
        active(gen);
      }
    }
  }
  async function prepare(action, data, gen) {
    let start = await api(action, data, gen), restarted = false;
    const deadline = Date.now() + 15 * 60 * 1000;
    while (Date.now() < deadline) {
      if (typeof start.request_id !== "string" || !start.request_id) throw new Error("准备请求无效，请重试。");
      let result;
      try { result = await api("status", { request_id: start.request_id }, gen); }
      catch (error) {
        // Monitor restarts lose preparation IDs. Recreate this idempotent
        // editor preparation once; do not loop on validation/access errors.
        if (error.code !== 'ide_expired' || restarted) throw error;
        restarted = true;
        status("连接准备状态已失效，正在重新建立连接…");
        start = await api(action, data, gen);
        continue;
      }
      if (result.pending === false) {
        progress(null);
        if (result.error) throw new Error(result.error);
        return result.result;
      }
      if (result.pending !== true) throw new Error("准备状态无效，请重试。");
      progress(result.progress);
      if (typeof result.message === "string" && result.message && (action !== "recover" || !connection?.ready)) status(result.message);
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
    busy = false; controls();
    if (!connection) { resetFrame(); initializationFailed = true; }
    recoveryFailures += 1;
    const delay = Math.min(120000, 5000 * 2 ** Math.min(recoveryFailures - 1, 5));
    nextCheck = Date.now() + delay;
    status(`${error.message || "连接暂时不可用。"} ${delay / 1000} 秒后自动修复。`, "error");
    $("ide-retry").hidden = false;
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => recover(), delay);
  }
  function reauthenticate(destination, gen) {
    return new Promise((resolve, reject) => {
      const frame = document.createElement('iframe'), signal = controller.signal;
      frame.hidden = true; frame.dataset.ideReauth = ''; frame.title = '恢复编辑器连接';
      function finish(error) {
        clearTimeout(timer); window.removeEventListener('message', message);
        signal.removeEventListener('abort', abort); frame.remove();
        if (error) reject(error); else resolve();
      }
      function abort() { finish(new DOMException('Cancelled', 'AbortError')); }
      function message(event) {
        if (event.source !== frame.contentWindow || event.origin !== destination.origin || event.data?.entry !== destination.pathname) return;
        if (event.data.type === 'ilab-editor-authenticated') finish();
        else if (event.data.type === 'ilab-editor-entry-expired') finish(new Error('认证入口已过期，正在等待重建。'));
      }
      const timer = setTimeout(() => finish(new Error('编辑器认证恢复暂未完成。')), 15000);
      window.addEventListener('message', message); signal.addEventListener('abort', abort, { once: true });
      active(gen);
      frame.src = destination.href + '?reauth=1'; document.body.append(frame);
    });
  }
  function workbenchAlive(gen) {
    return new Promise(resolve => {
      const frame = $('ide-frame'), origin = connection.origin, signal = controller.signal;
      const id = `${gen}:${Date.now()}`;
      function finish(alive) {
        clearTimeout(timer); window.removeEventListener('message', message);
        signal.removeEventListener('abort', abort); resolve(alive);
      }
      function abort() { finish(false); }
      function message(event) {
        if (event.source === frame.contentWindow && event.origin === origin && event.data?.type === 'ilab-editor-alive' && event.data.id === id) finish(event.data.disconnected !== true);
      }
      const timer = setTimeout(() => finish(false), 2000);
      window.addEventListener('message', message); signal.addEventListener('abort', abort, { once: true });
      window.MonitorConnectionLog?.note('正在确认编辑器页面响应。', 'loading');
      frame.contentWindow?.postMessage({type: 'ilab-editor-probe', id}, origin);
    });
  }
  const preservedFrames = [];
  function retainPreviousEditor() {
    const previous = $('ide-frame'), current = previous.cloneNode(false);
    preservedFrames.push(previous);
    previous.id = preservedFrames.length === 1 ? 'ide-preserved-frame' : `ide-preserved-frame-${preservedFrames.length}`;
    current.removeAttribute('src'); current.id = 'ide-frame'; previous.after(current);
    previous.style.cssText = 'width:100%;flex:1;min-height:calc(100vh - 180px);border:0';
    let select = $('ide-preserved-select');
    if (!select) {
      select = document.createElement('select'); select.id = 'ide-preserved-select'; select.className = 'button';
      select.setAttribute('aria-label', '切换保留的编辑内容');
      select.append(option('当前编辑器'));
      select.addEventListener('change', () => {
        $('ide-frame').hidden = select.selectedIndex !== 0;
        preservedFrames.forEach((frame, index) => { frame.hidden = select.selectedIndex !== index + 1; });
      });
      document.querySelector('.ide-header').append(select);
    }
    select.append(option(`恢复前 ${preservedFrames.length} · ${new Date().toLocaleTimeString('zh-CN', {hour12:false})}`));
    select.selectedIndex = 0; preservedFrames.forEach(frame => { frame.hidden = true; });
  }
  function sendHistoryToFrame(current = connection) {
    if (!current?.origin || !current.historyPayload) return;
    $("ide-frame").contentWindow?.postMessage({ type: 'ilab-codex-history', ...current.historyPayload }, current.origin);
  }
  async function refreshHistory(gen, current = connection) {
    if (!current?.workspace || !current.ready || current.entryExpired || current.codexPanel !== 'ready'
        || gen !== generation || !controller || controller.signal.aborted || busy || recoveryFailures || document.hidden || navigator.onLine === false
        || $('ide-preserved-select')?.selectedIndex > 0 || current.historyRequest || current.historySupported === false
        || (current.historyWorkspace === current.workspace && Date.now() < current.historyNextCheck)) return;
    const workspace = current.workspace, request = {};
    current.historyRequest = request;
    current.historyWorkspace = workspace;
    current.historyNextCheck = Date.now() + 30000;
    try {
      const result = await apiOnce('history', { task_id: taskId, instance: current.instance, container: current.container, workspace }, gen);
      identity(result);
      // The server resolves symlinks. Compare the captured request identity,
      // rather than rejecting its canonical workspace path.
      if (gen !== generation || connection !== current || current.workspace !== workspace) return;
      current.historyPayload = { workspace: result.workspace, records: Array.isArray(result.records) ? result.records : [], truncated: result.truncated === true, error: null, revision: ++historyRevision };
      if (current.historyState !== 'unavailable') current.historyState = 'loading';
      current.historyCount = 0;
      sendHistoryToFrame(current);
      setupStatus();
      window.MonitorConnectionLog?.note('Codex 项目历史已读取，等待列表过滤确认。', 'loading');
    } catch (error) {
      if (gen !== generation || connection !== current || current.workspace !== workspace) return;
      current.historyPayload = { workspace, records: [], error: 'history_unavailable', revision: ++historyRevision };
      current.historyState = 'unavailable'; current.historyCount = 0;
      sendHistoryToFrame(current); setupStatus();
      window.MonitorConnectionLog?.note('Codex 项目历史读取失败，已保留原生列表。', 'error');
    } finally {
      if (current.historyRequest === request) current.historyRequest = null;
    }
  }
  async function open(target, gen, preserve = false) {
    busy = true; controls(); if (!connection) resetFrame();
    status(preserve ? "正在重建编辑器连接，请稍候…" : "正在检查已有编辑器并建立连接，请稍候…");
    const result = await prepare("open", { task_id: taskId, instance: target.instance, container: target.container }, gen);
    identity(result);
    if (result.instance !== target.instance || result.container !== target.container || !Number.isInteger(result.port) || result.port < 1024 || result.port > 65535 || !/^[A-Za-z0-9_-]{40,100}$/.test(result.ticket || "") || typeof result.ticket !== "string") throw new Error("编辑器连接信息无效，请重试。");
    const destination = new URL(`${location.protocol}//${location.hostname}:${result.port}/_ilab_open/${result.ticket}`);
    if (destination.origin === location.origin) throw new Error("编辑器必须使用独立端口，请重试。");
    active(gen);
    if (preserve && connection?.ready && connection.origin === destination.origin) {
      status('正在恢复编辑器认证和连接；当前编辑内容已保留…');
      await reauthenticate(destination, gen); active(gen);
      const alive = await workbenchAlive(gen); active(gen);
      if (alive) {
        connection.entryExpired = false; recoveryFailures = 0; initializationFailed = false;
        connection.workspace = typeof result.workspace === 'string' ? result.workspace : connection.workspace;
        nextCheck = Date.now() + 30000;
        setupStatus(result); busy = false; controls();
        void refreshHistory(gen, connection);
        status('连接已恢复；编辑器页面正常，当前编辑内容已保留。', 'ready');
        return;
      }
    }
    if (preserve && connection?.ready) {
      window.MonitorConnectionLog?.note('编辑器页面未响应，正在恢复连接。', 'reconnecting');
      retainPreviousEditor();
    }
    initializationFailed = false;
    $("ide-status").dataset.connectionSeconds = ((performance.now() - loadingStarted) / 1000).toFixed(1);
    if (targets.length === 1 && !target.saved_at) {
      try { localStorage.setItem(recentKey, JSON.stringify({ task_id: taskId, instance: target.instance, container: target.container, saved_at: Date.now() })); } catch (_) {}
    }
    $("ide-task").textContent = `${result.name || taskId} · ${taskId}`;
    connection = { instance: target.instance, container: target.container, port: result.port, origin: destination.origin, entry: destination.pathname,
      workspace: typeof result.workspace === 'string' ? result.workspace : null, historyState: 'loading', historyCount: 0,
      historyPayload: null, ready: false, loadDeadline: Date.now() + 60000 };
    nextCheck = Date.now() + 30000;
    $("ide-frame").src = destination.href;
    $("ide-frame").hidden = false;
    busy = false; controls();
    setupNextCheck = Date.now() + 5000;
    setupStatus(result);
    $("ide-focus").hidden = false; applyFocus();
    status("连接已建立，正在加载编辑器…");
  }
  async function reconnect() {
    if (document.hidden) return;
    if (navigator.onLine === false) { status('网络已离线，网络恢复后会自动修复连接。', 'error'); return; }
    if (!connection) { await load(); return; }
    const target = { instance: connection.instance, container: connection.container };
    cancel(); controller = new AbortController(); const gen = generation;
    try { await open(target, gen, true); } catch (error) { failure(error, gen); }
  }
  async function recover(force = false) {
    if (busy || document.hidden || $('ide-preserved-select')?.selectedIndex > 0 || (!force && Date.now() < nextCheck)) return;
    if (navigator.onLine === false) {
      status("网络已离线；已保留编辑器，网络恢复后会自动修复连接。", "error"); return;
    }
    if (!connection) { if (initializationFailed) await load(); return; }
    if (!connection.ready && (connection.entryExpired || Date.now() >= connection.loadDeadline)) { await reconnect(); return; }
    cancel(); controller = new AbortController(); const gen = generation;
    busy = true; controls(); nextCheck = Date.now() + 30000;
    if (force) status("正在检查并恢复编辑器连接；已保留当前编辑内容…");
    let rebuilding = false;
    window.MonitorConnectionLog?.update('checking', '正在探测后台连接状态。');
    try {
      const result = await prepare("recover", { task_id: taskId, instance: connection.instance, container: connection.container }, gen);
      identity(result);
      if (result.instance !== connection.instance || result.container !== connection.container || result.port !== connection.port) throw new Error("编辑器连接已变化；请保留未保存内容后重新打开页面。");
      active(gen);
      if (connection.ready) {
        const alive = await workbenchAlive(gen); active(gen);
        if (!alive) { rebuilding = true; await open({instance: connection.instance, container: connection.container}, gen, true); return; }
      }
      setupStatus(result);
      recoveryFailures = 0;
      busy = false; controls();
      status(connection.ready ? `${connection.instance} / ${connection.container} · 连接可用` : "连接可用，正在加载编辑器…", connection.ready ? "ready" : "loading");
    } catch (error) {
      if (gen !== generation) return;
      if (rebuilding) { failure(error, gen); return; }
      // A health probe cannot recreate a session lost with the monitor process.
      // Escalate to the same verified full preparation used by explicit reconnect.
      status('检测到连接异常，正在自动重建编辑器连接…');
      try { await open({ instance: connection.instance, container: connection.container }, gen, true); }
      catch (repairError) { failure(repairError, gen); }
    }
  }
  async function load() {
    cancel(); resetFrame(); $("ide-picker").hidden = true;
    const unavailable = unsupported();
    if (unavailable) { status(unavailable, "error"); $("ide-retry").hidden = true; return; }
    const gen = generation; controller = new AbortController(); busy = true; controls();
    $("ide-retry").hidden = false; status("正在读取最新实例与容器…");
    try {
      const requested = requestedTarget();
      const remembered = requested ? null : recentTarget();
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
      if (requested) {
        const target = targets.find(t => t.instance === requested.instance && t.container === requested.container);
        if (!target) throw new Error('终端指定的实例或容器已不可用，请返回终端重新连接。');
        $("ide-instance").value = target.instance; containers(); $("ide-container").value = target.container;
        await open(target, gen); return;
      }
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
  $("ide-retry").addEventListener("click", () => { window.MonitorConnectionLog?.note('用户请求重新连接。', 'reconnecting'); reconnect(); });
  window.addEventListener("message", event => {
    if (!connection || event.source !== $("ide-frame").contentWindow || event.origin !== connection.origin) return;
    if (event.data?.type === 'ilab-editor-codex' && ['loading', 'ready', 'hidden'].includes(event.data.state)) {
      connection.codexPanel = event.data.state; setupStatus();
      if (event.data.state === 'ready') void refreshHistory(generation, connection);
      return;
    }
    if (event.data?.type === 'ilab-editor-codex-history-state' && ['ready', 'empty', 'unavailable'].includes(event.data.state)) {
      if (!connection.historyPayload || event.data.revision !== connection.historyPayload.revision
          || event.data.workspace !== connection.historyPayload.workspace || !Number.isSafeInteger(event.data.count) || event.data.count < 0) return;
      connection.historyState = event.data.state;
      connection.historyCount = event.data.count;
      connection.historySupported = event.data.supported;
      setupStatus();
      if (connection.historySupported) void refreshHistory(generation, connection);
      return;
    }
    if (event.data?.type === "ilab-editor-entry-expired") {
      // Only the failed entry document may be replaced. A ready workbench may
      // contain unsaved edits and must keep its existing iframe during recovery.
      if (connection.ready || event.data.entry !== connection.entry) return;
      cancel(); connection.entryExpired = true; controls();
      failure(new Error('编辑器入口已过期，正在自动重新打开。'), generation);
      return;
    }
    if (event.data?.type !== "ilab-editor-ready") return;
    if (connection.ready) return;
    connection.entryExpired = false;
    if (typeof event.data.workspace === 'string' && event.data.workspace.startsWith('/') && event.data.workspace.length <= 4096 && !/[\u0000-\u001f\u007f]/.test(event.data.workspace)) connection.workspace = event.data.workspace;
    connection.ready = true; recoveryFailures = 0; clearTimeout(retryTimer); retryTimer = null;
    sendHistoryToFrame(connection);
    const seconds = ((performance.now() - loadingStarted) / 1000).toFixed(1);
    $("ide-status").dataset.readySeconds = seconds;
    status(`${connection.instance} / ${connection.container} · 编辑器已就绪 · ${seconds} 秒`, "ready");
    void refreshHistory(generation, connection);
  });
  // A desktop window crossing the layout breakpoint must not unload the editor.
  mobileMedia.addEventListener("change", () => { if (!connection) load(); });
  window.addEventListener("focus", () => recover());
  window.addEventListener("online", () => recover(true));
  window.addEventListener("offline", () => {
    if (!connection && busy) initializationFailed = true;
    cancel(); controls();
    if (connection && !connection.ready) connection.loadDeadline = 0;
    status("网络已离线；已保留编辑器，网络恢复后会自动修复连接。", "error");
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { recover(); refreshSetup(); void refreshHistory(generation); } });
  window.addEventListener("pageshow", () => recover());
  setInterval(() => recover(), 30000);
  setInterval(refreshSetup, 5000);
  // Check between 30s recovery probes; the request's 30s cooldown limits reads.
  setInterval(() => { void refreshHistory(generation); }, 15000);
  window.addEventListener("pagehide", cancel);
  load();
})();
