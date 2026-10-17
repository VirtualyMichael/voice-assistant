import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QTabWidget

import voice_assistant.app as A
from voice_assistant.config import Config
from voice_assistant.permissions import FolderPolicy

app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True))
    yield w
    w.close()


def tab_text(w):
    tabs = w.settings.findChild(QTabWidget)
    return tabs, {tabs.tabText(i): {c.text() for c in tabs.widget(i).widget().findChildren(QCheckBox)} for i in range(tabs.count())}


def test_settings_are_grouped_into_tabs(win):
    tabs, content = tab_text(win)
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Voice", "Audio", "VR", "AI", "Apps", "General"]
    assert "Ask before opening an app" in content["Apps"]
    assert "Switch devices while SteamVR is running" in content["VR"]
    assert "Remember our conversation between sessions" in content["AI"]
    assert not tabs.usesScrollButtons()  # all tab names fit without arrows


class FakeHolder:
    def __init__(self):
        import threading

        self.event, self.result = threading.Event(), False


def answer_yes_and_tick(monkeypatch, tick):
    def fake_exec(self):
        if self.checkBox() is not None:
            self.checkBox().setChecked(tick)
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


def test_dont_ask_again_checkbox_turns_the_app_confirmation_off(win, monkeypatch):
    answer_yes_and_tick(monkeypatch, True)
    h = FakeHolder()
    win._on_confirm("Open YouTube Music?", h)
    assert h.result is True and win.cfg.confirm_launch is False
    assert win.ask_apps.isChecked() is False  # the settings checkbox follows


def test_ticking_it_but_answering_no_changes_nothing(win, monkeypatch):
    def fake_exec(self):
        self.checkBox().setChecked(True)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    h = FakeHolder()
    win._on_confirm("Open Steam?", h)
    assert h.result is False and win.cfg.confirm_launch is True


def test_links_and_other_actions_never_get_the_dont_ask_box(win, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: (seen.append(self.checkBox()), QMessageBox.Yes)[1])
    for prompt in ("Open this link in your browser?\n\nhttps://example.com", "Run this PowerShell command in C:/x?\n\nls", "Replace your clipboard?"):
        win._on_confirm(prompt, FakeHolder())
    assert seen == [None, None, None] and win.cfg.confirm_launch is True


def test_approved_folders_can_be_added_and_removed_from_the_list(win, tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    win.policy.add(d)
    win.cfg.approved_folders.append(str(d))
    win._fill_folders()
    assert win.folders_list.count() == 1
    win.folders_list.setCurrentRow(0)
    win._remove_folder()
    assert win.cfg.approved_folders == [] and win.policy.roots == [] and win.folders_list.count() == 0


def test_folder_policy_remove_blocks_access_again(tmp_path):
    p = FolderPolicy([tmp_path])
    assert p.resolve("a.txt")
    p.remove(tmp_path)
    with pytest.raises(Exception):
        p.resolve("a.txt")
