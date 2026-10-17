import numpy as np

from voice_assistant.audio_io import DECIM, FORWARD_RATE, AudioIO
from voice_assistant.config import Config


class FakeStream:
    pass


def make():
    io = AudioIO(Config(mic_forward=True, output_device_2="CABLE Input"), lambda x: None)
    io._fwd_stream = FakeStream()  # pretend the output stream is open
    return io


def test_forward_buffer_is_capped_to_bound_latency():
    io = make()
    for _ in range(100):
        io._forward(np.ones(1440, np.float32))
    assert len(io._fwd_buf) == FORWARD_RATE // 4


def test_mute_stops_forwarding_and_flushes_queue():
    io = make()
    io._forward(np.ones(1440, np.float32))
    io.forward_muted = True
    assert len(io._fwd_buf) == 0
    io._forward(np.ones(1440, np.float32))
    assert len(io._fwd_buf) == 0
    io.forward_muted = False
    io._forward(np.ones(1440, np.float32))
    assert len(io._fwd_buf) == 1440


def test_assistant_gets_decimated_mic_only_game_gets_full_rate():
    got = []
    io = AudioIO(Config(mic_forward=True, output_device_2="CABLE Input"), got.append)
    io._rate, io._fwd_stream = FORWARD_RATE, FakeStream()
    block = np.full((1440, 1), 0.5, np.float32)
    io._primary_cb(block, 1440)
    assert len(got[0]) == 1440 // DECIM
    assert len(io._fwd_buf) == 1440
