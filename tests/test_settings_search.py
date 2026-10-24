import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import voice_assistant.app as A
from voice_assistant.config import Config
from voice_assistant.settings_search import SettingsIndex

app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True, check_updates=False))
    yield w
    w.close()


def index(w) -> SettingsIndex:
    tabs = w._form.forms and w.settings.findChild(A.QTabWidget)
    names = list(A.PAGES)
    return SettingsIndex(tabs, names, {n: A.PAGES[n][0] for n in names})


def test_the_video_and_photo_settings_have_a_tab_of_their_own(win):
    nav = win.settings.findChild(A.QListWidget, "nav")
    names = [nav.item(i).text() for i in range(nav.count())]
    assert "Camera & photos" in names
    found = index(win).search("video")
    assert found and found[0].tab_name == "Video"
    titles = " | ".join(e.title for e in found)
    assert "Listen to the video's sound" in titles and "Work out what the video is about" in titles and "close-up frames" in titles.lower()
    ai_titles = [e.title for e in index(win).entries() if e.tab_name == "AI"]
    assert not any("video" in t.lower() for t in ai_titles)  # they moved out of the AI tab


def test_everyday_words_find_the_right_settings(win):
    idx = index(win)
    camera = idx.search("camera")
    assert sum(e.tab_name == "Video" for e in camera[:8]) >= 4  # "camera" lands on the video and photo settings
    assert any("Research photos online" in e.title for e in camera)
    assert any("sound" in e.title.lower() and e.tab_name == "Video" for e in idx.search("sound"))
    assert any(e.tab_name == "Tasks" for e in idx.search("reminder"))
    assert any("Save my chats" in e.title for e in idx.search("history"))
    assert idx.search("zzzzqq") == [] and idx.search("") == [] and idx.search("a") == []
    assert len(idx.search("e", limit=5)) <= 5


def test_titles_rank_above_tooltips_and_results_do_not_repeat(win):
    found = index(win).search("custom")
    titles = [(e.tab, e.title) for e in found]
    assert len(titles) == len(set(titles))
    assert any(e.tab_name == "Video" for e in found)  # the level box lists Custom among its choices
    exact = index(win).search("close-up frames")
    assert exact and exact[0].tab_name == "Video" and "close-up" in exact[0].title.lower()


def test_typing_in_the_search_box_shows_results_and_clicking_one_jumps_there(win):
    tabs = win.settings.findChild(A.QTabWidget)
    stack = tabs.parentWidget()
    nav = win.settings.findChild(A.QListWidget, "nav")
    results = [w for w in win.settings.findChildren(A.QListWidget) if w.objectName() == "nav"][1]
    assert stack.currentIndex() == 0
    win.settings_search.setText("video sound")
    assert stack.currentIndex() == 1 and results.count() >= 1
    first = results.item(0)
    assert "Camera & photos" in first.text() and "›" in first.text()
    results.itemClicked.emit(first)
    assert stack.currentIndex() == 0 and tabs.currentIndex() == nav.currentRow() == 3  # the Video tab
    win.settings_search.setText("")
    assert stack.currentIndex() == 0
    win.settings_search.setText("nothingmatchesthis")
    assert stack.currentIndex() == 1 and results.count() == 1 and "Nothing matches" in results.item(0).text()
    results.itemClicked.emit(results.item(0))  # the hint line is not a result: nothing happens
    assert stack.currentIndex() == 1


def test_the_video_tab_holds_the_level_the_toggles_and_the_photo_settings(win):
    tabs = win.settings.findChild(A.QTabWidget)
    page = tabs.widget(3).widget()
    assert win.video_box.parentWidget() is not None and page.isAncestorOf(win.video_box)
    texts = " ".join(c.text() for c in page.findChildren(A.QCheckBox))
    assert "Listen to the video's sound" in texts and "Research photos online" in texts and "Pl@ntNet" in texts and "Skip frames" in texts
