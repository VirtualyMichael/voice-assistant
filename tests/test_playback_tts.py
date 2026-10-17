import threading

import numpy as np
import pytest

import voice_assistant.audio_io as aio
from voice_assistant.chunker import SentenceChunker, clean_for_speech
from voice_assistant.config import Config
from voice_assistant.models import KOKORO_EN_V019


class FakeStream:
    instances = []

    def __init__(self, **kw):
        self.kw, self.writes, self.closed, self.aborted, self.stopped = kw, [], False, False, False
        FakeStream.instances.append(self)

    def start(self): pass
    def write(self, x): self.writes.append(len(x))
    def stop(self): self.stopped = True
    def abort(self): self.aborted = True
    def close(self): self.closed = True


class FakeSD:
    OutputStream = FakeStream

    @staticmethod
    def query_devices():
        return [{"name": "Out", "max_output_channels": 2, "max_input_channels": 0, "hostapi": 0}]


@pytest.fixture
def io(monkeypatch):
    FakeStream.instances.clear()
    monkeypatch.setattr(aio, "_sd", lambda: FakeSD)
    return aio.AudioIO(Config(output_device="Out"), lambda x: None)


def test_tail_silence_written_and_stream_kept_open(io):
    sr = 16000
    started = []
    io.player(np.ones(sr // 2, np.float32), sr, threading.Event(), lambda: started.append(1))
    s = FakeStream.instances[0]
    assert started and not s.closed  # not closed right after the last write, so the end is not clipped
    assert s.writes[0] == int(sr * aio.LEAD_IN_S)  # lead-in on a fresh stream
    assert s.writes[-1] == int(sr * aio.TAIL_S)  # trailing silence after the last word
    assert sum(s.writes) == sr // 2 + int(sr * aio.LEAD_IN_S) + int(sr * aio.TAIL_S)


def test_second_sentence_reuses_stream_without_lead_in(io):
    sr = 16000
    io.player(np.ones(sr // 4, np.float32), sr, threading.Event(), lambda: None)
    io.player(np.ones(sr // 4, np.float32), sr, threading.Event(), lambda: None)
    assert len(FakeStream.instances) == 1
    s = FakeStream.instances[0]
    assert s.writes.count(int(sr * aio.LEAD_IN_S)) == 1


def test_cancel_aborts_buffered_audio(io):
    ev = threading.Event()
    ev.set()
    io.player(np.ones(16000, np.float32), 16000, ev, lambda: None)
    s = FakeStream.instances[0]
    assert s.aborted and s.closed


def test_chunker_never_exceeds_max_chars():
    c = SentenceChunker(max_chars=80)
    out = c.feed("word " * 100)
    assert out and all(len(x) <= 80 for x in out)


def test_numbers_and_jargon_are_spoken():
    s = clean_for_speech("At 3:45 pm it cost $5.20, 50% off, 1.5x faster in VRChat on 2024-05-03.")
    for expect in ("three forty five p m", "five dollars and twenty cents", "fifty percent", "one point five times", "V R Chat", "May third"):
        assert expect in s
    assert clean_for_speech("Add 3-5 minutes, e.g. Dr. Lee", {"Lee": "Lea"}) == "Add three to five minutes, for example Doctor Lea"


def test_voice_table_has_both_genders_and_unique_ids():
    assert {v.gender for v in KOKORO_EN_V019} == {"female", "male"}
    assert len({v.sid for v in KOKORO_EN_V019}) == len(KOKORO_EN_V019)
