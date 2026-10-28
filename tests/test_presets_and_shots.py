import json

from voice_assistant.agent import Agent
from voice_assistant.config import Config
from voice_assistant.extras import Recent, build_extra_tools
from voice_assistant.screen import build_screenshot_tools
from voice_assistant.tools import ToolRegistry


class FakeAudio:
    pass


def make(cfg, now=lambda: 1000.0, ok=True):
    calls, shortcuts, recent = [], [], Recent(clock=now)
    ts = build_extra_tools(cfg, lambda p: ok, lambda n, a: calls.append((n, json.loads(a))) or "ok", None, lambda k, n=1: None, lambda a: None, FakeAudio(),
                           known_tools=lambda: ["vrchat_set_many", "set_lights"], persist=lambda: None, recent=recent, shortcuts=shortcuts)
    return {t.name: t.handler for t in ts}, calls, shortcuts[0], recent


def test_a_preset_is_saved_from_what_just_worked_and_then_runs_from_its_name_alone():
    cfg = Config()
    t, calls, quick, recent = make(cfg)
    recent.listener("vrchat_parameters", {"query": "hair"}, "hairColor 0.0")  # a lookup is not part of the preset
    recent.listener("vrchat_set_many", {"names": "hairColor,shirtColor", "value": "white"}, "ok")
    recent.listener("vrchat_set", {"name": "nope"}, "Error: no such switch")  # nor is a failure
    out = t["save_routine_from_recent"]("White Outfit")
    assert out.startswith("Saved the preset 'white outfit' with 1 step")
    assert cfg.routines["white outfit"] == [{"tool": "vrchat_set_many", "args": {"names": "hairColor,shirtColor", "value": "white"}}]
    for spoken in ("white outfit", "Run white outfit.", "activate the white outfit preset", "Switch to White Outfit"):
        calls.clear()
        assert quick(spoken) == "Done: white outfit." and [c[0] for c in calls] == ["vrchat_set_many"]


def test_ordinary_sentences_never_trigger_a_preset_and_nothing_runs_without_one():
    cfg = Config(routines={"white outfit": [{"tool": "set_lights", "args": {}}]})
    t, calls, quick, _ = make(cfg)
    assert quick("what is the weather") is None and quick("make my outfit white please") is None and quick("") is None and calls == []
    assert make(Config())[2]("white outfit") is None


def test_nothing_recent_or_declined_saves_nothing():
    cfg = Config()
    t, _, _, recent = make(cfg)
    assert t["save_routine_from_recent"]("x").startswith("Nothing to save")
    now = [1000.0]
    t2, _, _, recent2 = make(cfg, now=lambda: now[0], ok=False)
    recent2.listener("set_lights", {}, "ok")
    assert t2["save_routine_from_recent"]("x") == "The user declined this action." and cfg.routines == {}
    now[0] += 3600
    assert recent2.last(5) == []  # an hour old is not "what I just did"


def test_a_preset_that_partly_fails_says_so():
    cfg = Config(routines={"gaming": [{"tool": "set_lights", "args": {}}]})
    calls = []
    shortcuts = []
    build_extra_tools(cfg, lambda p: True, lambda n, a: "Error: lights offline", None, lambda k, n=1: None, lambda a: None, FakeAudio(), shortcuts=shortcuts)
    assert "had a problem" in shortcuts[0]("gaming mode")


class Dumb:
    def stream_chat(self, *a, **k):
        raise AssertionError("the model must not be asked")


def test_the_agent_answers_a_preset_without_asking_the_model():
    agent = Agent(Dumb(), "sys", ToolRegistry([]))
    agent.shortcuts.append(lambda text: "Done: x." if text == "x" else None)
    import threading
    assert "".join(agent.respond("x", threading.Event())) == "Done: x."
    assert agent.messages[-1] == {"role": "assistant", "content": "Done: x."}


def test_screenshots_of_several_named_windows_and_a_missing_one():
    shown = []
    wins = [(1, "VRChat", "VRChat"), (2, "Discord | #general", "Discord")]
    ts = build_screenshot_tools(lambda label, png: shown.append((label, png)), windows=lambda: wins, rect=lambda h: (0, 0, 10, h), capture=lambda m, max_width=0, region=None: b"png%d" % (region[3] if region else 0))
    shot = ts[0].handler
    out = shot("VRChat and discord, Steam")
    assert shown == [("VRChat", b"png1"), ("Discord | #general", b"png2")]
    assert "Took 2 screenshot" in out and "Steam" in out and "Do NOT open" in out
    shown.clear()
    assert "monitor 2" in shot("", 2) and shown[0][0] == "Monitor 2"
