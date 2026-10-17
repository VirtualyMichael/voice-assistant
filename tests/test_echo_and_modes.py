import time

from test_session import AUDIO, FakeRec, Rec, SlowLLM, make_speaker, speak_utterance, wait

from voice_assistant.agent import Agent
from voice_assistant.config import Config
from voice_assistant.session import Session, State


def build(cfg, deltas=("The weather today is sunny and warm. ", "Tomorrow looks cloudy and cool. "), play_s=0.4):
    rec, ev = FakeRec(), Rec()
    spk, played = make_speaker(play_s)
    return Session(cfg, Agent(SlowLLM(deltas), "sys"), spk, rec, events=ev), rec, ev, played


def test_own_voice_is_not_a_barge_in():
    s, rec, ev, played = build(Config(listen_tone=False, interrupt_mode="voice", follow_up=False))
    speak_utterance(s, rec)
    assert wait(lambda: s.state is State.SPEAKING)
    rec.t = "the weather today is sunny and warm"  # mic hears the speakers
    s.feed_audio(AUDIO)
    time.sleep(0.15)
    assert s.state is State.SPEAKING
    assert ev.done.wait(3)
    s.close()


def test_real_speech_still_interrupts():
    s, rec, ev, played = build(Config(listen_tone=False, interrupt_mode="voice", follow_up=False))
    speak_utterance(s, rec)
    assert wait(lambda: s.state is State.SPEAKING)
    rec.t = "actually play some jazz instead"
    s.feed_audio(AUDIO)
    assert wait(lambda: s.state is State.LISTENING)
    s.close()


def test_typed_turn_does_not_reopen_mic_in_push_mode():
    s, rec, ev, played = build(Config(listen_tone=False, follow_up=True, listen_mode="push"), play_s=0.0)
    s.submit_text("hi")
    assert ev.done.wait(3)
    assert wait(lambda: s.state is State.IDLE)
    s.close()


def test_always_mode_listens_from_start_and_after_turn():
    s, rec, ev, played = build(Config(listen_tone=False, listen_mode="always", follow_up=False), play_s=0.0)
    s.start()
    assert s.state is State.LISTENING
    s.submit_text("hi")
    assert ev.done.wait(3)
    assert wait(lambda: s.state is State.LISTENING)
    s.close()
