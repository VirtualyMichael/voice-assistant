"""Control the YouTube Music you are already signed in to, through a small browser extension that ships with the app (the `extension` folder: plain
JavaScript you can read).

The app listens on 127.0.0.1 only. The extension in your normal browser connects to it. The browser itself reports who is connecting (a web page shows up as its
own https origin and is refused; an extension shows up as chrome-extension://<id>), and the first extension that connects is remembered and the only one
accepted from then on. The app can then ask it to search, click play and report what is playing, in your existing YouTube Music tab or app window. It is the same page work as
the assistant's own YouTube Music window (ytm_browser.py), but in your own signed-in session. When the extension is not installed or not connected, the app
simply uses the other routes."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from pathlib import Path

from .ytm_browser import YtmError, YtmNoMatch, choose, kind_of, no_match_message, search_queries, started, tokens

PORTS = (47821, 47822, 47823, 47824, 47825)


class YtmPaused(YtmError):
    """The song was loaded in the user's tab but the browser did not let it start by itself: not a failure to fall back from."""


def extension_folder() -> Path:
    """Where the extension's files are: next to the installed program, or in the repository when running from source."""
    import sys

    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1] / "extension"
    here = Path(sys.executable).parent
    for candidate in (here / "extension", here / "_internal" / "extension", Path(getattr(sys, "_MEIPASS", here)) / "extension"):
        if (candidate / "manifest.json").exists():
            return candidate
    return here / "extension"


class YtmBridge:
    def __init__(self, ports=PORTS, extension_id: str = "", serve=None, on_trust=None):
        """extension_id: the extension remembered from an earlier run (empty the first time); on_trust(id) is called when a new one is remembered."""
        self.ports, self.extension_id, self.on_trust = ports, extension_id, on_trust
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
                self._server = self._serve(self._handler, "127.0.0.1", port, max_size=64 * 1024 * 1024)  # a whole library is a big message
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
        prefix = "chrome-extension://"
        if not origin.startswith(prefix) or (self.extension_id and origin != prefix + self.extension_id):  # a web page, or another extension than the remembered one
            ws.close(1008, "origin not allowed")
            return
        if not self.extension_id:  # first connection ever: remember this extension, refuse any other from now on
            self.extension_id = origin[len(prefix):]
            if self.on_trust:
                self.on_trust(self.extension_id)
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
    def ensure_tab(self, open_tab, sleep=time.sleep, clock=time.time, timeout: float = 25.0) -> None:
        """Make sure the user's browser has a YouTube Music tab or app window; if not, open their YouTube Music (signed in) and wait for it."""
        try:
            self.request("ping", timeout=8)
            return
        except YtmError as e:
            if "No YouTube Music tab" not in str(e) or open_tab is None:
                raise
        open_tab()
        end = clock() + timeout
        while clock() < end:
            sleep(1.0)
            try:
                self.request("ping", timeout=8)
                sleep(1.5)  # let the page finish loading its search box
                return
            except YtmError:
                continue
        raise YtmError("I opened YouTube Music in your browser, but its page never became ready.")

    def wait_connected(self, seconds: float = 8.0, sleep=time.sleep, clock=time.time) -> bool:
        """The extension reconnects by itself within about half a minute of the app starting; wait briefly for it when it is known to be installed."""
        end = clock() + seconds
        while not self.connected and clock() < end:
            sleep(0.4)
        return self.connected

    def play_playlist(self, name: str, library, sleep=time.sleep, clock=time.time, open_tab=None) -> str:
        """Play one of your saved playlists by name, and check that a song from it really started."""
        pl, closest = library.find_playlist(name)
        if pl is None:
            hint = " The closest saved playlists are: " + "; ".join(closest) + "." if closest else " No library is saved yet: run a library refresh first."
            raise YtmNoMatch(f"Could not find a playlist called '{name}' in your saved playlists. Nothing played." + hint, closest)
        self.ensure_tab(open_tab, sleep, clock)
        self.request("open_playlist", playlist=pl["id"])
        want = {"".join(tokens(t.get("title", ""))) for t in pl.get("tracks", [])}
        sleep(2.0)  # the page reloads; the extension reconnects its page script
        end, state = clock() + 20, {}
        while clock() < end:
            sleep(1.0)
            try:
                state = self.request("state", timeout=8).get("state", {})
            except YtmError:
                continue
            title = "".join(tokens(state.get("title", "")))
            if state.get("paused") is False and title and (not want or title in want):
                return f"Playing your playlist '{pl['title']}', starting with {state['title']}" + (f" by {state['by']}" if state.get("by") else "") + "."
        if state.get("paused") and state.get("title"):
            raise YtmPaused(f"Opened your playlist '{pl['title']}', but the browser did not let it start by itself; press play there.")
        raise YtmError(f"I opened your playlist '{pl['title']}', but the page did not start playing it (it shows '{state.get('title') or 'nothing'}').")

    def play(self, query: str, sleep=time.sleep, clock=time.time, open_tab=None, judge=None, library=None) -> str:
        """Search in the user's own YouTube Music tab, click play on the best match, confirm."""
        self.ensure_tab(open_tab, sleep, clock)
        best, closest = None, []
        spelled = (library.resolve(query) if library else None) or query  # the saved library's own spelling of the song, when it is one of yours
        for q in search_queries(spelled):
            cands = self.request("search", timeout=30, query=q).get("cands", [])
            try:
                best = choose(spelled, cands, judge)
                break
            except YtmNoMatch as e:
                closest += e.closest
        if best is None:
            raise YtmNoMatch(no_match_message(query, closest), closest)
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


def make_player(bridge: YtmBridge | None, browser=None, open_tab=None, judge=None, library=None):
    """One play(query) that tries the user's own signed-in tab (extension) first, then the assistant's own YouTube Music window.
    open_tab() starts the user's own YouTube Music (their app, signed in) when the extension is connected but no tab is open."""

    def play(query: str) -> str:
        problems: list[str] = []
        if bridge is not None:
            if not bridge.connected and bridge.extension_id:
                bridge.wait_connected()
            if bridge.connected:
                try:
                    return bridge.play(query, open_tab=open_tab, judge=judge, library=library)
                except YtmPaused as e:
                    return str(e)
                except YtmNoMatch as e:  # the song is not on YouTube Music: the other window would only find the same results
                    return str(e)
                except YtmError as e:
                    problems.append(f"your signed-in YouTube Music could not be used ({e})")
            elif bridge.extension_id:
                problems.append("your browser extension is not connected right now (it reconnects within about 30 seconds of the app starting)")
        if browser is not None:
            try:
                out = browser.play(query, judge=judge, library=library)
                return out + (f" Note: {problems[0]}, so the assistant's own window was used." if problems else "")
            except YtmNoMatch as e:
                return str(e)
            except Exception as e:
                problems.append(f"the assistant's own window: {e}")
        raise YtmError("; ".join(problems) or "No YouTube Music route is available.")

    return play


def make_playlist_player(bridge: YtmBridge | None, library, open_tab=None):
    """play_playlist(name): one of the user's saved playlists, played in their own signed-in tab (needs the extension and the saved library)."""

    def play(name: str) -> str:
        if bridge is None:
            raise YtmError("Playing a playlist needs the browser extension, and it is switched off in Settings > Apps.")
        if not bridge.connected and bridge.extension_id:
            bridge.wait_connected()
        if not bridge.connected:
            raise YtmError("Playing a playlist needs the browser extension, and it is not connected right now.")
        try:
            return bridge.play_playlist(name, library, open_tab=open_tab)
        except (YtmPaused, YtmNoMatch) as e:
            return str(e)

    return play
