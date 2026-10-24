import json
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


def test_only_mode_keeps_just_the_odysseus_tools():
    r = registry()
    r.add([Tool(n, "x", {"type": "object", "properties": {}}, lambda **k: "ok") for n in ("check_odysseus", "cancel_odysseus")])
    odysseus.restrict_tools(r, "only")
    assert sorted(r.names()) == ["ask_odysseus", "cancel_odysseus", "check_odysseus"]


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
    def __init__(self, reply="done", error=None, question="", tools=()):
        self.reply, self.error, self.question, self.tools, self.calls, self.stopped = reply, error, question, tools, [], False

    def run(self, message, new_conversation=False, on_tool=None, cancel=None):
        self.calls.append((message, new_conversation))
        if self.error:
            raise self.error
        for t in self.tools:
            on_tool(t, "cmd")
        res = odysseus.RunResult()
        res.final, res.question = self.reply, self.question
        return res

    def stop(self, client=None):
        self.stopped = True


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


# ---- streaming, progress and background jobs ------------------------------------------------
import threading
import time


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def test_the_stream_is_parsed_into_tools_text_and_questions():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"id": "s1"})
        assert request.url.path == "/api/chat_stream" and b"mode=agent" in request.content
        body = sse({"type": "turn_mode"}, {"type": "tool_start", "tool": "web_search", "command": "monstera care"},
                   {"delta": "thinking ", "round": 1}, {"delta": "The answer.", "round": 2}, {"type": "metrics", "data": {}})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    transport, real = httpx.MockTransport(handler), httpx.Client
    odysseus.httpx.Client = lambda **k: real(transport=transport, **{x: v for x, v in k.items() if x != "transport"})
    try:
        res = odysseus.OdysseusClient(configured()).run("hi", on_tool=lambda t, c: seen.append((t, c)))
    finally:
        odysseus.httpx.Client = real
    assert seen == [("web_search", "monstera care")] and res.text == "The answer."  # the last round is the answer


def test_a_final_response_event_wins_and_questions_are_captured():
    r = odysseus.RunResult()
    r.feed({"delta": "partial", "round": 1})
    r.feed({"type": "final_response", "content": "Final text"})
    assert r.text == "Final text"
    q = odysseus.RunResult()
    q.feed({"type": "ask_user", "data": {"question": "Which date?"}})
    assert q.question == "Which date?" and q.text == ""


def test_progress_is_spoken_sparingly_and_noted_every_time():
    clock = {"t": 0.0}
    spoken, noted = [], []
    jobs = odysseus.OdysseusJobs(FakeClient(), spoken.append, noted.append, clock=lambda: clock["t"], progress_gap_s=12, first_progress_s=6)
    job = odysseus.Job(1, "x", 0.0)
    for t, tool in [(2, "web_search"), (7, "web_search"), (9, "read_file"), (20, "send_email"), (22, "send_email")]:
        clock["t"] = t
        jobs._on_tool(job, tool, "cmd")
    assert spoken == ["Searching online.", "Checking your email."]  # not before 6 s, not within 12 s, no repeats
    assert len(noted) == 5


def blocking_client():
    release = threading.Event()

    class Slow(FakeClient):
        def run(self, message, new_conversation=False, on_tool=None, cancel=None):
            self.calls.append((message, new_conversation))
            while not release.is_set():
                if cancel is not None and cancel.is_set():
                    res = odysseus.RunResult()
                    res.cancelled = True
                    return res
                time.sleep(0.01)
            res = odysseus.RunResult()
            res.final = "All done. Extra detail."
            return res

    return Slow(), release


def wait_for(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_a_slow_job_is_detached_then_announced_and_blocks_a_second_one(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    client, release = blocking_client()
    spoken = []
    t = odysseus.build_odysseus_tools(configured(odysseus_auto_approve=True), lambda m: True, client, speak=spoken.append, foreground_s=0.05)[0]
    first = t.handler(message="big research")
    assert "still working on job 1" in first
    assert "still working on job 1" in t.handler(message="another thing")  # one job at a time
    release.set()
    assert wait_for(lambda: spoken)
    assert spoken[0] == "Odysseus finished job 1. All done."


def test_background_jobs_return_at_once(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    client, release = blocking_client()
    spoken = []
    t = odysseus.build_odysseus_tools(configured(odysseus_auto_approve=True), lambda m: True, client, speak=spoken.append, foreground_s=30)[0]
    start = time.time()
    assert "in the background" in t.handler(message="slow", background=True) and time.time() - start < 1
    release.set()
    assert wait_for(lambda: spoken) and "finished job 1" in spoken[0]


def test_check_and_cancel_tools(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    client, release = blocking_client()
    spoken = []
    tools = {x.name: x for x in odysseus.build_odysseus_tools(configured(odysseus_auto_approve=True), lambda m: True, client, speak=spoken.append, foreground_s=0.05)}
    assert tools["cancel_odysseus"].handler() == "No Odysseus job is running."
    tools["ask_odysseus"].handler(message="long job")
    assert "[running" in tools["check_odysseus"].handler()
    assert tools["cancel_odysseus"].handler() == "Stopped Odysseus job 1."
    assert wait_for(lambda: "cancelled" in tools["check_odysseus"].handler())
    assert client.stopped and spoken == []  # a cancelled job is not announced


def test_questions_from_odysseus_are_passed_to_the_user(monkeypatch):
    monkeypatch.setattr(odysseus, "reachable", lambda cfg: True)
    out = tool(configured(odysseus_auto_approve=True), lambda m: True, FakeClient(reply="", question="Which date should I use?")).handler(message="book it")
    assert "needs an answer" in out and "Which date should I use?" in out
