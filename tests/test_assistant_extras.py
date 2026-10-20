import json
import subprocess
from datetime import timedelta

from voice_assistant.approval import parse_yes_no, speakable
from voice_assistant.config import Config
from voice_assistant.delegate import UsageLog, build_command, build_delegate_tools, parse_result
from voice_assistant.profile import Profile, build_profile_tools


def test_parse_yes_no():
    assert parse_yes_no("yes") is True and parse_yes_no("yeah go ahead") is True and parse_yes_no("Okay.") is True
    assert parse_yes_no("no") is False and parse_yes_no("don't do it") is False and parse_yes_no("yes no wait") is False
    assert parse_yes_no("what time is it") is None and parse_yes_no("") is None
    assert parse_yes_no("yes " * 9) is None  # too long to be an answer


def test_speakable_trims_and_asks():
    assert speakable("Open Steam?").endswith("Say yes or no.")
    assert len(speakable("word " * 200)) < 260


def test_profile_prompt_and_facts(tmp_path):
    p = Profile(tmp_path / "p.md")
    assert p.prompt() == ""
    p.save("I like short answers.")
    assert "short answers" in p.prompt()
    assert p.add_fact("Drives a Mazda CX-5") == "Saved to the profile." and p.add_fact("drives a mazda cx-5") == "Already known."
    declined = {t.name: t for t in build_profile_tools(Config(), p, lambda q: False)}["learn_about_user"].handler("secret")
    assert "declined" in declined and "secret" not in p.load()
    assert build_profile_tools(Config(profile_learning=False), p, lambda q: True) == []


class Pol:
    def resolve(self, folder):
        return "C:/work"


def fake_run(stdout, code=0, seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, code, stdout, "")
    return run


def delegate(cfg, confirm=lambda p: True, run=None, usage=None, clock=lambda: 1000.0, which=lambda n: "/bin/" + n):
    ts = build_delegate_tools(cfg, Pol(), confirm, usage, run or fake_run("{}"), which, clock)
    return {t.name: t.handler for t in ts}


CLAUDE_JSON = json.dumps({"result": "Done: fixed it.", "total_cost_usd": 0.12, "usage": {"input_tokens": 100, "output_tokens": 50}})


def test_delegate_sends_task_on_stdin_after_approval_and_logs_usage(tmp_path):
    seen, asked = [], []
    u = UsageLog(tmp_path / "u.json")
    out = delegate(Config(), lambda p: asked.append(p) or True, fake_run(CLAUDE_JSON, seen=seen), u)["delegate_task"]("claude", "fix the bug; rm -rf /", ".")
    assert "Done: fixed it." in out and "Untrusted" in out
    cmd, kw = seen[0]
    assert kw["input"] == "fix the bug; rm -rf /" and "fix the bug" not in " ".join(cmd) and "acceptEdits" not in cmd
    assert "fix the bug; rm -rf /" in asked[0] and "read-only" in asked[0]
    assert u.today()["requests"] == 1 and u.today()["usd"] == 0.12


def test_delegate_declined_runs_nothing(tmp_path):
    seen = []
    out = delegate(Config(), lambda p: False, fake_run("{}", seen=seen), UsageLog(tmp_path / "u.json"))["delegate_task"]("codex", "x")
    assert "declined" in out and not seen


def test_delegate_limits(tmp_path):
    u = UsageLog(tmp_path / "u.json")
    u.add("claude", 3.0, 1, 1, True)
    out = delegate(Config(delegate_daily_usd=2.0), usage=u)["delegate_task"]("claude", "x")
    assert "budget" in out
    u2 = UsageLog(tmp_path / "u2.json")
    for _ in range(2):
        u2.add("codex", 0, 0, 0, True)
    assert "Daily limit" in delegate(Config(delegate_daily_requests=2), usage=u2)["delegate_task"]("codex", "x")


def test_delegate_cooldown_and_missing_cli(tmp_path):
    t = [1000.0]
    d = delegate(Config(delegate_cooldown_s=30), run=fake_run(CLAUDE_JSON), usage=UsageLog(tmp_path / "u.json"), clock=lambda: t[0])["delegate_task"]
    assert "Done" in d("claude", "one")
    assert "Wait" in d("claude", "two")  # a looping model cannot fire again at once
    t[0] += 31
    assert "Done" in d("claude", "three")
    assert "not installed" in delegate(Config(), which=lambda n: None)["delegate_task"]("claude", "x")


def test_commands_and_result_parsing():
    assert build_command("codex", "codex", False) == ["codex", "exec", "--sandbox", "read-only", "-"]
    assert "acceptEdits" in build_command("claude", "claude", True)
    assert parse_result("claude", CLAUDE_JSON) == ("Done: fixed it.", 0.12, 100, 50)
    assert parse_result("codex", "plain text\n")[0] == "plain text"


def test_usage_summary_windows(tmp_path):
    u = UsageLog(tmp_path / "u.json")
    u.add("claude", 0.5, 10, 5, True)
    assert u.since(timedelta(days=7))["requests"] == 1 and "Today: 1" in u.summary()


def test_voice_approval_listens_after_prompt_and_resolves():
    import threading, time
    from tests.test_session import AUDIO, FakeRec, SlowLLM, make_speaker
    from voice_assistant.agent import Agent
    from voice_assistant.session import Session
    from voice_assistant.tools import ToolRegistry

    rec = FakeRec()
    sp, _ = make_speaker(0.01)
    s = Session(Config(post_speech_guard_s=0.0), Agent(SlowLLM(["x"]), "sys", ToolRegistry([]), 2), sp, rec)
    results = []
    t = threading.Thread(target=lambda: results.append(s.ask_voice("Open Steam?", timeout=5)))
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not (s._approval and s._approval.listening):
        time.sleep(0.02)
    assert s._approval and s._approval.listening  # prompt was spoken, now listening
    s.feed_audio(AUDIO)  # the first chunk after listening starts only resets the recognizer
    time.sleep(0.2)
    rec.t, rec.end = "yes please", True
    s.feed_audio(AUDIO)
    t.join(5)
    assert results == [True]


def test_voice_approval_unclear_then_silence_is_none():
    import threading, time
    from tests.test_session import AUDIO, FakeRec, SlowLLM, make_speaker
    from voice_assistant.agent import Agent
    from voice_assistant.session import Session
    from voice_assistant.tools import ToolRegistry

    rec = FakeRec()
    sp, _ = make_speaker(0.01)
    s = Session(Config(post_speech_guard_s=0.0), Agent(SlowLLM(["x"]), "sys", ToolRegistry([]), 2), sp, rec)
    out = []
    t = threading.Thread(target=lambda: out.append(s.ask_voice("Delete it?", timeout=1.0)))
    t.start()
    while not (s._approval and s._approval.listening):
        time.sleep(0.02)
    s.feed_audio(AUDIO)
    time.sleep(0.2)
    rec.t, rec.end = "what is the weather", True
    s.feed_audio(AUDIO)
    t.join(5)
    assert out == [None]


def test_wake_probe_receives_audio_instead_of_waking():
    import time
    import numpy as np
    from tests.test_session import AUDIO, FakeRec, SlowLLM, make_speaker
    from voice_assistant.agent import Agent
    from voice_assistant.session import Session
    from voice_assistant.tools import ToolRegistry

    class Wake:
        def accept(self, samples):
            return True

    sp, _ = make_speaker(0.01)
    s = Session(Config(), Agent(SlowLLM(["x"]), "sys", ToolRegistry([]), 2), sp, FakeRec(), Wake())
    seen = []
    s.wake_probe = lambda samples, hit: seen.append(hit)
    s.feed_audio(AUDIO)
    time.sleep(0.3)
    assert seen == [True] and s.state.name == "IDLE"  # detection was reported, not acted on
    s.wake_probe = None
    s.feed_audio(AUDIO)
    time.sleep(0.3)
    assert s.state.name == "LISTENING"
