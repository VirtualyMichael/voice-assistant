"""The phone's history screen: list the PC's saved chats, open one, delete one."""
import json

from test_remote import Setup, collect, has
from voice_assistant.memory import ChatInfo
from voice_assistant.remote import ChatHooks


class FakeChats:
    def __init__(self):
        self.chats = {
            "aaa": {"title": "Pasta recipe", "updated": 2000.0, "messages": [{"role": "user", "content": "How long do I boil pasta?"}, {"role": "assistant", "content": "About ten minutes."}]},
            "bbb": {"title": "Alarm", "updated": 1000.0, "messages": [{"role": "user", "content": "Wake me at 7"}, {"role": "assistant", "content": "Done."}]},
            "ccc": {"title": "Empty", "updated": 500.0, "messages": []},
        }
        self.current, self.opened, self.deleted = "bbb", [], []

    def hooks(self):
        return ChatHooks(
            list=lambda: sorted((ChatInfo(i, c["title"], c["updated"], len(c["messages"])) for i, c in self.chats.items() if c["messages"]), key=lambda c: -c.updated),
            open=self.open, delete=self.delete, current=lambda: self.current)

    def open(self, chat_id):
        msgs = self.chats.get(chat_id, {}).get("messages")
        if not msgs:
            return None
        self.opened.append(chat_id)
        self.current = chat_id
        return msgs

    def delete(self, chat_id):
        self.deleted.append(chat_id)
        self.chats.pop(chat_id, None)


def connect(tmp_path, fake=None):
    s = Setup(tmp_path)
    if fake is not None:
        s.server.chat_hooks = fake.hooks()
    ws = s.connect()
    collect(ws, has("ready"))
    return s, ws


def ask(ws, kind, **extra):
    ws.send(json.dumps({"type": kind, **extra}))
    want = "chat_opened" if kind == "open_chat" else "chats"
    return next(m for m in collect(ws, has(want)) if m["type"] == want)


def test_the_phone_gets_the_list_newest_first_without_empty_chats(tmp_path):
    fake = FakeChats()
    s, ws = connect(tmp_path, fake)
    try:
        reply = ask(ws, "list_chats")
        assert [c["id"] for c in reply["items"]] == ["aaa", "bbb"] and reply["current"] == "bbb"
        assert reply["items"][0] == {"id": "aaa", "title": "Pasta recipe", "updated": 2000, "count": 2}
    finally:
        s.close()


def test_opening_a_chat_returns_its_messages_and_makes_it_the_conversation(tmp_path):
    fake = FakeChats()
    s, ws = connect(tmp_path, fake)
    try:
        reply = ask(ws, "open_chat", id="aaa")
        assert reply["ok"] and reply["messages"] == [{"role": "user", "text": "How long do I boil pasta?"}, {"role": "assistant", "text": "About ten minutes."}]
        assert fake.opened == ["aaa"] and fake.current == "aaa"
    finally:
        s.close()


def test_a_chat_that_cannot_be_opened_says_so(tmp_path):
    s, ws = connect(tmp_path, FakeChats())
    try:
        reply = ask(ws, "open_chat", id="missing")
        assert reply["ok"] is False and reply["messages"] == []
    finally:
        s.close()


def test_deleting_a_chat_returns_the_new_list(tmp_path):
    fake = FakeChats()
    s, ws = connect(tmp_path, fake)
    try:
        reply = ask(ws, "delete_chat", id="aaa")
        assert fake.deleted == ["aaa"] and [c["id"] for c in reply["items"]] == ["bbb"]
    finally:
        s.close()


def test_bad_ids_are_ignored_and_a_pc_without_chats_answers_with_an_empty_list(tmp_path):
    fake = FakeChats()
    s, ws = connect(tmp_path, fake)
    try:
        ws.send(json.dumps({"type": "open_chat", "id": "../../etc/passwd"}))
        ws.send(json.dumps({"type": "delete_chat", "id": "a b"}))
        assert ask(ws, "list_chats")["items"]  # the link still works, and nothing was opened or deleted
        assert fake.opened == [] and fake.deleted == []
    finally:
        s.close()
    s2, ws2 = connect(tmp_path / "other", None) if (tmp_path / "other").mkdir() is None else (None, None)
    try:
        assert ask(ws2, "list_chats") == {"type": "chats", "items": [], "current": ""}
    finally:
        s2.close()


def test_a_failing_store_does_not_drop_the_link(tmp_path):
    fake = FakeChats()
    hooks = fake.hooks()
    hooks.open = lambda chat_id: (_ for _ in ()).throw(OSError("disk"))
    s = Setup(tmp_path)
    s.server.chat_hooks = hooks
    ws = s.connect()
    collect(ws, has("ready"))
    try:
        reply = ask(ws, "open_chat", id="aaa")
        assert reply["ok"] is False and reply["error"] == "OSError"
        assert ask(ws, "list_chats")["items"]  # still connected
    finally:
        s.close()
