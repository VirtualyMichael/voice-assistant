import json
import threading

import httpx
import pytest

from voice_assistant.agent import Agent
from voice_assistant.chunker import SentenceChunker, clean_for_speech
from voice_assistant.config import Config, load_config, save_config
from voice_assistant.llm import LLMError, OpenAICompatClient, StreamEvent, ToolCall
from voice_assistant.permissions import FolderPolicy, PermissionDenied
from voice_assistant.tools import build_file_tools


def test_chunker_sentences_and_decimals():
    c = SentenceChunker()
    out = []
    for piece in ["The value is 3.", "5 today. Anoth", "er sentence here! Tail"]:
        out += c.feed(piece)
    assert out == ["The value is 3.5 today.", "Another sentence here!"]
    assert c.flush() == "Tail"


def test_chunker_first_chunk_soft_break():
    c = SentenceChunker()
    out = c.feed("Well, that is a rather long opening clause without any full stop yet, and more")
    assert out and out[0].endswith(",")


def test_clean_for_speech():
    assert clean_for_speech("**Bold** and [link](http://x.y) `code`") == "Bold and link code"


def test_policy_blocks_escape(tmp_path):
    root = tmp_path / "ok"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    p = FolderPolicy([root])
    assert p.resolve("a.txt") == (root / "a.txt").resolve()
    for bad in ["../secret.txt", str(tmp_path / "secret.txt"), "a.txt:stream", "C:drive-relative"]:
        with pytest.raises(PermissionDenied):
            p.resolve(bad)
    with pytest.raises(PermissionDenied):
        FolderPolicy([]).resolve("a.txt")


def test_file_tools_confirm_and_reject(tmp_path):
    root = tmp_path / "ok"
    root.mkdir()
    answers = iter([False, True])
    asked = []
    reg = build_file_tools(FolderPolicy([root]), lambda q: asked.append(q) or next(answers))

    def call(name, **args):
        return reg.call(name, json.dumps(args))

    assert "declined" in call("write_text_file", path="n.txt", content="hi")
    assert not (root / "n.txt").exists()
    assert "Wrote" in call("write_text_file", path="n.txt", content="hi")
    assert call("read_text_file", path="n.txt") == "hi"
    assert "n.txt" in call("list_directory")
    assert "permission denied" in call("read_text_file", path=str(tmp_path / "x.txt")).lower()
    assert len(asked) == 2 and "Create" in asked[0]
    assert "unknown tool" in call("rm_rf")
    assert reg.call("read_text_file", "not json").startswith("Error")


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.MockTransport(lambda req: httpx.Response(200, text=body))


def test_llm_streams_text_and_tool_calls():
    t = {"choices": [{"delta": {"content": "Hel"}}]}
    t2 = {"choices": [{"delta": {"content": "lo"}}]}
    tc1 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "list_directory", "arguments": '{"pa'}}]}}]}
    tc2 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th":"."}'}}]}}]}
    evs = list(OpenAICompatClient("http://x/v1", "m", transport=sse(t, t2, tc1, tc2)).stream_chat([]))
    assert [e.text for e in evs if e.kind == "text"] == ["Hel", "lo"]
    assert evs[-1].calls == (ToolCall("c1", "list_directory", '{"path":"."}'),)


def test_llm_http_error():
    c = OpenAICompatClient("http://x/v1", "m", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(LLMError):
        list(c.stream_chat([]))


class ScriptedLLM:
    def __init__(self, rounds):
        self.rounds = list(rounds)

    def stream_chat(self, messages, tools=None, cancel=None):
        yield from self.rounds.pop(0)


def test_agent_tool_loop(tmp_path):
    (tmp_path / "f.txt").write_text("data")
    reg = build_file_tools(FolderPolicy([tmp_path]), lambda q: True)
    llm = ScriptedLLM([
        [StreamEvent("tool_calls", calls=(ToolCall("1", "read_text_file", '{"path":"f.txt"}'),))],
        [StreamEvent("text", text="It says data.")],
    ])
    a = Agent(llm, "sys", reg)
    assert "".join(a.respond("read f", threading.Event())) == "It says data."
    assert [m["role"] for m in a.messages] == ["system", "user", "assistant", "tool", "assistant"]
    assert a.messages[3]["content"] == "data"


def test_agent_keeps_interrupted_text():
    cancel = threading.Event()
    llm = ScriptedLLM([[StreamEvent("text", text="Part one."), StreamEvent("text", text=" Part two")]])
    a = Agent(llm, "sys")
    g = a.respond("hi", cancel)
    assert next(g) == "Part one."
    cancel.set()
    g.close()
    assert a.messages[-1]["content"] == "Part one. [interrupted]"


def test_agent_trim_keeps_user_boundary():
    a = Agent(ScriptedLLM([]), "sys", max_messages=4)
    for i in range(3):
        a.messages += [{"role": "user", "content": str(i)}, {"role": "assistant", "content": "r"}]
    a._trim()
    assert a.messages[0]["role"] == "system" and a.messages[1]["role"] == "user"


def test_config_roundtrip(tmp_path):
    f = tmp_path / "c.json"
    save_config(Config(interrupt_mode="ignore", llm_base_url="https://api.example.com/v1"), f)
    got = load_config(f)
    assert got.interrupt_mode == "ignore" and got.is_cloud and not Config().is_cloud
    f.write_text(json.dumps({"interrupt_mode": "bogus"}))
    with pytest.raises(ValueError):
        load_config(f)
