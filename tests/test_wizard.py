import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import voice_assistant.setup_wizard as W
from voice_assistant.config import Config

app = QApplication.instance() or QApplication([])


def test_check_items_reports_missing_local_server(monkeypatch):
    monkeypatch.setattr(W, "list_devices", lambda kind: ["Default", "Mic A"] if kind == "input" else ["Speakers"])
    monkeypatch.setattr(W, "detect_gpus", lambda: [])
    monkeypatch.setattr(W.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(W.httpx.ConnectError("down")))
    items = W.check_items(Config())
    texts = [t for _, t in items]
    assert any("Microphone" in t for t in texts) and any("No local AI server" in t for t in texts)
    assert [ok for ok, t in items if "local AI server" in t] == [False]
    assert [ok for ok, t in items if "VB-Cable" in t] == [False]  # optional items warn instead of blocking


def test_models_page_preselects_recommended_and_skips_installed(tmp_path):
    cfg = Config(models_dir=str(tmp_path))
    (tmp_path / "asr").mkdir()
    (tmp_path / "asr" / "tokens.txt").write_text("x")
    page = W.ModelsPage(cfg)
    state = {s.slot: (cb.isChecked(), cb.isEnabled()) for s, cb in page.boxes}
    assert state["asr"] == (False, False)  # already installed
    assert state["kokoro"] == (True, True) and state["kws"] == (False, True)
    assert page.isComplete()


def test_brain_page_saves_cloud_choice_and_key(monkeypatch):
    saved = {}
    monkeypatch.setattr(W, "save_key", lambda p, k: saved.update({p: k}))
    cfg = Config()
    page = W.BrainPage(cfg)
    page.claude.setChecked(True)
    page.key.setText("sk-ant-test")
    assert page.validatePage()
    assert cfg.llm_provider == "anthropic" and cfg.cloud_ack and saved == {"anthropic": "sk-ant-test"}


def test_brain_page_local_keeps_model_name():
    cfg = Config()
    page = W.BrainPage(cfg)
    page.model.setText("my-model")
    assert page.validatePage() and cfg.llm_provider == "local" and cfg.llm_model == "my-model"


def test_devices_page_writes_choices():
    cfg = Config()
    page = W.DevicesPage(cfg)
    page.mic.addItem("Fake Mic", "Fake Mic")
    page.mic.setCurrentIndex(page.mic.count() - 1)
    assert page.validatePage() and cfg.input_device == "Fake Mic" and cfg.output_device == ""
