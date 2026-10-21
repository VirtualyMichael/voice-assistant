import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import os  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_launches(monkeypatch):
    """Tests must never start real programs. (A test once launched explorer.exe with a made-up app id, which opened a
    real File Explorer window on every run.) Tests that exercise launching patch these with their own fakes."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to launch a real program or open a real window")

    monkeypatch.setattr("voice_assistant.agent_tools.launch_start_app", refuse)
    monkeypatch.setattr("voice_assistant.apps.launch_start_app", refuse)
    monkeypatch.setattr(os, "startfile", refuse, raising=False)
    monkeypatch.setattr("webbrowser.open", refuse)
    monkeypatch.setattr("voice_assistant.agent_tools.list_windows", lambda: [])  # no real windows: results must not depend on what is open
    monkeypatch.setattr("voice_assistant.agent_tools.focus_window", refuse)
    import voice_assistant.app as app_module

    class QuietBridge(app_module.YtmBridge):  # tests that build the window must not open real network ports
        def start(self):
            self.start_called = True
            return False

    monkeypatch.setattr("voice_assistant.app.YtmBridge", QuietBridge)
    monkeypatch.setattr("keyboard.send", refuse)  # no test may press keys on the real desktop
    monkeypatch.setattr("keyboard.write", refuse)
    monkeypatch.setattr("voice_assistant.computer._foreground", lambda: None)  # tests never ask the real desktop which window is in front
    monkeypatch.setattr("voice_assistant.computer._verify_done", lambda *a, **k: True)
