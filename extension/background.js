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

async function handle(msg) {
  const tab = await pickTab();
  if (!tab) return { ok: false, error: "No YouTube Music tab or app window is open in this browser." };
  try {
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
chrome.alarms.create("reconnect", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" })); else connect(); }, 15000);
connect();
