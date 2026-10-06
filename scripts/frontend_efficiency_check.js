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
  let timerId = 0, now = Date.parse("2026-10-06T00:00:00Z");
  class Target {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(fn); }
    removeEventListener(name, fn) { this.listeners.set(name, (this.listeners.get(name) || []).filter(item => item !== fn)); }
    dispatchEvent(event) { for (const fn of this.listeners.get(event.type) || []) fn(event); }
  }
  class Node extends Target {
    constructor(tag = "div") {
      super(); this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.value = "";
      this.textContent = ""; this.hidden = false; this.open = false; this.checked = false; this.replacements = 0;
      this.firstChild = { textContent: "" }; this.isConnected = true;
      const classes = new Set();
      this.classList = { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
        toggle(name, on = !classes.has(name)) { if (on) classes.add(name); else classes.delete(name); } };
    }
    append(...items) { this.children.push(...items); }
    prepend(...items) { this.children.unshift(...items); }
    replaceChildren(...items) { this.replacements++; this.children = items; }
    get options() { return this.children; }
    setAttribute(name, value) { this[name] = value; }
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
  document.createDocumentFragment = () => new Node(); document.querySelectorAll = () => [];
  document.querySelector = selector => selector === ".sidebar" ? new Node() : null;
  document.getElementById("tasks-view").hidden = route !== "tasks";
  document.getElementById("task-history-size").value = "25";
  const window = new Target(), location = { hash: `#${route}` };
  const fetch = (url, options = {}) => new Promise((resolve, reject) => {
    requests.push({ url, options, reject, respond(data, ok = true) { resolve({ ok, status: ok ? 200 : 500, json: async () => data }); } });
  });
  window.MonitorSite = { id: "maoshanwang", key: key => key, fetch };
  window.matchMedia = () => {
    const query = new Target(); Object.defineProperty(query, "matches", { get: () => mobile });
    mediaQueries.push(query); return query;
  };
  window.echarts = { init() { const chart = { options: [], resize() {}, clear() {}, getWidth: () => 800, getHeight: () => 600,
    setOption(option) { this.options.push(option); } }; charts.push(chart); return chart; } };
  class Clock extends Date { static now() { return now; } }
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
  return { nodes, requests, timers, charts, context, document, window, location, load, emit,
    hide(value) { document.hidden = value; emit("visibilitychange"); },
    setMobile(value) { mobile = value; for (const query of [...mediaQueries]) query.dispatchEvent({ type: "change" }); },
    route(value) { location.hash = `#${value}`; document.getElementById("tasks-view").hidden = value !== "tasks"; emit("workspace-page", value); },
    advance(ms) { now += ms; },
    tick(delay) { for (const [id, timer] of [...timers]) if (timer.delay === delay) { if (!timer.interval) timers.delete(id); timer.fn(); } } };
}
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

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
  app.loadHistory(); assert.equal(reads().length, 0);
  b.document.dispatchEvent({ type: "click", target: { closest: selector => selector === ".task-card, .overview-task" } });
  assert.equal(reads().length, 1);
  // The generated back button is prepended to the existing detail panel.
  b.nodes.get("detail-panel").children[0].click();
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
