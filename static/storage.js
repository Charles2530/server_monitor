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
