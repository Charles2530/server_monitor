"use strict";

// Local-only behavior checks: real page scripts, deterministic DOM/timers/fetch.
// Run with: node --test scripts/frontend_efficiency_check.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function browser({ route = "overview", hidden = false, mobile = false } = {}) {
  const nodes = new Map(), requests = [], timers = new Map(), charts = [], mediaQueries = [];
  let timerId = 0, nodeCreations = 0, dateLocalizations = 0, now = Date.parse("2026-10-06T00:00:00Z");
  class Target {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(fn); }
    removeEventListener(name, fn) { this.listeners.set(name, (this.listeners.get(name) || []).filter(item => item !== fn)); }
    dispatchEvent(event) { for (const fn of this.listeners.get(event.type) || []) fn(event); }
  }
  class Node extends Target {
    constructor(tag = "div") {
      nodeCreations++;
      super(); this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.value = "";
      this.textContent = ""; this.hidden = false; this.open = false; this.checked = false; this.replacements = 0;
      this.firstChild = { textContent: "" }; this.isConnected = true;
      const classes = new Set();
      Object.defineProperty(this, "className", { get: () => [...classes].join(" "),
        set: value => { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach(name => classes.add(name)); } });
      this.insertions = 0; this.removals = 0;
      this.classList = { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
        toggle(name, on = !classes.has(name)) { if (on) classes.add(name); else classes.delete(name); } };
    }
    append(...items) { this.children.push(...items.flatMap(item => item.tagName === "#fragment" ? item.children : [item])); }
    prepend(...items) { this.children.unshift(...items); }
    replaceChildren(...items) { this.replacements++; this.children = []; this.append(...items); }
    insertBefore(item, before) {
      this.insertions++;
      const old = this.children.indexOf(item); if (old >= 0) this.children.splice(old, 1);
      this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, item);
    }
    removeChild(item) { this.removals++; this.children.splice(this.children.indexOf(item), 1); }
    get childNodes() { return this.children; }
    get options() { return this.children; }
    setAttribute(name, value) { this[name] = value; }
    getAttribute(name) { return this[name] ?? null; }
    before() {}
    after() {}
    removeAttribute(name) { delete this[name]; }
    querySelectorAll() { return []; }
    querySelector() { return null; }
    focus() {}
    scrollIntoView() {}
    close() { this.open = false; }
    showModal() { this.open = true; }
    click() { this.dispatchEvent({ type: "click" }); }
  }
  const document = new Target();
  document.hidden = hidden; document.documentElement = new Node(); document.body = new Node();
  document.getElementById = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  document.createElement = tag => new Node(tag); document.createElementNS = (_, tag) => new Node(tag);
  document.createTextNode = text => Object.assign(new Node(), { textContent: text });
  document.createComment = () => new Node("#comment");
  document.createDocumentFragment = () => new Node("#fragment"); document.querySelectorAll = () => [];
  document.querySelector = selector => [".sidebar", ".detail-tabs"].includes(selector) ? new Node() : null;
  document.getElementById("tasks-view").hidden = route !== "tasks";
  document.getElementById("task-history-size").value = "25";
  const window = new Target(), location = { hash: `#${route}` };
  const fetch = (url, options = {}) => new Promise((resolve, reject) => {
    requests.push({ url, options, reject, respond(data, ok = true) { resolve({ ok, status: ok ? 200 : 500, json: async () => data }); } });
  });
  window.scrollTo = () => {};
  window.MonitorSite = { id: "maoshanwang", key: key => key, fetch };
  window.matchMedia = () => {
    const query = new Target(); Object.defineProperty(query, "matches", { get: () => mobile });
    mediaQueries.push(query); return query;
  };
  window.echarts = { init() { const chart = { options: [], resize() {}, clear() {}, getWidth: () => 800, getHeight: () => 600,
    setOption(option) { this.options.push(option); } }; charts.push(chart); return chart; } };
  class Clock extends Date {
    static now() { return now; }
    toLocaleString(...args) { dateLocalizations++; return super.toLocaleString(...args); }
  }
  const context = vm.createContext({ window, document, location, fetch, AbortController, URL, URLSearchParams, Intl, Date: Clock,
    navigator: { userAgent: "test" }, localStorage: { getItem: () => null }, matchMedia: window.matchMedia,
    getComputedStyle: () => ({ getPropertyValue: () => "#000", fontFamily: "sans-serif" }),
    ResizeObserver: class { observe() {} }, MutationObserver: class { observe() {} },
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    Event: class { constructor(type) { this.type = type; } },
    Option: class extends Node { constructor(text, value) { super("option"); this.textContent = text; this.value = value; } },
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay, interval: true }); return id; },
    clearInterval(id) { timers.delete(id); }, requestAnimationFrame(fn) { fn(); }, console });
  function load(file, expose = "") {
    let source = fs.readFileSync(path.join(__dirname, "..", "static", file), "utf8");
    if (expose) source = source.replace(/\}\)\(\);\s*$/, `globalThis.subject = { ${expose} };\n})();`);
    vm.runInContext(source, context, { filename: file });
    return context.subject;
  }
  function emit(type, detail) { document.dispatchEvent({ type, detail }); }
  return { nodes, requests, timers, charts, context, document, window, location, load, emit, createdNodes: () => nodeCreations,
    dateLocalizations: () => dateLocalizations,
    hide(value) { document.hidden = value; emit("visibilitychange"); },
    setMobile(value) { mobile = value; for (const query of [...mediaQueries]) query.dispatchEvent({ type: "change" }); },
    route(value) { location.hash = `#${value}`; document.getElementById("tasks-view").hidden = value !== "tasks"; emit("workspace-page", value); },
    advance(ms) { now += ms; },
    tick(delay) { for (const [id, timer] of [...timers]) if (timer.delay === delay) { if (!timer.interval) timers.delete(id); timer.fn(); } } };
}
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const treeText = node => String(node.textContent || "") + node.children.map(treeText).join(" ");
const descendants = node => [node, ...node.children.flatMap(descendants)];
const clone = value => JSON.parse(JSON.stringify(value));
const storageData = () => ({ site: "maoshanwang", updated_at: "2026-10-06T00:00:00Z", status: "ok", refreshing: true,
  mount_refreshing: false, mount_status: { ACP: "ok", CCI: "ok" }, volumes: Array.from({ length: 40 }, (_, i) => ({
    id: `vol-${i}`, name: `Volume ${i}`, zone: i % 2 ? "zone-b" : "zone-a", state: "ACTIVE", expires_at: null,
    total_bytes: 1000, used_bytes: i, free_bytes: 1000 - i, used_percent: i / 10, mounts: [],
  })) });
const taskHistoryData = () => ({ site: "maoshanwang", loading: true, filtered_stopped: { ACP: 1 },
  source_status: { ACP: { status: "ok", updated_at: "2026-10-06T00:00:00Z" } },
  tasks: Array.from({ length: 40 }, (_, i) => ({ id: `ACP:history-${i}`, name: `Task ${i}`, kind: "ACP",
    status: "succeeded", status_label: "已成功", cluster: "cluster", gpu_count: 8, replicas: 1,
    created_at: "2026-10-06T00:00:00Z", started_at: null, ended_at: null, updated_at: null,
    duration_seconds: 100 + i, run_evidence: "observed", url: "https://console.ms-sc-01.maoshanwangtech.com/task/" + i,
  })) });
async function receiveRefresh(b, button, data, ok = true) {
  b.document.getElementById(button).click(); b.requests.at(-1).respond(clone(data), ok); await settle();
}

const runningData = (length = 40) => ({ status: "ok", connected: true, updated_at: "2026-10-06T00:00:00Z",
  source_status: { ACP: { status: "ok" }, CCI: { status: "ok" } }, clusters: [],
  tasks: Array.from({ length }, (_, i) => ({ id: `ACP:running-${i}`, name: `Running ${i}`, kind: "ACP", cluster: "shared",
    state: "running", gpu_count: 8, replicas: 1, workers: ["worker-0"], updated_at: "2026-10-06T00:00:00Z",
    metrics: { cpu: 10, gpu: 20, memory: 30, gpu_memory: 40 }, url: "https://console.ms-sc-01.maoshanwangtech.com/task/" + i,
  })) });
function runningPage(length = 40, route = "overview") {
  const b = browser({ route }), app = b.load("app.js", "model, renderTasks, renderDetail");
  app.model.snapshot = runningData(length); app.renderTasks();
  return { b, app, list: b.nodes.get("task-list") };
}

test("task selection and unchanged search preserve card nodes without remounting the list", t => {
  const { b, app, list } = runningPage(), cards = [...list.children];
  const before = [b.createdNodes(), list.replacements, list.insertions, list.removals];
  for (let i = 1; i <= 3; i++) { app.model.selected = `ACP:running-${i}`; app.renderTasks(); }
  const selectionChanges = [b.createdNodes(), list.replacements, list.insertions, list.removals].map((value, i) => value - before[i]);
  t.diagnostic(`3 selections among 40 tasks: created nodes/replacements/insertions/removals = ${selectionChanges.join("/")}`);
  assert.deepEqual(selectionChanges, [0, 0, 0, 0]);
  assert.ok(list.children.every((card, i) => card === cards[i]));
  assert.equal(cards[0].classList.contains("selected"), false); assert.equal(cards[0]["aria-pressed"], "false");
  assert.equal(cards[3].classList.contains("selected"), true); assert.equal(cards[3]["aria-pressed"], "true");
  assert.match(treeText(cards[3]), /正在查看历史/); assert.match(treeText(cards[0]), /查看使用历史/);
  for (const search of ["running", "shared", ""]) { app.model.search = search; app.renderTasks(); }
  assert.equal(b.createdNodes(), before[0]); assert.equal(list.insertions, before[2]); assert.equal(list.replacements, before[1]);
});

test("task filtering evicts hidden cards and retains visible nodes across removal, addition and reorder", () => {
  const { b, app, list } = runningPage(4), original = [...list.children];
  app.model.search = "running-1"; app.renderTasks();
  assert.equal(list.children.length, 1); assert.equal(list.children[0], original[1]);
  app.model.search = ""; app.renderTasks();
  assert.equal(list.children[1], original[1]); assert.notEqual(list.children[0], original[0], "hidden cards must be evicted");
  const current = [...list.children]; app.model.snapshot.tasks.reverse(); app.renderTasks();
  assert.deepEqual(list.children, current.slice().reverse());
  app.model.snapshot.tasks = app.model.snapshot.tasks.filter(task => task.id !== "ACP:running-2"); app.renderTasks();
  assert.deepEqual(list.children, [current[3], current[1], current[0]]);
  app.model.snapshot.tasks.push({ ...runningData(1).tasks[0], id: "CCI:new", kind: "CCI", name: "new task" }); app.renderTasks();
  assert.equal(list.children.length, 4); assert.match(treeText(list.children[3]), /new task/);
  assert.equal(list.children[0], current[3]);
});

test("task metrics and warnings replace only affected cards while current source warnings stay accurate", () => {
  const { app, list } = runningPage(3), original = [...list.children];
  app.model.snapshot.tasks[0].metrics.gpu = 88; app.model.snapshot.tasks[0].metric_error = "metric failed";
  app.model.snapshot.tasks[0].failed_metrics = ["gpu"]; app.model.snapshot.tasks[0].metric_updated_at = { gpu: "2026-10-05T00:00:00Z" };
  const insertions = list.insertions; app.renderTasks();
  assert.notEqual(list.children[0], original[0]); assert.equal(list.children[1], original[1]); assert.equal(list.children[2], original[2]);
  assert.equal(list.insertions - insertions, 1);
  assert.match(treeText(list.children[0]), /88/); assert.match(treeText(list.children[0]), /metric failed/); assert.match(treeText(list.children[0]), /旧采样/);
  app.model.snapshot.source_status.ACP.status = "error"; app.renderTasks(); assert.match(treeText(list.children[1]), /任务列表采集失败/);
  app.model.snapshot.source_status.ACP.status = "ok"; app.model.snapshot.tasks[0].metric_error = null;
  app.renderTasks(); assert.doesNotMatch(treeText(list), /任务列表采集失败|metric failed/);
});

test("reused task clicks preserve selection details and retry failed history reads", async () => {
  const { b, app, list } = runningPage(2, "tasks"), card = list.children[1], selections = [];
  b.document.addEventListener("monitor-selection", event => selections.push(event.detail));
  card.click(); const history = () => b.requests.filter(request => request.url.startsWith("/api/history"));
  assert.equal(app.model.selected, "ACP:running-1"); assert.equal(selections.at(-1).id, "ACP:running-1");
  assert.equal(b.nodes.get("detail-title").textContent, "Running 1"); assert.equal(history().length, 1);
  history()[0].reject(new Error("offline")); await settle();
  assert.equal(list.children[1], card); card.click(); assert.equal(history().length, 2);
  app.model.snapshot.tasks[1].name = "updated name"; app.renderTasks(); list.children[1].click();
  assert.equal(b.nodes.get("detail-title").textContent, "updated name"); assert.equal(history().length, 2);
});

test("task empty states update, cancel selection and discard previously visible cards", () => {
  const { b, app, list } = runningPage(2), old = [...list.children], selections = [];
  b.document.addEventListener("monitor-selection", event => selections.push(event.detail));
  app.model.search = "absent"; app.renderTasks(); assert.equal(app.model.selected, null); assert.equal(selections.at(-1), null);
  assert.equal(b.nodes.get("detail-panel").hidden, true); assert.match(treeText(list), /没有符合条件/);
  const before = [b.createdNodes(), list.replacements]; app.renderTasks();
  assert.deepEqual([b.createdNodes(), list.replacements], before);
  app.model.search = ""; app.renderTasks(); assert.notEqual(list.children[0], old[0]); assert.equal(app.model.selected, "ACP:running-0");
  app.model.snapshot.tasks = []; app.model.snapshot.source_status.ACP.status = "error"; app.renderTasks(); assert.match(treeText(list), /任务数量暂时未知/);
  app.model.snapshot.source_status.ACP.status = "ok"; app.renderTasks(); assert.match(treeText(list), /当前没有运行任务/);
  app.model.snapshot = null; app.model.unavailable = true; app.renderTasks(); assert.match(treeText(list), /暂时无法读取任务/);
});

test("poll refreshes task warnings for source-only changes and recovers from failed reads", async () => {
  const b = browser(), app = b.load("app.js", "model"), data = runningData(2);
  b.requests[0].respond(clone(data)); await settle(); const list = b.nodes.get("task-list"), card = list.children[0];
  data.source_status.ACP.status = "error";
  b.emit("monitor-refresh"); b.requests.at(-1).respond(clone(data)); await settle();
  assert.match(treeText(list), /任务列表采集失败/); assert.notEqual(list.children[0], card);
  const failedCard = list.children[0]; b.emit("monitor-refresh"); b.requests.at(-1).reject(new Error("offline")); await settle();
  assert.equal(list.children[0], failedCard); assert.equal(app.model.unavailable, true);
  data.source_status.ACP.status = "ok";
  b.emit("monitor-refresh"); b.requests.at(-1).respond(clone(data)); await settle();
  assert.doesNotMatch(treeText(list), /任务列表采集失败/); assert.equal(app.model.unavailable, false);
});

test("unchanged storage polling reuses rows, summary and zone options while metadata updates", async t => {
  const b = browser({ route: "storage" }), data = storageData(); b.load("storage.js");
  b.requests[0].respond(clone(data)); await settle();
  const body = b.nodes.get("storage-body"), zones = b.nodes.get("storage-zone"), summary = b.nodes.get("storage-summary");
  const before = [body.replacements, zones.replacements, summary.replacements, b.createdNodes()];
  for (let i = 1; i <= 3; i++) {
    b.tick(2000); assert.equal(b.requests.length, i + 1);
    b.requests.at(-1).respond({ ...clone(data), status: "partial", warnings: [`warning ${i}`], updated_at: `2026-10-06T00:00:0${i}Z` });
    await settle();
  }
  const changes = [body.replacements, zones.replacements, summary.replacements, b.createdNodes()].map((value, i) => value - before[i]);
  t.diagnostic(`3 unchanged 40-volume refreshes: table/options/summary replacements and created nodes = ${changes.join("/")}`);
  assert.deepEqual(changes, [0, 0, 0, 0]);
  assert.equal(b.nodes.get("storage-warning").textContent, "warning 3");
  assert.match(b.nodes.get("storage-status").textContent, /部分信息未获取/);
  assert.ok(b.nodes.get("storage-status").textContent.includes(new Date("2026-10-06T00:00:03Z").toLocaleString("zh-CN", { hour12: false })));
});

test("storage rows invalidate for mount states, visible fields and filters, but zone options only for zone changes", async () => {
  const b = browser({ route: "storage" }), data = storageData(); b.load("storage.js");
  data.volumes = data.volumes.slice(0, 2); b.requests[0].respond(clone(data)); await settle();
  const body = b.nodes.get("storage-body"), zones = b.nodes.get("storage-zone"), zoneBuilds = zones.replacements;
  assert.match(treeText(body), /暂无 charles 挂载记录/);
  data.mount_refreshing = true; await receiveRefresh(b, "storage-refresh", data);
  assert.match(treeText(body), /挂载记录正在后台读取/);
  data.mount_refreshing = false; data.mount_status.ACP = "error"; await receiveRefresh(b, "storage-refresh", data);
  assert.match(treeText(body), /挂载记录未完整读取/);
  data.volumes[0].mounts = [{ kind: "ACP", task_name: "mounted-job", subdir: "old", mount_path: "/mnt", task_state: "running", created_at: null }];
  data.volumes[0].used_bytes = 500; data.volumes[0].free_bytes = 500;
  data.volumes[0].used_percent = 50; data.volumes[0].state = "EXPIRED";
  await receiveRefresh(b, "storage-refresh", data);
  assert.match(treeText(body), /mounted-job/); assert.match(treeText(body), /EXPIRED/); assert.match(treeText(body), /已用 50.0%/);
  assert.equal(zones.replacements, zoneBuilds);
  zones.value = "zone-b"; zones.dispatchEvent({ type: "input" }); assert.equal(body.children.length, 1);
  await receiveRefresh(b, "storage-refresh", data); assert.equal(zones.value, "zone-b");
  data.volumes = [data.volumes[0]]; await receiveRefresh(b, "storage-refresh", data);
  assert.equal(zones.value, ""); assert.equal(zones.replacements, zoneBuilds + 1);
  b.document.getElementById("storage-search").value = "mounted-job";
  b.nodes.get("storage-search").dispatchEvent({ type: "input" }); assert.equal(body.children.length, 1);
  b.nodes.get("storage-search").value = "missing"; b.nodes.get("storage-search").dispatchEvent({ type: "input" });
  assert.match(treeText(body), /没有匹配的存储卷/);
  b.nodes.get("storage-search").value = ""; b.document.getElementById("storage-related").checked = true;
  data.volumes[0].mounts = []; data.mount_refreshing = true; await receiveRefresh(b, "storage-refresh", data);
  assert.match(treeText(body), /挂载记录正在后台读取，稍后补齐/);
  data.mount_refreshing = false; await receiveRefresh(b, "storage-refresh", data);
  assert.match(treeText(body), /没有匹配的存储卷/);
});

test("storage recovers its previous rows after a malformed response interrupts rendering", async () => {
  const b = browser({ route: "storage" }), data = storageData(); b.load("storage.js");
  b.requests[0].respond(clone(data)); await settle();
  const body = b.nodes.get("storage-body"), malformed = clone(data);
  malformed.volumes[0].mounts = null;
  await receiveRefresh(b, "storage-refresh", malformed);
  assert.equal(b.nodes.get("storage-warning").hidden, false);
  await receiveRefresh(b, "storage-refresh", data);
  assert.equal(body.children.length, data.volumes.length);
  assert.match(treeText(body), /Volume 39/);
  assert.equal(b.nodes.get("storage-warning").hidden, true);
});

test("history polling reuses rows while source status, loading, counts and warnings remain live", async t => {
  const b = browser({ route: "history" }), data = taskHistoryData(); b.load("task-history.js");
  b.requests[0].respond(clone(data)); await settle(); const body = b.nodes.get("task-history-body");
  const before = [body.replacements, b.createdNodes()];
  for (let i = 1; i <= 3; i++) {
    b.tick(2000); assert.equal(b.requests.length, i + 1);
    data.source_status.ACP = { status: "error", updated_at: `2026-10-06T00:00:0${i}Z` };
    data.filtered_stopped.ACP = i + 1;
    b.requests.at(-1).respond(clone(data)); await settle();
  }
  t.diagnostic(`3 unchanged 25-row history pages: table replacements/created nodes = ${body.replacements - before[0]}/${b.createdNodes() - before[1]}`);
  assert.equal(body.replacements, before[0]); assert.equal(b.createdNodes(), before[1]);
  assert.match(b.nodes.get("task-history-warning").textContent, /ACP 历史读取失败/);
  assert.match(b.nodes.get("task-history-status").textContent, /读取失败/);
  assert.match(b.nodes.get("task-history-excluded").textContent, /ACP 4 条/);
  data.loading = false; data.source_status.ACP.status = "ok";
  await receiveRefresh(b, "task-history-refresh", data);
  assert.equal(body.replacements, before[0]); assert.match(b.nodes.get("task-history-status").textContent, /历史读取完成/);
  assert.equal(b.nodes.get("task-history-warning").hidden, true);
  assert.equal([...b.timers.values()].filter(timer => timer.delay === 60000).length, 1);
});

test("history empty hints distinguish loading, source errors, filters and empty success", async () => {
  const b = browser({ route: "history" }), data = taskHistoryData(); data.tasks = []; b.load("task-history.js");
  b.requests[0].respond(clone(data)); await settle(); const body = b.nodes.get("task-history-body");
  assert.match(treeText(body), /正在读取云端历史任务/);
  data.loading = false; data.source_status.ACP.status = "error"; await receiveRefresh(b, "task-history-refresh", data);
  assert.match(treeText(body), /历史数据未完整获取/);
  data.source_status.ACP.status = "ok"; await receiveRefresh(b, "task-history-refresh", data);
  assert.match(treeText(body), /暂无符合条件的历史任务/);
  b.document.getElementById("task-history-search").value = "missing";
  data.tasks = taskHistoryData().tasks; await receiveRefresh(b, "task-history-refresh", data);
  assert.match(treeText(body), /没有匹配的历史任务/);
  b.nodes.get("task-history-reset").click(); assert.equal(body.children.length, 25);
});

test("history filters, pagination, visible task updates and restart payload remain current", async () => {
  const b = browser({ route: "history" }), data = taskHistoryData(); b.load("task-history.js");
  b.requests[0].respond(clone(data)); await settle(); const body = b.nodes.get("task-history-body");
  const restart = []; b.document.addEventListener("acp-restart", event => restart.push(event.detail));
  descendants(body).find(node => node.tagName === "button").click();
  assert.equal(restart[0].id, "ACP:history-0");
  b.nodes.get("task-history-next").click(); assert.equal(body.children.length, 15); assert.match(treeText(body), /Task 25/);
  b.nodes.get("task-history-prev").click();
  b.document.getElementById("task-history-size").value = "10"; b.nodes.get("task-history-size").dispatchEvent({ type: "input" });
  assert.equal(body.children.length, 10);
  data.tasks[0].name = "renamed"; data.tasks[0].status = "failed"; data.tasks[0].status_label = "已失败";
  data.tasks[0].duration_seconds = 200; data.tasks[0].url = "https://console.ms-sc-01.maoshanwangtech.com/new";
  await receiveRefresh(b, "task-history-refresh", data);
  assert.match(treeText(body), /renamed/); assert.match(treeText(body), /已失败/); assert.match(treeText(body), /3 分 20 秒/);
  descendants(body).find(node => node.tagName === "button").click(); assert.equal(restart.at(-1).name, "renamed");
  assert.equal(descendants(body).find(node => node.tagName === "a").href, data.tasks[0].url);
  b.document.getElementById("task-history-state").value = "failed"; b.nodes.get("task-history-state").dispatchEvent({ type: "input" });
  assert.equal(body.children.length, 1); assert.equal(b.nodes.get("history-failed").textContent, "1");
});

for (const [file, route, button, bodyId, warningId, statusId, makeData] of [
  ["storage.js", "storage", "storage-refresh", "storage-body", "storage-warning", "storage-status", storageData],
  ["task-history.js", "history", "task-history-refresh", "task-history-body", "task-history-warning", "task-history-status", taskHistoryData],
]) test(`${file}: failed reads and same-data recovery keep accurate errors without replacing good rows`, async () => {
  const b = browser({ route }), data = makeData(); b.load(file);
  b.requests[0].respond({ error: "permission denied" }, false); await settle();
  assert.match(treeText(b.nodes.get(bodyId)), /数据未获取/);
  await receiveRefresh(b, button, data);
  const body = b.nodes.get(bodyId), rows = body.replacements;
  await receiveRefresh(b, button, { error: "permission denied" }, false);
  assert.equal(body.replacements, rows); assert.match(b.nodes.get(warningId).textContent, /permission denied/);
  await receiveRefresh(b, button, data);
  assert.equal(body.replacements, rows); assert.equal(b.nodes.get(warningId).hidden, true);
  assert.doesNotMatch(b.nodes.get(statusId).textContent, /读取失败|旧数据/);
});

for (const [file, route, endpoint, data] of [
  ["storage.js", "storage", "/api/storage", { site: "maoshanwang", volumes: [], mount_status: {}, status: "ok" }],
  ["task-history.js", "history", "/api/task-history", { site: "maoshanwang", tasks: [], status: "ok" }],
  ["tokenhub.js", "overview", "/api/tokenhub/state", { sections: {}, connected: false, status: "disconnected" }],
]) {
  test(`${file}: hidden and inactive pages pause reads; visibility resumes once`, async () => {
    const b = browser({ route, hidden: true }); b.load(file);
    assert.equal(b.requests.length, 0, "hidden initialization must not fetch");
    b.hide(false); assert.equal(b.requests.length, 1); assert.equal(b.requests[0].url, endpoint);
    b.hide(true); assert.equal(b.requests[0].options.signal.aborted, true);
    b.requests[0].respond(data); await settle();
    assert.equal([...b.timers.values()].filter(timer => timer.delay === 60000).length, 0);
    b.hide(false); assert.equal(b.requests.length, 2);
    b.requests[1].respond(data); await settle();
    assert.equal([...b.timers.values()].filter(timer => timer.delay === 60000).length, 1);
    b.route("tasks"); b.tick(60000); assert.equal(b.requests.length, 2);
    b.route(route); assert.equal(b.requests.length, 3);
  });
}

function historyPage(options = {}) {
  const b = browser({ route: "tasks", ...options });
  const app = b.load("app.js", "model, loadHistory, renderDetail");
  app.model.selected = "ACP:one"; app.model.snapshot = { updated_at: "r1", tasks: [{ id: "ACP:one", kind: "ACP" }] };
  app.model.chart = b.window.echarts.init();
  return { b, app, reads: () => b.requests.filter(request => request.url.startsWith("/api/history")) };
}
const curve = value => ({ series: [{ metric: "cpu", points: [[60, value]] }] });

test("slow history reads do not hold state polling or duplicate unchanged history", async () => {
  const b = browser({ route: "tasks" });
  b.load("app.js");
  const state = runningData(2);
  const states = () => b.requests.filter(request => request.url === "/api/state");
  const histories = () => b.requests.filter(request => request.url.startsWith("/api/history"));
  states()[0].respond(clone(state)); await settle();
  assert.equal(histories().length, 1);
  b.tick(10000);
  assert.equal(states().length, 2, "pending history must not block the next state read");
  states()[1].respond(clone(state)); await settle();
  assert.equal(histories().length, 1);
  assert.equal(histories()[0].options.signal.aborted, false);
  b.tick(10000);
  states()[2].respond({ ...clone(state), updated_at: "2026-10-06T00:01:00Z" }); await settle();
  assert.equal(histories().length, 2);
  assert.equal(histories()[0].options.signal.aborted, true);
  histories()[1].respond(curve(90)); await settle();
  histories()[0].respond(curve(10)); await settle();
  assert.equal(b.charts.at(-1).options.at(-1).series[0].data[0][1], 90);
});

for (const file of ["storage.js", "task-history.js", "tokenhub.js", "utilization-queue.js"]) {
  test(`${file}: bulk date formatting preserves text without constructing per-row locale formatters`, t => {
    const b = browser({ route: "other" }), subject = b.load(file, "date");
    const options = { hour12: false, ...(file === "tokenhub.js" ? { timeZone: "Asia/Shanghai" } : {}) };
    for (const value of [null, "", "invalid"]) assert.equal(subject.date(value), "—");
    const samples = ["2026-10-06T00:00:00Z", "2026-10-06T16:00:00Z", "2026-01-01T01:02:03+08:00"];
    const before = b.dateLocalizations();
    for (let i = 0; i < 100; i++) {
      const value = samples[i % samples.length];
      assert.equal(subject.date(value), new Date(value).toLocaleString("zh-CN", options));
    }
    t.diagnostic(`100 dates: ${b.dateLocalizations() - before} per-value locale formatters`);
    assert.equal(b.dateLocalizations(), before);
  });
}

test("reused billing and storage number formatters preserve zero, precision and unknown values", () => {
  const tokenhub = browser({ route: "other" }).load("tokenhub.js", "money, count, usageFormat");
  for (const value of [0, .0001, .123456, 99999.12345, -1.23]) {
    assert.equal(tokenhub.money(value), "$" + value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 }));
    assert.equal(tokenhub.count(value), value.toLocaleString("zh-CN"));
    assert.equal(tokenhub.usageFormat.format(value), value.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 }));
  }
  for (const value of [null, undefined, NaN, Infinity, true, "0"]) {
    assert.equal(tokenhub.money(value), "—"); assert.equal(tokenhub.count(value), "—");
  }
  const storage = browser({ route: "other" }).load("storage.js", "amount");
  for (const [value, expected] of [[0, "0 B"], [999, "999 B"], [1234567, "1.23 MB"], [1e12, "1 TB"], [-1, "—"], [null, "—"]]) {
    assert.equal(storage.amount(value), expected);
  }
});

if (process.env.MONITOR_FORMAT_BENCHMARK === "1") test("formatting benchmark (informational, real page helpers)", t => {
  const subject = browser({ route: "other" }).load("tokenhub.js", "date, money");
  const dates = Array.from({ length: 2000 }, (_, i) => new Date(1791244800000 + i * 60000).toISOString());
  const costs = Array.from({ length: 2000 }, (_, i) => i / 13);
  const median = values => values.sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const measure = (values, format) => {
    values.forEach(format);
    return median(Array.from({ length: 7 }, () => {
      const start = process.hrtime.bigint(); values.forEach(format);
      return Number(process.hrtime.bigint() - start) / 1e6;
    })).toFixed(3);
  };
  const oldDate = value => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
  const oldMoney = value => "$" + value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  t.diagnostic(JSON.stringify({ values: 2000, median_ms: {
    old_dates: measure(dates, oldDate), reused_dates: measure(dates, subject.date),
    old_money: measure(costs, oldMoney), reused_money: measure(costs, subject.money),
  } }));
});

test("history coalesces pending/completed keys, refreshes revisions, and cancels obsolete reads", async () => {
  const { b, app, reads } = historyPage();
  app.loadHistory(); app.loadHistory(); app.loadHistory();
  assert.equal(reads().length, 1, "same key must share a pending read");
  reads()[0].respond(curve(10)); await settle(); app.loadHistory();
  assert.equal(reads().length, 1, "unchanged successful snapshot must reuse chart");
  app.model.snapshot.updated_at = "r2"; app.loadHistory(); assert.equal(reads().length, 2);
  app.model.selected = "ACP:two"; app.loadHistory(); assert.equal(reads().length, 3);
  assert.equal(reads()[1].options.signal.aborted, true);
  reads()[2].respond(curve(30)); await settle(); reads()[1].respond(curve(20)); await settle();
  assert.equal(app.model.chart.options.at(-1).series[0].data[0][1], 30, "late cancelled result must not overwrite selection");
  app.model.hours = 24; app.loadHistory(); assert.equal(reads().length, 4);
  b.route("storage"); assert.equal(reads()[3].options.signal.aborted, true);
  b.hide(true); b.route("tasks"); assert.equal(reads().length, 4);
  b.hide(false); assert.equal(reads().length, 5, "visibility restores the cancelled read");
});

test("history read failure can retry the same key", async () => {
  const { app, reads } = historyPage(); app.loadHistory();
  reads()[0].reject(new Error("offline")); await settle(); app.loadHistory();
  assert.equal(reads().length, 2);
});

test("entering monitor initializes the chart when the first task was selected on logs", async () => {
  const { b, app, reads } = historyPage(); app.model.chart = null;
  b.document.getElementById("detail-monitor").hidden = true; app.renderDetail(); assert.equal(app.model.chart, null);
  b.nodes.get("detail-monitor").hidden = false; b.emit("monitor-detail-tab", "monitor");
  assert.ok(app.model.chart, "monitor tab must initialize its chart before requesting history");
  reads()[0].respond(curve(10)); await settle();
  assert.equal(app.model.chart.options.at(-1).series[0].data[0][1], 10);
});

test("foregrounding a task page first loaded in the background initializes its unchanged snapshot chart", async () => {
  const b = browser({ route: "tasks", hidden: true }); const app = b.load("app.js", "model");
  const snapshot = { updated_at: "2026-10-06T00:00:00Z", status: "ok", connected: true,
    tasks: [{ id: "ACP:one", kind: "ACP" }], clusters: [],
    source_status: { ACP: { status: "ok" }, CCI: { status: "ok" } } };
  b.requests[0].respond(snapshot); await settle();
  assert.equal(app.model.selected, "ACP:one"); assert.equal(app.model.chart, null);
  assert.equal(b.requests.filter(request => request.url.startsWith("/api/history")).length, 0);
  b.hide(false);
  b.requests.filter(request => request.url === "/api/state").at(-1).respond(snapshot); await settle();
  assert.ok(app.model.chart, "visibility recovery must initialize even when poll receives the same snapshot");
  const reads = b.requests.filter(request => request.url.startsWith("/api/history"));
  assert.equal(reads.length, 1); reads[0].respond(curve(42)); await settle();
  assert.equal(app.model.chart.options.at(-1).series[0].data[0][1], 42);
});

test("unrendered history is not cached when the chart component is unavailable", async () => {
  const { b, app, reads } = historyPage(); app.model.chart = null;
  const echarts = b.window.echarts; b.window.echarts = undefined;
  app.loadHistory(); reads()[0].respond(curve(10)); await settle();
  assert.equal(Boolean(app.model.historyKey), false, "a response is not a successfully rendered chart");
  b.window.echarts = echarts; app.renderDetail(); app.loadHistory();
  assert.equal(reads().length, 2); reads()[1].respond(curve(20)); await settle();
  assert.equal(app.model.chart.options.at(-1).series[0].data[0][1], 20);
});

test("desktop/mobile transitions initialize visible charts and cancel hidden curves", () => {
  const { b, app, reads } = historyPage({ mobile: true }); app.model.chart = null; b.load("mobile.js");
  app.renderDetail(); app.loadHistory(); assert.equal(app.model.chart, null); assert.equal(reads().length, 0);
  b.setMobile(false); assert.ok(app.model.chart); assert.equal(reads().length, 1);
  b.setMobile(true); assert.equal(reads()[0].options.signal.aborted, true);
  b.setMobile(false); assert.equal(reads().length, 2);
});

test("reselecting the current monitor tab shares the current history request", () => {
  const { b, app, reads } = historyPage(); app.loadHistory();
  b.emit("monitor-detail-tab", "monitor"); b.emit("monitor-detail-tab", "monitor");
  assert.equal(reads().length, 1);
  assert.equal(reads()[0].options.signal.aborted, false);
});

test("returning to the last completed curve clears an interrupted loading message without another read", async () => {
  const { b, app, reads } = historyPage(); app.loadHistory(); reads()[0].respond(curve(10)); await settle();
  app.model.selected = "ACP:two"; app.loadHistory(); b.route("storage");
  app.model.selected = "ACP:one"; b.route("tasks");
  assert.equal(reads().length, 2);
  assert.equal(b.nodes.get("chart-message").hidden, true);
});

test("TokenHub visibility pauses polling without discarding an active group save result", async () => {
  const b = browser({ route: "tokenhub" }); const hub = b.load("tokenhub.js", "openGroupDialog");
  hub.openGroupDialog({ id: 1, name: "key" });
  b.requests.find(request => request.url.startsWith("/api/tokenhub/key-groups")).respond({
    account_id: 1, key: { id: 1, group_id: 1 }, items: [{ id: 1 }, { id: 2 }],
  }); await settle();
  b.nodes.get("tokenhub-group-select").value = "2";
  b.nodes.get("tokenhub-group-form").dispatchEvent({ type: "submit", preventDefault() {} });
  const saving = b.requests.find(request => request.options.method === "POST");
  assert.ok(saving);
  b.hide(true);
  assert.equal(b.nodes.get("tokenhub-group-dialog").open, true, "a background transition must preserve the in-progress dialog");
  assert.equal(saving.options.signal.aborted, false);
  saving.reject(new Error("write result uncertain")); await settle();
  assert.match(b.nodes.get("tokenhub-group-status").textContent, /write result uncertain/);
});

test("mobile list does not load curves; opening and closing detail controls the read", () => {
  const { b, app, reads } = historyPage({ mobile: true }); b.load("mobile.js");
  app.renderDetail(); app.loadHistory(); assert.equal(reads().length, 0);
  b.document.dispatchEvent({ type: "click", target: { closest: selector => selector === ".task-card, .overview-task" } });
  assert.equal(reads().length, 1);
  // The generated back button is prepended to the existing detail panel.
  b.nodes.get("detail-panel").children.find(node => node.id === "mobile-task-back").click();
  assert.equal(reads()[0].options.signal.aborted, true);
});

test("waiting table renders only visible changes and still invalidates expired action permissions", () => {
  const b = browser({ route: "overview" }); const acp = b.load("acp.js", "state, renderWaiting");
  const table = b.nodes.get("waiting-body") || b.document.getElementById("waiting-body");
  const timestamp = new b.context.Date(b.context.Date.now()).toISOString();
  const snapshot = { connected: true, status: "ok", updated_at: timestamp,
    source_status: Object.fromEntries(["ACP", "CCI_queue", "ACP_queue"].map(key => [key, { status: "ok", updated_at: timestamp }])),
    waiting_tasks: [{ id: "ACP:queued", kind: "ACP", name: "charles-one", can_stop: true, state: "WAITING" }] };
  b.emit("monitor-snapshot", { snapshot, unavailable: false }); b.tick(10000);
  assert.equal(table.replacements, 0, "hidden queue must not construct rows");
  b.route("waiting"); assert.equal(table.replacements, 1);
  const buttons = node => [...(node.tagName === "button" ? [node] : []), ...node.children.flatMap(buttons)];
  assert.equal(buttons(table)[0].disabled, false);
  b.emit("monitor-snapshot", { snapshot, unavailable: false }); b.tick(10000);
  assert.equal(table.replacements, 1, "same visible queue must reuse rows");
  acp.state.stopping = true; acp.renderWaiting(); assert.equal(table.replacements, 2);
  assert.equal(buttons(table)[0].disabled, true);
  acp.state.stopping = false;
  b.advance(181000); b.tick(10000); assert.equal(table.replacements, 3, "freshness expiration must still update rows");
  assert.equal(buttons(table)[0].disabled, true, "stale data must disable stop even when no action is pending");
  b.hide(true); snapshot.waiting_tasks[0].name = "charles-two"; b.emit("monitor-snapshot", { snapshot, unavailable: false });
  assert.equal(table.replacements, 3);
  b.hide(false); assert.equal(table.replacements, 4, "visible queue must catch up immediately");
});

test("chart preparation scales linearly with worker series and preserves labels/styles", t => {
  const b = browser({ route: "tasks" }); const app = b.load("app.js", "model, renderChart");
  app.model.selected = "ACP:one"; app.model.chart = b.window.echarts.init();
  let reads = 0;
  const metrics = ["cpu", "gpu", "memory", "gpu_memory"];
  const series = Array.from({ length: 400 }, (_, index) => ({
    get metric() { reads++; return metrics[index % 4]; },
    label: `${metrics[index % 4]} · job-worker-${Math.floor(index / 4)}`,
    points: [[60, 10], [120, null], [180, 30]],
  }));
  app.renderChart(series);
  t.diagnostic(`${series.length} series required ${reads} metric reads`);
  assert.ok(reads <= series.length * 16, "chart setup must avoid scanning earlier series for every worker");
  const option = app.model.chart.options.at(-1);
  assert.equal(option.series.length, 400);
  assert.equal(option.series[0].lineStyle.type, "solid");
  assert.equal(option.series[4].lineStyle.type, "dashed");
  assert.equal(option.series[8].lineStyle.type, "dotted");
  assert.equal(option.series[12].lineStyle.type, "solid");
  assert.deepEqual(Array.from(option.series[0].data, point => Array.from(point)), [[60000,10],[120000,null],[180000,30]]);
  assert.equal(option.series[0].connectNulls, false);
  app.renderChart([
    { metric: "gpu", label: "GPU · first-worker-0", points: [[60, 1]] },
    { metric: "gpu", label: "GPU · second-worker-0", points: [[60, 2]] },
    { metric: "cpu", label: "CPU · unique-worker-1", points: [[60, 3]] },
  ]);
  const labels = app.model.chart.options.at(-1).legend.formatter;
  assert.equal(labels("GPU 利用率 · first-worker-0"), "GPU 利用率 · first-worker-0");
  assert.equal(labels("GPU 利用率 · second-worker-0"), "GPU 利用率 · second-worker-0");
  assert.equal(labels("CPU · unique-worker-1"), "CPU · worker-1");
});

test("a resize burst redraws the chart once per animation frame using the current selection", () => {
  const b = browser({ route: "tasks" }); const app = b.load("app.js", "model, renderChart");
  const frames = []; b.context.requestAnimationFrame = fn => { frames.push(fn); return frames.length; };
  app.model.selected = "ACP:one"; app.model.chart = b.window.echarts.init();
  app.renderChart(curve(10).series);
  let resizes = 0; app.model.chart.resize = () => resizes++;
  for (let i = 0; i < 100; i++) b.window.dispatchEvent({ type: "resize" });
  assert.equal(app.model.chart.options.length, 1, "resize events must not each rebuild the series synchronously");
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.equal(resizes, 1); assert.equal(app.model.chart.options.length, 2);
  b.window.dispatchEvent({ type: "resize" });
  app.model.selected = "ACP:two";
  frames.shift()();
  assert.equal(resizes, 2); assert.equal(app.model.chart.options.length, 2, "old task curves must not redraw after selection changes");
  b.window.dispatchEvent({ type: "resize" });
  app.model.chart = null;
  frames.shift()();
  assert.equal(resizes, 2);
});

for (const [status, message] of [["reconnecting", "正在重连，完成后会自动采集。"], ["collecting", "正在采集，完成后页面会自动更新。"], ["started", "刷新已启动，完成后页面会自动更新。"]]) {
  test(`refresh notice uses server progress: ${status}`, async () => {
    const b = browser(), app = b.load("app.js", "model, renderStatus");
    b.requests.find(request => request.url === "/api/state").respond(runningData());
    await settle();
    b.nodes.get("refresh-button").click();
    const request = b.requests.find(request => request.url === "/api/refresh");
    assert.equal(request.options.method, "POST");
    request.respond({ status, started: status === "started", message });
    await settle();
    assert.equal(b.nodes.get("action-message").textContent, message);
    b.requests.at(-1).respond({ ...runningData(), connecting: status === "reconnecting", status: "collecting" });
    await settle();
    assert.equal(b.nodes.get("status-title").textContent, status === "reconnecting" ? "正在重连" : "正在采集");
    assert.equal(b.nodes.get("refresh-button").disabled, false);
  });
}

test("verified rename preserves selected task and curves when its old name matched search", () => {
  const { b, app } = runningPage(2, "tasks");
  app.model.snapshot.tasks.forEach((task, i) => { task.name = `charles_old_${i}`; });
  app.model.search = "old"; app.renderTasks(); app.renderDetail();
  const id = app.model.selected;
  app.model.chartTaskId = id;
  const reads = b.requests.length;
  b.emit("monitor-task-renamed", { site: "maoshanwang", task_id: id, name: "charles_new", verified_at: "2026-10-06T00:01:00Z" });
  assert.equal(app.model.selected, id);
  assert.equal(app.model.chartTaskId, id);
  assert.equal(b.nodes.get("detail-title").textContent, "charles_new");
  assert.equal(app.model.search, "");
  assert.equal(b.requests.length, reads, "rename must not reload chart data");
});

test("verified names survive stale source snapshots and yield to fresh authoritative names", async () => {
  const b = browser({ route: "tasks" }), app = b.load("app.js", "model");
  const data = runningData(2), states = () => b.requests.filter(r => r.url === "/api/state");
  states()[0].respond(clone(data)); await settle();
  b.emit("monitor-task-renamed", { site: "maoshanwang", task_id: "ACP:running-0", name: "charles_new", verified_at: "2026-10-06T00:01:00Z" });
  b.tick(10000);
  const failed = clone(data);
  failed.updated_at = "2026-10-06T00:02:00Z";
  failed.source_status.ACP = {status:"error",updated_at:data.updated_at};
  states().at(-1).respond(failed); await settle();
  assert.equal(b.nodes.get("detail-title").textContent, "charles_new");
  b.tick(10000);
  const fresh = clone(data);
  fresh.updated_at = "2026-10-06T00:03:00Z";
  fresh.source_status.ACP = {status:"ok",updated_at:fresh.updated_at};
  fresh.tasks[0].name = "charles_changed_in_console";
  states().at(-1).respond(fresh); await settle();
  assert.equal(b.nodes.get("detail-title").textContent, "charles_changed_in_console");
  b.tick(10000);
  const failedAgain = clone(fresh);
  failedAgain.updated_at = "2026-10-06T00:04:00Z";
  failedAgain.source_status.ACP.status = "error";
  states().at(-1).respond(failedAgain); await settle();
  assert.equal(b.nodes.get("detail-title").textContent, "charles_changed_in_console", "a retired task-source override must not revive while queue remains stale");
  b.emit("monitor-task-renamed", {site:"sensecore",task_id:"ACP:running-0",name:"wrong_site",verified_at:fresh.updated_at});
  assert.equal(b.nodes.get("detail-title").textContent, "charles_changed_in_console");
});
