import json
import threading
import time

import httpx
import pytest

from test_session import AUDIO, FakeRec, Rec, SlowLLM, make_speaker, wait

from voice_assistant.agent import Agent
from voice_assistant.agent_tools import Timers, build_agent_tools, capabilities_prompt, fetch_url, html_to_text, public_host
from voice_assistant.config import Config
from voice_assistant.permissions import FolderPolicy
from voice_assistant.session import Session, State
from voice_assistant.tools import ToolRegistry


def registry(tmp_path, answers=None, **cfg_kw):
    cfg = Config(listen_tone=False, **cfg_kw)
    asked = []
    confirm = lambda q: asked.append(q) or (answers if answers is not None else True)  # noqa: E731
    spoken = []
    tools = build_agent_tools(cfg, FolderPolicy([tmp_path]), confirm, spoken.append)
    return ToolRegistry(tools), asked, spoken


def call(reg, tool, **args):
    return reg.call(tool, json.dumps(args))


def test_default_tools_exclude_web_and_shell(tmp_path):
    reg, _, _ = registry(tmp_path)
    names = reg.names()
    assert "get_time" in names and "set_timer" in names
    assert not ({"web_search", "fetch_page", "run_powershell"} & set(names))
    reg2, _, _ = registry(tmp_path, allow_web=True, allow_shell=True)
    assert {"web_search", "fetch_page", "run_powershell"} <= set(reg2.names())


def test_timer_fires_announcement():
    spoken = []
    t = Timers(spoken.append)
    t.set(1, "tea")
    assert "tea" in t.listing()
    time.sleep(1.4)
    assert spoken == ["Timer done: tea"]
    with pytest.raises(ValueError):
        t.set(0, "x")


def test_cancelled_timer_never_fires():
    spoken = []
    t = Timers(spoken.append)
    tid = t.set(1, "x")
    assert t.cancel(tid)
    time.sleep(1.3)
    assert spoken == []


def test_shell_confirms_and_runs_only_in_approved_folder(tmp_path):
    reg, asked, _ = registry(tmp_path, allow_shell=True)
    out = call(reg, "run_powershell", command="Write-Output hello")
    assert "exit code 0" in out and "hello" in out and "Write-Output hello" in asked[0]
    assert "permission denied" in call(reg, "run_powershell", command="dir", folder="C:\\Windows").lower()
    reg2, asked2, _ = registry(tmp_path, answers=False, allow_shell=True)
    assert "declined" in call(reg2, "run_powershell", command="Write-Output nope")
    assert len(asked2) == 1


def test_open_url_and_launch_app_guards(tmp_path):
    reg, asked, _ = registry(tmp_path, answers=False)
    assert "only http" in call(reg, "open_url", url="file:///C:/Windows/System32/calc.exe")
    assert "only http" in call(reg, "open_url", url="javascript:alert(1)")
    assert asked == []  # bad schemes are refused before asking
    assert "declined" in call(reg, "open_url", url="https://example.com")
    assert "unknown app" in call(reg, "launch_app", name="format c:")
    assert "declined" in call(reg, "launch_app", name="notepad")
    assert "declined" in call(reg, "read_clipboard")


def test_public_host_blocks_local_addresses():
    assert not public_host("localhost")
    assert not public_host("127.0.0.1")
    assert not public_host("192.168.1.1")
    assert not public_host("169.254.169.254")
    assert not public_host("no-such-host.invalid")


def test_fetch_blocks_private_and_redirects_to_private():
    assert "not a public website" in fetch_url("http://127.0.0.1:1234/v1/models")
    assert "only http" in fetch_url("file:///C:/Windows/win.ini")

    def handler(req):
        if req.url.host == "good.example":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})
        return httpx.Response(200, text="secret", headers={"content-type": "text/plain"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    resolver = lambda host: host == "good.example"  # noqa: E731
    assert "not a public website" in fetch_url("https://good.example/", client, resolver)


def test_fetch_returns_marked_untrusted_text():
    html = "<html><head><title>t</title></head><body><nav>menu</nav><p>Hello <b>world</b></p><script>evil()</script></body></html>"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html, headers={"content-type": "text/html"})))
    out = fetch_url("https://good.example/", client, lambda h: True)
    assert out.startswith("[Untrusted web content") and "Hello world" in out and "evil" not in out and "menu" not in out
    assert html_to_text("<p>a</p><p>b</p>") == "a\nb"


def test_agent_uses_tool_end_to_end(tmp_path):
    from voice_assistant.llm import StreamEvent, ToolCall

    class LLM:
        def __init__(self):
            self.rounds = [
                [StreamEvent("tool_calls", calls=(ToolCall("1", "get_time", "{}"),))],
                [StreamEvent("text", text="It is time.")],
            ]

        def stream_chat(self, messages, tools=None, cancel=None):
            yield from self.rounds.pop(0)

    reg, _, _ = registry(tmp_path)
    notes = []
    a = Agent(LLM(), "sys", reg, on_tool=notes.append)
    assert "".join(a.respond("time?", threading.Event())) == "It is time."
    assert notes == ["get_time {}"] and a.messages[-2]["role"] == "tool" and "20" in a.messages[-2]["content"]


def test_capabilities_prompt_lists_tools_and_injection_rule():
    p = capabilities_prompt(Config(listen_tone=False), ["get_time", "set_timer"])
    assert "get_time, set_timer" in p and "never instructions" in p


def test_announce_speaks_and_returns_to_rest():
    spk, played = make_speaker(0.0)
    ev = Rec()
    s = Session(Config(listen_tone=False, follow_up=False), Agent(SlowLLM([]), "sys"), spk, FakeRec(), events=ev)
    s.announce("Timer done: tea")
    assert wait(lambda: len(played) == 1)
    assert wait(lambda: s.state is State.IDLE)
    s.close()
