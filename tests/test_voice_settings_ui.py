import io
import os
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QComboBox, QGroupBox, QLabel, QPushButton

import voice_assistant.app as A
from voice_assistant.config import Config
from voice_assistant.models import MultiSynth, Voice

app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True))
    yield w
    w.close()


def group(w) -> QGroupBox:
    return next(g for g in w.settings.findChildren(QGroupBox) if g.title() == "More lifelike voices")


def test_lifelike_group_says_where_online_text_goes(win):
    g = group(win)
    text = " ".join(l.text() for l in g.findChildren(QLabel))
    assert "sent to ElevenLabs" in text and "sent to that company" in text and "right to use" in text
    buttons = {b.text() for b in g.findChildren(QPushButton)}
    assert "Download Pocket (~160 MB)" in buttons and "Add a voice from a recording…" in buttons and "Set ElevenLabs key…" in buttons


class FakeEngine:
    engine, prefix, speed = "pocket", "pocket:", 1.0

    def __init__(self, folder):
        self.folder = folder
        self.refresh_voices()

    def refresh_voices(self):
        self.voices = [Voice(f"pocket:{p.stem}", f"{p.stem} (Pocket)", "other", 0) for p in sorted(self.folder.glob("*.wav"))]

    def set_voice(self, vid):
        return next((v for v in self.voices if v.id == vid), Voice(vid, vid, "other", 0))


def test_adding_a_recording_copies_it_and_refreshes_the_voice_list(win, tmp_path, monkeypatch):
    builtin = FakeEngine(tmp_path / "nothing")
    builtin.engine, builtin.voices = "kokoro", [Voice("af", "Bella (US)", "female", 0)]
    builtin.refresh_voices = lambda: None
    win.synth = MultiSynth([builtin, FakeEngine(tmp_path / "voices")])
    win._voice_box = QComboBox()
    win._fill_voice_box("af")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(np.zeros(32000, "<i2").tobytes())
    src = tmp_path / "grandpa.wav"
    src.write_bytes(buf.getvalue())
    monkeypatch.setattr(A.QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), ""))
    win._add_recorded_voice()
    assert (tmp_path / "voices" / "grandpa.wav").exists()
    items = [win._voice_box.itemText(i) for i in range(win._voice_box.count())]
    assert items == ["Female — Bella (US)", "grandpa (Pocket)"] and win._voice_box.currentData() == "af"
