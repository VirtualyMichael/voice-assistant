import threading
import time

import numpy as np

from voice_assistant.agent import Agent
from voice_assistant.config import Config
from voice_assistant.llm import StreamEvent
from voice_assistant.session import Session, SessionEvents, State
from voice_assistant.speaker import Speaker

AUDIO = np.zeros(160, np.float32)


class FakeRec:
    """Text is whatever the test sets; endpoint is a flag the test sets."""

    def __init__(self):
        self.t, self.end = "", False

    def accept(self, s):
        pass

    def text(self):
        return self.t

    def is_endpoint(self):
        return self.end

    def reset(self):
        self.t, self.end = "", False


class SlowLLM:
    def __init__(self, deltas, delay=0.01):
        self.deltas, self.delay = deltas, delay

    def stream_chat(self, messages, tools=None, cancel=None):
        for d in self.deltas:
            time.sleep(self.delay)
            if cancel is not None and cancel.is_set():
                return
            yield StreamEvent("text", text=d)


class Rec(SessionEvents):
    def __init__(self):
        self.deltas, self.done, self.finals = [], threading.Event(), []

    def user_final(self, t):
        self.finals.append(t)

    def assistant_delta(self, t):
        self.deltas.append(t)

    def assistant_done(self, m):
        self.done.set()


def make_speaker(play_s):
    played = []

    def player(samples, sr, cancel, on_start):
        on_start()
        end = time.monotonic() + play_s
        while time.monotonic() < end and not cancel.is_set():
            time.sleep(0.005)
        if not cancel.is_set():
            played.append(1)

    return Speaker(lambda text: (AUDIO, 16000), player), played


def wait(cond, t=3.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def build(mode="voice", deltas=("First sentence is here. ", "Second sentence follows. "), play_s=0.0):
    rec, ev = FakeRec(), Rec()
    spk, played = make_speaker(play_s)
    s = Session(Config(listen_tone=False, interrupt_mode=mode, follow_up=False), Agent(SlowLLM(deltas), "sys"), spk, rec, events=ev)
    return s, rec, ev, played


def speak_utterance(s, rec):
    s.activate()
    s.feed_audio(AUDIO)  # the first chunk after activation performs the recognizer reset
    time.sleep(0.05)
    rec.t, rec.end = "hello there", True
    s.feed_audio(AUDIO)


def talk_over(s, rec):
    rec.t = "talking over it"
    s.feed_audio(AUDIO)


def test_full_turn_streams_text_audio_and_metrics():
    s, rec, ev, played = build()
    speak_utterance(s, rec)
    assert ev.done.wait(3)
    assert "".join(ev.deltas).startswith("First sentence")
    assert len(played) == 2  # one audio chunk per sentence
    m = s.metrics.turns[0]
    assert m.first_text_ms is not None and m.first_audio_ms is not None
    assert m.first_audio_ms >= m.first_text_ms
    assert s.state is State.IDLE
    s.close()


def test_voice_barge_in_interrupts_and_returns_to_listening():
    s, rec, ev, played = build("voice", ("Long reply one. ", "Long reply two. ", "Long reply three. "), play_s=0.4)
    speak_utterance(s, rec)
    assert wait(lambda: s.state is State.SPEAKING)
    talk_over(s, rec)
    assert wait(lambda: s.state is State.LISTENING)
    time.sleep(0.6)
    assert not ev.done.is_set() and len(played) < 3
    s.close()


def test_hotkey_mode_ignores_voice_but_hotkey_stops():
    s, rec, ev, played = build("hotkey", play_s=0.3)
    speak_utterance(s, rec)
    assert wait(lambda: s.state is State.SPEAKING)
    talk_over(s, rec)
    time.sleep(0.1)
    assert s.state is State.SPEAKING
    s.toggle()
    assert s.state is State.IDLE
    s.close()


def test_ignore_mode_finishes_reply():
    s, rec, ev, played = build("ignore", play_s=0.1)
    speak_utterance(s, rec)
    assert wait(lambda: s.state is State.SPEAKING)
    talk_over(s, rec)
    s.toggle()  # hotkey does nothing while speaking in this mode
    assert s.state is State.SPEAKING
    assert ev.done.wait(3) and len(played) == 2
    s.close()


def test_listen_timeout_returns_to_idle():
    rec = FakeRec()
    s = Session(Config(listen_tone=False, listen_timeout_s=0.05), Agent(SlowLLM([]), "sys"), None, rec, events=Rec())
    s.activate()
    time.sleep(0.1)
    s.feed_audio(AUDIO)
    assert wait(lambda: s.state is State.IDLE)
    s.close()


def test_typed_text_works_without_audio_stack():
    ev = Rec()
    s = Session(Config(listen_tone=False), Agent(SlowLLM(["Typed reply."]), "sys"), None, None, events=ev)
    s.submit_text("hi")
    assert ev.done.wait(3) and "".join(ev.deltas) == "Typed reply."
    s.close()
