import json
import threading
import time

import numpy as np
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from test_session import AUDIO, FakeRec, Rec, SlowLLM, wait
from voice_assistant.agent import Agent
from voice_assistant.config import Config
from voice_assistant.remote import PairedDevices, RemoteServer, tailscale_ip, to_pcm16
from voice_assistant.session import MultiEvents, Session, SessionEvents, State
from voice_assistant.speaker import Speaker


class Setup:
    """A real Session, Speaker and RemoteServer on a loopback port, with a fake recognizer and a fake local speaker."""

    def __init__(self, tmp_path, reply_s=0.0, deltas=("First sentence is here. ",), output="phone"):
        self.rec, self.events = FakeRec(), Rec()
        self.multi = MultiEvents(self.events)
        self.local_played = []
        local_audio = np.zeros(int(16000 * max(reply_s, 0.01)), np.float32)

        def local_player(samples, sr, cancel, on_start):
            on_start()
            self.local_played.append(len(samples))

        self.speaker = Speaker(lambda text: (local_audio, 16000), lambda *a: self.server.make_player(local_player)(*a))
        cfg = Config(listen_tone=False, interrupt_mode="voice", follow_up=False, remote_port=0, remote_output=output)
        self.session = Session(cfg, Agent(SlowLLM(deltas), "sys"), self.speaker, self.rec, events=self.multi)
        self.devices = PairedDevices(tmp_path / "devices.json")
        self.server = RemoteServer(self.session, self.multi, cfg, self.devices, host="127.0.0.1")
        assert self.server.start()
        self.device_id, self.token = self.devices.pair("Test phone")

    def connect(self, token=None, **kwargs):
        ws = connect(f"ws://127.0.0.1:{self.server.port}", **kwargs)
        ws.send(json.dumps({"type": "hello", "token": self.token if token is None else token, "device": "test"}))
        return ws

    def close(self):
        self.server.stop()
        self.session.close()


@pytest.fixture
def setup(tmp_path):
    s = Setup(tmp_path)
    yield s
    s.close()


def collect(ws, until, timeout=4.0):
    """Receive messages (dicts for JSON, bytes for audio) until `until(messages)` is true."""
    messages, end = [], time.monotonic() + timeout
    while time.monotonic() < end and not until(messages):
        try:
            raw = ws.recv(timeout=0.2)
        except TimeoutError:
            continue
        messages.append(raw if isinstance(raw, bytes) else json.loads(raw))
    return messages


def kinds(messages):
    return [m["type"] if isinstance(m, dict) else "audio" for m in messages]


def has(kind):
    return lambda messages: kind in kinds(messages)


def test_paired_phone_gets_ready(setup):
    ws = setup.connect()
    ready = collect(ws, has("ready"))[0]
    assert ready["type"] == "ready" and ready["protocol"] == 1 and ready["state"] == "idle"
    assert setup.server.status()["connected"]


def test_wrong_token_is_refused(setup):
    ws = setup.connect(token="not-a-real-token")
    messages = collect(ws, has("error"))
    assert "not paired" in messages[0]["message"]
    with pytest.raises(ConnectionClosed):
        ws.recv(timeout=2)
    assert not setup.server.status()["connected"]


def test_browsers_are_refused_even_with_a_valid_token(setup):
    with pytest.raises(ConnectionClosed):  # the server may close before the client even sends its hello
        ws = setup.connect(additional_headers={"Origin": "https://evil.example"})
        ws.recv(timeout=2)
    assert not setup.server.status()["connected"]


def test_revoked_phone_cannot_connect_again(setup):
    assert setup.devices.revoke(setup.device_id)
    ws = setup.connect()
    assert "not paired" in collect(ws, has("error"))[0]["message"]
    assert not setup.server.status()["connected"]


def test_voice_turn_from_the_phone(setup):
    ws = setup.connect()
    collect(ws, has("ready"))
    ws.send(json.dumps({"type": "wake"}))
    assert wait(lambda: setup.session.state is State.LISTENING)
    ws.send(to_pcm16(AUDIO).tobytes())  # the first chunk after activation resets the recognizer
    time.sleep(0.1)
    setup.rec.t = "what time is it"
    ws.send(to_pcm16(AUDIO).tobytes())
    ws.send(json.dumps({"type": "end_of_speech"}))
    messages = collect(ws, has("done"))
    assert {"state", "user_final", "assistant_delta", "done"} <= set(kinds(messages))
    assert next(m for m in messages if m["type"] == "user_final")["text"] == "what time is it"
    assert next(m for m in messages if m["type"] == "assistant_delta")["text"].startswith("First sentence")


def test_reply_audio_streams_to_the_phone_not_the_pc(setup):
    ws = setup.connect()
    collect(ws, has("ready"))
    ws.send(json.dumps({"type": "text", "text": "hello"}))
    messages = collect(ws, has("audio_end"))
    order = kinds(messages)
    assert order.index("audio_start") < order.index("audio") < order.index("audio_end")
    start = next(m for m in messages if m["type"] == "audio_start")
    assert start["sr"] == 16000
    assert sum(len(m) for m in messages if isinstance(m, bytes)) == start["samples"] * 2
    assert setup.local_played == []  # phone only: the PC speakers stay quiet


def test_both_mode_also_plays_on_the_pc(tmp_path):
    s = Setup(tmp_path, output="both")
    try:
        ws = s.connect()
        collect(ws, has("ready"))
        ws.send(json.dumps({"type": "text", "text": "hello"}))
        collect(ws, has("audio_end"))
        assert wait(lambda: s.local_played)
    finally:
        s.close()


def test_reply_plays_on_the_pc_when_no_phone_is_connected(setup):
    setup.session.submit_text("hello")
    assert wait(lambda: setup.events.done.is_set())
    assert wait(lambda: setup.local_played)


def test_interrupt_flushes_the_phones_audio(tmp_path):
    s = Setup(tmp_path, reply_s=2.0)
    try:
        ws = s.connect()
        collect(ws, has("ready"))
        ws.send(json.dumps({"type": "text", "text": "hello"}))
        collect(ws, has("audio_start"))
        ws.send(json.dumps({"type": "interrupt"}))
        messages = collect(ws, has("audio_flush"))
        assert "audio_flush" in kinds(messages)
        assert "audio_end" not in kinds(messages)
    finally:
        s.close()


def test_approval_round_trip(setup):
    ws = setup.connect()
    collect(ws, has("ready"))
    answer = []
    asker = threading.Thread(target=lambda: answer.append(setup.server.ask("Open notepad?", timeout=5)))
    asker.start()
    confirm = next(m for m in collect(ws, has("confirm")) if m["type"] == "confirm")
    assert confirm["prompt"] == "Open notepad?"
    ws.send(json.dumps({"type": "confirm_result", "id": confirm["id"], "ok": True}))
    asker.join(3)
    assert answer == [True]


def test_approval_without_a_phone_returns_none(setup):
    assert setup.server.ask("Open notepad?", timeout=0.2) is None


def test_approval_stops_when_the_phone_goes_away(setup):
    ws = setup.connect()
    collect(ws, has("ready"))
    answer = []
    asker = threading.Thread(target=lambda: answer.append(setup.server.ask("Delete it?", timeout=10)))
    asker.start()
    collect(ws, has("confirm"))
    ws.close()
    asker.join(4)
    assert answer == [None]


def test_a_newer_connection_replaces_the_older_one(setup):
    first = setup.connect()
    collect(first, has("ready"))
    second = setup.connect()
    collect(second, has("ready"))
    with pytest.raises(ConnectionClosed):
        for _ in range(20):
            first.recv(timeout=1)


def test_oversized_and_odd_audio_frames_are_ignored(setup):
    ws = setup.connect()
    collect(ws, has("ready"))
    fed = []
    setup.session.feed_audio = lambda samples, remote=False: fed.append(samples)
    ws.send(b"\x01")  # half a sample
    ws.send(b"\x00" * 200000)  # far more than any phone sends in one frame
    ws.send(to_pcm16(AUDIO).tobytes() + b"\x07")  # a whole frame plus a stray byte: keep the whole samples
    ws.send(json.dumps({"type": "ping"}))
    collect(ws, has("pong"))
    assert [len(x) for x in fed] == [len(AUDIO)]


def test_paired_devices_store_only_a_hash(tmp_path):
    path = tmp_path / "devices.json"
    devices = PairedDevices(path)
    device_id, token = devices.pair("Pixel")
    assert token not in path.read_text()
    assert devices.verify(token) == device_id
    assert PairedDevices(path).verify(token) == device_id  # survives a restart
    assert devices.verify("wrong") is None
    assert [d["name"] for d in devices.list()] == ["Pixel"]
    assert devices.revoke(device_id) and devices.verify(token) is None
    assert not devices.revoke(device_id)


def test_tailscale_ip_only_accepts_tailscale_addresses():
    class Out:
        def __init__(self, text):
            self.stdout = text

    assert tailscale_ip(lambda *a, **k: Out("100.126.148.55\n")) == "100.126.148.55"
    assert tailscale_ip(lambda *a, **k: Out("192.168.1.5\n")) != "192.168.1.5"


def test_server_refuses_to_start_without_tailscale(tmp_path):
    cfg = Config(remote_port=0)
    multi = MultiEvents()
    server = RemoteServer(None, multi, cfg, PairedDevices(tmp_path / "d.json"), find_ip=lambda: None)
    assert not server.start()
    assert "Tailscale" in server.error and not server.running


def test_finalize_ends_the_sentence_only_when_something_was_heard():
    rec, ev = FakeRec(), Rec()
    session = Session(Config(listen_tone=False, follow_up=False), Agent(SlowLLM(("Ok. ",)), "sys"), None, rec, events=ev)
    session.activate()
    session.feed_audio(AUDIO)  # performs the recognizer reset
    time.sleep(0.1)
    session.finalize()  # nothing heard yet: no turn
    time.sleep(0.2)
    assert session.state is State.LISTENING and not ev.deltas
    rec.t = "turn the lights on"
    session.finalize()
    assert wait(lambda: ev.done.is_set())
    session.close()


def test_multi_events_survive_a_failing_listener():
    seen = []

    class Boom(SessionEvents):
        def partial(self, text):
            raise RuntimeError("boom")

    class Seen(SessionEvents):
        def partial(self, text):
            seen.append(text)

    multi = MultiEvents(Boom(), Seen())
    multi.partial("hi")
    assert seen == ["hi"]


def test_a_phone_paired_by_another_process_is_accepted_while_the_server_runs(setup, tmp_path):
    """`phone pair` runs in its own process while the assistant is already running."""
    other = PairedDevices(tmp_path / "devices.json")  # a second process sharing the same file
    device_id, token = other.pair("Pixel from the command line")
    ws = setup.connect(token=token)
    assert collect(ws, has("ready"))[0]["type"] == "ready"
    assert other.revoke(device_id)
    assert setup.devices.verify(token) is None  # and a removal elsewhere takes effect too


def test_no_chime_or_follow_up_window_while_a_phone_is_connected(tmp_path):
    chimes = []
    s = Setup(tmp_path)
    try:
        s.session.cfg.listen_tone = True
        s.session.cfg.follow_up, s.session.cfg.follow_up_s = True, 2.0
        s.speaker.play_samples = lambda samples, sr: chimes.append(len(samples))
        s.session.activate()  # no phone yet: the PC plays its chime
        assert chimes
        s.session.stop()
        chimes.clear()
        ws = s.connect()
        collect(ws, has("ready"))
        assert s.server.phone_active()
        s.session.activate()
        assert not chimes
        s.session._voice_turn = True
        s.session.submit_text("hello")
        collect(ws, has("done"))
        time.sleep(0.2)
        assert s.session.state is State.IDLE  # no follow-up listening window waiting for a mic the phone is not streaming
        ws.close()
        assert wait(lambda: not s.server.phone_active())
    finally:
        s.close()


def test_the_recognizers_own_endpoint_does_not_end_a_phone_turn(setup):
    """Holding the button is the phone user's way of saying 'still talking', however long the pause."""
    ws = setup.connect()
    collect(ws, has("ready"))
    ws.send(json.dumps({"type": "wake"}))
    assert wait(lambda: setup.session.state is State.LISTENING)
    ws.send(to_pcm16(AUDIO).tobytes())  # the first chunk after activation resets the recognizer
    time.sleep(0.1)
    setup.rec.t, setup.rec.end = "tell me a short", True  # the recognizer thinks a pause means the sentence is over
    ws.send(to_pcm16(AUDIO).tobytes())
    time.sleep(0.3)
    assert setup.session.state is State.LISTENING and not setup.events.deltas
    setup.rec.t = "tell me a short story"
    ws.send(json.dumps({"type": "end_of_speech"}))
    messages = collect(ws, has("done"))
    assert next(m for m in messages if m["type"] == "user_final")["text"] == "tell me a short story"


def test_finalize_gives_the_recognizer_trailing_silence_before_reading_the_text():
    fed = []

    class Rec2(FakeRec):
        def accept(self, s):
            fed.append(len(s))

    rec, ev = Rec2(), Rec()
    session = Session(Config(listen_tone=False, follow_up=False), Agent(SlowLLM(("Ok. ",)), "sys"), None, rec, events=ev)
    session.activate()
    session.feed_audio(AUDIO)
    time.sleep(0.1)
    rec.t = "hello"
    before = len(fed)
    session.finalize()
    assert wait(lambda: ev.done.is_set())
    assert fed[before:] and fed[before] >= 8000  # at least half a second of padding
    session.close()


def test_wake_while_the_assistant_is_talking_interrupts_it_and_listens(tmp_path):
    s = Setup(tmp_path, reply_s=2.0)
    try:
        ws = s.connect()
        collect(ws, has("ready"))
        ws.send(json.dumps({"type": "text", "text": "hello"}))
        assert wait(lambda: s.session.state is State.SPEAKING)
        ws.send(json.dumps({"type": "wake"}))
        assert wait(lambda: s.session.state is State.LISTENING)
    finally:
        s.close()


def test_phone_asks_the_pc_to_tokenise_its_wake_phrase(setup, monkeypatch):
    seen = []

    def fake_lines(folder, phrase, boost=1.5, threshold=0.12):
        seen.append((str(folder).endswith("kws"), phrase, threshold))
        if phrase == "bad phrase":
            raise ValueError("no token for that")
        return "▁HEY ▁JARVIS :1.5 #0.12 @HEY_JARVIS\n"

    monkeypatch.setattr("voice_assistant.models.keyword_lines", fake_lines)
    ws = setup.connect()
    collect(ws, has("ready"))
    ws.send(json.dumps({"type": "get_keywords", "phrase": "hey jarvis", "threshold": 0.2}))
    reply = next(m for m in collect(ws, has("keywords")) if m["type"] == "keywords")
    assert reply["phrase"] == "hey jarvis" and "▁HEY ▁JARVIS" in reply["lines"]
    ws.send(json.dumps({"type": "get_keywords", "phrase": "bad phrase", "threshold": 99}))
    bad = next(m for m in collect(ws, has("keywords_error")) if m["type"] == "keywords_error")
    assert "no token" in bad["message"]
    assert seen == [(True, "hey jarvis", 0.2), (True, "bad phrase", 0.6)]  # the threshold is clamped to a sane range


def test_keyword_lines_matches_what_write_keywords_saves(tmp_path):
    from pathlib import Path

    from voice_assistant.config import load_config
    from voice_assistant.models import keyword_lines, write_keywords

    kws = Path(load_config().models_dir) / "kws"
    if not (kws / "bpe.model").exists():
        pytest.skip("the wake-word model is not installed on this machine")
    work = tmp_path / "kws"
    work.mkdir()
    for name in ("bpe.model", "tokens.txt"):
        (work / name).write_bytes((kws / name).read_bytes())
    text = keyword_lines(work, "Hey Jarvis", threshold=0.15)
    assert write_keywords(work, "Hey Jarvis", threshold=0.15).read_text(encoding="utf-8") == text
    assert text.splitlines()[0].endswith("#0.15 @HEY_JARVIS")
    assert len(text.splitlines()) == 1  # a multi-word phrase has no extra "hey"/"ok" variants


def test_this_pcs_own_microphone_is_ignored_while_a_phone_is_connected(setup):
    """Two streams of audio interleaved into one recognizer produce no words at all."""
    heard = []
    setup.rec.accept = heard.append
    setup.session.activate()
    setup.session.feed_audio(AUDIO)  # no phone yet: the PC mic counts
    assert wait(lambda: len(heard) == 1)
    ws = setup.connect()
    collect(ws, has("ready"))
    setup.session.feed_audio(AUDIO)  # the PC mic while the phone is connected: dropped
    ws.send(to_pcm16(AUDIO).tobytes())  # the phone's audio: used
    assert wait(lambda: len(heard) == 2)
    time.sleep(0.2)
    assert len(heard) == 2
