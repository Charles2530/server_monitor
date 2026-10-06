"use strict";

// Run locally: node --test scripts/logs_efficiency_check.js
// Executes logs.js itself; no browser, remote logs, or permission cache.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function page() {
  const nodes = new Map(), requests = [], timers = new Map();
  let timerId = 0;
  class Target {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, listener) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(listener);
    }
    dispatchEvent(event) { for (const listener of this.listeners.get(event.type) || []) listener(event); }
  }
  class Node extends Target {
    constructor() {
      super(); this._text = ""; this._scroll = 0; this.value = ""; this.children = [];
      this.hidden = false; this.checked = false; this.textWrites = 0; this.writtenBytes = 0; this.scrollWrites = 0;
      this.scrollHeight = 2000; this.selection = null; this.classList = { toggle() {} };
    }
    get textContent() { return this._text; }
    set textContent(value) {
      this._text = value; this.textWrites++; this.writtenBytes += Buffer.byteLength(value);
      this.selection = null; // Replacing a text node invalidates a range into that node.
    }
    get scrollTop() { return this._scroll; }
    set scrollTop(value) { this._scroll = value; this.scrollWrites++; }
    replaceChildren(...children) { this.children = children; this.value = children[0]?.value || ""; }
    setAttribute(name, value) { this[name] = value; }
    focus() {}
    click() { this.dispatchEvent({ type: "click" }); }
  }
  const document = new Target(); document.hidden = false;
  document.getElementById = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  document.createElement = () => new Node();
  for (const id of ["logs-auto", "logs-follow"]) document.getElementById(id).checked = true;
  const fetch = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, reject,
    respond(data, ok = true) { resolve({ ok, json: async () => data }); } }));
  const context = vm.createContext({ document, window: { MonitorSite: { fetch } }, fetch, AbortController, URLSearchParams,
    Date, TypeError, console, navigator: {},
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "static", "logs.js"), "utf8"), context, { filename: "logs.js" });
  return { nodes, requests, timers, document, node: id => document.getElementById(id),
    emit(type, detail) { document.dispatchEvent({ type, detail }); },
    tick() { for (const [id, timer] of [...timers]) if (timer.delay === 10000) { timers.delete(id); timer.fn(); } } };
}
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const log = (text, extra = {}) => ({ task_id: "ACP:one", worker: "worker-0", container: "main", text,
  entries: 1, updated_at: "2026-10-06T00:00:00Z", ...extra });

async function openLogs(text) {
  const b = page(); b.emit("monitor-selection", { id: "ACP:one" }); b.node("detail-tab-logs").click();
  assert.equal(b.requests.length, 1);
  b.requests[0].respond({ task_id: "ACP:one", workers: [{ name: "worker-0", containers: ["main", "other"] },
    { name: "worker-1", containers: ["main"] }] }); await settle();
  assert.equal(b.requests.length, 2); b.requests[1].respond(log(text)); await settle();
  return b;
}

test("identical 1 MiB snapshots preserve text/selection/scroll while fresh metadata and remote checks continue", async t => {
  const text = "x".repeat(1024 * 1024), b = await openLogs(text), output = b.node("logs-output");
  const baseline = { writes: output.textWrites, bytes: output.writtenBytes, scrolls: output.scrollWrites };
  const selection = { start: 4, end: 12 }; output.selection = selection;
  for (let i = 1; i <= 3; i++) {
    b.tick(); assert.equal(b.requests.length, 2 + i, "each auto refresh must still check the server");
    b.requests.at(-1).respond(log(text, { entries: 100 + i, warning: `warning-${i}`, truncated: true,
      updated_at: `2026-10-06T00:00:0${i}Z` })); await settle();
  }
  t.diagnostic(`3 unchanged 1 MiB responses: ${output.textWrites - baseline.writes} text rewrites, ${output.writtenBytes - baseline.bytes} bytes assigned, ${output.scrollWrites - baseline.scrolls} forced scrolls`);
  assert.equal(output.textWrites, baseline.writes);
  assert.equal(output.writtenBytes, baseline.bytes);
  assert.equal(output.scrollWrites, baseline.scrolls);
  assert.equal(output.selection, selection);
  assert.match(b.node("logs-status").textContent, /最新 103 条/);
  assert.match(b.node("logs-status").textContent, /已截断/);
  assert.ok(b.node("logs-status").textContent.includes(new Date("2026-10-06T00:00:03Z").toLocaleString("zh-CN", { hour12: false })));
  assert.equal(b.node("logs-message").textContent, "warning-3");
});

test("reselecting active logs by mouse and keyboard does not abort or duplicate an in-flight read", async t => {
  const b = await openLogs("first"); b.tick(); const pending = b.requests.at(-1), before = b.requests.length;
  for (let i = 0; i < 3; i++) b.node("detail-tab-logs").click();
  b.node("detail-tab-logs").dispatchEvent({ type: "keydown", key: "End", preventDefault() {} });
  t.diagnostic(`4 selections of current logs tab: ${b.requests.length - before} additional requests; original aborted=${pending.options.signal.aborted}`);
  assert.equal(b.requests.length, before); assert.equal(pending.options.signal.aborted, false);
  pending.respond(log("still current")); await settle();
  assert.equal(b.node("logs-output").textContent, "still current");
  assert.equal([...b.timers.values()].filter(timer => timer.delay === 10000).length, 1);
});

test("changed logs update once and follow behavior remains explicit", async () => {
  const b = await openLogs("first"), output = b.node("logs-output");
  b.node("logs-follow").checked = false; output.scrollTop = 37;
  const writes = output.textWrites, scrolls = output.scrollWrites;
  b.tick(); b.requests.at(-1).respond(log("new content")); await settle();
  assert.equal(output.textContent, "new content"); assert.equal(output.textWrites, writes + 1);
  assert.equal(output.scrollTop, 37); assert.equal(output.scrollWrites, scrolls);
  b.node("logs-follow").checked = true; b.node("logs-follow").dispatchEvent({ type: "change" });
  assert.equal(output.scrollTop, output.scrollHeight);
  const afterToggle = output.scrollWrites;
  b.tick(); b.requests.at(-1).respond(log("another line")); await settle();
  assert.equal(output.scrollWrites, afterToggle + 1);
});

test("unchanged visible tail skips rewriting even if the discarded prefix changed", async () => {
  const tail = "z".repeat(1024 * 1024), b = await openLogs("old prefix" + tail), output = b.node("logs-output");
  const writes = output.textWrites;
  b.tick(); b.requests.at(-1).respond(log("new prefix" + tail)); await settle();
  assert.equal(output.textContent, tail); assert.equal(output.textWrites, writes);
  assert.match(b.node("logs-status").textContent, /已截断/);
});

for (const change of ["task", "worker", "container", "tab", "hidden", "page"]) {
  test(`${change} change still cancels reads and ignores their late response`, async () => {
    const b = await openLogs("original"); b.tick(); const old = b.requests.at(-1);
    if (change === "task") b.emit("monitor-selection", { id: "ACP:two" });
    if (change === "worker") { b.node("logs-worker").value = "worker-1"; b.node("logs-worker").dispatchEvent({ type: "change" }); }
    if (change === "container") { b.node("logs-container").value = "other"; b.node("logs-container").dispatchEvent({ type: "change" }); }
    if (change === "tab") b.node("detail-tab-monitor").click();
    if (change === "hidden") { b.document.hidden = true; b.emit("visibilitychange"); }
    if (change === "page") { b.node("tasks-view").hidden = true; b.emit("workspace-page", "storage"); }
    assert.equal(old.options.signal.aborted, true);
    const expected = b.node("logs-output").textContent;
    old.respond(log("must not appear")); await settle();
    assert.equal(b.node("logs-output").textContent, expected);
    if (["tab", "hidden", "page"].includes(change)) assert.equal([...b.timers.values()].filter(timer => timer.delay === 10000).length, 0);
  });
}

test("fresh permission errors retain old logs and show an explicit stale/error notice", async () => {
  const b = await openLogs("old lines"), output = b.node("logs-output"), writes = output.textWrites;
  b.tick(); b.requests.at(-1).respond({ code: "auth_required" }, false); await settle();
  assert.equal(output.textContent, "old lines"); assert.equal(output.textWrites, writes);
  assert.match(b.node("logs-message").textContent, /云会话已失效或没有日志权限/);
  assert.match(b.node("logs-status").textContent, /保留旧日志/);
  b.node("logs-refresh").click(); assert.equal(b.requests.length, 4, "manual retry must reach the server");
});
