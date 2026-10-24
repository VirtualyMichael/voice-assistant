import json
import os
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from voice_assistant.memory import ChatLibrary, ConversationStore, merge_messages

U = lambda t: {"role": "user", "content": t}  # noqa: E731
A = lambda t: {"role": "assistant", "content": t}  # noqa: E731


def lib(tmp_path, **kw):
    return ChatLibrary(tmp_path / "chats", **kw)


def test_a_chat_is_saved_titled_listed_and_reloaded(tmp_path):
    L = lib(tmp_path)
    cid = L.new_id()
    assert L.save(cid, [U("What plant is on my desk and is it safe for my cat to chew on its leaves?"), A("A monstera.")])
    [info] = L.list()
    assert info.id == cid and info.count == 2 and info.title.endswith("…") and len(info.title) <= 48
    assert L.load(cid) == [U("What plant is on my desk and is it safe for my cat to chew on its leaves?"), A("A monstera.")]


def test_nothing_is_created_for_an_empty_conversation(tmp_path):
    L = lib(tmp_path)
    assert not L.save(L.new_id(), [{"role": "system", "content": "prompt"}])
    assert L.list() == []


def test_only_messages_are_kept_never_tool_output(tmp_path):
    L = lib(tmp_path)
    cid = L.new_id()
    L.save(cid, [{"role": "system", "content": "SECRET PROMPT"}, U("read it"), {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
                 {"role": "tool", "tool_call_id": "1", "content": "CONFIDENTIAL FILE"}, A("done")])
    raw = (tmp_path / "chats" / f"{cid}.json").read_text(encoding="utf-8")
    assert "SECRET" not in raw and "CONFIDENTIAL" not in raw


def test_the_trimmed_tail_the_model_holds_extends_the_saved_chat_instead_of_replacing_it(tmp_path):
    L = lib(tmp_path)
    cid = L.new_id()
    full = [U(f"q{i}") if i % 2 == 0 else A(f"a{i}") for i in range(10)]
    L.save(cid, full)
    # later the model only holds the last 4 messages, then two new ones arrive
    L.save(cid, full[-4:] + [U("q10"), A("a11")])
    assert [m["content"] for m in L.load(cid)] == [f"q{i}" if i % 2 == 0 else f"a{i}" for i in range(10)] + ["q10", "a11"]
    assert merge_messages([], [U("x")]) == [U("x")]
    assert merge_messages([U("a")], [U("b")]) == [U("a"), U("b")]  # no overlap: simply appended


def test_chats_are_capped_and_ordered_newest_first(tmp_path):
    L = lib(tmp_path, max_messages=6)
    a, b = L.new_id(), L.new_id()
    L.save(a, [U(f"m{i}") for i in range(10)])
    time.sleep(0.02)
    L.save(b, [U("second chat")])
    assert [c.id for c in L.list()] == [b, a] and L.list()[1].count == 6


def test_rename_sticks_and_search_finds_titles_and_text(tmp_path):
    L = lib(tmp_path)
    a, b = L.new_id(), L.new_id()
    L.save(a, [U("plant questions"), A("Water weekly.")])
    L.save(b, [U("timer stuff"), A("Set for ten minutes.")])
    L.rename(a, "Houseplants")
    L.save(a, [U("plant questions"), A("Water weekly."), U("more")])  # a later save must not reset a custom title
    assert {c.id: c.title for c in L.list()}[a] == "Houseplants"
    assert [c.id for c in L.list("ten minutes")] == [b] and [c.id for c in L.list("HOUSEPLANTS")] == [a] and L.list("zebra") == []


def test_delete_one_delete_all_and_bad_ids(tmp_path):
    L = lib(tmp_path)
    ids = [L.new_id() for _ in range(3)]
    for i in ids:
        L.save(i, [U("hi")])
    L.delete(ids[0])
    L.delete(ids[0])  # deleting twice is harmless
    assert len(L.list()) == 2 and L.delete_all() == 2 and L.list() == []
    for bad in ("../evil", "", "ABCDEF123456", "x" * 12):
        with pytest.raises(ValueError):
            L._path(bad)


def test_damaged_files_are_ignored(tmp_path):
    L = lib(tmp_path)
    good = L.new_id()
    L.save(good, [U("fine")])
    (tmp_path / "chats" / "aaaaaaaaaaaa.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "chats" / "bbbbbbbbbbbb.json").write_text(json.dumps({"messages": "nope"}), encoding="utf-8")
    assert [c.id for c in L.list()] == [good]


def test_the_old_single_conversation_becomes_the_first_chat_once(tmp_path):
    legacy = tmp_path / "conversation.json"
    ConversationStore(legacy).save([U("old question"), A("old answer")])
    L = lib(tmp_path, legacy=legacy)
    cid = L.migrate_legacy()
    assert cid and L.load(cid) == [U("old question"), A("old answer")] and not legacy.exists()
    assert L.migrate_legacy() is None


def test_dates_read_naturally():
    from voice_assistant.history_panel import when

    now = datetime(2026, 10, 24, 15, 0)
    assert when(datetime(2026, 10, 24, 9, 5).timestamp(), now) == "09:05"
    assert when((now - timedelta(days=1)).timestamp(), now) == "Yesterday"
    assert when((now - timedelta(days=3)).timestamp(), now) == "Wednesday"
    assert when(datetime(2026, 10, 2, 8, 0).timestamp(), now) == "2 Oct"


# ---- the window ------------------------------------------------------------------------------
@pytest.fixture
def win(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import voice_assistant.app as A_
    from voice_assistant.config import Config

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(A_, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w = A_.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True, check_updates=False))
    yield w
    w.close()


def talk(w, *pairs):
    w._save_chat([m for q, a in pairs for m in (U(q), A(a))])


def test_new_chat_keeps_the_old_one_in_the_list_and_it_can_be_reopened(win):
    talk(win, ("what time is it", "Noon."))
    first = win.chat_id
    win._new_chat()
    assert win.chat_id is None and win.session.agent.messages[1:] == []
    talk(win, ("set a timer", "Done."))
    assert len(win.chats.list()) == 2 and win.history_panel.list.count() == 2
    win._open_chat(first)
    assert win.chat_id == first and win.session.agent.messages[1:] == [U("what time is it"), A("Noon.")]
    assert win.chat._labels[-1].text() == "Noon."


def test_deleting_the_open_chat_starts_a_fresh_one_and_delete_all_empties_the_list(win):
    talk(win, ("one", "1"))
    win._delete_chat(win.chat_id)
    assert win.chats.list() == [] and win.chat_id is None
    talk(win, ("two", "2"))
    win._new_chat()
    talk(win, ("three", "3"))
    win._delete_all_chats()
    assert win.chats.list() == [] and win.history_panel.list.count() == 0 and not win.history_panel.list.isVisible()


def test_turning_history_off_stops_saving_but_keeps_saved_chats(win):
    talk(win, ("keep me", "ok"))
    win._set_remember(False)
    assert win.session.agent.on_update is None and not win.history_panel.banner.isHidden() and win._remember_cb.isChecked() is False
    win._new_chat()
    win._save_chat([U("not saved"), A("no")])
    assert [c.count for c in win.chats.list()] == [2] and win.chat_id is None
    win._set_remember(True)
    assert win.session.agent.on_update == win._save_chat


def test_the_sidebar_opens_and_closes_and_filters(win):
    talk(win, ("plants", "ok"))
    win._new_chat()
    talk(win, ("timers", "ok"))
    w0 = win.width()
    win.history_btn.setChecked(True)
    assert not win.history_panel.isHidden() and win.width() == w0 + 240  # offscreen windows are never shown, so check the flag
    win.history_panel.search.setText("plants")
    assert win.history_panel.list.count() == 1
    win.history_btn.setChecked(False)
    assert win.history_panel.isHidden() and win.width() == w0


def test_opening_a_chat_clicks_through_the_list(win):
    talk(win, ("first", "1"))
    first = win.chat_id
    win._new_chat()
    talk(win, ("second", "2"))
    item = next(win.history_panel.list.item(i) for i in range(win.history_panel.list.count()) if win.history_panel.list.item(i).data(0x0100) == first)
    win.history_panel.list.itemClicked.emit(item)
    assert win.chat_id == first
