"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const state = { options: null, snapshot: null, unavailable: false, selected: null, preview: null, stop: null, creating: false, stopping: false, createUnknown: false, stopUnknown: false, storageError: false };
  const siteKey = (name) => window.MonitorSite?.key(name) || name;
  const pendingKeys = { create: siteKey("charles-monitor.acp.create.pending"), stop: siteKey("charles-monitor.acp.stop.pending") };
  const form = byId("acp-create-form");
  const command = byId("acp-command");
  let commandTouched = false, commandRevision = 0, pastePending = false;
  const partitioned = window.MonitorSite?.id === "sensecore";
  const partitionDrafts = new Map();
  let partitionSelect = null, optionsRequest = 0, optionsLoading = false;
  const createDialog = byId("acp-create-dialog"), stopDialog = byId("acp-stop-dialog");
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function message(id, text) { byId(id).textContent = text || ""; byId(id).hidden = !text; }
  function date(value) { const parsed = new Date(value); return value && Number.isFinite(parsed.getTime()) ? dateFormat.format(parsed) : "—"; }
  function refresh() { document.dispatchEvent(new CustomEvent("monitor-refresh")); }
  function pair(container, label, value, command = false) {
    const dd = element("dd");
    dd.append(element(command ? "pre" : "span", "", String(value ?? "—")));
    container.append(element("dt", "", label), dd);
  }

  function updatePartitionLock() {
    if (partitionSelect) partitionSelect.disabled = !partitionSelect.value || state.creating || state.createUnknown || state.storageError;
  }

  function pending(kind) {
    const raw = localStorage.getItem(pendingKeys[kind]);
    if (raw === null) return null;
    const value = JSON.parse(raw), fields = kind === "create" ? ["preview_id"] : ["request_id", "task_id", "display_name"];
    if (!value || Object.keys(value).length !== fields.length || !fields.every((field) => typeof value[field] === "string" && value[field].length > 0 && value[field].length <= 300)) throw new Error("请求恢复记录无法读取。请保留记录并核对云端结果。");
    return value;
  }

  function pendingIdentity(kind, value) { return kind === "create" ? value?.preview_id : value?.request_id; }

  function syncPending(kind) {
    try {
      const saved = pending(kind), wasUnknown = state[`${kind}Unknown`];
      state[`${kind}Unknown`] = Boolean(saved);
      if (saved) {
        if (kind === "create") {
          if (state.preview?.preview_id !== saved.preview_id) {
            state.preview = saved;
            byId("acp-preview-details").replaceChildren();
            pair(byId("acp-preview-details"), "恢复的创建请求", saved.preview_id);
          }
          message("acp-create-message", "上次提交结果未知，已恢复原请求。核对会复用同一次请求；若为释放重启，会在核实新任务后继续原任务的释放步骤，不会重复创建。");
        } else {
          state.stop = saved;
          byId("acp-stop-target").replaceChildren();
          pair(byId("acp-stop-target"), "任务名称", saved.display_name);
          pair(byId("acp-stop-target"), "完整任务 ID", saved.task_id);
          message("acp-stop-message", "上次停止结果未知，已恢复原请求。请核对原任务的停止结果。");
        }
        message("acp-message", "有尚未确认结果的请求。请通过下方核对入口继续原请求；相关新操作暂时锁定。");
      } else if (wasUnknown && !state[kind === "create" ? "creating" : "stopping"]) {
        if (kind === "create") { state.preview = null; createDialog.close(); }
        else { state.stop = null; stopDialog.close(); }
      }
      byId(`acp-${kind}-resume`).hidden = !saved;
      byId(`acp-${kind}-confirm`).textContent = saved ? `核对此次${kind === "create" ? "创建" : "停止"}结果` : kind === "create" ? "确认创建" : "确认停止";
      byId(`acp-${kind}-cancel`).textContent = saved ? "暂时关闭" : kind === "create" ? "返回修改" : "取消";
      byId("acp-preview-button").disabled = !state.options || state.createUnknown;
    } catch {
      state.storageError = true;
      byId("acp-preview-button").disabled = true;
      message("acp-message", "本地请求恢复记录不可用，已暂停新操作。请核对云端已有任务与浏览器存储状态。");
    }
    updatePartitionLock();
    renderSelected(); renderWaiting();
  }

  async function remember(kind, value) {
    if (!navigator.locks || state.storageError) throw new Error("浏览器暂时无法保护请求记录，未发送操作。请使用支持本地存储与 Web Locks 的浏览器。");
    // Serialize the read-and-write claim across tabs; the network request uses the persisted identity.
    await navigator.locks.request(siteKey("charles-monitor.acp.pending"), () => {
      const existing = pending(kind);
      if (existing && pendingIdentity(kind, existing) !== pendingIdentity(kind, value)) {
        syncPending(kind);
        throw Object.assign(new Error("另一标签页有尚未确认的请求，请通过核对入口继续。"), { conflict: true });
      }
      localStorage.setItem(pendingKeys[kind], JSON.stringify(value));
    });
  }

  function forget(kind, value) {
    try {
      if (pendingIdentity(kind, pending(kind)) === pendingIdentity(kind, value)) localStorage.removeItem(pendingKeys[kind]);
    } catch { state.storageError = true; }
  }

  function fresh(sourceKey = "ACP") {
    const snapshot = state.snapshot, source = snapshot?.source_status?.[sourceKey];
    if (state.unavailable || !snapshot?.connected || ["disconnected", "error"].includes(snapshot.status) || source?.status !== "ok") return false;
    const limit = Math.max(180, (snapshot.interval_seconds || 60) * 3) * 1000;
    const timestamps = [snapshot.updated_at, source.observed_at || source.updated_at || (sourceKey.endsWith("_queue") ? null : snapshot.updated_at)];
    return timestamps.every((value) => value && Number.isFinite(new Date(value).getTime()) && Date.now() - new Date(value).getTime() <= limit);
  }

  function stopFresh(taskId) {
    const queued = state.snapshot?.waiting_tasks?.some((task) => task.id === taskId);
    return fresh() && (!queued || fresh("ACP_queue"));
  }

  async function request(path, body) {
    const mutating = path.endsWith("/create") || path.endsWith("/stop");
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), mutating ? 90000 : 60000);
    try {
      const response = await (window.MonitorSite?.fetch || fetch)(path, { method: body ? "POST" : "GET", headers: body ? { "Content-Type": "application/json", "X-Monitor-Request": "1" } : {}, body: body ? JSON.stringify(body) : undefined, cache: "no-store", signal: controller.signal });
      let data;
      try { data = await response.json(); } catch { throw Object.assign(new Error("本地服务返回的数据无法读取。"), { unknown: mutating }); }
      if (!response.ok) {
        const codes = {
          invalid_request: "请核对输入：名称须包含 charles 且不超过 128 字符，副本数须为正整数，命令须为有效文本。",
          template_unavailable: "暂时无法确认可用模板，请连接云会话后重新读取配置。", unsupported_template: "当前最新模板暂不支持复制，请在云控制台核对模板。",
          resources_unavailable: "可用集群或资源规格暂时无法确认，请稍后重新预览。", source_unavailable: "云端数据暂不可用，请检查连接后重试。",
          target_mismatch: "任务身份或名称已变化，请刷新列表后重新核对。", state_changed: "任务状态已变化，当前无法停止，请刷新列表。",
          request_conflict: "该请求标识已有其他用途，请刷新并核对操作结果。", cloud_rejected: "云平台拒绝了本次操作，请核对资源和任务配置。", auth_required: "云会话已失效或没有操作权限，请重新连接。",
          restart_state_changed: "任务状态已变化，请刷新列表。释放重启仅支持运行中的任务；历史重启仅支持已结束任务。",
          restart_source_changed: "原任务配置已变化，请关闭并重新打开重新启动窗口。",
          restart_resources_unavailable: "原集群或原资源规格已不可用，请到创建 ACP 任务页面选择新资源。",
          partition_unavailable: "AFS 分区或模板已变化，请重新读取分区配置。",
          cluster_unavailable: "指定集群已不可用或与模板挂载不兼容，请重新读取配置并选择。",
          invalid_name: "任务名称必须包含 charles。", invalid_replicas: "请填写有效的正整数副本数。", invalid_gpu: "请选择模板支持的 GPU 规格。",
          preview_expired: "预览已过期，请返回修改并重新预览。", preview_not_found: "预览已失效，请重新预览。",
          not_connected: "请先连接云会话，再重新预览。", template_not_found: "未找到可用模板，请重新读取配置。",
          task_not_found: "任务已不在可操作列表中，请刷新后核对。", task_not_stoppable: "任务当前状态不支持停止，请刷新后核对。",
        };
        const mobileBlocked = ["mobile_auth_required", "mobile_access_denied", "mobile_connection_required"].includes(data.code);
        const releaseError = ["release_incomplete", "release_failed", "release_already_requested", "release_replacement_ended"].includes(data.code);
        throw Object.assign(new Error((releaseError || mobileBlocked) && typeof data.error === "string" ? data.error : codes[data.code] || "请求未完成，请刷新状态并核对任务配置或连接情况。"), { code: data.code, mobileBlocked, unknown: mutating && (response.status >= 500 || /unknown|uncertain|in_progress|release_incomplete/i.test(data.code || "")), outcomeDetail: (data.code === "outcome_unknown" || releaseError || mobileBlocked) && typeof data.error === "string" ? data.error : "" });
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError" || error instanceof TypeError) throw Object.assign(new Error("本地服务连接中断或请求超时。"), { unknown: mutating });
      throw error;
    } finally { clearTimeout(timer); }
  }

  function total() {
    const replicas = Number(byId("acp-replicas").value), gpus = Number(byId("acp-gpus").value);
    byId("acp-total").textContent = Number.isSafeInteger(replicas) && replicas > 0 && gpus > 0 && Number.isSafeInteger(replicas * gpus) ? `${replicas * gpus} 张 GPU` : "—";
  }

  function gpuOptions() {
    const choice = byId("acp-cluster").value, data = state.options;
    if (!data) return;
    const counts = choice ? (data.clusters || []).find(cluster => cluster.id === choice)?.gpu_options || [] : data.gpu_options;
    const select = byId("acp-gpus"), previous = select.value;
    select.replaceChildren(...counts.map(count => { const option = element("option", "", `${count} 张`); option.value = String(count); return option; }));
    select.value = previous || String(data.default_gpus_per_replica);
    if (!select.value && counts.length) select.value = String(counts[0]);
    if (!counts.length) { const option = element("option", "", "所选集群不可用，请重新选择"); option.value = ""; select.append(option); }
    select.disabled = !counts.length;
    select.setCustomValidity(counts.length ? "" : "请选择可用集群。");
    total();
  }

  function renderTemplateConfig(container, config) {
    pair(container, "镜像", (config?.images || []).join("\n") || "—");
    if (!config?.afs_mounts?.length) pair(container, "AFS", "未配置 AFS 挂载");
    (config?.afs_mounts || []).forEach((mount, index) => {
      pair(container, `AFS ${index + 1}`, `${mount.zone} · ${mount.id}\n${mount.subdir || "/"} → ${mount.mount_path}`);
    });
  }

  function savePartitionDraft() {
    if (!state.options?.partition?.id) return;
    partitionDrafts.set(state.options.partition.id, { command: command.value, touched: commandTouched, cluster: byId("acp-cluster").value, gpu: byId("acp-gpus").value });
  }

  async function options(requestedPartition = partitionSelect?.value || "", switching = false) {
    const revision = ++optionsRequest;
    const previousCluster = byId("acp-cluster").value;
    const previousGPU = byId("acp-gpus").value;
    optionsLoading = true;
    byId("acp-options-retry").hidden = true;
    if (partitioned) {
      state.options = null;
      byId("acp-preview-button").disabled = true;
      byId("acp-cluster").disabled = true;
      byId("acp-gpus").disabled = true;
      byId("acp-command-restore").disabled = true;
      command.disabled = true;
      byId("acp-command-paste").disabled = true;
      byId("acp-options-status").textContent = "正在读取所选分区的镜像、AFS 与 GPU 规格…";
      byId("acp-template-config").replaceChildren();
    }
    try {
      const query = partitioned && requestedPartition ? `?partition_id=${encodeURIComponent(requestedPartition)}` : "";
      const data = await request(`/api/acp/options${query}`);
      if (revision !== optionsRequest) return;
      if (!Array.isArray(data.gpu_options) || !data.gpu_options.length || !data.template?.id) throw new Error("模板配置暂不可用。");
      if (partitioned && (!data.partition?.id || !Array.isArray(data.partitions) || (requestedPartition && data.partition.id !== requestedPartition))) throw new Error("分区配置不一致，请重新读取。");
      state.options = data;
      const draft = partitioned ? partitionDrafts.get(data.partition.id) : null;
      if (partitioned) {
        partitionSelect.replaceChildren(...data.partitions.map(partition => { const option = element("option", "", partition.name); option.value = partition.id; return option; }));
        partitionSelect.value = data.partition.id;
        updatePartitionLock();
        if (draft) { command.value = draft.command; commandTouched = draft.touched; }
        else if (switching) commandTouched = false;
      }
      const config = byId("acp-template-config"); config.replaceChildren();
      const created = data.template.created_at ? new Date(data.template.created_at).toLocaleString("zh-CN", { hour12: false }) : "—";
      pair(config, "模板创建时间", created);
      renderTemplateConfig(config, data.template_config);
      if (!commandTouched) command.value = typeof data.default_command === "string" && data.default_command.trim() ? data.default_command : "sleep inf";
      byId("acp-command-source").textContent = data.command_source ? `来源：${data.command_source.name} · ${data.command_source.id}` : partitioned ? "所选分区模板未运行，默认 sleep inf" : "无可用的运行中任务命令，默认 sleep inf";
      byId("acp-command-restore").disabled = false;
      renderCommand();
      const select = byId("acp-cluster"), previous = draft?.cluster ?? (switching ? "" : previousCluster);
      const automatic = element("option", "", "自动选择（使用优先规则）"); automatic.value = "";
      select.replaceChildren(automatic, ...(data.clusters || []).map(cluster => {
        const option = element("option", "", `${cluster.name} · ${cluster.id}`); option.value = cluster.id; return option;
      }));
      if (previous && !(data.clusters || []).some(cluster => cluster.id === previous)) {
        const unavailable = element("option", "", `${previous}（已不可用，请重新选择）`); unavailable.value = previous; select.append(unavailable);
      }
      select.value = previous;
      select.disabled = false;
      // Seed the GPU value before rebuilding the selected cluster's supported sizes.
      const gpu = byId("acp-gpus"), desiredGPU = draft?.gpu ?? (switching ? String(data.default_gpus_per_replica) : previousGPU);
      if (desiredGPU) { const value = element("option", "", desiredGPU); value.value = desiredGPU; gpu.replaceChildren(value); }
      gpuOptions();
      byId("acp-preview-button").disabled = state.createUnknown || state.storageError;
      byId("acp-options-status").textContent = `当前模板：${data.template.name} · ${data.template.id}`;
      total();
    } catch (error) {
      if (revision !== optionsRequest) return;
      byId("acp-options-status").textContent = partitioned ? `分区配置读取失败：${error.message} 请重试或切换分区。` : "创建配置暂不可用，请连接云会话后重新读取。监控仍会继续更新。";
      byId("acp-options-retry").hidden = false;
    } finally {
      if (revision === optionsRequest) {
        optionsLoading = false;
        command.disabled = partitioned && !state.options;
        byId("acp-command-paste").disabled = pastePending || (partitioned && !state.options);
      }
    }
  }

  function renderSelected() {
    const task = state.selected, button = byId("acp-stop-selected");
    const terminal = byId("cci-terminal-selected");
    terminal.hidden = !(["CCI", "ACP"].includes(task?.kind) && task.state === "running" && task.name?.toLowerCase().includes("charles"));
    terminal.removeAttribute("href");
    if (!terminal.hidden) terminal.href = `/terminal?${new URLSearchParams({ site: window.MonitorSite?.id || "maoshanwang", task_id: task.id })}`;
    const ide = byId("cci-ide-selected");
    ide.hidden = !(!/Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) && !matchMedia("(max-width:760px), (pointer:coarse)").matches && task?.kind === "CCI" && task.state === "running" && task.name?.toLowerCase().includes("charles"));
    ide.removeAttribute("href");
    if (!ide.hidden) ide.href = `/ide?${new URLSearchParams({ site: window.MonitorSite?.id || "maoshanwang", task_id: task.id })}`;
    const releaseButton = byId("acp-release-restart-selected");
    releaseButton.hidden = !(task?.kind === "ACP" && task.can_stop && task.state === "running" && task.name?.toLowerCase().includes("charles"));
    releaseButton.disabled = !fresh() || state.creating || state.createUnknown || state.stopping || state.stopUnknown || state.storageError;
    button.hidden = !(task?.kind === "ACP" && task.can_stop);
    button.disabled = !fresh() || state.creating || state.stopping || state.stopUnknown || state.storageError;
    byId("acp-stop-confirm").disabled = state.stopping || state.storageError || !stopFresh(state.stop?.task_id);
    const note = byId("detail-action-note");
    note.textContent = task?.kind === "ACP" && !fresh() ? "当前为旧数据或连接不可用；成功采集后可操作任务" : "";
    note.hidden = !note.textContent;
  }

  function openStop(task) {
    if (!task?.can_stop || task.kind !== "ACP" || state.stopUnknown || state.storageError || !stopFresh(task.id)) return;
    state.stop = { task_id: task.id, display_name: task.name, request_id: crypto.randomUUID() };
    state.stopUnknown = false;
    byId("acp-stop-target").replaceChildren();
    pair(byId("acp-stop-target"), "云站点", window.MonitorSite?.id || "maoshanwang");
    pair(byId("acp-stop-target"), "任务名称", task.name);
    pair(byId("acp-stop-target"), "完整任务 ID", task.id);
    message("acp-stop-message", "");
    byId("acp-stop-confirm").textContent = "确认停止";
    byId("acp-stop-cancel").disabled = false;
    stopDialog.showModal();
  }

  function renderWaiting() {
    const snapshot = state.snapshot, kinds = ["CCI", "ACP"];
    const tasks = (Array.isArray(snapshot?.waiting_tasks) ? snapshot.waiting_tasks : []).filter((task) => task.state !== "SUSPENDING");
    const missing = kinds.filter((kind) => !fresh(`${kind}_queue`));
    const known = missing.length === 0;
    byId("waiting-count").textContent = known ? String(tasks.length) : "—";
    byId("waiting-summary").textContent = kinds.map((kind) => `${kind} ${fresh(`${kind}_queue`) ? tasks.filter((task) => task.kind === kind).length : "未知"}`).join(" · ");
    message("waiting-notice", missing.length ? `${missing.join("、")} 全用户队列暂不可用或已过期；保留上次等待队列的旧数据，完整数量未知。` : "");
    const fragment = document.createDocumentFragment();
    for (const task of tasks) {
      const row = element("tr"), name = element("td", "waiting-identity"), current = fresh(`${task.kind}_queue`);
      name.append(element("strong", "", task.name || task.id), element("small", "", task.id));
      row.append(name, element("td", "", task.kind || "—"), element("td", "", task.owner || "—"), element("td", "", task.cluster || "—"));
      const status = element("td");
      status.append(element("span", "queue-badge", task.state_label || task.state || "状态未知"));
      if (!current) status.append(element("small", "", " · 旧数据"));
      row.append(status, element("td", "", `${task.gpu_count ?? "—"} GPU / ${task.replicas ?? "—"} 副本`));
      const time = element("td", "inventory-time");
      time.append(element("div", "", `创建 ${date(task.created_at)}`), element("div", "", `观测 ${date(task.observed_at)}`));
      row.append(time);
      const action = element("td");
      if (task.kind === "ACP" && task.can_stop && task.name?.toLowerCase().includes("charles")) {
        const button = element("button", "button button-danger", "停止");
        button.type = "button";
        button.disabled = !current || !fresh() || state.stopping || state.stopUnknown || state.storageError;
        button.setAttribute("aria-label", `停止 ${task.name || task.id}`);
        button.addEventListener("click", () => openStop(task));
        action.append(button);
      } else action.textContent = "只读";
      try {
        const url = new URL(task.url);
        const origin = window.MonitorSite?.id === "sensecore" ? "https://console.sensecore.cn" : "https://console.ms-sc-01.maoshanwangtech.com";
        if (url.origin === origin && !url.username && !url.password) {
          const link = element("a", "text-link", "跳转任务 ↗"), linkRow = element("div");
          link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
          link.setAttribute("aria-label", `跳转至任务 ${task.name || task.id}`);
          linkRow.append(link); action.append(linkRow);
        }
      } catch { /* Missing or untrusted task URLs have no navigation link. */ }
      row.append(action);
      fragment.append(row);
    }
    if (!tasks.length) {
      const row = element("tr"), cell = element("td", "table-empty", known ? "当前没有等待或启动中的 CCI / ACP 任务" : "等待任务数量暂时未知；请等待成功采集后确认。");
      cell.colSpan = 8;
      row.append(cell); fragment.append(row);
    }
    byId("waiting-body").replaceChildren(fragment);
  }

  function renderCommand() {
    const lines = command.value.split("\n").length;
    byId("acp-command-lines").textContent = Array.from({ length: lines }, (_, index) => index + 1).join("\n");
    byId("acp-command-lines").scrollTop = command.scrollTop;
    byId("acp-command-stats").textContent = `${lines} 行 · ${command.value.length} 字符`;
  }
  command.addEventListener("input", () => { commandTouched = true; commandRevision += 1; renderCommand(); });
  command.addEventListener("scroll", () => { byId("acp-command-lines").scrollTop = command.scrollTop; });
  new ResizeObserver(() => {
    if (command.clientHeight) byId("acp-command-lines").style.height = `${command.clientHeight}px`;
  }).observe(command);
  byId("acp-command-restore").addEventListener("click", () => {
    if (!state.options) return;
    commandTouched = true; commandRevision += 1;
    command.value = state.options.default_command || "sleep inf";
    renderCommand(); command.focus();
    message("acp-command-notice", "已恢复读取到的模板命令，可继续修改。");
  });
  byId("acp-command-expand").addEventListener("click", () => {
    const expanded = byId("acp-command-editor").classList.toggle("expanded");
    command.style.height = "";
    byId("acp-command-expand").setAttribute("aria-expanded", String(expanded));
    byId("acp-command-expand").textContent = expanded ? "收起编辑" : "展开编辑";
    command.focus();
  });
  byId("acp-command-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(command.value); message("acp-command-notice", "启动命令已复制。"); }
    catch { command.focus(); command.select(); message("acp-command-notice", "浏览器未允许复制，已选中命令，请按 Ctrl+C 或 ⌘C 复制。"); }
  });
  byId("acp-command-paste").addEventListener("click", async () => {
    if (pastePending) return;
    const draft = command.value, revision = commandRevision, start = command.selectionStart, end = command.selectionEnd;
    commandTouched = true; pastePending = true;
    byId("acp-command-paste").disabled = true;
    try {
      const text = await navigator.clipboard.readText();
      if (commandRevision !== revision || command.value !== draft) {
        message("acp-command-notice", "等待剪贴板期间命令已变化，未粘贴。请在需要的位置重新粘贴。");
        return;
      }
      command.setRangeText(text, start, end, "end");
      commandRevision += 1; renderCommand(); command.focus();
      message("acp-command-notice", "已粘贴，可继续编辑。");
    } catch { command.focus(); message("acp-command-notice", "浏览器未允许读取剪贴板，请在编辑框按 Ctrl+V 或 ⌘V 粘贴。"); }
    finally { pastePending = false; byId("acp-command-paste").disabled = partitioned && (optionsLoading || !state.options); }
  });
  renderCommand();
  byId("acp-cluster").addEventListener("change", gpuOptions);
  form.addEventListener("input", total);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.options || optionsLoading || state.creating || state.createUnknown || state.storageError) return;
    if (!byId("acp-gpus").value) { message("acp-message", "请选择可用集群和 GPU 规格。"); return; }
    const button = byId("acp-preview-button");
    button.disabled = true; button.textContent = "正在生成预览…";
    message("acp-message", "");
    try {
      const context = state.options, revision = optionsRequest;
      const submission = { display_name: byId("acp-name").value, replicas: Number(byId("acp-replicas").value), gpus_per_replica: Number(byId("acp-gpus").value), command: byId("acp-command").value, cluster_id: byId("acp-cluster").value };
      if (partitioned) submission.partition_id = context.partition.id;
      const data = await request("/api/acp/preview", submission);
      if (revision !== optionsRequest || context !== state.options) return;
      showCreatePreview(data, submission.display_name);
    } catch (error) { message("acp-message", error.message); }
    finally { button.disabled = optionsLoading || !state.options || state.createUnknown || state.storageError; button.textContent = "预览创建配置"; }
  });

  function showCreatePreview(data, displayName) {
      state.preview = data;
      byId("acp-create-title").textContent = data.release_restart ? "确认释放重启 ACP 任务" : data.restart ? "确认重新启动 ACP 任务" : "确认创建 ACP 任务";
      const details = byId("acp-preview-details"); details.replaceChildren();
      pair(details, "云站点", window.MonitorSite?.id || "maoshanwang");
      pair(details, "任务名称", displayName);
      pair(details, "来源模板", `${data.template.name} · ${data.template.id}`);
      if (partitioned) {
        pair(details, "AFS 分区", data.partition?.name || state.options?.partition?.name || "—");
      }
      if (data.restart) pair(details, "重新启动方式", data.release_restart ? "先以相同名称、集群和配置提交新任务（新 ID），核实云端已接收后再停止原任务。" : "提交新任务，保留原任务记录；名称与原任务相同，任务 ID 不同。");
      if (data.release_restart) pair(details, "随后释放的原任务", `${data.template.name} · ${data.template.id}`);
      renderTemplateConfig(details, data.template_config);
      pair(details, data.selection_mode === "manual" ? "指定集群" : "自动选择的集群", `${data.cluster.name} · ${data.cluster.id}`);
      pair(details, "GPU 总量", `${data.replicas} 副本 × ${data.gpus_per_replica} GPU = ${data.total_gpus} 张`);
      pair(details, "每副本实际资源", `${data.vcpu_per_replica} vCPU · ${data.memory_gib_per_replica} GiB 内存`);
      if (data.capacity_checks?.some(check => check.node_check?.status !== "not_checked")) {
        for (const check of data.capacity_checks) {
          pair(details, `${check.cluster_name} 资源检查`, `${check.quota_sufficient ? "总配额充足" : "总配额不足 / 未知"}；${check.node_check?.message || "节点资源未知"}`);
        }
      }
      pair(details, "调度预期", `${data.will_queue ? "将排队" : "按当前可用资源提交"} · ${data.message || "实际调度以云端状态为准。"}`);
      pair(details, "最终启动命令", data.command, true);
      pair(details, "预览有效期至", date(data.expires_at));
      message("acp-create-message", "");
      byId("acp-create-confirm").disabled = false;
      byId("acp-create-confirm").textContent = data.release_restart ? "确认提交新任务并释放原任务" : data.restart ? "确认重新启动" : "确认创建";
      byId("acp-create-cancel").disabled = false;
      byId("acp-create-cancel").textContent = "返回修改";
      createDialog.showModal();
  }

  const restartDialog = byId("acp-restart-dialog"), restartCommand = byId("acp-restart-command");
  let restartSource = null, restartSerial = 0, restartLoading = false;
  function restartBlocked() {
    syncPending("create");
    return state.creating || state.createUnknown || state.storageError;
  }
  document.addEventListener("acp-restart", async (event) => {
    if (restartBlocked()) { if (state.createUnknown) createDialog.showModal(); return; }
    const release = event.detail.release === true;
    if (release && (state.stopping || state.stopUnknown || !fresh())) return;
    byId("acp-restart-title").textContent = release ? "释放重启 ACP 任务" : "重新启动 ACP 任务";
    byId("acp-restart-description").textContent = release ? "沿用原名称、集群、资源、镜像和 AFS，先提交新任务，确认云端接收后再停止原任务。请编辑启动命令后预览确认。" : "沿用原任务名称、集群、资源、镜像和 AFS 配置，提交一个新任务，原历史记录保留。可在下方修改启动命令。";
    byId("acp-restart-preview").textContent = release ? "预览释放重启" : "预览重新启动";
    const serial = ++restartSerial; restartSource = null;
    restartCommand.value = ""; restartCommand.disabled = true;
    byId("acp-restart-preview").disabled = true; byId("acp-restart-restore").disabled = true;
    const details = byId("acp-restart-details"); details.replaceChildren();
    pair(details, "原任务", `${event.detail.name} · ${event.detail.id}`);
    message("acp-restart-message", "正在读取原任务配置…"); restartDialog.showModal();
    try {
      const data = await request(`/api/acp/${release ? "release-restart" : "restart"}-options?task_id=${encodeURIComponent(event.detail.id)}`);
      if (serial !== restartSerial || !restartDialog.open) return;
      restartSource = data;
      restartCommand.value = data.command; restartCommand.disabled = false;
      pair(details, "原集群 / 资源", `${data.cluster} · ${data.replicas} 副本 · ${data.resource_spec}`);
      renderTemplateConfig(details, data.template_config);
      byId("acp-restart-preview").disabled = false; byId("acp-restart-restore").disabled = false;
      message("acp-restart-message", "已填入原启动命令，可直接编辑后预览。");
    } catch (error) { if (serial === restartSerial) message("acp-restart-message", error.message); }
  });
  byId("acp-restart-restore").addEventListener("click", () => { if (restartSource) restartCommand.value = restartSource.command; });
  byId("acp-restart-cancel").addEventListener("click", () => restartDialog.close());
  restartDialog.addEventListener("close", () => {
    restartSerial += 1; restartSource = null; restartCommand.value = "";
  });
  byId("acp-restart-preview").addEventListener("click", async () => {
    if (!restartSource || restartLoading || restartBlocked()) return;
    const source = restartSource, serial = restartSerial;
    restartLoading = true; byId("acp-restart-preview").disabled = true;
    message("acp-restart-message", "正在核对原任务与资源…");
    try {
      const data = await request(`/api/acp/${source.release_restart ? "release-restart" : "restart"}-preview`, { task_id: source.template.id, revision: source.revision, command: restartCommand.value });
      if (serial !== restartSerial || !restartDialog.open || restartBlocked()) return;
      message("acp-restart-message", "可继续修改命令并重新预览。");
      showCreatePreview(data, source.template.name);
    } catch (error) { if (serial === restartSerial) message("acp-restart-message", error.message); }
    finally { restartLoading = false; byId("acp-restart-preview").disabled = !restartSource; }
  });

  byId("acp-create-confirm").addEventListener("click", async () => {
    if (state.creating || !state.preview || state.storageError) return;
    const submission = { preview_id: state.preview.preview_id }, wasUnknown = state.createUnknown;
    let remembered = false;
    state.creating = true;
    updatePartitionLock();
    const button = byId("acp-create-confirm"); button.disabled = true; button.textContent = "正在提交…";
    byId("acp-create-cancel").disabled = true;
    message("acp-create-message", "正在提交此预览，请等待结果。");
    try {
      await remember("create", submission);
      remembered = true;
      const result = await request("/api/acp/create", submission);
      forget("create", submission);
      state.createUnknown = false;
      createDialog.close();
      restartDialog.close();
      message("acp-message", result.release_restart ? `释放重启已提交：新任务 ${result.name} · ${result.id}（${result.state}）；原任务 ${result.original_id}（${result.original_state}）。请等待采集确认启动和资源释放状态。` : `已提交创建：${result.name} · ${result.id}。状态：${result.state}。任务列表将在采集后更新。`);
      state.preview = null;
      refresh();
    } catch (error) {
      state.createUnknown = Boolean(error.unknown || error.conflict || (wasUnknown && (!remembered || error.mobileBlocked)));
      if (!state.createUnknown && remembered) { forget("create", submission); state.preview = null; }
      const text = state.createUnknown ? error.code === "release_incomplete" ? error.outcomeDetail : `创建结果未知，请勿重新创建新任务。可核对此次请求，或暂时关闭弹窗查看监控；核对会复用同一次请求。${error.outcomeDetail || ""}` : error.message;
      message("acp-create-message", text);
      if (state.createUnknown) message("acp-message", text);
    } finally {
      state.creating = false; button.disabled = !state.preview || state.storageError;
      button.textContent = state.createUnknown ? "核对此次创建结果" : "确认创建";
      byId("acp-create-cancel").disabled = false;
      byId("acp-create-cancel").textContent = state.createUnknown ? "暂时关闭" : "返回修改";
      byId("acp-preview-button").disabled = state.createUnknown || state.storageError || !state.options;
      byId("acp-create-resume").hidden = !state.createUnknown;
      updatePartitionLock();
    }
  });

  byId("acp-stop-confirm").addEventListener("click", async () => {
    if (state.stopping || !state.stop || state.storageError || !stopFresh(state.stop.task_id)) return;
    const submission = { ...state.stop }, wasUnknown = state.stopUnknown;
    let remembered = false;
    state.stopping = true;
    const button = byId("acp-stop-confirm"); button.disabled = true; button.textContent = "正在停止…";
    byId("acp-stop-cancel").disabled = true;
    try {
      await remember("stop", submission);
      remembered = true;
      const result = await request("/api/acp/stop", submission);
      forget("stop", submission);
      state.stopUnknown = false;
      state.stop = null;
      stopDialog.close();
      message("acp-message", `已提交停止：${result.name} · ${result.id}。请等待采集确认最终状态。`);
      refresh();
    } catch (error) {
      state.stopUnknown = Boolean(error.unknown || error.conflict || (wasUnknown && (!remembered || error.mobileBlocked)));
      if (!state.stopUnknown && remembered) forget("stop", submission);
      const text = state.stopUnknown ? `停止结果未知。请核对本次停止请求，勿另行重复操作；核对会复用同一次请求。${error.outcomeDetail || ""}` : error.message;
      message("acp-stop-message", text);
      if (state.stopUnknown) message("acp-message", text);
    } finally {
      state.stopping = false; button.disabled = false;
      button.textContent = state.stopUnknown ? "核对此次停止结果" : "确认停止";
      byId("acp-stop-cancel").disabled = false;
      byId("acp-stop-cancel").textContent = state.stopUnknown ? "暂时关闭" : "取消";
      byId("acp-stop-resume").hidden = !state.stopUnknown;
      renderSelected(); renderWaiting();
    }
  });

  byId("acp-create-cancel").addEventListener("click", () => createDialog.close());
  byId("acp-stop-cancel").addEventListener("click", () => stopDialog.close());
  createDialog.addEventListener("cancel", (event) => { if (state.creating) event.preventDefault(); });
  stopDialog.addEventListener("cancel", (event) => { if (state.stopping) event.preventDefault(); });
  byId("acp-create-resume").addEventListener("click", () => createDialog.showModal());
  byId("acp-stop-resume").addEventListener("click", () => stopDialog.showModal());
  byId("acp-stop-selected").addEventListener("click", () => openStop(state.selected));
  byId("acp-release-restart-selected").addEventListener("click", () => {
    const task = state.selected;
    if (!task || byId("acp-release-restart-selected").disabled || byId("acp-release-restart-selected").hidden) return;
    document.dispatchEvent(new CustomEvent("acp-restart", { detail: { id: task.id, name: task.name, release: true } }));
  });
  byId("acp-options-retry").addEventListener("click", () => options());
  document.addEventListener("monitor-selection", (event) => { state.selected = event.detail; renderSelected(); });
  document.addEventListener("monitor-snapshot", (event) => { state.snapshot = event.detail.snapshot; state.unavailable = event.detail.unavailable; renderWaiting(); renderSelected(); });
  window.addEventListener("storage", (event) => {
    for (const kind of ["create", "stop"]) if (event.key === pendingKeys[kind] || event.key === null) syncPending(kind);
  });
  syncPending("create"); syncPending("stop");
  matchMedia("(max-width:760px), (pointer:coarse)").addEventListener("change", renderSelected);
  setInterval(() => { renderSelected(); renderWaiting(); }, 10000);
  const controls = byId("acp-partition-controls"); controls.hidden = false;
  if (partitioned) {
    byId("acp-command-help").textContent = "读取所选分区模板的启动命令；模板未运行时默认 sleep inf。提交时若末尾没有独立的 sleep inf，会自动补上。";
    const label = element("label", "", "AFS 分区");
    partitionSelect = element("select"); partitionSelect.id = "acp-partition"; partitionSelect.name = "partition_id"; partitionSelect.disabled = true;
    const loading = element("option", "", "正在读取分区…"); loading.value = ""; partitionSelect.append(loading);
    label.append(partitionSelect, element("small", "", "使用此 AFS 分区最近的 charles 挂载模板，镜像和存储配置随分区切换。"));
    controls.append(label);
    partitionSelect.addEventListener("change", () => {
      if (state.creating || state.createUnknown || state.storageError) return;
      savePartitionDraft();
      commandRevision += 1; commandTouched = false; command.value = "";
      renderCommand();
      byId("acp-cluster").value = "";
      state.preview = null;
      options(partitionSelect.value, true);
    });
  }
  const config = element("dl", "acp-preview-details"); config.id = "acp-template-config";
  controls.append(config);
  options();
})();
