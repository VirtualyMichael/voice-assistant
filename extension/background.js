// Voice Assistant: YouTube Music bridge, background part.
//
// What it does: keeps a connection to the Voice Assistant app running on THIS computer (ws://127.0.0.1, never the internet),
// receives small commands (search / click / state / control) and forwards them to your YouTube Music tab or app window.
// What it never does: read other sites, send anything off this PC, or run without the app asking.

const PORTS = [47821, 47822, 47823, 47824, 47825]; // the app listens on the first free one
const YTM = "https://music.youtube.com/*";
let ws = null;
let connecting = false;

function log(...a) { console.debug("[va-bridge]", ...a); }

async function pickTab() {
  const tabs = await chrome.tabs.query({ url: YTM });
  if (!tabs.length) return null;
  // prefer the one that is playing, then one in an installed-app window, then any
  const wins = await Promise.all(tabs.map((t) => chrome.windows.get(t.windowId).catch(() => null)));
  const score = (t, i) => (t.audible ? 4 : 0) + (wins[i] && wins[i].type === "app" ? 2 : 0) + (t.active ? 1 : 0);
  return tabs.map((t, i) => [score(t, i), t]).sort((a, b) => b[0] - a[0])[0][1];
}

async function ask(tab, message) {
  const send = () => chrome.tabs.sendMessage(tab.id, message);
  try {
    return await send();
  } catch (e) {
    // the tab was open before the extension was installed: inject the page script once, then retry
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    return await send();
  }
}


// Runs INSIDE the YouTube Music page (chrome.scripting, world MAIN), so it can use the page's own signed-in session the way the page itself does.
// It lists your playlists and every track in them (titles and artists, plus the video id) and returns that to the app. Nothing leaves your PC.
async function fetchLibrary() {
  const cfg = (k) => window.ytcfg.get(k);
  const context = JSON.parse(JSON.stringify(cfg("INNERTUBE_CONTEXT") || null));
  const delegated = cfg("DELEGATED_SESSION_ID"); // signed in as a channel / brand account: the page acts on its behalf, so must we
  if (context && delegated) context.user = { ...(context.user || {}), onBehalfOfUser: delegated };
  const sapisid = (document.cookie.match(/(?:^|; )(?:__Secure-3PAPISID|SAPISID)=([^;]+)/) || [])[1];
  if (!context || !sapisid) throw new Error("You are not signed in to YouTube Music in this browser.");
  const sha1hex = async (s) => [...new Uint8Array(await crypto.subtle.digest("SHA-1", new TextEncoder().encode(s)))].map((x) => x.toString(16).padStart(2, "0")).join("");
  async function browse(body) {
    const ts = Math.floor(Date.now() / 1000), origin = location.origin;
    const r = await fetch("/youtubei/v1/browse?prettyPrint=false", {
      method: "POST", credentials: "include",
      headers: { "content-type": "application/json", authorization: `SAPISIDHASH ${ts}_${await sha1hex(`${ts} ${sapisid} ${origin}`)}`, "x-origin": origin,
                 "x-goog-authuser": String(cfg("SESSION_INDEX") || "0"), ...(delegated ? { "x-goog-pageid": delegated } : {}), "x-youtube-client-name": "67", "x-youtube-client-version": cfg("INNERTUBE_CONTEXT_CLIENT_VERSION") },
      body: JSON.stringify({ context, ...body }),
    });
    if (!r.ok) throw new Error("YouTube Music answered " + r.status);
    return r.json();
  }
  const walk = (o, fn) => { if (Array.isArray(o)) o.forEach((x) => walk(x, fn)); else if (o && typeof o === "object") { fn(o); Object.values(o).forEach((v) => walk(v, fn)); } };
  const runs = (x) => ((x && x.runs) || []).map((r) => r.text).join("");
  const nextToken = (j) => { let t = null; walk(j, (o) => { if (!t && o.nextContinuationData) t = o.nextContinuationData.continuation; if (!t && o.continuationCommand && o.continuationCommand.token) t = o.continuationCommand.token; }); return t; };
  const tracksOf = (j) => {
    const out = [];
    walk(j, (o) => {
      const r = o.musicResponsiveListItemRenderer;
      if (!r) return;
      const cols = (r.flexColumns || []).map((c) => runs((c.musicResponsiveListItemFlexColumnRenderer || {}).text));
      if (cols[0]) out.push({ videoId: (r.playlistItemData || {}).videoId || "", title: cols[0], artist: cols[1] || "", album: cols[2] || "" });
    });
    return out;
  };
  const lists = [], seen = new Set();
  const addLists = (j) => walk(j, (o) => {
    const r = o.musicTwoRowItemRenderer;
    if (!r) return;
    const id = (((r.navigationEndpoint || {}).browseEndpoint) || {}).browseId || "";
    if (id.startsWith("VL") && !seen.has(id)) { seen.add(id); lists.push({ id: id.slice(2), title: runs(r.title), sub: runs(r.subtitle) }); }
  });
  let j = await browse({ browseId: "FEmusic_liked_playlists" });
  addLists(j);
  let tok = nextToken(j), guard = 0;
  while (tok && guard++ < 30) { j = await browse({ continuation: tok }); addLists(j); tok = nextToken(j); }
  const out = new Array(lists.length);
  let next = 0;
  async function worker() {
    while (next < lists.length) {
      const k = next++;
      try {
        let page = await browse({ browseId: "VL" + lists[k].id });
        let tracks = tracksOf(page), t = nextToken(page), n = 0;
        while (t && n++ < 80) { page = await browse({ continuation: t }); tracks = tracks.concat(tracksOf(page)); t = nextToken(page); }
        out[k] = { ...lists[k], tracks };
      } catch (e) {
        out[k] = { ...lists[k], tracks: [], error: String(e && e.message ? e.message : e) };
      }
    }
  }
  await Promise.all([worker(), worker(), worker(), worker()]);
  return { fetchedAt: Date.now(), playlists: out };
}

async function handle(msg) {
  const tab = await pickTab();
  if (!tab) return { ok: false, error: "No YouTube Music tab or app window is open in this browser." };
  try {
    if (msg.action === "library") {
      const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "MAIN", func: fetchLibrary });
      return { ok: true, library: res.result };
    }
    if (msg.action === "open_playlist") {
      const id = String(msg.playlist || "");
      if (!/^[A-Za-z0-9_-]{2,64}$/.test(id)) return { ok: false, error: "That is not a playlist id." };
      // The page asks "Leave site?" when it is playing; swallow that one question so the navigation is not blocked.
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "MAIN", func: () => window.addEventListener("beforeunload", (e) => e.stopImmediatePropagation(), true) });
      await chrome.tabs.update(tab.id, { url: "https://music.youtube.com/watch?list=" + encodeURIComponent(id) });
      return { ok: true, result: "opened" };
    }
    const res = await ask(tab, msg);
    return res || { ok: false, error: "The YouTube Music page did not answer." };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

function openSocket(port) {
  return new Promise((resolve) => {
    let done = false;
    const sock = new WebSocket(`ws://127.0.0.1:${port}`);
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    sock.onopen = () => finish(sock);
    sock.onerror = () => finish(null);
    sock.onclose = () => finish(null);
  });
}

async function connect() {
  if (connecting || (ws && ws.readyState === WebSocket.OPEN)) return;
  connecting = true;
  try {
    for (const port of PORTS) {
      const sock = await openSocket(port);
      if (!sock) continue;
      ws = sock;
      ws.onmessage = async (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (!msg || msg.type === "pong") return;
        const result = await handle(msg);
        ws.send(JSON.stringify({ id: msg.id, ...result }));
      };
      ws.onclose = () => { ws = null; };
      ws.send(JSON.stringify({ type: "hello", version: chrome.runtime.getManifest().version }));
      log("connected on", port);
      return;
    }
  } finally {
    connecting = false;
  }
}

// keep trying: the app may start after the browser, and the service worker can be put to sleep
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 }); // the browser's minimum: reconnect within ~30 s of the app starting
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" })); else connect(); }, 15000);
connect();
