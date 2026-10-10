"use strict";

(() => {
  function transferPreview(accounts, input, script) {
    const name = input.name.trim(), tag = input.tag.trim();
    const targetName = input.targetName.trim() || name, targetTag = input.targetTag.trim() || tag;
    const validName = value => value.length <= 200 && /^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:\/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*$/.test(value)
      && !(value.includes("/") && (value.split("/")[0].includes(".") || value.split("/")[0] === "localhost"));
    const validTag = value => /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(value);
    if (!Object.hasOwn(accounts, input.target) || (input.mode === "transfer" && !Object.hasOwn(accounts, input.source))) return {error: "请选择已配置的仓库。"};
    if (input.mode === "transfer" && input.source === input.target) return {error: "请选择不同的源仓库与目标仓库。"};
    if (!validName(name) || !validName(targetName)) return {error: "镜像名请填写小写 repo 或 namespace/repo，不含域名、tag 或空格。"};
    if (!validTag(tag) || !validTag(targetTag)) return {error: "Tag 必须为 1–128 个字母、数字、下划线、点或短横线，不能以点或短横线开头。"};
    const resolve = (id, ref) => `${accounts[id].registry}/${ref.includes("/") ? ref : `${accounts[id].namespace}/${ref}`}`;
    const from = `${name}:${tag}`, to = `${targetName}:${targetTag}`;
    const prefix = input.location === "server" ? "sudo bash docker/registry.sh" : `bash '${script.replace(/'/g, "'\\''")}'`;
    return {command: input.mode === "push" ? `${prefix} push ${input.target} ${from} ${to}` : `${prefix} transfer ${input.source} ${from} ${input.target} ${to}`,
      source: input.mode === "push" ? from : resolve(input.source, from), target: resolve(input.target, to)};
  }
  const environmentFields = {cuda: "CUDA", pytorch: "PyTorch", python: "Python", architecture: "架构"};
  function imageEnvironmentValue(row, field) {
    const value = String(row[field] || "").trim();
    return value ? (field === "architecture" ? value : value.match(/^v?(\d+\.\d+)(?:\D|$)/i)?.[1] || value) : "__unknown__";
  }
  function filterImages(rows, source, query, environment = {}) {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return rows.filter(row => {
      const text = [row.path, row.name, row.tag, ...Object.keys(environmentFields).map(field => row[field])].join(" ").toLowerCase();
      return (!source || row.source === source || row.products.includes(source)) && terms.every(term => text.includes(term))
        && Object.keys(environmentFields).every(field => !environment[field] || imageEnvironmentValue(row, field) === environment[field]);
    });
  }
  function imageFacetOptions(rows, field, source, query, environment) {
    const counts = new Map(rows.map(row => [imageEnvironmentValue(row, field), 0]));
    if (environment[field]) counts.set(environment[field], 0);
    for (const row of filterImages(rows, source, query, {...environment, [field]: ""})) {
      const value = imageEnvironmentValue(row, field); counts.set(value, counts.get(value) + 1);
    }
    return [...counts].sort(([a], [b]) => a === b ? 0 : a === "__unknown__" ? 1 : b === "__unknown__" ? -1
      : field === "architecture" ? a.localeCompare(b) : b.localeCompare(a, undefined, {numeric: true}))
      .map(([value, count]) => ({value, count}));
  }
  const byId = id => document.getElementById(id);
  if (!byId("images-section")) return;
  const site = window.MonitorSite.id;
  const element = (tag, text, className = "") => Object.assign(document.createElement(tag), {textContent: text, className});
  const labels = {local: "本地上传", snapshot: "快照", copy: "拷贝", cci: "云容器实例", acp: "高性能算力池", aicl: "云开发机", unknown: "未知来源"};
  let data = {images: [], accounts: {}, refreshing: false}, timer, controller, page = 1, accountKey = "", loaded = false, catalogNamespace = "";
  const environmentSelects = Object.fromEntries(Object.keys(environmentFields).map(field => [field, byId(`image-filter-${field}`)]));
  window.MonitorImages = window.MonitorImages || {};
  const tabs = [...document.querySelectorAll("[data-image-tab]")];
  function selectTab(name, focus = false) {
    for (const tab of tabs) {
      const selected = tab.dataset.imageTab === name;
      tab.classList.toggle("active", selected); tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
      byId(tab.getAttribute("aria-controls")).hidden = !selected;
      if (selected && focus) tab.focus();
    }
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.imageTab));
    tab.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      selectTab(tabs[next].dataset.imageTab, true);
    });
  });
  const source = byId("image-transfer-source"), target = byId("image-transfer-target");
  const name = byId("image-transfer-image"), tag = byId("image-transfer-tag"), targetName = byId("image-transfer-name"), targetTag = byId("image-transfer-target-tag");
  const mode = byId("image-transfer-mode"), locationSelect = byId("image-transfer-location");
  const copy = byId("image-transfer-copy"), command = byId("image-transfer-command"), status = byId("image-transfer-status");
  function updateTransfer() {
    const preview = transferPreview(data.accounts, {source: source.value, target: target.value, name: name.value, tag: tag.value,
      targetName: targetName.value, targetTag: targetTag.value, mode: mode.value, location: locationSelect.value}, data.script || "registry.sh");
    byId("image-transfer-source-field").hidden = mode.value === "push";
    byId("image-transfer-name-label").textContent = mode.value === "push" ? "本地镜像名" : "源镜像名";
    copy.disabled = Boolean(preview.error);
    command.textContent = preview.command || "—";
    status.textContent = preview.error || `${preview.source} → ${preview.target}；${mode.value === "push" ? "登录目标仓库、tag、push" : "登录两端仓库、pull、tag、push"}。命令需在所选机器执行。`;
  }
  [source, target, mode, locationSelect].forEach(select => select.addEventListener("change", updateTransfer));
  [name, tag, targetName, targetTag].forEach(input => input.addEventListener("input", updateTransfer));
  copy.addEventListener("click", async () => {
    updateTransfer(); if (copy.disabled) return;
    try { await navigator.clipboard.writeText(command.textContent); status.textContent = "命令已复制，尚未执行。"; }
    catch { status.textContent = "浏览器未允许复制，请手动选择命令。"; }
  });
  byId("image-client-upload").addEventListener("click", () => { mode.value = "push"; target.value = site; updateTransfer(); selectTab("transfer"); name.focus(); });
  function publishCatalog() {
    window.MonitorImages[site] = data;
    document.dispatchEvent(new CustomEvent("monitor-images", {detail: site}));
  }
  function configure() {
    const key = JSON.stringify(data.accounts);
    if (accountKey !== key) {
      accountKey = key;
      for (const [select, fallback] of [[source, site === "sensecore" ? "maoshanwang" : "sensecore"], [target, site]]) {
        const selected = select.value || fallback;
        select.replaceChildren(...Object.entries(data.accounts).map(([id, account]) => {
          const option = element("option", `${id} · ${account.namespace}`); option.value = id; option.selected = id === selected; return option;
        }));
      }
      updateTransfer();
    }
    const namespaces = [...new Set([...(data.namespaces || []), data.namespace].filter(Boolean))];
    const select = byId("image-catalog-namespace");
    select.replaceChildren(...namespaces.map(namespace => {
      const option = element("option", namespace); option.value = namespace; return option;
    }));
    select.value = data.namespace || ""; select.disabled = !namespaces.length;
  }
  function size(bytes) {
    if (bytes === null || bytes === undefined || !Number.isFinite(Number(bytes))) return "未知";
    const value = Number(bytes), unit = value >= 1024 ** 3 ? 3 : value >= 1024 ** 2 ? 2 : value >= 1024 ? 1 : 0;
    return `${(value / 1024 ** unit).toFixed(unit ? 2 : 0)} ${["B", "KiB", "MiB", "GiB"][unit]}`;
  }
  function renderCatalog() {
    const sourceFilter = byId("image-catalog-source").value, query = byId("image-catalog-search").value;
    const environment = Object.fromEntries(Object.entries(environmentSelects).map(([field, select]) => [field, select.value]));
    const rows = filterImages(data.images, sourceFilter, query, environment);
    for (const [field, select] of Object.entries(environmentSelects)) {
      const all = element("option", "全部"); all.value = "";
      select.replaceChildren(all, ...imageFacetOptions(data.images, field, sourceFilter, query, environment).map(({value, count}) => {
        const label = value === "__unknown__" ? "未知" : field !== "architecture" && /^\d+\.\d+$/.test(value) ? `${value} 系列` : value;
        const option = element("option", `${label} · ${count}`); option.value = value;
        option.disabled = count === 0 && value !== environment[field]; return option;
      }));
      select.value = environment[field];
    }
    byId("image-filter-reset").hidden = !(sourceFilter || query || Object.values(environment).some(Boolean));
    const pages = Math.max(1, Math.ceil(rows.length / 30)); page = Math.min(page, pages);
    byId("image-catalog-count").textContent = `${rows.length} / ${data.images.length} 个版本`;
    byId("image-page-status").textContent = `第 ${page} / ${pages} 页`;
    byId("image-page-prev").disabled = page === 1; byId("image-page-next").disabled = page === pages;
    const rendered = rows.slice((page - 1) * 30, page * 30).map(image => {
      const row = element("tr", ""), identity = element("td", "");
      identity.append(element("strong", image.name), element("small", image.path, "image-address"));
      const chips = element("div", "", "image-chips");
      for (const [field, label] of Object.entries(environmentFields)) {
        const value = imageEnvironmentValue(image, field), version = String(image[field] || "").trim() || "未知";
        const chip = element("button", `${label} ${version}`, "meta-pill"); chip.type = "button";
        chip.title = `${environment[field] === value ? "取消" : "按"} ${label} ${value === "__unknown__" ? "未知" : value} 筛选`;
        chip.setAttribute("aria-pressed", String(environment[field] === value));
        chip.addEventListener("click", () => {
          environmentSelects[field].value = environment[field] === value ? "" : value;
          page = 1; renderCatalog(); environmentSelects[field].focus();
        });
        chips.append(chip);
      }
      identity.append(chips);
      const origin = element("td", "");
      for (const value of new Set([image.source, ...image.products])) origin.append(element("span", labels[value] || value, `image-source image-source-${Object.hasOwn(labels, value) ? value : "unknown"}`));
      const actions = element("td", ""), transfer = element("button", "传递", "button button-secondary"), addressCopy = element("button", "复制地址", "button button-secondary");
      transfer.type = addressCopy.type = "button"; transfer.disabled = !image.tag;
      transfer.title = image.tag ? "填入传递表单" : "该镜像没有 tag，registry.sh 需要显式 tag";
      transfer.addEventListener("click", () => {
        mode.value = "transfer"; source.value = site; target.value = site === "sensecore" ? "maoshanwang" : "sensecore";
        name.value = `${image.namespace}/${image.name}`; tag.value = image.tag; targetName.value = image.name; targetTag.value = "";
        updateTransfer(); selectTab("transfer"); name.focus();
      });
      addressCopy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(image.path); byId("image-data-note").textContent = "镜像地址已复制。"; }
        catch { byId("image-data-note").textContent = "复制失败，请手动选择镜像地址。"; }
      });
      actions.className = "image-row-actions"; actions.append(transfer, addressCopy);
      const date = image.created_at ? new Date(image.created_at) : null;
      const created = element("td", date && Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", {hour12: false}) : "未知");
      created.title = image.pushed_at ? `推送时间：${image.pushed_at}` : "创建时间未提供";
      const version = element("td", ""); version.append(element("code", image.tag || "无 tag", "image-tag"));
      row.append(identity, element("td", image.namespace), origin, version, element("td", size(image.size_bytes)), created, actions);
      return row;
    });
    if (!rendered.length) {
      const row = element("tr", ""), cell = element("td", data.refreshing ? "正在读取真实镜像…" : data.status === "error" ? "镜像读取失败，请查看上方提示后重试" : data.images.length ? "没有匹配的镜像" : "该命名空间暂无镜像", "table-empty");
      cell.colSpan = 7; row.append(cell); rendered.push(row);
    }
    byId("image-catalog-body").replaceChildren(...rendered);
  }
  ["image-catalog-source", "image-catalog-search"].forEach(id => byId(id).addEventListener(id.endsWith("search") ? "input" : "change", () => {page = 1; renderCatalog();}));
  Object.values(environmentSelects).forEach(select => select.addEventListener("change", () => {page = 1; renderCatalog();}));
  byId("image-filter-reset").addEventListener("click", () => {
    for (const select of Object.values(environmentSelects)) select.value = "";
    byId("image-catalog-source").value = ""; byId("image-catalog-search").value = "";
    page = 1; renderCatalog(); byId("image-catalog-search").focus();
  });
  byId("image-page-prev").addEventListener("click", () => {page -= 1; renderCatalog();});
  byId("image-page-next").addEventListener("click", () => {page += 1; renderCatalog();});
  const active = () => ["#images", "#acp"].includes(location.hash) && !document.hidden;
  async function load(force = false) {
    if (controller || !active()) return;
    clearTimeout(timer); controller = new AbortController(); byId("image-catalog-refresh").disabled = true;
    let aborted = false;
    const requestedNamespace = catalogNamespace;
    try {
      const params = new URLSearchParams();
      if (force) params.set("refresh", "true");
      if (requestedNamespace) params.set("namespace", requestedNamespace);
      const response = await window.MonitorSite.fetch(`/api/images?${params}`, {signal: controller.signal});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "镜像读取失败");
      if (requestedNamespace !== catalogNamespace) return;
      data = result; catalogNamespace = data.namespace || ""; loaded = true;
      configure(); renderCatalog();
      byId("image-site-name").textContent = site;
      byId("image-site-description").textContent = `${data.registry || "仓库未配置"}/${data.namespace || "—"} · ${data.repository_count} 个镜像仓库 · ${data.images.length} 个版本`;
      byId("image-data-status").dataset.status = data.refreshing ? "loading" : data.status;
      byId("image-data-status").textContent = data.refreshing ? `读取中 ${data.completed_repositories}/${data.repository_count}` : data.status === "ok" ? "已连接" : data.status === "partial" ? "部分数据 / 可能过期" : "读取失败";
      byId("image-data-note").textContent = data.warnings.join(" ") || (data.refreshing ? "正在读取镜像版本，已读取的记录会逐步显示。" : data.updated_at ? `最近完整更新：${new Date(data.updated_at).toLocaleString("zh-CN", {hour12: false})}` : "");
      publishCatalog();
    } catch (error) {
      aborted = error.name === "AbortError";
      if (error.name !== "AbortError" && requestedNamespace === catalogNamespace) {
        data.refreshing = false;
        byId("image-data-status").dataset.status = "error";
        byId("image-data-status").textContent = "连接失败";
        byId("image-data-note").textContent = `${error.message}；保留已读取的记录，请刷新重试。`;
        if (!loaded) { data.status = "error"; renderCatalog(); }
        data = {...data, status: "error", warnings: [byId("image-data-note").textContent]};
        publishCatalog();
      }
    } finally {
      controller = null; byId("image-catalog-refresh").disabled = data.refreshing;
      if (active()) timer = setTimeout(() => load(), aborted ? 0 : data.refreshing || data.namespaces_refreshing ? 2000 : 60000);
    }
  }
  function selectNamespace(namespace) {
    if (!namespace || namespace === catalogNamespace) return;
    catalogNamespace = namespace; controller?.abort(); page = 1;
    data = {...data, namespace, images: [], repository_count: 0, completed_repositories: 0, refreshing: true, warnings: [], status: "loading"};
    configure(); renderCatalog(); publishCatalog();
    if (!controller) load();
  }
  byId("image-catalog-namespace").addEventListener("change", event => selectNamespace(event.target.value));
  document.addEventListener("monitor-image-namespace", event => { if (location.hash === "#acp") selectNamespace(event.detail); });
  byId("image-catalog-refresh").addEventListener("click", () => load(true));
  byId("acp-image-refresh").addEventListener("click", () => load(true));
  function visibility() {
    clearTimeout(timer);
    if (active()) {
      if (location.hash === "#acp") selectNamespace(byId("acp-image-namespace").value);
      load();
    } else controller?.abort();
  }
  document.addEventListener("workspace-page", visibility);
  document.addEventListener("visibilitychange", visibility);
  updateTransfer(); visibility();
})();
