import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from websockets.sync.client import connect

from voice_assistant.ytm_bridge import PORTS, YtmBridge, YtmPaused, extension_folder, make_player
from voice_assistant.ytm_browser import EXTRACT_JS, YtmError

ROOT = Path(__file__).resolve().parents[1]
RESULTS = [
    {"kind": "card", "i": 0, "title": "Eternity", "sub": "Video • Stellardrone • 2.1M views", "aria": ""},
    {"kind": "row", "i": 0, "title": "Eternity", "sub": "Song • Stellardrone 3.1M plays", "aria": ""},
    {"kind": "row", "i": 1, "title": "Light Years", "sub": "Album • Stellardrone • 2013", "aria": ""},
]


EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
trusted = []


@pytest.fixture
def bridge():
    trusted.clear()
    b = YtmBridge(ports=(0,), extension_id=EXTENSION_ID, on_trust=trusted.append)  # port 0: the OS picks a free one so tests never collide with a real app
    assert b.start() or True
    b.port = b._server.socket.getsockname()[1]
    yield b
    b.stop()


class FakeExtension(threading.Thread):
    """Plays the extension: connects with the right origin and answers search / click / state like the page script would."""

    def __init__(self, port, origin=f"chrome-extension://{EXTENSION_ID}", playing_after_click=True, title="Eternity"):
        super().__init__(daemon=True)
        self.port, self.origin, self.playing_after_click, self.title = port, origin, playing_after_click, title
        self.clicked, self.playing, self.log = [], False, []

    def run(self):
        try:
            with connect(f"ws://127.0.0.1:{self.port}", additional_headers={"Origin": self.origin}) as ws:
                ws.send(json.dumps({"type": "hello", "version": "1.0.0"}))
                for raw in ws:
                    msg = json.loads(raw)
                    self.log.append(msg["action"])
                    if msg["action"] == "ping":
                        out = {"ok": True}
                    elif msg["action"] == "search":
                        out = {"ok": True, "cands": RESULTS}
                    elif msg["action"] == "click":
                        self.clicked.append((msg["kind"], msg["i"]))
                        self.playing = self.playing_after_click
                        out = {"ok": True, "result": "clicked", "state": {}}
                    elif msg["action"] == "state":
                        out = {"ok": True, "state": {"paused": not self.playing, "title": self.title, "by": "x"}}
                    else:
                        out = {"ok": False, "error": "unknown"}
                    ws.send(json.dumps({"id": msg["id"], **out}))
        except Exception:
            pass


def wait_connected(b, want=True, seconds=3):
    end = time.time() + seconds
    while time.time() < end and b.connected != want:
        time.sleep(0.02)
    return b.connected == want


def test_extension_connects_and_the_app_can_play_through_it(bridge):
    ext = FakeExtension(bridge.port)
    ext.start()
    assert wait_connected(bridge) and bridge.version == "1.0.0"
    out = bridge.play("Eternity by Stellardrone", sleep=lambda s: None)
    assert out.startswith("Playing Eternity by Stellardrone (a song)") and "in your YouTube Music" in out
    assert ext.clicked == [("row", 0)] and ext.log[:2] == ["ping", "search"]  # the Song, not the Video card


def test_a_web_page_or_other_origin_is_refused(bridge):
    for origin in ("https://evil.example", "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba", "http://127.0.0.1:8000", ""):
        ext = FakeExtension(bridge.port, origin=origin)
        ext.start()
        time.sleep(0.4)
        assert not bridge.connected


def test_not_connected_raises_a_clear_error():
    b = YtmBridge(ports=(0,))
    with pytest.raises(YtmError, match="not connected"):
        b.request("state")


def test_disconnect_is_noticed(bridge):
    ext = FakeExtension(bridge.port)
    ext.start()
    assert wait_connected(bridge)
    bridge._ws.close()
    assert wait_connected(bridge, want=False)


def test_a_paused_result_is_reported_as_opened_not_as_a_failure(bridge):
    ext = FakeExtension(bridge.port, playing_after_click=False)
    ext.start()
    assert wait_connected(bridge)
    t = iter(range(0, 1000))
    with pytest.raises(YtmPaused, match="press play"):
        bridge.play("Eternity by Stellardrone", sleep=lambda s: None, clock=lambda: next(t))
    assert make_player(bridge)("Eternity by Stellardrone").startswith("Opened Eternity")


def test_something_else_playing_is_a_failure(bridge):
    ext = FakeExtension(bridge.port, title="Obama - 15")
    ext.start()
    assert wait_connected(bridge)
    t = iter(range(0, 1000))
    ext.playing_after_click = True
    with pytest.raises(YtmError, match="did not start playing that song"):
        bridge.play("Eternity by Stellardrone", sleep=lambda s: None, clock=lambda: next(t))


def test_player_prefers_the_extension_then_falls_back_to_the_own_window(bridge):
    calls = []

    class Browser:
        def play(self, q, judge=None, library=None):
            calls.append(q)
            return "Playing X (a song) in the assistant's YouTube Music window."

    assert make_player(YtmBridge(ports=(0,)), Browser())("a").startswith("Playing X") and calls == ["a"]  # no extension was ever installed: own window
    bridge.wait_connected = lambda *a, **k: False
    with pytest.raises(YtmError, match="did not connect"):  # installed but not connected: not the other window
        make_player(bridge, Browser())("a")
    del bridge.wait_connected
    player = make_player(bridge, Browser())
    ext = FakeExtension(bridge.port)
    ext.start()
    assert wait_connected(bridge)
    assert "in your YouTube Music" in player("Eternity by Stellardrone") and calls == ["a"]
    with pytest.raises(YtmError, match="No YouTube Music route"):
        make_player(None, None)("x")


def test_first_extension_is_remembered_and_later_ones_refused():
    b = YtmBridge(ports=(0,), on_trust=trusted.append)
    trusted.clear()
    assert b.start() or True
    port = b._server.socket.getsockname()[1]
    try:
        first = FakeExtension(port, origin="chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        first.start()
        assert wait_connected(b) and trusted == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] and b.extension_id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        b._ws.close()
        assert wait_connected(b, want=False)
        FakeExtension(port, origin="chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").start()
        time.sleep(0.4)
        assert not b.connected and trusted == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
        FakeExtension(port, origin="https://evil.example").start()
        time.sleep(0.3)
        assert not b.connected
    finally:
        b.stop()


def test_ports_and_files_in_the_repo_agree():
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert re.search(r"PORTS = \[" + ", ".join(str(p) for p in PORTS) + r"\]", background)
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3 and manifest["host_permissions"] == ["https://music.youtube.com/*"]
    assert extension_folder() == ROOT / "extension" and (extension_folder() / "content.js").exists()


def test_extension_page_script_and_python_script_read_the_page_the_same_way():
    content = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    for needle in ("ytmusic-card-shelf-renderer", "ytmusic-responsive-list-item-renderer", "ytmusic-play-button-renderer", ".secondary-flex-columns",
                   "getClientRects().length > 0", "ytmusic-search-box input", "ytmusic-player-bar .title"):
        assert needle in content, needle
        assert needle in EXTRACT_JS or needle in ("ytmusic-search-box input", "ytmusic-player-bar .title") or needle.startswith("."), needle


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_extension_javascript_logic_with_node():
    """Runs the extension's own source in Node with a fake browser: tab choice, injection retry, message relay and the page script's actions."""
    script = ROOT / "tests" / "extension_test.mjs"
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr


def test_frozen_app_finds_the_extension_next_to_or_inside_the_program(tmp_path, monkeypatch):
    import sys

    from voice_assistant import ytm_bridge

    exe = tmp_path / "VoiceAssistant.exe"
    exe.write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    (tmp_path / "_internal" / "extension").mkdir(parents=True)
    (tmp_path / "_internal" / "extension" / "manifest.json").write_text("{}")
    assert ytm_bridge.extension_folder() == tmp_path / "_internal" / "extension"
    (tmp_path / "extension").mkdir()
    (tmp_path / "extension" / "manifest.json").write_text("{}")
    assert ytm_bridge.extension_folder() == tmp_path / "extension"  # the installer's copy wins when both exist


def test_no_tab_open_opens_the_users_youtube_music_then_plays_there(bridge):
    opened, state = [], {"tab": False}

    class Ext(FakeExtension):
        def run(self):
            try:
                with connect(f"ws://127.0.0.1:{self.port}", additional_headers={"Origin": self.origin}) as ws:
                    ws.send(json.dumps({"type": "hello", "version": "1.0.0"}))
                    for raw in ws:
                        msg = json.loads(raw)
                        if msg["action"] == "ping":
                            out = {"ok": True} if state["tab"] else {"ok": False, "error": "No YouTube Music tab or app window is open in this browser."}
                        elif msg["action"] == "search":
                            out = {"ok": True, "cands": RESULTS}
                        elif msg["action"] == "click":
                            self.playing = True
                            out = {"ok": True, "result": "clicked", "state": {}}
                        else:
                            out = {"ok": True, "state": {"paused": not self.playing, "title": "Eternity", "by": "x"}}
                        ws.send(json.dumps({"id": msg["id"], **out}))
            except Exception:
                pass

    ext = Ext(bridge.port)
    ext.start()
    assert wait_connected(bridge)

    def open_tab():
        opened.append(1)
        state["tab"] = True  # the user's browser now has the page

    t = iter(range(0, 10000))
    out = bridge.play("Eternity by Stellardrone", sleep=lambda s: None, clock=lambda: next(t), open_tab=open_tab)
    assert out.startswith("Playing Eternity") and opened == [1]
    with pytest.raises(YtmError, match="No YouTube Music tab"):
        state["tab"] = False
        bridge.play("Eternity by Stellardrone", sleep=lambda s: None, clock=lambda: next(t), open_tab=None)


def test_player_waits_briefly_for_a_known_extension_and_explains_a_fallback():
    calls = []

    class Browser:
        def play(self, q, judge=None, library=None):
            calls.append(q)
            return "Playing X (a song) in the assistant's YouTube Music window."

    b = YtmBridge(ports=(0,), extension_id="abcdefghijklmnopabcdefghijklmnop")
    waits = []
    b.wait_connected = lambda *a, **k: waits.append(1) or False
    with pytest.raises(YtmError, match="did not connect"):  # a known extension that never connects: say so, never switch to the not-signed-in window
        make_player(b, Browser())("a")
    assert waits == [1] and calls == []
    unknown = YtmBridge(ports=(0,))  # never installed: no waiting, no remark
    unknown.wait_connected = lambda *a, **k: waits.append(2) or False
    assert make_player(unknown, Browser())("b") == "Playing X (a song) in the assistant's YouTube Music window." and waits == [1]


def test_no_match_is_a_final_answer_not_a_reason_to_try_another_window():
    from voice_assistant.ytm_browser import YtmNoMatch

    class Br:
        extension_id, connected = "x", True

        def play(self, q, open_tab=None, judge=None, library=None):
            raise YtmNoMatch("Could not find 'q' on YouTube Music. Nothing played.")

    class Own:
        def play(self, q, judge=None, library=None):
            raise AssertionError("must not be tried: it would find the same results")

    assert make_player(Br(), Own())("q").startswith("Could not find")


def test_a_closed_browser_is_started_with_the_users_youtube_music_then_waited_for_before_searching():
    events = []

    class Br:
        extension_id, connected = "x", False

        def wait_connected(self, seconds=8.0, *a, **k):
            events.append(("wait", seconds))
            if seconds == 30:
                self.connected = True  # the extension came up once the browser started
            return self.connected

        def play(self, q, open_tab=None, judge=None, library=None):
            events.append(("play", q))
            return "Playing Forever by Lowx (a song) in your YouTube Music."

    class Own:
        def play(self, q, judge=None, library=None):
            raise AssertionError("the assistant's own window must not be used")

    out = make_player(Br(), Own(), open_tab=lambda: events.append("opened"), sleep=lambda s: events.append(("sleep", s)))("Forever by Lowx")
    assert out.startswith("Playing Forever") and events == [("wait", 8.0), "opened", ("wait", 30), ("sleep", 4), ("play", "Forever by Lowx")]


class ActBridge:
    """A bridge double whose page answers like the real one: a song is playing, and control/radio behave as given."""
    extension_id, connected = "x", True

    def __init__(self, control_result="liked", state=None, after_radio=None):
        self.control_result, self.sent = control_result, []
        self.state = state if state is not None else {"paused": False, "title": "Dance With Me", "by": "Sam", "list": "PLx"}
        self.after_radio = after_radio

    def request(self, action, timeout=0, **kw):
        self.sent.append((action, kw))
        if action == "control":
            return {"ok": True, "result": self.control_result, "state": self.state}
        if action == "radio":
            if self.after_radio is not None:
                self.state = self.after_radio
            return {"ok": True, "result": "opened"}
        return {"ok": True, "state": self.state}


def test_music_actions_report_what_the_page_showed_not_just_that_a_button_was_pressed():
    from voice_assistant.ytm_bridge import make_music_actions

    nosleep = dict(sleep=lambda s: None)
    assert "Liked Dance With Me by Sam" in make_music_actions(ActBridge("liked"), **nosleep)("like")
    assert "already liked" in make_music_actions(ActBridge("already liked"), **nosleep)("like")
    assert "Removed your like" in make_music_actions(ActBridge("like removed"), **nosleep)("unlike")
    assert "queue order changed" in make_music_actions(ActBridge("shuffle toggled"), **nosleep)("shuffle")
    with pytest.raises(YtmError, match="did not confirm"):
        make_music_actions(ActBridge("unconfirmed"), **nosleep)("like")
    assert make_music_actions(ActBridge(), **nosleep)("now") == "Playing: Dance With Me by Sam."
    with pytest.raises(YtmError, match="Nothing is playing"):
        make_music_actions(ActBridge(state={"title": ""}), **nosleep)("like")
    with pytest.raises(YtmError, match="extension"):
        make_music_actions(None)("like")
    with pytest.raises(YtmError, match="action must be"):
        make_music_actions(ActBridge())("explode")


def test_radio_opens_more_like_this_and_waits_for_the_radio_to_play():
    from voice_assistant.ytm_bridge import make_music_actions

    t = iter(range(10_000))
    ok = ActBridge(after_radio={"paused": False, "title": "Again", "by": "Other", "list": "RDAMVMabc"})
    out = make_music_actions(ok, sleep=lambda s: None, clock=lambda: next(t))("more like this")
    assert out.startswith("Playing more like Dance With Me by Sam") and "starting with Again by Other" in out and ("radio", {}) in ok.sent
    t = iter(range(10_000))
    stuck = ActBridge(after_radio={"paused": False, "title": "Dance With Me", "by": "Sam", "list": "PLx"})  # the page never moved to a radio
    with pytest.raises(YtmError, match="did not start playing"):
        make_music_actions(stuck, sleep=lambda s: None, clock=lambda: next(t))("radio")


def test_sleep_timer_pauses_after_the_time_and_can_be_cancelled():
    from voice_assistant.config import Config
    from voice_assistant.music import build_music_tools

    made, paused = [], []

    class FakeTimer:
        def __init__(self, secs, fn):
            self.secs, self.fn, self.cancelled = secs, fn, False
            made.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    tools = {t.name: t.handler for t in build_music_tools(Config(), lambda m: True, lambda: [], music_act=lambda a: f"did {a}", pause=lambda: paused.append(1), timer=FakeTimer)}
    assert "30 minute" in tools["sleep_timer"](30) and made[0].secs == 1800
    tools["sleep_timer"](10)  # a new timer replaces the old one
    assert made[0].cancelled and made[1].secs == 600
    made[1].fn()
    assert paused == [1]
    assert tools["sleep_timer"](0) == "Sleep timer cancelled." and made[1].cancelled and tools["sleep_timer"](999).startswith("Error")
    assert tools["music_action"]("like") == "did like"
