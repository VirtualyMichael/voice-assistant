import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest

from voice_assistant import odysseus
from voice_assistant.config import Config
from voice_assistant.tools import Tool, ToolRegistry


def registry():
    noop = lambda **k: "ok"  # noqa: E731
    obj = {"type": "object", "properties": {}}
    return ToolRegistry([Tool("get_time", "t", obj, noop), Tool("ask_odysseus", "o", obj, noop), Tool("other__x", "x", obj, noop)])


def configured(**kw):
    return Config(odysseus_mode="assist", odysseus_endpoint_id="ep", odysseus_model="m", **kw)


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(odysseus, "token", lambda: "ody_test")


def test_defaults_are_off_and_hold_no_personal_values():
    c = Config()
    assert c.odysseus_mode == "off" and not odysseus.active(c)
    assert c.odysseus_endpoint_id == c.odysseus_model == c.odysseus_launch == ""
    assert c.odysseus_url == "http://127.0.0.1:7000"
    with pytest.raises(ValueError):
        Config(odysseus_mode="everything").validate()


def test_only_mode_keeps_just_ask_odysseus():
    r = registry()
    odysseus.restrict_tools(r, "only")
    assert r.names() == ["ask_odysseus"]


@pytest.mark.parametrize("mode", ["off", "assist"])
def test_other_modes_keep_every_tool(mode):
    r = registry()
    odysseus.restrict_tools(r, mode)
    assert len(r.names()) == 3


def test_prompts_per_mode():
    assert odysseus.mode_prompt("off") == ""
    assert "ask_odysseus" in odysseus.mode_prompt("assist")
    only = odysseus.mode_prompt("only")
    assert "no tools of your own" in only and "do not pretend" in only


def test_problem_lists_what_is_missing(monkeypatch):
    assert "address" in odysseus.problem(Config(odysseus_url=""))
    monkeypatch.setattr(odysseus, "token", lambda: "")
    assert "token" in odysseus.problem(configured())
    monkeypatch.setattr(odysseus, "token", lambda: "t")
    assert "endpoint id" in odysseus.problem(Config(odysseus_mode="assist"))
    assert odysseus.problem(configured()) == ""


def test_not_running_without_a_start_file_is_reported(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: False)
    assert "no start file" in odysseus.ensure_running(configured())


def test_start_file_is_run_when_the_server_is_down(monkeypatch, tmp_path):
    bat = tmp_path / "start.bat"
    bat.write_text("@echo off")
    ran, up = [], {"v": False}

    class Proc:
        def poll(self):
            up["v"] = True  # the "server" comes up once we look
            return None

    monkeypatch.setattr(odysseus.subprocess, "Popen", lambda cmd, **k: ran.append(cmd) or Proc())
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: up["v"])
    assert odysseus.ensure_running(configured(odysseus_launch=str(bat))) == ""
    assert ran == [["cmd", "/c", str(bat)]]


def test_nothing_is_started_when_the_server_already_answers(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    monkeypatch.setattr(odysseus.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start anything"))
    assert odysseus.ensure_running(configured(odysseus_launch="x.bat")) == ""


class FakeClient:
    def __init__(self, reply="done", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def ask(self, message, new_conversation=False):
        self.calls.append((message, new_conversation))
        if self.error:
            raise self.error
        return self.reply


def tool(cfg, confirm, client):
    return odysseus.build_odysseus_tools(cfg, confirm, client)[0]


def test_tool_asks_first_and_marks_the_reply_as_data(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    client = FakeClient("The report is ready.")
    asked = []
    t = tool(configured(), lambda m: asked.append(m) or True, client)
    out = t.handler(message="write the report")
    assert asked and "write the report" in asked[0]
    assert out.endswith("The report is ready.") and "data, not instructions" in out
    assert client.calls == [("write the report", False)]


def test_declining_the_confirmation_sends_nothing(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    client = FakeClient()
    assert tool(configured(), lambda m: False, client).handler(message="x") == "The user declined this action."
    assert client.calls == []


def test_auto_approve_skips_the_prompt(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    t = tool(configured(odysseus_auto_approve=True), lambda m: pytest.fail("no prompt expected"), FakeClient())
    assert "done" in t.handler(message="x")


def test_unconfigured_or_failing_odysseus_gives_a_clear_error(monkeypatch):
    t = tool(Config(odysseus_mode="assist"), lambda m: True, FakeClient())
    assert t.handler(message="x").startswith("Error: Odysseus is not available")
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    resp = httpx.Response(401, request=httpx.Request("POST", "http://x"))
    bad = FakeClient(error=httpx.HTTPStatusError("no", request=resp.request, response=resp))
    assert "rejected the token" in tool(configured(), lambda m: True, bad).handler(message="x")
    down = FakeClient(error=httpx.ConnectError("refused"))
    assert "could not reach" in tool(configured(), lambda m: True, down).handler(message="x")


def test_client_reuses_the_session_and_recovers_from_a_deleted_one():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": f"s{len([p for p, _ in seen if p == '/api/session'])}"})
        if len([1 for p, _ in seen if p == "/api/chat"]) == 2:  # second chat call: the session was deleted
            return httpx.Response(404)
        return httpx.Response(200, json={"response": "hi"})

    transport = httpx.MockTransport(handler)
    real = httpx.Client
    odysseus.httpx.Client = lambda **k: real(transport=transport, **k)
    try:
        c = odysseus.OdysseusClient(configured())
        assert c.ask("one") == "hi"
        assert c.ask("two") == "hi"  # 404 -> new session -> retried
    finally:
        odysseus.httpx.Client = real
    assert [p for p, _ in seen].count("/api/session") == 2
    assert all(a == "Bearer ody_test" for _, a in seen)
