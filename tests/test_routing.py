import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import voice_assistant.audio_io as aio
from voice_assistant.config import Config
from voice_assistant.routing import (
    ADAPTER_KEY, LISTEN_KEY, NAME_KEY, ListenRoute, double_voice_warnings, listen_routes, parse_listen_enabled,
)

TRUE = bytes.fromhex("0b0000000100000000ffff00"[:0] + "0b00000001000000ffff0000")  # VT_BOOL true, as stored by Windows
FALSE = bytes.fromhex("0b0000000100000000000000")


def test_parse_listen_flag():
    assert parse_listen_enabled(TRUE) is True
    assert parse_listen_enabled(FALSE) is False
    assert parse_listen_enabled(b"") is False and parse_listen_enabled("x") is False and parse_listen_enabled(None) is False


def fake_reader(listen_on=True, target="{0.0.0.00000000}.{AAA}"):
    capture = {
        "{mic1}": {"state": 1, "props": {NAME_KEY: "Microphone", ADAPTER_KEY: "Razer Seiren Mini", f"{LISTEN_KEY},1": TRUE if listen_on else FALSE, f"{LISTEN_KEY},0": target}},
        "{mic2}": {"state": 1, "props": {NAME_KEY: "Microphone", ADAPTER_KEY: "VIVE Pro", f"{LISTEN_KEY},1": FALSE}},
        "{mic3}": {"state": 4, "props": {NAME_KEY: "Unplugged", f"{LISTEN_KEY},1": TRUE}},  # not an active device
    }
    render = {"{aaa}": {"state": 1, "props": {NAME_KEY: "CABLE Input", ADAPTER_KEY: "VB-Audio Virtual Cable"}}}
    return lambda flow: capture if flow == "Capture" else render


def test_listen_routes_found_with_resolved_target():
    assert listen_routes(fake_reader()) == [ListenRoute("Microphone (Razer Seiren Mini)", "CABLE Input (VB-Audio Virtual Cable)")]
    assert listen_routes(fake_reader(listen_on=False)) == []


def test_warning_only_when_listen_hits_the_cable_we_forward_into():
    routes = listen_routes(fake_reader())
    cfg = Config(mic_forward=True, output_device_2="CABLE Input (VB-Audio Virtual C")  # saved names are truncated by Windows
    msgs = double_voice_warnings(cfg, routes)
    assert len(msgs) == 1 and "hears you twice" in msgs[0] and "Razer Seiren Mini" in msgs[0] and "Listen" in msgs[0]
    assert double_voice_warnings(Config(mic_forward=False, output_device_2=cfg.output_device_2), routes) == []  # we do not forward: no clash
    assert double_voice_warnings(Config(mic_forward=True, output_device_2="Headphones"), routes) == []  # listen goes elsewhere
    assert double_voice_warnings(cfg, []) == []


def test_registry_failure_is_not_fatal():
    def broken(flow):
        raise OSError("no access")

    assert listen_routes(broken) == []


# ---- one pipeline at a time ----------------------------------------------------
class FakeStream:
    live = 0
    lock = threading.Lock()
    peak = 0

    def __init__(self, **kw):
        self.kw = kw
        self.started = False
        time.sleep(0.002)  # widen the window in which two threads could interleave

    def start(self):
        self.started = True
        with FakeStream.lock:
            FakeStream.live += 1
            FakeStream.peak = max(FakeStream.peak, FakeStream.live)

    def stop(self):
        pass

    def close(self):
        if self.started:
            with FakeStream.lock:
                FakeStream.live -= 1
            self.started = False


class FakeSD:
    InputStream = OutputStream = FakeStream

    @staticmethod
    def query_devices():
        return [{"name": "Mic A", "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
                {"name": "Mic B", "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
                {"name": "CABLE Input", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
                {"name": "CABLE Output", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0}]


@pytest.fixture
def fake_audio(monkeypatch):
    FakeStream.live = FakeStream.peak = 0
    monkeypatch.setattr(aio, "_sd", lambda: FakeSD)


def test_concurrent_restarts_never_leave_two_pipelines(fake_audio):
    io = aio.AudioIO(Config(mic_forward=True, input_device="Mic A", vr_input_device="Mic B", output_device_2="CABLE Input"), lambda x: None)
    threads = []
    for i in range(12):
        def go(vr=bool(i % 2)):
            io.vr_active = vr
            io.start_input()
        t = threading.Thread(target=go)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    assert FakeStream.live == 2  # exactly one microphone stream and one forwarding stream remain
    assert FakeStream.peak <= 2  # and there was never a moment with a second copy
    io.stop_input()
    assert FakeStream.live == 0


def test_same_mic_twice_and_cable_loop_are_refused(fake_audio):
    warned = []
    io = aio.AudioIO(Config(input_device="Mic A", input_device_2="Mic A"), lambda x: None, warned.append)
    io.start_input()
    assert FakeStream.live == 1  # the duplicate second input was dropped
    io.stop_input()
    io = aio.AudioIO(Config(mic_forward=True, input_device="CABLE Output", output_device_2="CABLE Input"), lambda x: None, warned.append)
    io.start_input()
    assert not io.forwarding and any("loop" in w for w in warned)
    io.stop_input()


def test_routing_summary_names_only_the_active_profile(fake_audio):
    cfg = Config(mic_forward=True, input_device="Razer", vr_input_device="Vive mic", output_device="Headphones",
                 vr_output_device="Vive speakers", output_device_2="CABLE Input")
    io = aio.AudioIO(cfg, lambda x: None)
    assert "Razer" in io.routing_summary() and "Vive" not in io.routing_summary() and "Headphones" in io.routing_summary()
    io.vr_active = True
    s = io.routing_summary()
    assert "Vive mic" in s and "Vive speakers" in s and "Razer" not in s and "Headphones" not in s


# ---- VR switching in the window --------------------------------------------------
def test_flapping_vr_states_apply_one_at_a_time_and_only_the_newest_wins(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import voice_assistant.app as A

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr(A, "VR_SETTLE_S", 0.05)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True, vr_announce=False))
    try:
        applied, running, overlap = [], [0], [False]

        class FakeAudio:
            vr_active = False
            level = 0.0

            def refresh_devices(self):
                running[0] += 1
                overlap[0] |= running[0] > 1
                time.sleep(0.03)
                applied.append(self.vr_active)

            def start_input(self):
                running[0] -= 1
                return True

            def routing_summary(self):
                return "x"

            def stop_input(self): pass
            def close_outputs(self): pass

        w.audio = FakeAudio()
        w.session.recognizer = object()  # so start_input is exercised
        for state in (True, False, True, True, False, True):
            w._on_vr(state)
        time.sleep(1.0)
        assert not overlap[0]  # never two applies at once
        assert applied and applied[-1] is True  # the newest state is what ends up applied
        assert len(applied) < 12  # superseded switches were skipped instead of all running
    finally:
        w.session.recognizer = None
        w.close()
