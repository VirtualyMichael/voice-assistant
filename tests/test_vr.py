import threading
import time

from voice_assistant.config import Config
from voice_assistant.vr import VrWatcher, vr_running


def test_vr_running_matches_case_insensitively():
    assert vr_running(lister=lambda: ["explorer.exe", "VRServer.exe"])
    assert not vr_running(lister=lambda: ["steam.exe", "chrome.exe"])
    assert vr_running(["custom.exe"], lister=lambda: ["Custom.EXE"])


def test_config_vr_device_profile():
    c = Config(input_device="Razer", vr_input_device="Index Mic")
    assert c.device("input_device") == "Razer"
    assert c.device("input_device", vr=True) == "Index Mic"
    assert Config(input_device="Razer").device("input_device", vr=True) == "Razer"  # empty VR device = keep normal


def test_watcher_debounces_and_reports_changes():
    states = iter([False, True, False, True, True, True, False, False])  # one-poll blips are ignored
    seen, last = [], [False]

    def probe():
        last[0] = next(states, last[0])
        return last[0]

    done = threading.Event()
    w = VrWatcher(lambda a: (seen.append(a), done.set() if len(seen) == 2 else None), interval_s=0.01, probe=probe)
    w.start()
    assert done.wait(2)
    w._stop.set()
    assert seen == [True, False]


def test_stop_restores_normal_devices():
    seen = []
    w = VrWatcher(seen.append, interval_s=0.01, probe=lambda: True)
    w.start()
    time.sleep(0.2)
    w.stop()
    assert seen == [True, False]
