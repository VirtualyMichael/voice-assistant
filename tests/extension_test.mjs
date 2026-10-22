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
  assert.deepEqual(plain((await ask({ action: "state" })).state), { paused: false, time: 3, title: "Eternity", by: "Stellardrone", liked: null, video: "", list: "" });
  assert.equal((await ask({ action: "nope" })).ok, false);
});

await test("like, unlike and shuffle press the page's own buttons and check the page really changed", async () => {
  const page = makePage({ rows: ROWS });
  const st = { like: "INDIFFERENT", icon: "M1", queue: ["A", "B", "C"], likeClicks: 0, shuffleClicks: 0, broken: false };
  const base = page.document;
  page.document = {
    querySelector(sel) {
      if (sel === "ytmusic-player-bar ytmusic-like-button-renderer") return { getAttribute: (n) => (n === "like-status" ? st.like : null) };
      if (sel === "ytmusic-player-bar ytmusic-like-button-renderer #button-shape-like button") return { click() { st.likeClicks++; if (!st.broken) st.like = st.like === "LIKE" ? "INDIFFERENT" : "LIKE"; } };
      if (sel === "ytmusic-player-bar .shuffle button") return { click() { st.shuffleClicks++; if (!st.broken) { st.queue = st.queue.length > 1 ? [...st.queue].reverse() : st.queue; st.icon = st.icon === "M1" ? "M2" : "M1"; } } };
      if (sel === "ytmusic-player-bar .shuffle path") return { getAttribute: () => st.icon };
      return base.querySelector(sel);
    },
    querySelectorAll(sel) {
      if (sel === "ytmusic-player-queue-item .song-title") return st.queue.map((t) => ({ innerText: t }));
      return base.querySelectorAll(sel);
    },
  };
  const { api } = loadContent(page);
  assert.equal(await api.control("like"), "liked");
  assert.equal(await api.control("like"), "already liked");
  assert.equal(st.likeClicks, 1); // already liked: not clicked again (that would remove it)
  assert.equal(await api.control("unlike"), "like removed");
  assert.equal(await api.control("unlike"), "already not liked");
  assert.equal(await api.control("shuffle"), "shuffle toggled");
  st.queue = ["only one song"]; st.icon = "M1"; // a queue of one cannot visibly reorder: the button's icon change confirms the toggle instead
  assert.equal(await api.control("shuffle"), "shuffle toggled");
  st.broken = true; // the page ignores the click: it must say so instead of claiming success
  assert.equal(await api.control("like"), "unconfirmed");
  assert.equal(await api.control("shuffle"), "unconfirmed");
  assert.equal(plain(api.playerState()).liked, "INDIFFERENT");
});

await test("the message listener reports like status, the video and the list in the state", async () => {
  const page = makePage({ rows: ROWS });
  page.location.href = "https://music.youtube.com/watch?v=abc123&list=RDAMVMabc123";
  const { listeners } = loadContent(page);
  const ask = (msg) => new Promise((resolve) => { listeners[0](msg, {}, resolve); });
  const s = plain((await ask({ action: "state" })).state);
  assert.equal(s.video, "abc123");
  assert.equal(s.list, "RDAMVMabc123");
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

// ---------- the library reader that runs inside the page ------------------------------------------
await test("fetchLibrary reads every playlist and track as the page's own (channel) account, following continuations", async () => {
  const src = read("background.js");
  const fn = src.slice(src.indexOf("async function fetchLibrary()"), src.indexOf("async function handle(msg)"));
  const calls = [];
  const track = (id, title, artist) => ({ musicResponsiveListItemRenderer: { playlistItemData: { videoId: id }, flexColumns: [
    { musicResponsiveListItemFlexColumnRenderer: { text: { runs: [{ text: title }] } } }, { musicResponsiveListItemFlexColumnRenderer: { text: { runs: [{ text: artist }] } } }] } });
  const list = (id, title) => ({ musicTwoRowItemRenderer: { navigationEndpoint: { browseEndpoint: { browseId: id } }, title: { runs: [{ text: title }] }, subtitle: { runs: [{ text: "x" }] } } });
  const answers = {
    FEmusic_liked_playlists: { contents: [list("VLLM", "Liked Music"), list("VLPLa", "likeish"), list("SEskip", "Episodes")], more: { nextContinuationData: { continuation: "LISTS2" } } },
    LISTS2: { contents: [list("VLPLb", "second page list")] },
    VLLM: { contents: [track("1", "One", "A")] },
    VLPLa: { contents: [track("2", "Two", "B")], more: { continuationCommand: { token: "TRACKS2" } } },
    TRACKS2: { contents: [track("3", "Three", "C")] },
    VLPLb: { contents: [] },
  };
  const context = { window: { ytcfg: { get: (k) => ({ INNERTUBE_CONTEXT: { client: {} }, DELEGATED_SESSION_ID: "channel123", SESSION_INDEX: "1", INNERTUBE_CONTEXT_CLIENT_VERSION: "1.2" })[k] } },
    document: { cookie: "a=b; SAPISID=secret" }, location: { origin: "https://music.youtube.com" }, crypto: { subtle: { digest: async () => new Uint8Array([1, 2, 3]).buffer } },
    TextEncoder, Uint8Array, Math, Date, JSON, Promise, Array, Object, String, Error, Set,
    fetch: async (url, opts) => { const body = JSON.parse(opts.body); calls.push({ headers: opts.headers, body }); const key = body.continuation || body.browseId;
      return { ok: true, json: async () => answers[key] || {} }; } };
  vm.createContext(context);
  const lib = plain(await vm.runInContext("(" + fn + ")()", context));
  assert.deepEqual(lib.playlists.map((p) => p.title), ["Liked Music", "likeish", "second page list"]); // the Episodes shelf and "New playlist" cards are not playlists
  assert.deepEqual(lib.playlists[1].tracks.map((t) => [t.videoId, t.title, t.artist]), [["2", "Two", "B"], ["3", "Three", "C"]]);
  assert.ok(calls.every((c) => c.headers["x-goog-authuser"] === "1" && c.headers["x-goog-pageid"] === "channel123" && c.body.context.user.onBehalfOfUser === "channel123"));
  assert.match(calls[0].headers.authorization, /^SAPISIDHASH \d+_[0-9a-f]+$/);
});

console.log(`${passed} extension tests passed`);
