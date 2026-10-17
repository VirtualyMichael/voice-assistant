import json
import threading

from voice_assistant.agent import Agent
from voice_assistant.llm import StreamEvent, ToolCall
from voice_assistant.memory import ConversationStore
from voice_assistant.tools import Tool, ToolRegistry


def test_save_keeps_only_user_and_final_assistant_text(tmp_path):
    store = ConversationStore(tmp_path / "c.json")
    store.save([
        {"role": "system", "content": "secret system prompt"},
        {"role": "user", "content": "read my file"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "CONFIDENTIAL FILE CONTENTS"},
        {"role": "assistant", "content": "It says hello."},
    ])
    raw = (tmp_path / "c.json").read_text(encoding="utf-8")
    assert "CONFIDENTIAL" not in raw and "secret system prompt" not in raw
    assert store.load() == [{"role": "user", "content": "read my file"}, {"role": "assistant", "content": "It says hello."}]


def test_load_survives_missing_and_damaged_files(tmp_path):
    p = tmp_path / "c.json"
    store = ConversationStore(p)
    assert store.load() == []
    p.write_text("{not json", encoding="utf-8")
    assert store.load() == []
    p.write_text(json.dumps({"messages": [{"role": "user"}]}), encoding="utf-8")
    assert store.load() == []


def test_history_is_capped_and_clear_erases(tmp_path):
    store = ConversationStore(tmp_path / "c.json", max_messages=4)
    store.save([{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(10)])
    assert [m["content"] for m in store.load()] == ["m6", "m7", "m8", "m9"]
    store.clear()
    store.clear()  # clearing twice is fine
    assert store.load() == []


class LLM:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def stream_chat(self, messages, tools=None, cancel=None):
        self.seen.append([dict(m) for m in messages])
        yield from self.replies.pop(0)


def test_agent_saves_after_turn_and_next_session_continues(tmp_path):
    store = ConversationStore(tmp_path / "c.json")
    a = Agent(LLM([[StreamEvent("text", text="Nice to meet you, Sam.")]]), "sys")
    a.on_update = store.save
    "".join(a.respond("My name is Sam.", threading.Event()))

    llm2 = LLM([[StreamEvent("text", text="Your name is Sam.")]])
    b = Agent(llm2, "sys")  # a new session
    b.load_history(store.load())
    "".join(b.respond("What is my name?", threading.Event()))
    roles = [m["role"] for m in llm2.seen[0]]
    assert roles == ["system", "user", "assistant", "user"]  # earlier exchange is in the prompt
    assert llm2.seen[0][1]["content"] == "My name is Sam."


def test_tool_turn_is_saved_without_tool_traffic(tmp_path):
    store = ConversationStore(tmp_path / "c.json")
    reg = ToolRegistry([Tool("ping", "d", {"type": "object", "properties": {}}, lambda: "PRIVATE RESULT")])
    llm = LLM([[StreamEvent("tool_calls", calls=(ToolCall("1", "ping", "{}"),))], [StreamEvent("text", text="Done.")]])
    a = Agent(llm, "sys", reg)
    a.on_update = store.save
    "".join(a.respond("ping it", threading.Event()))
    saved = store.load()
    assert saved == [{"role": "user", "content": "ping it"}, {"role": "assistant", "content": "Done."}]
    assert "PRIVATE RESULT" not in store.path.read_text(encoding="utf-8")


def test_reset_keeps_only_system_prompt():
    a = Agent(LLM([]), "sys")
    a.load_history([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    assert len(a.messages) == 3
    a.reset()
    assert a.messages == [{"role": "system", "content": "sys"}]


def test_save_failure_does_not_break_a_reply():
    a = Agent(LLM([[StreamEvent("text", text="ok")]]), "sys")
    a.on_update = lambda m: (_ for _ in ()).throw(OSError("disk full"))
    assert "".join(a.respond("hi", threading.Event())) == "ok"
