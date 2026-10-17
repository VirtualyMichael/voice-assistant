import time

import numpy as np
from test_session import FakeRec, Rec, SlowLLM, make_speaker, wait

from voice_assistant.agent import Agent
from voice_assistant.config import Config
from voice_assistant.session import Session, State

HALF_SECOND = np.zeros(8000, np.float32)


class Events(Rec):
    def __init__(self):
        super().__init__()
        self.users = []

    def user_final(self, t):
        self.users.append(t)


class FakeAccurate:
    def __init__(self, reply="I'm going into the kitchen.", fail=False):
        self.reply, self.fail, self.heard = reply, fail, []

    def transcribe(self, samples):
        self.heard.append(len(samples))
        if self.fail:
            raise RuntimeError("model crashed")
        return self.reply


def build(accurate, **cfg_kw):
    rec, ev = FakeRec(), Events()
    cfg = Config(listen_tone=False, follow_up=False, **cfg_kw)
    return Session(cfg, Agent(SlowLLM(["Okay."]), "sys"), None, rec, events=ev, accurate=accurate), rec, ev


def say(s, rec, text="in two the kitchen", chunks=3):
    s.activate()
    s.feed_audio(HALF_SECOND)  # the first chunk after activation resets the recognizer
    time.sleep(0.05)
    for _ in range(chunks - 1):
        s.feed_audio(HALF_SECOND)
    time.sleep(0.05)
    rec.t, rec.end = text, True
    s.feed_audio(HALF_SECOND)


def test_accurate_text_replaces_what_the_fast_model_heard():
    acc = FakeAccurate()
    s, rec, ev = build(acc)
    say(s, rec)
    assert ev.done.wait(3)
    assert ev.users == ["I'm going into the kitchen."]
    assert acc.heard and acc.heard[0] >= 8000 * 3  # the whole sentence, not just the last chunk
    s.close()


def test_fast_text_used_when_accurate_fails_or_is_empty():
    for acc in (FakeAccurate(fail=True), FakeAccurate(reply="")):
        s, rec, ev = build(acc)
        say(s, rec)
        assert ev.done.wait(3)
        assert ev.users == ["in two the kitchen"]
        s.close()


def test_very_short_audio_is_not_rechecked():
    acc = FakeAccurate()
    s, rec, ev = build(acc)
    s.activate()
    s.feed_audio(np.zeros(160, np.float32))
    time.sleep(0.05)
    rec.t, rec.end = "yes", True
    s.feed_audio(np.zeros(160, np.float32))
    assert ev.done.wait(3)
    assert acc.heard == [] and ev.users == ["yes"]
    s.close()


def test_silence_before_speech_is_not_sent_to_the_accurate_model():
    acc = FakeAccurate()
    s, rec, ev = build(acc)
    s.activate()
    for _ in range(40):  # 20 s of quiet
        s.feed_audio(HALF_SECOND)
    time.sleep(0.3)
    speech = (0.1 * np.sin(2 * np.pi * 220 * np.arange(8000) / 16000)).astype(np.float32)
    rec.t, rec.end = "hello there", True
    s.feed_audio(speech)
    assert ev.done.wait(3)
    assert 8000 <= acc.heard[0] <= 16000 * 3  # half a second of pre-roll plus the speech, not 20 s of silence
    s.close()


def test_barge_in_keeps_the_words_spoken_before_the_interruption():
    acc = FakeAccurate(reply="actually play some jazz")
    spk, played = make_speaker(0.6)
    rec, ev = FakeRec(), Events()
    cfg = Config(listen_tone=False, follow_up=False, interrupt_mode="voice")
    s = Session(cfg, Agent(SlowLLM(["A long answer here. ", "And a second part. "]), "sys"), spk, rec, events=ev, accurate=acc)
    s.activate()
    s.feed_audio(HALF_SECOND)
    time.sleep(0.05)
    rec.t, rec.end = "tell me something", True
    s.feed_audio(HALF_SECOND)
    assert wait(lambda: s.state is State.SPEAKING)
    rec.t, rec.end = "actually play some jazz", False
    s.feed_audio(HALF_SECOND)
    s.feed_audio(HALF_SECOND)
    assert wait(lambda: s.state is State.LISTENING)
    time.sleep(0.05)
    rec.t, rec.end = "actually play some jazz instead", True
    s.feed_audio(HALF_SECOND)
    assert wait(lambda: len(ev.users) == 2)
    assert acc.heard[-1] >= 8000 * 3  # the interrupting words were buffered while the assistant was still talking
    s.close()


def test_follow_up_window_is_configurable_and_can_be_off():
    # off: after a spoken turn the mic is not reopened
    s, rec, ev = build(None, follow_up_s=0.0)
    s.cfg.follow_up = True
    say(s, rec, "hello there", chunks=1)
    assert ev.done.wait(3)
    assert wait(lambda: s.state is State.IDLE)
    s.close()

    # short window: listening reopens, then closes by itself after that many seconds of silence
    s, rec, ev = build(None, follow_up_s=0.3)
    s.cfg.follow_up = True
    say(s, rec, "hello there", chunks=1)
    assert ev.done.wait(3)
    assert wait(lambda: s.state is State.LISTENING)
    time.sleep(0.5)
    s.feed_audio(HALF_SECOND)
    assert wait(lambda: s.state is State.IDLE)
    s.close()
