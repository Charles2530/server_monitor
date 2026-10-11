"use strict";

(() => {
  const site = window.MonitorSite;
  const $ = (id) => document.getElementById(id);
  let snapshot = null, active = false, controller = null, serial = 0, timer = null;
  let renderSignature = null, zoneSignature = null;
  const expandedVolumes = new Set();
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

  function render() {
    if (!snapshot) return;
    const query = $("storage-search").value.trim().toLowerCase();
    const zone = $("storage-zone").value;
    const rows = snapshot.volumes.filter((v) => (!zone || v.zone === zone)
      && (!$("storage-related").checked || v.mounts.length)
      && (!query || [v.name, v.id, v.zone, ...v.mounts.flatMap((m) => [m.task_name, m.subdir, m.mount_path])].join(" ").toLowerCase().includes(query)));
    const signature = JSON.stringify([rows, query, snapshot.volumes.length, snapshot.mount_refreshing, snapshot.mount_status, $("storage-related").checked]);
    if (signature === renderSignature) return;
    renderSignature = null;
    $("storage-count").textContent = `${rows.length} / ${snapshot.volumes.length}`;
    const summary = $("storage-summary"); summary.replaceChildren();
    for (const [field, label] of [["total_bytes", "总容量"], ["used_bytes", "已用容量"], ["free_bytes", "剩余容量"]]) {
      const values = rows.filter((v) => known(v[field]));
      const card = node("div"); card.append(node("span", label), node("strong", values.length ? amount(values.reduce((sum, v) => sum + v[field], 0)) : "—"));
      card.append(node("small", `${values.length} 个已知 · ${rows.length - values.length} 个未知${values.length < rows.length ? "（部分汇总）" : ""}`)); summary.append(card);
    }
    const body = $("storage-body"); body.replaceChildren();
    if (!rows.length) {
      const row = node("tr"), cell = node("td", snapshot.mount_refreshing && $("storage-related").checked ? "挂载记录正在后台读取，稍后补齐" : snapshot.volumes.length ? "没有匹配的存储卷" : "当前站点未返回可访问的 AFS 存储卷", "table-empty"); cell.colSpan = 4; row.append(cell); body.append(row);
    }
    for (const v of rows) {
      const row = node("tr"), name = node("td"), capacity = node("td"), state = node("td"), mounts = node("td");
      name.append(node("strong", v.name), node("small", zoneName(v.zone)), node("code", v.id));
      capacity.append(node("strong", `${amount(v.used_bytes)} / ${amount(v.total_bytes)}`), node("small", `剩余 ${amount(v.free_bytes)}`));
      if (known(v.used_percent)) {
        const bar = node("progress"); bar.max = 100; bar.value = Math.min(100, v.used_percent); bar.setAttribute("aria-label", "存储使用率");
        capacity.append(bar, node("small", `已用 ${v.used_percent.toFixed(1)}%`));
      } else capacity.append(node("small", "使用率未知"));
      state.append(node("strong", v.state === "ACTIVE" ? "可用 · ACTIVE" : v.state), node("small", `到期 ${date(v.expires_at)}`));
      if (!v.mounts.length) mounts.append(node("small", snapshot.mount_refreshing ? "挂载记录正在后台读取…" : Object.values(snapshot.mount_status).includes("error") ? "挂载记录未完整读取" : "暂无 charles 挂载记录"));
      const volumeKey = `${v.zone}:${v.id}`;
      const more = node("details"), extra = v.mounts.length - 3;
      if (extra > 0) {
        more.append(node("summary", `其余 ${extra} 条挂载 · 共 ${v.mounts.length} 条`));
        more.open = Boolean(query) || expandedVolumes.has(volumeKey);
        more.addEventListener("toggle", () => { if (!query) { if (more.open) expandedVolumes.add(volumeKey); else expandedVolumes.delete(volumeKey); } });
      }
      const orderedMounts = v.mounts.slice().sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
      for (const [index, m] of orderedMounts.entries()) {
        const reference = node("div", undefined, "storage-mount");
        reference.append(node("code", `${m.subdir ?? "（子目录未知）"} → ${m.mount_path ?? "—"}`), node("span", `${m.kind} · ${m.task_name}`), node("small", `${m.task_state || "状态未知"} · 创建 ${date(m.created_at)}`));
        if (m.mount_path) {
          const copy = node("button", "复制挂载路径", "text-link"); copy.type = "button";
          copy.addEventListener("click", async () => {
            try { await navigator.clipboard.writeText(m.mount_path); copy.textContent = "已复制"; }
            catch { copy.textContent = "请手动复制上方路径"; }
            setTimeout(() => { copy.textContent = "复制挂载路径"; }, 2000);
          });
          reference.append(copy);
        }
        (index < 3 ? mounts : more).append(reference);
      }
      if (extra > 0) mounts.append(more);
      row.append(name, capacity, state, mounts); body.append(row);
    }
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
    $("storage-refresh").disabled = true;
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
      if (request === serial) { controller = null; $("storage-refresh").disabled = false; if (active && !document.hidden) timer = setTimeout(refresh, nextDelay); }
    }
  }
  function activate(route) {
    active = route === "storage"; clearTimeout(timer);
    if (active && !document.hidden) refresh();
    else { serial += 1; controller?.abort(); controller = null; $("storage-refresh").disabled = false; }
  }
  for (const id of ["storage-zone", "storage-search", "storage-related"]) $(id).addEventListener("input", render);
  $("storage-refresh").addEventListener("click", () => refresh(true));
  $("refresh-button").addEventListener("click", () => { if (active) refresh(true); });
  document.addEventListener("workspace-page", (event) => activate(event.detail));
  document.addEventListener("visibilitychange", () => activate(location.hash.slice(1)));
  activate(location.hash.slice(1));
})();
