import threading

import voice_assistant.audio_io as aio
from voice_assistant.config import Config


class FakeSD:
    calls = []

    @classmethod
    def _terminate(cls):
        cls.calls.append("terminate")

    @classmethod
    def _initialize(cls):
        cls.calls.append("initialize")

    class OutputStream:
        def __init__(self, **kw): FakeSD.calls.append("open")
        def start(self): pass
        def write(self, x): pass
        def abort(self): FakeSD.calls.append("abort")
        def stop(self): pass
        def close(self): FakeSD.calls.append("close")

    @staticmethod
    def query_devices():
        return [{"name": "Out", "max_output_channels": 2, "max_input_channels": 0, "hostapi": 0}]


def test_refresh_closes_streams_before_reinitialising(monkeypatch):
    import numpy as np

    FakeSD.calls.clear()
    monkeypatch.setattr(aio, "_sd", lambda: FakeSD)
    io = aio.AudioIO(Config(output_device="Out"), lambda x: None)
    io.player(np.ones(1600, np.float32), 16000, threading.Event(), lambda: None)
    io.refresh_devices()
    c = FakeSD.calls
    assert c.index("close") < c.index("terminate") < c.index("initialize")
    assert io._outs == {}  # no stale stream survives the refresh
    io.player(np.ones(1600, np.float32), 16000, threading.Event(), lambda: None)
    assert c.count("open") == 2  # reopened against the fresh device list


def test_refresh_waits_for_playback_to_finish(monkeypatch):
    import numpy as np

    FakeSD.calls.clear()
    monkeypatch.setattr(aio, "_sd", lambda: FakeSD)
    io = aio.AudioIO(Config(output_device="Out"), lambda x: None)
    inside, release, order = threading.Event(), threading.Event(), []

    def slow_on_start():
        inside.set()
        release.wait(2)
        order.append("playback done")

    t = threading.Thread(target=io.player, args=(np.ones(800, np.float32), 16000, threading.Event(), slow_on_start))
    t.start()
    inside.wait(2)
    r = threading.Thread(target=lambda: (io.refresh_devices(), order.append("refreshed")))
    r.start()
    release.set()
    t.join(2)
    r.join(2)
    assert order == ["playback done", "refreshed"]
