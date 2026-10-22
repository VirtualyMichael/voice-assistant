// ytmusic-control: runs inside the YouTube Music page itself (world MAIN) before the page's own scripts.
// The page asks "Leave site?" when you navigate away while a song is playing, and that question freezes the tab, so the extension could not open a playlist or a radio.
// This keeps the page's own "Leave site?" behaviour exactly as it is, except while the extension itself is moving the tab (window.__ytmNavigating), when it stays silent.
// It touches only "beforeunload" handlers registered on the page's window and nothing else.
(() => {
  if (window.__ytmUnloadGuard) return;
  window.__ytmUnloadGuard = true;
  const handlers = [];
  let prop = null;
  const realAdd = EventTarget.prototype.addEventListener;
  const realRemove = EventTarget.prototype.removeEventListener;
  EventTarget.prototype.addEventListener = function (type, fn, opts) {
    if (type === "beforeunload" && this === window && fn) { handlers.push(fn); return; }
    return realAdd.call(this, type, fn, opts);
  };
  EventTarget.prototype.removeEventListener = function (type, fn, opts) {
    if (type === "beforeunload" && this === window) { const i = handlers.indexOf(fn); if (i >= 0) handlers.splice(i, 1); return; }
    return realRemove.call(this, type, fn, opts);
  };
  Object.defineProperty(window, "onbeforeunload", { configurable: true, get() { return prop; }, set(v) { prop = v; } });
  realAdd.call(window, "beforeunload", (e) => {
    if (window.__ytmNavigating) return;
    for (const fn of handlers.slice()) { try { typeof fn === "function" ? fn.call(window, e) : fn.handleEvent(e); } catch (_) { /* the page's own handler failed: ignore */ } }
    if (typeof prop === "function") { try { const r = prop.call(window, e); if (r !== undefined && r !== null) { e.preventDefault(); e.returnValue = r; } } catch (_) { /* ignore */ } }
  });
})();
