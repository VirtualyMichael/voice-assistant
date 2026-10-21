// Tests the browser extension's real source files with fake browser objects. Run by tests/test_ytm_bridge.py (or: node tests/extension_test.mjs).
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const ext = path.resolve(import.meta.dirname, "..", "extension");
const read = (f) => fs.readFileSync(path.join(ext, f), "utf8");
const plain = (x) => JSON.parse(JSON.stringify(x)); // values made inside the vm sandbox have another Array prototype
const tick = () => new Promise((r) => setImmediate(r));
let passed = 0;
const test = async (name, fn) => { await fn(); passed++; console.log("ok -", name); };

// ---------- a tiny fake page ----------------------------------------------------------------
function el(tag, props = {}, kids = {}) {
  return { tag, innerText: "", offsetParent: {}, getClientRects: () => [1], attrs: {}, clicked: 0, kids,
    getAttribute(n) { return this.attrs[n] ?? null; }, querySelector(sel) { return this.kids[sel] ?? null; }, click() { this.clicked++; }, ...props };
}
function makePage({ rows, card = null, hiddenRows = [] }) {
  const input = el("input", { value: "", focus() {}, dispatchEvent(ev) { this.events.push(ev.type); if (ev.type === "keydown") page.onEnter(); return true; }, events: [] });
  const page = {
    input, location: { href: "https://music.youtube.com/" }, searchRows: rows, onEnter() { this.location.href = "https://music.youtube.com/search?q=" + encodeURIComponent(input.value); },
    queried: [],
  };
  const row = (r) => el("row", {}, { "ytmusic-play-button-renderer": el("pb", { attrs: { "aria-label": "Play " + r.title } }), ".title": { innerText: r.title }, ".secondary-flex-columns": { innerText: r.sub } });
  const hiddenBuilt = hiddenRows.map((h) => ({ ...row(h), offsetParent: null, getClientRects: () => [] })); // built once, so a click lands on the same object
  const visibleBuilt = rows.map(row);
  page.document = {
    querySelector(sel) {
      if (sel === "ytmusic-search-box input") return input;
      if (sel === "video") return { paused: false, currentTime: 3 };
      if (sel === "ytmusic-player-bar .title") return { innerText: "Eternity" };
      if (sel === "ytmusic-player-bar .byline") return { innerText: "Stellardrone" };
      return null;
    },
    querySelectorAll(sel) {
      if (sel === "ytmusic-card-shelf-renderer") return card ? [el("card", {}, { "ytmusic-play-button-renderer": el("pb", { attrs: { "aria-label": "Play card" } }), ".title": { innerText: card.title }, ".subtitle": { innerText: card.sub } })] : [];
      if (sel === "ytmusic-responsive-list-item-renderer") {
        if (!input.value) return [];
        return [...hiddenBuilt, ...visibleBuilt];
      }
      return [];
    },
  };
  return page;
}

function loadContent(page) {
  const listeners = [];
  const context = {
    document: page.document, location: page.location, URL, console, setTimeout: (fn) => setImmediate(fn),
    Event: class { constructor(type) { this.type = type; } }, KeyboardEvent: class { constructor(type) { this.type = type; } },
    HTMLInputElement: { prototype: Object.defineProperty({}, "value", { set(v) { page.input.value = v; }, get() { return page.input.value; } }) },
    chrome: { runtime: { onMessage: { addListener: (fn) => listeners.push(fn) } } },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(read("content.js"), context);
  return { api: context.window.__vaYtm, listeners };
}

const ROWS = [
  { title: "Eternity", sub: "Song • Stellardrone 3.1M plays" },
  { title: "Light Years", sub: "Album • Stellardrone • 2013" },
  { title: "Eternity (Reprise)", sub: "Song • Stellardrone • 322K plays" },
];

await test("search types into the page's own box, waits for THIS search and ignores hidden rows from earlier searches", async () => {
  const page = makePage({ rows: ROWS, card: { title: "Eternity", sub: "Video • Stellardrone" }, hiddenRows: [{ title: "Old Song", sub: "Song • Someone" }] });
  const { api } = loadContent(page);
  const cands = await api.search("Eternity Stellardrone");
  assert.equal(page.input.value, "Eternity Stellardrone");
  assert.deepEqual(plain(page.input.events.slice(0, 2)), ["input", "keydown"]);
  assert.deepEqual(plain(cands.map((c) => [c.kind, c.i, c.title])), [["card", 0, "Eternity"], ["row", 0, "Eternity"], ["row", 1, "Light Years"], ["row", 2, "Eternity (Reprise)"]]);
});

await test("click presses the play button of the chosen visible row only", async () => {
  const page = makePage({ rows: ROWS, hiddenRows: [{ title: "Old Song", sub: "x" }] });
  const { api } = loadContent(page);
  page.input.value = "x";
  const rows = page.document.querySelectorAll("ytmusic-responsive-list-item-renderer").filter((r) => r.getClientRects().length);
  assert.equal(api.click("row", 1), "clicked");
  assert.equal(rows[1].kids["ytmusic-play-button-renderer"].clicked, 1);
  assert.equal(rows[0].kids["ytmusic-play-button-renderer"].clicked, 0);
  assert.equal(api.click("row", 99), "missing");
});

await test("message listener answers actions and reports unknown ones", async () => {
  const page = makePage({ rows: ROWS });
  const { listeners } = loadContent(page);
  const ask = (msg) => new Promise((resolve) => { listeners[0](msg, {}, resolve); });
  assert.deepEqual(plain((await ask({ action: "state" })).state), { paused: false, time: 3, title: "Eternity", by: "Stellardrone" });
  assert.equal((await ask({ action: "nope" })).ok, false);
});

// ---------- the background script with a fake browser --------------------------------------------
function loadBackground({ tabs, windows = {}, failFirstSend = false }) {
  const sent = [], injected = [], sockets = [];
  class WS {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); setImmediate(() => { this.readyState = 1; this.onopen && this.onopen(); }); }
    send(data) { sent.push(JSON.parse(data)); }
  }
  let first = failFirstSend;
  const chrome = {
    tabs: {
      query: async () => tabs,
      sendMessage: async (id, msg) => { if (first) { first = false; throw new Error("Receiving end does not exist"); } return { ok: true, answeredBy: id, echo: msg.action }; },
    },
    windows: { get: async (id) => windows[id] ?? { type: "normal" } },
    scripting: { executeScript: async (opts) => { injected.push(opts); } },
    alarms: { create() {}, onAlarm: { addListener() {} } },
    runtime: { getManifest: () => ({ version: "1.0.0" }), onStartup: { addListener() {} }, onInstalled: { addListener() {} } },
  };
  const context = { chrome, WebSocket: WS, setInterval: () => 0, console, JSON, Promise };
  vm.createContext(context);
  vm.runInContext(read("background.js"), context);
  return { sockets, sent, injected };
}

await test("connects to the first free app port and says hello", async () => {
  const bg = loadBackground({ tabs: [] });
  await tick(); await tick();
  assert.match(bg.sockets[0].url, /^ws:\/\/127\.0\.0\.1:47821$/);
  assert.deepEqual(plain(bg.sent[0]), { type: "hello", version: "1.0.0" });
});

await test("relays a command to the playing app-window tab and returns the answer with the same id", async () => {
  const bg = loadBackground({
    tabs: [{ id: 1, windowId: 10, audible: false, active: true }, { id: 2, windowId: 20, audible: true, active: false }, { id: 3, windowId: 30, audible: false, active: false }],
    windows: { 10: { type: "normal" }, 20: { type: "app" }, 30: { type: "app" } },
  });
  await tick(); await tick();
  await bg.sockets[0].onmessage({ data: JSON.stringify({ id: "abc", action: "state" }) });
  const reply = bg.sent.find((m) => m.id === "abc");
  assert.equal(reply.answeredBy, 2); // audible + app window beats the plain active tab
  assert.equal(reply.ok, true);
});

await test("injects the page script when the tab was open before install, then retries", async () => {
  const bg = loadBackground({ tabs: [{ id: 5, windowId: 1, audible: false, active: true }], failFirstSend: true });
  await tick(); await tick();
  await bg.sockets[0].onmessage({ data: JSON.stringify({ id: "z", action: "ping" }) });
  assert.equal(bg.injected.length, 1);
  assert.deepEqual(plain(bg.injected[0].files), ["content.js"]);
  assert.equal(bg.sent.find((m) => m.id === "z").ok, true);
});

await test("says so plainly when no YouTube Music tab exists", async () => {
  const bg = loadBackground({ tabs: [] });
  await tick(); await tick();
  await bg.sockets[0].onmessage({ data: JSON.stringify({ id: "n", action: "state" }) });
  const reply = bg.sent.find((m) => m.id === "n");
  assert.equal(reply.ok, false);
  assert.match(reply.error, /No YouTube Music tab/);
});

console.log(`${passed} extension tests passed`);
