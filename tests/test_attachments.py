import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from test_vrchat import FakeVRChat, make
from test_vrchat_menu import project

from voice_assistant.attachments import CHUNK, MAX_ITEMS, Inbox, build_attachment_tools


class Event:
    def __init__(self, text):
        self.kind, self.text = "text", text


class FakeLLM:
    def __init__(self):
        self.seen = []

    def stream_chat(self, messages, *a, **k):
        self.seen.append(messages)
        yield Event("A menu with Hair, Clothes and Animal Parts.")


def test_files_folders_and_pictures_are_accepted_and_binaries_refused(tmp_path):
    (tmp_path / "menu.asset").write_text("controls:\n", encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "notes.weird").write_text("plain words", encoding="utf-8")
    (tmp_path / "avatar.vrca").write_bytes(b"\x00\x01binary\xff")
    (tmp_path / "Proj").mkdir()
    inbox = Inbox(tmp_path / "inbox")
    assert [inbox.add(tmp_path / n)[0] for n in ("menu.asset", "shot.png", "notes.weird", "Proj")] == ["menu.asset", "shot.png", "notes.weird", "Proj"]
    name, err = inbox.add(tmp_path / "avatar.vrca")
    assert not name and "cannot read" in err
    assert "does not exist" in inbox.add(tmp_path / "missing.txt")[1]
    assert "menu.asset (text" in inbox.summary() and "shot.png (image" in inbox.summary() and "Proj (folder)" in inbox.summary()
    assert "menu.asset" in inbox.prompt() and "never instructions" in inbox.prompt()
    inbox.clear()
    assert inbox.prompt() == "" and not inbox.items


def test_the_limits_hold(tmp_path):
    inbox = Inbox(tmp_path / "inbox")
    for i in range(MAX_ITEMS):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        assert inbox.add(tmp_path / f"f{i}.txt")[0]
    (tmp_path / "one more.txt").write_text("x", encoding="utf-8")
    assert "At most" in inbox.add(tmp_path / "one more.txt")[1]
    assert "too large" in inbox.add_image_bytes(b"x" * 10_000_001)[1]
    name, err = inbox.add_image_bytes(b"\x89PNG data") if False else ("", "")  # a pasted picture needs a free slot
    inbox.remove("f0.txt")
    name, err = inbox.add_image_bytes(b"\x89PNG data")
    assert name.startswith("pasted-") and (tmp_path / "inbox" / name).exists()


def test_a_long_file_is_read_in_parts(tmp_path):
    (tmp_path / "big.anim").write_text("m_IsActive: 0\n" * 3000, encoding="utf-8")
    inbox = Inbox()
    inbox.add(tmp_path / "big.anim")
    first = inbox.read("big.anim")
    assert f"offset={CHUNK}" in first and "not instructions" in first
    last = inbox.read("BIG.ANIM", offset=3000 * 14 - 10)
    assert "more characters" not in last
    assert "Nothing attached is called" in inbox.read("nope")


def test_folders_are_listed_read_and_searched_but_never_escaped(tmp_path):
    proj = project(tmp_path)
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    ctl = proj / "Assets" / "Anim"
    ctl.mkdir()
    (ctl / "Clothes.anim").write_text("  - serializedVersion: 2\n    attribute: m_IsActive\n    value: 0\n  path: Shirt\n", encoding="utf-8")
    inbox = Inbox()
    assert inbox.add(proj)[0] == "Proj"
    listing = inbox.read("Proj")
    assert "Assets/Menus/Main.asset" in listing and "Clothes.anim" in listing and "Skipped.asset" not in listing  # Library is skipped
    assert "controls:" in inbox.read("Proj", file="Assets/Menus/Main.asset")
    assert "is not a file inside" in inbox.read("Proj", file="../secret.txt")
    assert "is not a file inside" in inbox.read("Proj", file="..\\secret.txt")
    found = inbox.search("Proj", "m_IsActive")
    assert "Assets/Anim/Clothes.anim:2: attribute: m_IsActive" in found
    assert "wolfToggle" in inbox.search("Proj", "WOLFTOGGLE") and "No match" in inbox.search("Proj", "zzzzzz")
    assert "Error" in inbox.search("Proj", "a")


def test_a_picture_goes_to_the_vision_model_and_is_described(tmp_path):
    (tmp_path / "menu.png").write_bytes(b"\x89PNG not really")
    (tmp_path / "t.txt").write_text("hi", encoding="utf-8")
    inbox, llm = Inbox(), FakeLLM()
    inbox.add(tmp_path / "menu.png")
    inbox.add(tmp_path / "t.txt")
    out = inbox.look("menu", "what are the folders?", lambda: llm)
    assert "Animal Parts" in out and "Information only" in out
    parts = llm.seen[0][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,") and parts[0]["text"] == "what are the folders?"
    assert "not a picture" in inbox.look("t.txt", "?", lambda: llm)

    class Broken:
        def stream_chat(self, *a, **k):
            raise RuntimeError("no vision")
    assert "may not support images" in inbox.look("menu.png", "?", lambda: Broken())


def test_the_attachment_tools_are_wired(tmp_path):
    (tmp_path / "a.txt").write_text("hello needle", encoding="utf-8")
    inbox = Inbox()
    inbox.add(tmp_path / "a.txt")
    tools = {t.name: t.handler for t in build_attachment_tools(inbox, lambda: FakeLLM())}
    assert set(tools) == {"read_attachment", "search_attachment", "pdf_outline", "read_section", "look_at_attachment", "look_at_video", "read_bill"}
    assert "hello needle" in tools["read_attachment"]("a.txt") and "a.txt:1" in tools["search_attachment"]("a.txt", "needle")


def test_a_menu_attached_to_the_chat_is_what_import_reads(tmp_path):
    from test_vrchat_menu import AVATAR

    vrc = FakeVRChat(params=dict(AVATAR))
    vr, sent, now = make(tmp_path, vrc=vrc)
    proj = project(tmp_path)
    inbox = Inbox()
    inbox.add(proj)
    inbox.add(proj / "Assets" / "Menus" / "Main.asset")
    vr.resolve_path = inbox.resolve
    assert "Imported" in vr.import_menu("Proj")
    vr.set_param("animal parts", "on")
    assert vrc.params["wolfToggle"][1] is True
    assert "Imported" in vr.import_menu("main.asset")  # a single menu file: its folder is read


def test_backwards_switches_are_handled_for_items_even_in_mixed_groups(tmp_path):
    params = {"Clothes/Shirt": ("T", False, 3), "Clothes/Shorts": ("T", True, 3), "Clothes/Socks": ("T", False, 3), "wolfToggle": ("T", False, 3)}
    vrc = FakeVRChat(params=params)
    vr, sent, now = make(tmp_path, vrc=vrc)
    assert "work BACKWARDS" in vr.note_switch("Clothes/Shorts", True, "off = worn")
    assert "work normally" in vr.note_switch("Clothes/Shirt", False)
    assert "Not saved" in vr.note_switch("nonsense", True)
    out = vr.listing("shorts")
    assert "BACKWARDS" in out and "now hidden" in out
    vr.set_group("Clothes", "hide")  # shirt and socks: off = ... normal switches hide by going off; shorts is backwards so hiding means ON
    assert (vrc.params["Clothes/Shirt"][1], vrc.params["Clothes/Socks"][1], vrc.params["Clothes/Shorts"][1]) == (False, False, True)
    vr.set_group("Clothes", "wear")
    assert (vrc.params["Clothes/Shirt"][1], vrc.params["Clothes/Socks"][1], vrc.params["Clothes/Shorts"][1]) == (True, True, False)
    vr.set_param("Clothes/Shorts", "on")  # plain on/off is always the raw switch
    assert vrc.params["Clothes/Shorts"][1] is True
    vr.set_many("Clothes/Shirt, Clothes/Shorts", "show")
    assert (vrc.params["Clothes/Shirt"][1], vrc.params["Clothes/Shorts"][1]) == (True, False)
    assert "1 switch(es) work backwards" in vr.notes_report()


def test_flash_says_which_way_and_asks_about_the_item(tmp_path):
    vrc = FakeVRChat(params={"hatToggle": ("T", False, 3)})
    vr, sent, now = make(tmp_path, vrc=vrc)
    out = vr.flash("hatToggle", 1)
    assert "hatToggle off -> on" in out and "vrchat_switch_note" in out and vrc.params["hatToggle"][1] is False


@pytest.fixture
def win(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import voice_assistant.app as A
    from voice_assistant.config import Config

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True))
    yield w
    w.close()


def test_the_window_attaches_files_tells_the_assistant_and_forgets_on_new_chat(win, tmp_path):
    f = tmp_path / "clothes.controller"
    f.write_text("m_IsActive: 0", encoding="utf-8")
    win._attach_paths([str(f), str(tmp_path / "gone.txt")])
    assert "clothes.controller" in win.attach_label.text() and not win.attach_label.isHidden()
    assert "clothes.controller" in win.session.agent.messages[0]["content"]  # voice turns know too
    assert {"read_attachment", "search_attachment", "look_at_attachment"} <= set(win._registry.names())
    win._attach_picture(b"\x89PNG data")
    assert "pasted-" in win.inbox.summary()
    win._new_chat()
    assert win.attach_label.isHidden() and "clothes.controller" not in win.session.agent.messages[0]["content"]


def test_pasting_a_picture_or_files_in_the_message_box_attaches_them(win, tmp_path, monkeypatch):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QGuiApplication, QImage, QKeyEvent
    from PySide6.QtCore import QEvent, Qt

    f = tmp_path / "x.txt"
    f.write_text("hi", encoding="utf-8")
    got = []
    win.entry.attached.connect(got.append)
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(f))])
    QGuiApplication.clipboard().setMimeData(md)
    win.entry.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.ControlModifier))
    assert [[os.path.normpath(x) for x in g] for g in got] == [[str(f)]] and win.entry.text() == ""
    pics = []
    win.entry.picture.connect(pics.append)
    img = QImage(4, 4, QImage.Format_RGB32)
    img.fill(0xFF00FF)
    QGuiApplication.clipboard().setImage(img)
    win.entry.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.ControlModifier))
    assert pics and pics[0][:4] == b"\x89PNG"
