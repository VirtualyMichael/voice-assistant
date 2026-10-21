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
                    if msg["action"] == "search":
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
    assert ext.clicked == [("row", 0)] and ext.log[0] == "search"  # the Song, not the Video card


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
        def play(self, q):
            calls.append(q)
            return "Playing X (a song) in the assistant's YouTube Music window."

    player = make_player(bridge, Browser())
    assert player("a").startswith("Playing X") and calls == ["a"]  # extension not connected yet: own window
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
