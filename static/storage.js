"use strict";

(() => {
  const site = window.MonitorSite;
  const $ = (id) => document.getElementById(id);
  let snapshot = null, active = false, controller = null, serial = 0, timer = null;
  let renderSignature = null, zoneSignature = null;
  const expandedVolumes = new Set(), volumeRows = new Map();
  const mountBatchSize = 50;
  const refreshLabel = $("storage-refresh").textContent;
  function setBusy(busy) {
    $("storage-refresh").disabled = busy;
    $("storage-refresh").textContent = busy ? "刷新中…" : refreshLabel;
    $("storage-refresh").setAttribute("aria-busy", String(busy));
    $("storage-section").setAttribute("aria-busy", String(busy));
  }
  const node = (tag, value, className) => {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = value;
    if (className) el.className = className;
    return el;
  };
  const known = (n) => typeof n === "number" && Number.isFinite(n) && n >= 0;
  const amountFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric", hour12: false });
  const amount = (n) => {
    if (!known(n)) return "—";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let i = 0;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i += 1; }
    return `${amountFormat.format(n)} ${units[i]}`;
  };
  const date = (value) => value && !Number.isNaN(Date.parse(value)) ? dateFormat.format(new Date(value)) : "—";
  const zoneName = (zone) => site.id === "sensecore" && /^cn-sh-01[a-z]$/.test(zone) ? `${zone.slice(-1).toUpperCase()} 区 · ${zone}` : zone;
  const message = (text) => { $("storage-warning").textContent = text; $("storage-warning").hidden = !text; };
  $("storage-console").href = site.id === "sensecore" ? "https://console.sensecore.cn/cn-sh-01/afs/list" : "https://console.ms-sc-01.maoshanwangtech.com/afs/list";

  // Only move nodes whose order changed: moving a focused node can blur it.
  function reconcile(parent, children) {
    const wanted = new Set(children);
    for (const child of [...parent.children]) if (!wanted.has(child)) child.remove();
    children.forEach((child, index) => { if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null); });
  }
  const mountText = (m) => [m.task_name, m.subdir, m.mount_path].join(" ").toLowerCase();
  function mountNode(record, entry) {
    let mount = record.mounts.get(entry.key);
    if (!mount) {
      const reference = node("div", undefined, "storage-mount"), path = node("code"), task = node("span"), state = node("small");
      reference.append(path, task, state);
      mount = { reference, path, task, state, copy: null, copyRequest: 0, data: entry.data };
      record.mounts.set(entry.key, mount);
    }
    const m = entry.data;
    mount.data = m;
    const signature = JSON.stringify(m);
    if (mount.signature === signature) return mount.reference;
    mount.signature = signature;
    mount.path.textContent = `${m.subdir ?? "（子目录未知）"} → ${m.mount_path ?? "—"}`;
    mount.task.textContent = `${m.kind} · ${m.task_name}`;
    mount.state.textContent = `${m.task_state || "状态未知"} · 创建 ${date(m.created_at)}`;
    if (m.mount_path && !mount.copy) {
      const copy = node("button", "复制挂载路径", "text-link"); copy.type = "button"; mount.copy = copy;
      copy.addEventListener("click", async () => {
        clearTimeout(mount.feedbackTimer);
        copy.textContent = "复制挂载路径";
        const target = mount.data.mount_path, request = ++mount.copyRequest;
        const current = () => request === mount.copyRequest && mount.data.mount_path === target
          && record.mounts.get(entry.key) === mount && mount.copy === copy;
        let feedback;
        try { await navigator.clipboard.writeText(target); feedback = "已复制"; }
        catch { feedback = "请手动复制上方路径"; }
        if (!current() || !copy.isConnected) return;
        copy.textContent = feedback;
        mount.feedbackTimer = setTimeout(() => { if (current()) copy.textContent = "复制挂载路径"; }, 2000);
      });
      mount.reference.append(copy);
    } else if (!m.mount_path && mount.copy) { mount.copy.remove(); mount.copy = null; }
    return mount.reference;
  }
  function renderMounts(record) {
    clearTimeout(record.batchTimer); record.batchTimer = null;
    const ordered = record.ordered, query = record.query;
    // Search still selects volumes using all mounts. For a large volume, expose
    // matching records directly instead of constructing unrelated hidden records.
    const extras = query && ordered.length > mountBatchSize
      ? ordered.slice(3).filter(entry => mountText(entry.data).includes(query)) : ordered.slice(3);
    const visibleKeys = new Set([...ordered.slice(0, 3), ...extras].map(entry => entry.key));
    for (const [key, mount] of record.mounts) mount.reference.hidden = !visibleKeys.has(key);
    const initial = ordered.slice(0, 3).map(entry => mountNode(record, entry));
    if (ordered.length > 3) initial.push(record.more);
    if (!ordered.length) initial.push(record.empty);
    reconcile(record.cell, initial);
    const materialize = (limit) => {
      const children = [record.summary];
      for (const entry of extras.slice(0, limit)) {
        const reference = mountNode(record, entry); reference.hidden = false; children.push(reference);
      }
      reconcile(record.more, children);
      if (limit < extras.length && record.more.open && record.row.isConnected) {
        record.batchTimer = setTimeout(() => { record.batchTimer = null; materialize(Math.min(extras.length, limit + mountBatchSize)); }, 0);
      }
    };
    // Collapsed records already materialized can be reused without creating more.
    const retained = extras.filter(entry => record.mounts.has(entry.key));
    if (record.more.open) materialize(Math.min(extras.length, Math.max(mountBatchSize, retained.length)));
    else reconcile(record.more, [record.summary, ...retained.map(entry => mountNode(record, entry))]);
  }
  function updateVolume(record, v, query) {
    const metadata = JSON.stringify([v.name, v.id, v.zone, v.total_bytes, v.used_bytes, v.free_bytes, v.used_percent, v.state, v.expires_at]);
    if (metadata !== record.metadata) {
      record.name.replaceChildren(node("strong", v.name), node("small", zoneName(v.zone)), node("code", v.id));
      record.capacity.replaceChildren(node("strong", `${amount(v.used_bytes)} / ${amount(v.total_bytes)}`), node("small", `剩余 ${amount(v.free_bytes)}`));
      if (known(v.used_percent)) {
        const bar = node("progress"); bar.max = 100; bar.value = Math.min(100, v.used_percent); bar.setAttribute("aria-label", "存储使用率");
        record.capacity.append(bar, node("small", `已用 ${v.used_percent.toFixed(1)}%`));
      } else record.capacity.append(node("small", "使用率未知"));
      record.state.replaceChildren(node("strong", v.state === "ACTIVE" ? "可用 · ACTIVE" : v.state), node("small", `到期 ${date(v.expires_at)}`));
      record.metadata = metadata;
    }
    const mountSignature = JSON.stringify([v.mounts, query, snapshot.mount_refreshing, snapshot.mount_status]);
    if (mountSignature === record.mountSignature) return;
    // Native toggle events are queued. Capture a user's latest open state
    // before a search/refresh can supersede that event.
    if (!record.query && record.row.isConnected && record.more.open !== record.lastOpen) {
      if (record.more.open) expandedVolumes.add(record.key); else expandedVolumes.delete(record.key);
    }
    record.mountSignature = mountSignature; record.query = query;
    const occurrences = new Map();
    record.ordered = v.mounts.slice().sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)).map(data => {
      const identity = JSON.stringify([data.kind, data.task_id || data.task_name, data.subdir, data.mount_path]);
      const occurrence = occurrences.get(identity) || 0; occurrences.set(identity, occurrence + 1);
      return { key: `${identity}:${occurrence}`, data };
    });
    const liveKeys = new Set(record.ordered.map(entry => entry.key));
    for (const [key, mount] of record.mounts) if (!liveKeys.has(key)) { clearTimeout(mount.feedbackTimer); mount.reference.remove(); record.mounts.delete(key); }
    // Update existing cached controls too, including copies inside a closed detail.
    for (const entry of record.ordered) if (record.mounts.has(entry.key)) mountNode(record, entry);
    record.empty.textContent = snapshot.mount_refreshing ? "挂载记录正在后台读取…" : Object.values(snapshot.mount_status || {}).includes("error") ? "挂载记录未完整读取" : "暂无 charles 挂载记录";
    record.summary.textContent = `其余 ${Math.max(0, v.mounts.length - 3)} 条挂载 · 共 ${v.mounts.length} 条`;
    record.lastOpen = Boolean(query) || expandedVolumes.has(record.key);
    record.more.open = record.lastOpen;
    renderMounts(record);
  }
  function render() {
    if (!snapshot) return;
    const query = $("storage-search").value.trim().toLowerCase();
    const zone = $("storage-zone").value;
    const rows = snapshot.volumes.filter((v) => (!zone || v.zone === zone)
      && (!$("storage-related").checked || v.mounts.length)
      && (!query || [v.name, v.id, v.zone, ...v.mounts.flatMap((m) => [m.task_name, m.subdir, m.mount_path])].join(" ").toLowerCase().includes(query)));
    const signature = JSON.stringify([rows, query, snapshot.volumes.length, snapshot.mount_refreshing, snapshot.mount_status, $("storage-related").checked]);
    if (signature === renderSignature) return;
    const scroll = $("storage-section").querySelector(".table-scroll"), scrollTop = scroll.scrollTop, scrollLeft = scroll.scrollLeft;
    const focused = document.activeElement;
    $("storage-count").textContent = `${rows.length} / ${snapshot.volumes.length}`;
    const summary = $("storage-summary"); summary.replaceChildren();
    for (const [field, label] of [["total_bytes", "总容量"], ["used_bytes", "已用容量"], ["free_bytes", "剩余容量"]]) {
      const values = rows.filter((v) => known(v[field]));
      const card = node("div"); card.append(node("span", label), node("strong", values.length ? amount(values.reduce((sum, v) => sum + v[field], 0)) : "—"));
      card.append(node("small", `${values.length} 个已知 · ${rows.length - values.length} 个未知${values.length < rows.length ? "（部分汇总）" : ""}`)); summary.append(card);
    }
    const totalValues = rows.filter((v) => known(v.total_bytes) && v.total_bytes > 0);
    const usedValues = rows.filter((v) => known(v.used_bytes) && known(v.total_bytes) && v.total_bytes > 0);
    const totalBytes = totalValues.reduce((sum, v) => sum + v.total_bytes, 0);
    const usedBytes = usedValues.reduce((sum, v) => sum + v.used_bytes, 0);
    const usedRate = totalBytes > 0 ? `${(usedBytes / totalBytes * 100).toFixed(1)}%` : "—";
    const zones = new Set(rows.map((v) => v.zone).filter(Boolean)).size;
    const overview = node("div", undefined, "storage-overview-metric");
    overview.append(node("span", "存储概况"), node("strong", `${rows.length} 卷`), node("small", `${zones} 个分区 · 综合使用率 ${usedRate}`));
    summary.append(overview);
    const body = $("storage-body"), children = [], newlyConnected = [];
    const liveKeys = new Set(snapshot.volumes.map(v => `${v.zone}:${v.id}`));
    for (const [key, record] of volumeRows) if (!liveKeys.has(key)) {
      clearTimeout(record.batchTimer); record.row.remove(); volumeRows.delete(key); expandedVolumes.delete(key);
      for (const mount of record.mounts.values()) clearTimeout(mount.feedbackTimer);
    }
    for (const v of rows) {
      const key = `${v.zone}:${v.id}`;
      let record = volumeRows.get(key);
      if (!record) {
        record = { key, row: node("tr"), name: node("td"), capacity: node("td"), state: node("td"), cell: node("td"), more: node("details"), summary: node("summary"), empty: node("small"), mounts: new Map() };
        record.more.append(record.summary); record.row.append(record.name, record.capacity, record.state, record.cell);
        record.more.addEventListener("toggle", () => {
          if (!record.row.isConnected || record.more.open === record.lastOpen) return;
          record.lastOpen = record.more.open;
          if (!record.query) { if (record.more.open) expandedVolumes.add(key); else expandedVolumes.delete(key); }
          renderMounts(record);
        });
        volumeRows.set(key, record);
      }
      if (!record.row.isConnected) newlyConnected.push(record);
      updateVolume(record, v, query); children.push(record.row);
    }
    if (!rows.length) {
      const row = node("tr"), cell = node("td", snapshot.mount_refreshing && $("storage-related").checked ? "挂载记录正在后台读取，稍后补齐" : snapshot.volumes.length ? "没有匹配的存储卷" : "当前站点未返回可访问的 AFS 存储卷", "table-empty"); cell.colSpan = 4; row.append(cell); children.push(row);
    }
    reconcile(body, children);
    // A newly connected expanded row can now schedule its remaining batches.
    for (const record of newlyConnected) if (record.more.open && !record.batchTimer) renderMounts(record);
    if (focused !== document.activeElement && focused?.isConnected && body.contains(focused)) focused.focus({ preventScroll: true });
    scroll.scrollTop = scrollTop; scroll.scrollLeft = scrollLeft;
    renderSignature = signature;
  }

  async function refresh(force = false) {
    if (!active || document.hidden || controller) return;
    clearTimeout(timer);
    let nextDelay = 60000;
    const request = ++serial;
    const currentController = new AbortController();
    controller = currentController;
    const timeout = setTimeout(() => currentController.abort(), 90000);
    setBusy(true);
    $("storage-status").textContent = snapshot ? `正在刷新 · 上次成功 ${date(snapshot.updated_at)}` : "正在读取存储卷、容量与挂载信息…";
    try {
      const response = await site.fetch(`/api/storage${force ? "?refresh=true" : ""}`, { signal: currentController.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "读取存储失败");
      if (!Array.isArray(data.volumes) || data.site !== site.id) throw new Error("存储响应与当前站点不匹配");
      if (!active || request !== serial) return;
      snapshot = data;
      if (data.mount_refreshing || data.refreshing) nextDelay = 2000;
      const zones = [...new Set(data.volumes.map((v) => v.zone))].sort(), signature = JSON.stringify(zones);
      if (signature !== zoneSignature) {
        const selected = $("storage-zone").value;
        $("storage-zone").replaceChildren(new Option("全部分区", ""));
        for (const zone of zones) $("storage-zone").append(new Option(zoneName(zone), zone));
        $("storage-zone").value = zones.includes(selected) ? selected : "";
        zoneSignature = signature;
      }
      render(); message((data.warnings || []).join(" "));
      $("storage-status").textContent = `${data.status === "partial" ? "部分信息未获取" : "已更新"} · ${date(data.updated_at)} · 每 60 秒刷新${data.mount_refreshing ? " · 挂载记录后台读取中" : data.mount_updated_at ? ` · 挂载更新 ${date(data.mount_updated_at)}` : ""}`;
    } catch (error) {
      if (!active || request !== serial) return;
      message(`${error.name === "AbortError" ? "存储读取超时" : error.message}。${snapshot ? "保留上次成功数据，当前可能已过期。" : "尚未取得存储数据，可刷新重试。"}`);
      $("storage-status").textContent = snapshot ? `旧数据 · ${date(snapshot.updated_at)}` : "读取失败";
      if (!snapshot) { const cell = node("td", "存储数据未获取", "table-empty"); cell.colSpan = 4; const row = node("tr"); row.append(cell); $("storage-body").replaceChildren(row); }
    } finally {
      clearTimeout(timeout);
      if (request === serial) { controller = null; setBusy(false); if (active && !document.hidden) timer = setTimeout(refresh, nextDelay); }
    }
  }
  function activate(route) {
    active = route === "storage"; clearTimeout(timer);
    if (active && !document.hidden) refresh();
    else { serial += 1; controller?.abort(); controller = null; setBusy(false); }
  }
  for (const id of ["storage-zone", "storage-search", "storage-related"]) $(id).addEventListener("input", render);
  $("storage-refresh").addEventListener("click", () => refresh(true));
  $("refresh-button").addEventListener("click", () => { if (active) refresh(true); });
  document.addEventListener("workspace-page", (event) => activate(event.detail));
  document.addEventListener("visibilitychange", () => activate(location.hash.slice(1)));
  activate(location.hash.slice(1));
})();

(() => {
  const $ = (id) => document.getElementById(id);
  const tabs = [...document.querySelectorAll('[data-storage-tab]')];
  const panels = {afs: $('storage-afs-panel'), transfer: $('storage-transfer-panel')};
  if (!tabs.length || !panels.transfer) return;
  const submit = $('transfer-submit'), message = $('transfer-message');
  const sides = ['source', 'destination'];
  const state = {executors: [], active: false, loading: false, polling: false, pollAgain: false, timer: null, executorError: ''};
  const json = async (url, data, signal) => {
    const response = await fetch(url, {method: data ? 'POST' : 'GET', headers: {'Content-Type': 'application/json', 'X-Monitor-Request': '1'}, body: data ? JSON.stringify(data) : undefined, cache: 'no-store', signal});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '请求失败，请重试。');
    return result;
  };
  const nodeType = (side) => $(`transfer-${side}-node-type`).value;
  const nodeSelect = (side) => $(`transfer-${side}-node`);
  const pathInput = (side) => $(`transfer-${side}-path`);
  const pathText = (endpoint, job) => `${endpointName(endpoint, job)} · ${endpoint.path}`;
  const stageLabel = (job) => ({starting: '准备中', prepare: '准备传输工具（首次使用会自动安装）', upload: '上传到中转存储', copy: '复制存储数据', sync: '同步存储数据', download: '下载到目标节点', completed: '已完成', cancelled: '已取消'}[job.stage?.split(':')[0]] || '准备中');
  function setMessage(text, error = false) { message.textContent = text || ''; message.dataset.state = error ? 'error' : text ? 'success' : ''; }
  function filtered(kind) { return state.executors.filter(item => item.type === kind); }
  function renderNodes(side, preserve = '') {
    const select = nodeSelect(side), list = filtered(nodeType(side));
    select.replaceChildren(...(list.length
      ? list.map(item => new Option(`${item.label} · 可用`, item.id))
      : [new Option(nodeType(side) === 'ssh-node' ? '暂无可用 SSH 节点' : '暂无可用 CCI 运行任务', '')]));
    if (preserve && list.some(item => item.id === preserve)) select.value = preserve;
    if (!select.value && list[0]) select.value = list[0].id;
    $(`transfer-${side}-browse`).disabled = !select.value;
    renderRoute();
  }
  function endpointPayload(side) {
    return {type: 'folder', path: pathInput(side).value,
      executor: {type: nodeType(side), id: nodeSelect(side).value}};
  }
  function renderRoute() {
    const name = side => ({sensecore: 'SenseCore CCI', maoshanwang: 'maoshanwang CCI', 'ssh-node': 'SSH 节点'}[nodeType(side)]);
    $('transfer-route').textContent = `${name('source')} → ${name('destination')}`;
    submit.disabled = state.loading || Boolean(validate());
    $('transfer-executor-status').textContent = state.executorError || '首次使用自动准备工具，再上传、中转和下载。同名文件可能更新，中转数据会保留。';
  }
  async function loadExecutors() {
    try {
      const result = await json('/api/transfers/executors');
      state.executors = result.executors || [];
      state.executorError = '';
      sides.forEach(side => renderNodes(side, nodeSelect(side).value));
      renderJobPage(); renderLog();
      renderRoute();
    } catch (error) {
      state.executorError = error.message;
      renderRoute();
    }
  }
  function browse(side) {
    const executor = state.executors.find(item => item.id === nodeSelect(side).value);
    if (!executor) return;
    window.openMonitorDirectoryPicker({
      title: `选择${side === 'source' ? '源端' : '目标端'}目录`, label: executor.label,
      path: pathInput(side).value, opener: $(`transfer-${side}-browse`),
      browse: (path, signal) => json('/api/transfers/browse', {executor_id: executor.id, path}, signal),
      onSelect: path => { pathInput(side).value = path.replace(/\/+$/, '') + '/'; renderRoute(); },
    });
  }
  function validate() {
    if (state.executorError) return state.executorError;
    const source = endpointPayload('source'), destination = endpointPayload('destination');
    for (const endpoint of [source, destination]) if (!endpoint.path) return '请填写源端和目标端路径。';
    for (const endpoint of [source, destination]) {
      if (!state.executors.some(item => item.id === endpoint.executor.id)) return '请选择可用节点。';
    }
    if (!source.path.startsWith('/')) return '源端文件夹必须是绝对路径。';
    if (!destination.path.startsWith('/')) return '目标端文件夹必须是绝对路径。';
    return '';
  }
  const jobRows = new Map(), logDialog = $('transfer-log-dialog');
  const logIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>';
  const jobStatus = job => ({running: '传输中', cancelling: '正在取消', completed: '已完成', failed: '失败', cancelled: '已取消'}[job.state] || '未知状态');
  function endpointName(endpoint, job) {
    const saved = endpoint.executor || job.executor || {};
    const node = state.executors.find(item => item.id === saved.id) || (job.executor?.id === saved.id ? job.executor : saved);
    const site = {sensecore: 'SenseCore', maoshanwang: 'maoshanwang', 'ssh-node': 'SSH'}[saved.type] || '节点';
    const name = (node.label || node.alias)?.split(' · ')[0] || (saved.id?.startsWith('cci:') ? saved.id.split(':').slice(2, 4).join(':') : saved.id?.replace(/^ssh:/, '')) || '未知节点';
    return `${site} · ${name}`;
  }
  const jobName = job => `${endpointName(job.source, job)} → ${endpointName(job.destination, job)}`;
  const jobMeta = job => job.started_at ? new Date(job.started_at * 1000).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) : '时间未知';
  let jobPage = 1;
  function renderJobPage() {
    const query = $('transfer-job-search').value.trim().toLowerCase(), status = $('transfer-job-filter').value;
    const records = [...jobRows.values()].sort((a, b) => a.index - b.index), matches = records.filter(({job}) => (!status || job.state === status) && jobName(job).toLowerCase().includes(query));
    const size = Number($('transfer-job-size').value) || 10, pages = Math.max(1, Math.ceil(matches.length / size));
    jobPage = Math.max(1, Math.min(jobPage, pages));
    const visible = new Set(matches.slice((jobPage - 1) * size, jobPage * size));
    for (const record of records) {
      record.row.hidden = !visible.has(record);
      sides.forEach((side, index) => {
        const cell = record.row.cells[index], label = endpointName(record.job[side], record.job);
        const parts = label.split(' · ');
        cell.title = label;
        cell.querySelector('strong').textContent = parts.slice(1).join(' · ');
        cell.querySelector('small').textContent = parts[0];
      });
    }
    $('transfer-jobs').hidden = !matches.length;
    const empty = $('transfer-jobs-empty'); empty.hidden = Boolean(matches.length);
    empty.textContent = records.length ? '没有符合条件的传输任务，请调整搜索或筛选条件。' : '暂无传输任务。任务创建后，点击日志图标查看执行详情。';
    $('transfer-job-page').textContent = `${matches.length === records.length ? `共 ${records.length} 项` : `${matches.length} 项匹配 / 共 ${records.length} 项`} · 第 ${jobPage} / ${pages} 页`;
    $('transfer-job-prev').disabled = jobPage === 1;
    $('transfer-job-next').disabled = jobPage === pages;
  }
  for (const id of ['transfer-job-search', 'transfer-job-filter', 'transfer-job-size']) {
    $(id).addEventListener(id === 'transfer-job-search' ? 'input' : 'change', () => { jobPage = 1; renderJobPage(); });
  }
  $('transfer-job-prev').addEventListener('click', () => { jobPage--; renderJobPage(); });
  $('transfer-job-next').addEventListener('click', () => { jobPage++; renderJobPage(); });
  let logId = null, cancellingId = null, logState = null;
  function logNotice(text = '') {
    $('transfer-log-notice').textContent = text; $('transfer-log-notice').hidden = !text;
  }
  function applyLog(result) {
    logState.lines = logState.lines.slice(Math.max(0, result.first - logState.first));
    logState.first = result.first;
    logState.lines.splice(result.start - result.first, logState.lines.length, ...result.lines);
    logState.offset = result.next;
    const output = $('transfer-log-output'), text = logState.lines.join('\n') || '等待执行输出…';
    if (output.textContent !== text) {
      const follow = output.scrollHeight - output.clientHeight - output.scrollTop < 24;
      output.textContent = text;
      if (follow) output.scrollTop = output.scrollHeight;
    }
    const full = $('transfer-log-full');
    full.hidden = !logState.full && !result.first;
    full.textContent = logState.full ? '查看最近 2000 条' : '查看完整日志';
    $('transfer-log-count').textContent = `${logState.lines.length} 条${logState.full ? ' · 完整日志' : result.first ? ` · 较早 ${result.first} 条已截断` : ' · 从任务开始记录'} · 连续进度合并`;
  }
  async function loadLog() {
    if (!logDialog.open || !logState) return;
    if (logState.loading) { logState.again = true; return; }
    const current = logState;
    current.loading = true;
    try {
      const query = new URLSearchParams({offset: String(current.offset)});
      if (current.full) query.set('full', 'true');
      const result = await json(`/api/transfers/${encodeURIComponent(current.id)}/logs?${query}`);
      if (logState !== current || !logDialog.open) return;
      applyLog(result);
    } catch (error) {
      if (logState === current && logDialog.open) logNotice(`日志更新失败：${error.message}`);
    } finally {
      current.loading = false;
      if (current.again && logState === current) { current.again = false; loadLog(); }
    }
  }
  function toggleFullLog() {
    if (!logState) return Promise.resolve();
    logState.full = !logState.full;
    logState.first = logState.offset = 0;
    logState.lines = [];
    return loadLog();
  }
  function renderLog() {
    if (!logDialog.open) return;
    const job = jobRows.get(logId)?.job;
    if (!job) { logNotice('任务记录已不在列表中，以下保留最后一次结果。'); $('transfer-log-cancel').hidden = true; return; }
    logNotice();
    $('transfer-log-name').textContent = `${jobName(job)} · ${jobMeta(job)}`;
    $('transfer-log-state').textContent = jobStatus(job); $('transfer-log-state').dataset.state = job.state;
    $('transfer-log-stage').textContent = `${stageLabel(job)}${job.stage_index ? ` · 第 ${job.stage_index} / ${job.stage_count} 步` : ''}${Number.isFinite(job.progress_percent) ? ` · ${job.progress_percent}%` : ''}`;
    const progress = $('transfer-log-progress');
    if (Number.isFinite(job.progress_percent)) progress.value = job.progress_percent;
    else progress.removeAttribute('value');
    $('transfer-log-source').textContent = pathText(job.source, job);
    $('transfer-log-destination').textContent = pathText(job.destination, job);
    $('transfer-log-error').textContent = job.error || ''; $('transfer-log-error').hidden = !job.error;
    loadLog();
    const cancel = $('transfer-log-cancel');
    cancel.hidden = !['running', 'cancelling'].includes(job.state);
    cancel.disabled = job.state === 'cancelling' || cancellingId === job.id;
  }
  $('transfer-log-close').addEventListener('click', () => logDialog.close());
  $('transfer-log-full').addEventListener('click', toggleFullLog);
  logDialog.addEventListener('close', () => { (jobRows.get(logId)?.row.hidden === false ? jobRows.get(logId).row.querySelector('button') : $('transfer-refresh-jobs')).focus(); logId = null; logState = null; });
  $('transfer-log-cancel').addEventListener('click', async () => {
    const id = logId; cancellingId = id; renderLog();
    try { await json(`/api/transfers/${encodeURIComponent(id)}/cancel`, {}); await poll(); }
    catch (error) { if (logId === id) logNotice(error.message); }
    finally { cancellingId = null; $('transfer-log-cancel').disabled = jobRows.get(logId)?.job.state === 'cancelling'; }
  });
  function renderJobs(result) {
    $('transfer-jobs-notice').hidden = true;
    const root = $('transfer-jobs'), jobs = result.jobs || [], ids = new Set(jobs.map(job => job.id));
    for (const [id, record] of jobRows) if (!ids.has(id)) { record.row.remove(); jobRows.delete(id); }
    jobs.forEach((job, index) => {
      let record = jobRows.get(job.id);
      if (!record) {
        const row = document.createElement('tr'); row.className = 'transfer-job';
        row.innerHTML = `<td><strong></strong><small></small></td><td><strong></strong><small></small></td><td class="transfer-job-time"></td><td><span class="transfer-job-state"></span></td><td><button class="ssh-copy transfer-job-log" type="button" aria-label="查看执行日志" title="查看执行日志" aria-haspopup="dialog" aria-controls="transfer-log-dialog">${logIcon}</button></td>`;
        record = {row}; jobRows.set(job.id, record);
        row.querySelector('button').addEventListener('click', () => {
          logId = job.id; logState = {id: job.id, first: 0, offset: 0, lines: [], loading: false, full: false};
          $('transfer-log-output').textContent = '正在读取日志…'; $('transfer-log-count').textContent = '';
          logDialog.showModal(); renderLog();
          $('transfer-log-output').scrollTop = $('transfer-log-output').scrollHeight;
        });
      }
      record.job = job; record.index = index;
      const row = record.row;
      row.querySelector('.transfer-job-time').textContent = jobMeta(job);
      const status = row.querySelector('.transfer-job-state'); status.textContent = jobStatus(job); status.dataset.state = job.state;
      if (root.children[index] !== row) root.insertBefore(row, root.children[index] || null);
    });
    renderJobPage(); renderLog();
  }
  async function poll() {
    clearTimeout(state.timer);
    if (!state.active || document.hidden) return;
    if (state.polling) { state.pollAgain = true; return; }
    state.polling = true;
    let delay = 0;
    try { const result = await json('/api/transfers'); renderJobs(result); if ((result.jobs || []).some(job => ['running', 'cancelling'].includes(job.state))) delay = 1000; }
    catch (error) {
      const notice = $('transfer-jobs-notice');
      notice.textContent = `更新失败：${error.message}，正在重试。`; notice.hidden = false;
      if (logDialog.open) logNotice(notice.textContent);
      delay = 3000;
    }
    finally {
      state.polling = false;
      if (state.pollAgain) { delay = 1; state.pollAgain = false; }
      if (delay && state.active && !document.hidden) state.timer = setTimeout(poll, delay);
    }
  }
  async function submitJob() {
    const error = validate(); if (error) return setMessage(error, true);
    state.loading = true; renderRoute(); setMessage('正在创建远端传输任务…');
    try { await json('/api/transfers', {source: endpointPayload('source'), destination: endpointPayload('destination')}); setMessage('任务已创建，正在执行。'); poll(); }
    catch (failure) { setMessage(failure.message, true); }
    finally { state.loading = false; renderRoute(); }
  }
  function selectTab(name) {
    tabs.forEach(tab => { const selected = tab.dataset.storageTab === name; tab.classList.toggle('active', selected); tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1; });
    Object.entries(panels).forEach(([key, panel]) => { panel.hidden = key !== name; });
    state.active = name === 'transfer';
    clearTimeout(state.timer);
    if (state.active) { loadExecutors(); poll(); }
  }
  for (const side of sides) {
    $(`transfer-${side}-node-type`).addEventListener('change', () => renderNodes(side));
    $(`transfer-${side}-node`).addEventListener('change', renderRoute);
    $(`transfer-${side}-browse`).addEventListener('click', () => browse(side));
    $(`transfer-${side}-path`).addEventListener('input', renderRoute);
  }
  tabs.forEach(tab => tab.addEventListener('click', () => selectTab(tab.dataset.storageTab)));
  submit.addEventListener('click', submitJob); $('transfer-refresh-jobs').addEventListener('click', poll);
  document.addEventListener('workspace-page', event => {
    state.active = event.detail === 'storage' && !panels.transfer.hidden;
    clearTimeout(state.timer);
    if (state.active) { loadExecutors(); poll(); }
  });
  document.addEventListener('visibilitychange', () => { if (state.active && !document.hidden) poll(); });
  sides.forEach(side => renderNodes(side));
})();
