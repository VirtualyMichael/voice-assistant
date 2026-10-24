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
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Voice", "Phone", "Audio", "VR", "AI", "Skills", "Apps", "Home", "Tasks", "Memory", "Delegate", "Look", "General"]
    assert "Ask before opening an app" in content["Apps"]
    assert "Switch devices while SteamVR is running" in content["VR"]
    assert "Save my chats (history)" in content["AI"]


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


# ---- skills tab -------------------------------------------------------------------------
def _make_skill(root, name, desc):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nInstructions for {name}.", encoding="utf-8")
    return d


def test_skills_tab_lists_toggles_and_updates_the_prompt(win, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog

    _make_skill(tmp_path / "pack", "chem-helper", "Chemistry help")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "pack"))
    win._add_skill_folder()
    assert win.skills_list.count() == 1 and "chem-helper" in win.session.agent.messages[0]["content"]
    assert win.cfg.skill_folders == [str(tmp_path / "pack")]
    win.skills_list.item(0).setCheckState(Qt.Unchecked)  # untick = the assistant stops using it
    assert "chem-helper" not in win.session.agent.messages[0]["content"] and win.cfg.disabled_skills == ["chem-helper"]
    win.skills_list.item(0).setCheckState(Qt.Checked)
    assert "chem-helper" in win.session.agent.messages[0]["content"] and win.cfg.disabled_skills == []
    win.skill_folders_list.setCurrentRow(0)
    win._remove_skill_folder()
    assert win.skills_list.count() == 0 and "chem-helper" not in win.session.agent.messages[0]["content"]


def test_skill_tools_are_always_registered(win):
    assert {"use_skill", "read_skill_file", "search_skill_pdf", "read_skill_pdf"} <= set(win._registry.names())


def test_mcp_import_dialog_adds_only_ticked_servers_with_optional_trust(win, tmp_path, monkeypatch):
    import json

    from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog

    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {"a": {"command": "py", "args": ["a.py"]}, "b": {"command": "py"}, "c": {"command": "py"}}}))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(f), ""))

    def fake_exec(self):
        boxes = {c.text().split("  ")[0]: c for c in self.findChildren(QCheckBox) if "  —  " in c.text()}
        trusts = [c for c in self.findChildren(QCheckBox) if c.text().startswith("run its tools")]
        boxes["a"].setChecked(True)
        boxes["c"].setChecked(True)
        trusts[2].setChecked(True)  # trust only server c
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    win._import_mcp()
    assert win.cfg.mcp_servers == {"a": {"command": "py", "args": ["a.py"]}, "c": {"command": "py", "auto_approve": True}}


def test_glass_style_is_see_through_and_solid_style_is_not():
    from voice_assistant.theme import build_style, on_accent, window_tint

    solid, glass = build_style(glass=False), build_style(glass=True, tint=40)
    assert "QWidget { background: #14161b;" in solid and "QWidget { background: transparent;" in glass
    assert "rgba(" in glass.split("#panel")[1][:60]
    assert on_accent("#fbbf24") == "#0b0d12" and on_accent("#1e3a8a") == "#ffffff"
    assert window_tint(0) < window_tint(100) <= 0.75


def test_look_settings_change_the_config(win):
    win._set_look("accent", "#2dd4bf")
    win._set_look("glass_tint", 80)
    assert win.cfg.accent == "#2dd4bf" and win.cfg.glass_tint == 80
    assert "#2dd4bf" in win.styleSheet() or "45, 212, 191" in win.styleSheet()


def test_settings_page_replaces_the_chat(win):
    win.show()
    win._gear.setChecked(True)
    assert win.settings.isVisible() and not win.chat_page.isVisible()
    win._gear.setChecked(False)
    assert win.chat_page.isVisible() and not win.settings.isVisible()


def test_jarvis_preset_sets_voice_persona_and_wake_phrase(win):
    win._apply_jarvis_preset()
    assert win.cfg.tts_voice == "bm_george" and win.cfg.persona_jarvis and win.cfg.wake_phrase == "Jarvis"


def test_the_window_gives_the_bridge_the_real_ports_and_starts_it(win):
    from voice_assistant.ytm_bridge import PORTS

    assert win.ytm_bridge.ports == PORTS and win.ytm_bridge.start_called and win.ytm_bridge.extension_id == ""  # a positional slip once made the port list empty


def test_open_extension_folder_says_so_when_it_is_missing(win, monkeypatch, tmp_path):
    import voice_assistant.app as app_module

    monkeypatch.setattr(app_module, "extension_folder", lambda: tmp_path / "nope")
    win._open_extension_folder()
    assert any("not found" in lbl.text() for lbl in win.chat._labels)


def test_ask_first_switches_are_in_settings_and_change_the_config(win):
    for key in ("links", "screen", "clipboard", "routines"):
        box = getattr(win, "ask_" + key)
        assert box.isChecked() is True  # everything asks by default
        box.setChecked(False)
        assert getattr(win.cfg, "confirm_" + key) is False
        box.setChecked(True)
        assert getattr(win.cfg, "confirm_" + key) is True


def test_voice_tab_has_cloned_voice_controls(win):
    tabs, content = tab_text(win)
    assert "Speak in my cloned voice (Chatterbox, restart to apply)" in content["Voice"]
    assert win._clone_text().startswith("Cloned voice: off")
    clone = next(c for c in tabs.widget(0).widget().findChildren(QCheckBox) if c.text().startswith("Speak in my cloned voice"))
    clone.setChecked(True)
    assert win.cfg.chatterbox_url == "http://127.0.0.1:8123"
    assert "on, server http://127.0.0.1:8123" in win._clone_status.text()
    clone.setChecked(False)
    assert win.cfg.chatterbox_url == ""
