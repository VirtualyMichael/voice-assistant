import time

import numpy as np
from test_session import AUDIO, FakeRec, Rec, SlowLLM, make_speaker, wait

from voice_assistant.agent import Agent
from voice_assistant.config import Config
from voice_assistant.session import Session, State
from voice_assistant.tones import duration, listening_chime


def test_chime_is_short_quiet_and_click_free():
    c = listening_chime()
    assert 0.15 < duration(c) < 0.4
    assert abs(c).max() <= 0.3
    assert abs(c[0]) < 0.01 and abs(c[-1]) < 0.01  # starts and ends at silence: no click


def build(**kw):
    spk, played = make_speaker(0.0)
    rec, ev = FakeRec(), Rec()
    cfg = Config(follow_up=False, listen_delay_s=0.1, **kw)
    return Session(cfg, Agent(SlowLLM(["Okay."]), "sys"), spk, rec, events=ev), rec, ev, played


def test_activation_plays_chime_and_mic_waits_for_it():
    s, rec, ev, played = build()
    s.activate()
    assert wait(lambda: len(played) == 1)  # the chime went to the speaker
    rec.t, rec.end = "too early", True
    s.feed_audio(AUDIO)  # during the chime window: discarded, no turn starts
    time.sleep(0.15)
    assert s.state is State.LISTENING and not ev.deltas
    time.sleep(0.5)  # window over
    rec.t, rec.end = "hello there", True
    s.feed_audio(AUDIO)
    assert ev.done.wait(3)
    s.close()


def test_no_chime_when_disabled():
    s, rec, ev, played = build(listen_tone=False)
    s.activate()
    time.sleep(0.2)
    assert played == []
    s.close()
