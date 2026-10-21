"""Control the YouTube Music you are already signed in to, through a small browser extension that ships with the app (the `extension` folder: plain
JavaScript you can read).

The app listens on 127.0.0.1 only. The extension in your normal browser connects to it (the browser sends the extension's own origin, which the app checks),
and then the app can ask it to search, click play and report what is playing, in your existing YouTube Music tab or app window. It is the same page work as
the assistant's own YouTube Music window (ytm_browser.py), but in your own signed-in session. When the extension is not installed or not connected, the app
simply uses the other routes."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from pathlib import Path

from .ytm_browser import YtmError, choose, kind_of, started

EXTENSION_ID = "fhlaljjbhimogfjcdpohgjkkaeboilac"  # fixed by the public key in extension/manifest.json
PORTS = (47821, 47822, 47823, 47824, 47825)


class YtmPaused(YtmError):
    """The song was loaded in the user's tab but the browser did not let it start by itself: not a failure to fall back from."""


def extension_folder() -> Path:
    """Where the extension's files are: next to the installed program, or in the repository when running from source."""
    import sys

    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return base / "extension"


class YtmBridge:
    def __init__(self, ports=PORTS, extension_id: str = EXTENSION_ID, serve=None):
        self.ports, self.extension_id = ports, extension_id
        self._serve = serve
        self.port: int | None = None
        self.version = ""
        self._server = None
        self._ws = None
        self._lock = threading.Lock()
        self._pending: dict[str, queue.Queue] = {}

    # ---- server -----------------------------------------------------------------------
    def start(self) -> bool:
        if self._serve is None:
            from websockets.sync.server import serve

            self._serve = serve
        for port in self.ports:
            try:
                self._server = self._serve(self._handler, "127.0.0.1", port)
            except OSError:
                continue
            self.port = port
            threading.Thread(target=self._server.serve_forever, daemon=True).start()
            return True
        return False

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def _handler(self, ws) -> None:
        origin = ""
        try:
            origin = ws.request.headers.get("Origin", "")
        except Exception:
            pass
        if origin != f"chrome-extension://{self.extension_id}":  # only our extension: a web page cannot talk to this
            ws.close(1008, "origin not allowed")
            return
        with self._lock:
            self._ws = ws
        try:
            for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "hello":
                    self.version = str(msg.get("version", ""))
                elif msg.get("type") == "ping":
                    ws.send(json.dumps({"type": "pong"}))
                elif "id" in msg:
                    q = self._pending.get(msg["id"])
                    if q is not None:
                        q.put(msg)
        finally:
            with self._lock:
                if self._ws is ws:
                    self._ws = None

    # ---- requests -----------------------------------------------------------------------
    def request(self, action: str, timeout: float = 25.0, **payload) -> dict:
        ws = self._ws
        if ws is None:
            raise YtmError("The browser extension is not connected.")
        mid = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        self._pending[mid] = q
        try:
            ws.send(json.dumps({"id": mid, "action": action, **payload}))
            try:
                msg = q.get(timeout=timeout)
            except queue.Empty:
                raise YtmError(f"The browser extension did not answer '{action}' in time.")
        finally:
            self._pending.pop(mid, None)
        if not msg.get("ok"):
            raise YtmError(msg.get("error") or "The extension reported an error.")
        return msg

    # ---- playing ------------------------------------------------------------------------
    def play(self, query: str, sleep=time.sleep, clock=time.time) -> str:
        """Search in the user's own YouTube Music tab, click play on the best match, confirm."""
        cands = self.request("search", timeout=30, query=query).get("cands", [])
        best = choose(query, cands)
        state: dict = {}
        for attempt in (1, 2):
            res = self.request("click", kind=best["kind"], i=best["i"])
            if res.get("result") != "clicked":
                raise YtmError("The play button of the best match could not be found on the page.")
            end = clock() + 9
            while clock() < end:
                sleep(0.7)
                state = self.request("state").get("state", {})
                done = started(best, state)
                if done:
                    return done.replace("in the assistant's YouTube Music window", "in your YouTube Music")
        if state.get("paused") and state.get("title") and set(state["title"].lower().split()) & set(best["title"].lower().split()):
            raise YtmPaused(f"Opened {state['title']} in your YouTube Music, but the browser did not let it start by itself; press play there.")
        raise YtmError(f"I clicked play on '{best['title']}', but the page did not start playing that song (it shows '{state.get('title') or 'nothing'}').")


def make_player(bridge: YtmBridge | None, browser=None):
    """One play(query) that tries the user's own signed-in tab (extension) first, then the assistant's own YouTube Music window."""

    def play(query: str) -> str:
        problems: list[str] = []
        if bridge is not None and bridge.connected:
            try:
                return bridge.play(query)
            except YtmPaused as e:
                return str(e)
            except YtmError as e:
                problems.append(f"your browser extension: {e}")
        if browser is not None:
            try:
                return browser.play(query)
            except Exception as e:
                problems.append(f"the assistant's own window: {e}")
        raise YtmError("; ".join(problems) or "No YouTube Music route is available.")

    return play
