// Voice Assistant: YouTube Music bridge, page part. Runs only on music.youtube.com.
//
// It reads the search results and the player on the page, types into the page's own search box and presses the page's own play buttons,
// the same things you would do by hand. It reports back what it found; it does not read anything else on the page.
(() => {
  if (window.__vaYtm) return; // injected twice (page was open before install): keep the first copy

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const tokens = (s) => (s || "").toLowerCase().replace(/&/g, " and ").match(/[a-z0-9']+/g) || [];
  // earlier searches stay in the page, hidden: only ever look at what is actually on screen
  const visible = (el) => !!el && el.getClientRects().length > 0 && el.offsetParent !== null;

  // Same selectors and shape as voice_assistant/ytm_browser.py (EXTRACT_JS); a test keeps the two in step.
  function extract() {
    const cands = [];
    const card = [...document.querySelectorAll("ytmusic-card-shelf-renderer")].find(visible);
    if (card) {
      const pb = card.querySelector("ytmusic-play-button-renderer");
      const title = norm((card.querySelector(".title") || {}).innerText);
      const sub = norm((card.querySelector(".subtitle") || card.querySelector(".secondary-flex-columns") || {}).innerText);
      if (pb && title) cands.push({ kind: "card", i: 0, title, sub, aria: pb.getAttribute("aria-label") || "" });
    }
    [...document.querySelectorAll("ytmusic-responsive-list-item-renderer")].filter(visible).forEach((it, i) => {
      const pb = it.querySelector("ytmusic-play-button-renderer");
      if (!pb) return;
      cands.push({ kind: "row", i, title: norm((it.querySelector(".title") || {}).innerText),
                   sub: norm((it.querySelector(".secondary-flex-columns") || {}).innerText), aria: pb.getAttribute("aria-label") || "" });
    });
    return { url: location.href, q: new URL(location.href).searchParams.get("q") || "", cands };
  }

  function playerState() {
    const v = document.querySelector("video");
    const t = document.querySelector("ytmusic-player-bar .title");
    const by = document.querySelector("ytmusic-player-bar .byline");
    return { paused: v ? v.paused : null, time: v ? v.currentTime : null, title: t ? t.innerText : "", by: by ? norm(by.innerText) : "" };
  }

  // Type into the page's own search box so its router handles it as a normal in-page search (no reload, no "Leave site?" prompt).
  async function search(query) {
    const input = document.querySelector("ytmusic-search-box input");
    if (!input) throw new Error("The YouTube Music search box was not found on the page.");
    input.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, query);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await sleep(150);
    for (const type of ["keydown", "keypress", "keyup"]) {
      input.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, composed: true }));
    }
    const want = tokens(query).join(" ");
    let previous = null;
    for (let i = 0; i < 28; i++) { // up to ~16 s for results of THIS search to appear and stop changing
      await sleep(600);
      const data = extract();
      if (tokens(data.q).join(" ") !== want || data.cands.length < 3) { previous = null; continue; }
      const sig = JSON.stringify(data.cands.map((c) => [c.kind, c.i, c.title]));
      if (sig === previous) return data.cands;
      previous = sig;
    }
    return [];
  }

  function click(kind, i) {
    const el = kind === "card" ? [...document.querySelectorAll("ytmusic-card-shelf-renderer")].find(visible)
                               : [...document.querySelectorAll("ytmusic-responsive-list-item-renderer")].filter(visible)[i];
    const pb = el && el.querySelector("ytmusic-play-button-renderer");
    if (!pb) return "missing";
    pb.click();
    return "clicked";
  }

  function control(what) {
    const sel = { toggle: ".play-pause-button", next: ".next-button", previous: ".previous-button" }[what];
    const b = sel && document.querySelector("ytmusic-player-bar " + sel);
    if (!b) return "missing";
    b.click();
    return "clicked";
  }

  async function onMessage(msg) {
    switch (msg.action) {
      case "ping": return { ok: true, url: location.href };
      case "state": return { ok: true, state: playerState() };
      case "search": return { ok: true, cands: await search(String(msg.query || "")) };
      case "click": return { ok: true, result: click(msg.kind, msg.i), state: playerState() };
      case "control": return { ok: true, result: control(msg.what), state: playerState() };
      default: return { ok: false, error: "unknown action " + msg.action };
    }
  }

  window.__vaYtm = { extract, playerState, search, click, control, onMessage };
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
      onMessage(msg).then(reply, (e) => reply({ ok: false, error: String(e && e.message ? e.message : e) }));
      return true; // answer asynchronously
    });
  }
})();
