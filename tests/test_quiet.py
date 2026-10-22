import subprocess
import sys

import pytest

from voice_assistant import quiet


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console flag")
def test_children_get_no_console_window(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)  # restored after the test
    quiet.hide_child_consoles()
    seen = {}
    real_init = subprocess.Popen.__mro__[1].__init__

    def spy(self, *a, **kw):
        seen.update(kw)
        real_init(self, *a, **kw)

    monkeypatch.setattr(subprocess.Popen.__mro__[1], "__init__", spy)
    out = subprocess.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert out.stdout.strip() == "hi"
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_second_call_does_not_stack(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)
    quiet.hide_child_consoles()
    first = subprocess.Popen
    quiet.hide_child_consoles()
    assert subprocess.Popen is first
