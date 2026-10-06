"use strict";

// No browser or cloud calls. Run: node --test scripts/terminal_efficiency_check.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function terminal({ automatic = true } = {}) {
  const nodes = new Map(), writes = [], pendingWrites = [], sockets = [], requests = [], events = [];
  let timer = 0;
  const node = () => ({ textContent: "", dataset: {}, hidden: false, disabled: false, value: "", open: false,
    classList: { add() {}, remove() {} }, addEventListener() {}, replaceChildren() {} });
  const document = { title: "", hidden: false, body: node(), addEventListener() {}, querySelectorAll: () => [],
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); } };
  class Socket {
    static OPEN = 1;
    constructor() { this.readyState = 1; this.sent = []; sockets.push(this); }
    send(value) { this.sent.push(JSON.parse(value)); events.push("socket:" + JSON.parse(value).type); }
    close() { this.readyState = 3; events.push("socket:close"); }
    receive(data) { this.onmessage({ data: JSON.stringify(data) }); }
  }
  const term = { options: {}, write(bytes, callback) {
    writes.push(Buffer.from(bytes)); events.push("write");
    const complete = () => { events.push("rendered"); callback(); };
    if (automatic) queueMicrotask(complete); else pendingWrites.push(complete);
  } };
  const context = vm.createContext({ document, URL, URLSearchParams, Uint8Array, AbortController, Date, Promise,
    atob: value => atob(value), WebSocket: Socket,
    location: { href: "http://localhost/terminal?task_id=ACP:one", protocol: "http:", host: "localhost" },
    window: { addEventListener() {}, MonitorSite: { id: "maoshanwang", async fetch(url, options) {
      requests.push({ url, options }); events.push("request:" + url); return { ok: true, json: async () => ({}) };
    } } },
    TerminalMobile: class { constructor() { this.query = { matches: false }; } updateControls() {} },
    setTimeout: () => ++timer, clearTimeout() {}, setInterval: () => ++timer, clearInterval() {}, console });
  const source = fs.readFileSync(path.join(__dirname, "..", "static", "terminal.js"), "utf8");
  // Inject access to the real closure; skip only automatic startup/target discovery.
  const exposed = source.replace(/  load\(\);\s*\}\)\(\);\s*$/, `
    globalThis.subject = {
      renderOutput, useSocket, drain: () => outputQueue,
      configure(output) { term = output; generation = 7; cursor = 0; session = "test-session"; alive = true; },
      cancel() { generation++; },
      state: () => ({ generation, cursor, session, alive })
    };
  })();`);
  assert.notEqual(exposed, source, "test hook must replace only terminal startup");
  vm.runInContext(exposed, context, { filename: "terminal.js" });
  const subject = context.subject; subject.configure(term);
  return { subject, writes, pendingWrites, sockets, requests, events, nodes,
    async connect() {
      const connecting = subject.useSocket(7), socket = sockets.at(-1);
      socket.receive({ type: "ready", sequence: 0 }); await connecting; return socket;
    } };
}
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const output = (chunks, cursor = 1, extra = {}) => ({ type: "output", chunks: chunks.map(bytes => Buffer.from(bytes).toString("base64")), cursor, ...extra });
const acks = socket => socket.sent.filter(message => message.type === "output-ack");

test("4096 small chunks share one bounded render callback", async t => {
  const b = terminal(), chunks = Array.from({ length: 4096 }, () => Buffer.from("0123456789abcdef"));
  await b.subject.renderOutput(output(chunks, 4096), 7);
  t.diagnostic(`${chunks.length} chunks / 65536 bytes used ${b.writes.length} term.write callbacks`);
  assert.equal(b.writes.length, 1, "one 64 KiB response should need one xterm callback");
  assert.deepEqual(Buffer.concat(b.writes), Buffer.concat(chunks));
  assert.equal(b.subject.state().cursor, 4096);
});

test("large chunks are bounded; split UTF-8, ANSI and arbitrary bytes retain exact order", async () => {
  const b = terminal();
  const utf8 = Buffer.concat([Buffer.alloc(65535, 0x61), Buffer.from("中文😀\x1b[31m红\x1b[0m\r\n".repeat(9000))]);
  const chunks = [utf8.subarray(0, 65536), utf8.subarray(65536, 65537), utf8.subarray(65537)];
  await b.subject.renderOutput(output(chunks), 7);
  assert.ok(b.writes.every(bytes => bytes.length <= 65536), "every render batch must be bounded");
  assert.deepEqual(Buffer.concat(b.writes), utf8);
  const decoder = new TextDecoder();
  assert.equal(b.writes.map(bytes => decoder.decode(bytes, { stream: true })).join("") + decoder.decode(), utf8.toString("utf8"));
  const allBytes = Buffer.from(Array.from({ length: 256 }, (_, value) => value));
  const binary = terminal(); await binary.subject.renderOutput(output([...allBytes].map(value => [value])), 7);
  assert.deepEqual(Buffer.concat(binary.writes), allBytes);
});

test("socket cursor and output ACK advance only after the final render callback", async () => {
  const b = terminal({ automatic: false }), socket = await b.connect();
  socket.receive(output([Buffer.alloc(65536), Buffer.from("tail")], 9)); await settle();
  assert.equal(b.writes.length, 1); assert.equal(b.subject.state().cursor, 0); assert.equal(acks(socket).length, 0);
  b.pendingWrites.shift()(); await settle();
  assert.equal(b.writes.length, 2); assert.equal(b.subject.state().cursor, 0); assert.equal(acks(socket).length, 0);
  b.pendingWrites.shift()(); await b.subject.drain();
  assert.equal(b.subject.state().cursor, 9); assert.deepEqual(acks(socket), [{ type: "output-ack", cursor: 9 }]);
});

test("generation cancellation stops remaining batches and suppresses cursor and ACK updates", async () => {
  const b = terminal({ automatic: false }), socket = await b.connect();
  socket.receive(output([Buffer.alloc(65536), Buffer.from("must not write")], 9)); await settle();
  b.subject.cancel(); b.pendingWrites.shift()(); await b.subject.drain();
  assert.equal(b.writes.length, 1); assert.equal(b.subject.state().cursor, 0); assert.equal(acks(socket).length, 0);
  await b.subject.renderOutput(output([Buffer.from("old generation")]), 7);
  assert.equal(b.writes.length, 1);
});

test("queued socket messages preserve render order and acknowledge their own cursor", async () => {
  const b = terminal({ automatic: false }), socket = await b.connect();
  socket.receive(output([Buffer.from("first")], 1)); socket.receive(output([Buffer.from("second")], 2)); await settle();
  assert.equal(b.writes.length, 1); assert.equal(acks(socket).length, 0);
  b.pendingWrites.shift()(); await settle();
  assert.equal(b.writes.length, 2); assert.deepEqual(acks(socket), [{ type: "output-ack", cursor: 1 }]);
  b.pendingWrites.shift()(); await b.subject.drain();
  assert.equal(Buffer.concat(b.writes).toString(), "firstsecond");
  assert.deepEqual(acks(socket), [{ type: "output-ack", cursor: 1 }, { type: "output-ack", cursor: 2 }]);
});

test("closed output drains before disconnect; empty output advances without a write", async () => {
  const b = terminal({ automatic: false }), socket = await b.connect();
  socket.receive(output([Buffer.from("final text")], 9, { closed: true, error: "closed message" })); await settle();
  assert.equal(b.subject.state().session, "test-session"); assert.equal(b.requests.length, 0);
  b.pendingWrites.shift()(); await b.subject.drain();
  assert.equal(b.subject.state().cursor, 9); assert.equal(b.subject.state().session, null);
  assert.equal(b.nodes.get("terminal-hint").textContent, "closed message");
  assert.deepEqual(b.requests.map(request => request.url), ["/api/terminal/close"]);
  assert.equal(acks(socket).length, 0);
  assert.ok(b.events.indexOf("rendered") < b.events.indexOf("request:/api/terminal/close"));
  const empty = terminal(); await empty.subject.renderOutput(output([], 12), 7);
  assert.equal(empty.writes.length, 0); assert.equal(empty.subject.state().cursor, 12);
});
