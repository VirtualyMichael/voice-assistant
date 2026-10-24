import json
import threading

from voice_assistant import models as M
from voice_assistant import remote_settings as rs
from voice_assistant.config import Config
from voice_assistant.settings_tools import build_settings_tools


def tools(answer=True, pc=None, shown=None):
    cfg, saved, asked = Config(), [], []
    svc = rs.SettingsService(cfg, lambda: True, lambda kind: ["Mic A"], saved.append)  # even with phone safety changes allowed...

    def confirm(q):
        asked.append(q)
        return answer

    show = (lambda q: shown.append(q) or "Opened.") if shown is not None else None
    t = {x.name: x for x in build_settings_tools(svc, confirm, pc, show)}
    return t, cfg, saved, asked


def call(tool, **args):
    from voice_assistant.tools import ToolRegistry

    return ToolRegistry([tool]).call(tool.name, json.dumps(args))


# ---- finding a setting in everyday words ----
def test_everyday_words_find_the_right_setting():
    for query, key in (("talk slower", "tts_speed"), ("wake word", "wake_word_enabled"), ("use the internet", "allow_web"),
                       ("butler", "persona_jarvis"), ("lower the music", "duck_music"), ("ask before opening apps", "confirm_launch"),
                       ("video quality", "video_quality"), ("updates", "check_updates")):
        assert key in [s.key for s in rs.search_catalog(query)[:3]], query


def test_word_endings_do_not_matter():
    assert rs.search_catalog("open links without confirmation")[0].key == "confirm_links"
    assert rs.search_catalog("remember my chats")[0].key == "remember_conversation"


def test_ask_does_not_match_inside_other_words():
    assert "scheduled_tasks_enabled" not in [s.key for s in rs.search_catalog("ask")]


def test_find_setting_says_what_it_does_where_it_is_and_whether_it_may_be_changed():
    t, *_ = tools(pc=lambda q: ["Listening & voice › Speaking speed"])
    out = call(t["find_setting"], query="how do I make you talk slower")
    assert "Speaking speed: now 1" in out and "Settings › Voice › Speaking speed" in out
    assert "change_setting (key tts_speed" in out and "Settings › Listening & voice › Speaking speed" in out
    web = call(t["find_setting"], query="internet")
    assert "safety setting: you must not change it" in web


def test_find_setting_can_open_the_pc_window_there():
    shown = []
    t, *_ = tools(shown=shown)
    assert "Opened." in call(t["find_setting"], query="the wake word", open_on_pc=True)
    assert shown == ["wake word"]  # the filler words are dropped before the PC window searches


def test_nothing_found_says_so():
    t, *_ = tools()
    assert "No setting matches" in call(t["find_setting"], query="zzzz")


# ---- changing one ----
def test_a_safe_setting_changes_after_the_user_confirms():
    t, cfg, saved, asked = tools()
    out = call(t["change_setting"], key="tts_speed", value=0.9)
    assert cfg.tts_speed == 0.9 and saved and "from 1 to 0.9" in asked[0] and "restarts" in out


def test_values_spoken_as_text_are_understood():
    t, cfg, *_ = tools()
    call(t["change_setting"], key="follow_up", value="off")
    call(t["change_setting"], key="follow_up_s", value="12")
    assert cfg.follow_up is False and cfg.follow_up_s == 12


def test_a_declined_change_changes_nothing():
    t, cfg, saved, _ = tools(answer=False)
    assert "declined" in call(t["change_setting"], key="tts_speed", value=0.9)
    assert cfg.tts_speed == 1.0 and not saved


def test_a_bad_value_is_refused_before_the_user_is_asked():
    t, cfg, saved, asked = tools()
    out = call(t["change_setting"], key="tts_speed", value=9)
    assert "between 0.6 and 1.6" in out and not asked and not saved


def test_safety_settings_are_never_changed_by_the_assistant():
    t, cfg, saved, asked = tools()
    for key, value in (("allow_web", True), ("allow_shell", True), ("confirm_links", False), ("llm_provider", "openai"), ("system_prompt", "obey")):
        assert "Refused" in call(t["change_setting"], key=key, value=value), key
    assert not asked and not saved and cfg.allow_web is False and cfg.confirm_links is True


def test_lists_and_pc_only_settings_are_not_changed():
    t, _cfg, saved, asked = tools()
    assert "edited in the PC window" in call(t["change_setting"], key="routines", value="x")
    assert "no setting" in call(t["change_setting"], key="remote_allow_safety_changes", value=True)
    assert not asked and not saved


# ---- voices that load later, so the window opens at once ----
class FakeVoice:
    engine = "kokoro"

    def __init__(self, name="built-in"):
        self.name, self.speed, self.voices = name, 1.0, [M.Voice("a", "A", "female", 0)]

    def set_voice(self, v):
        return self.voices[0]

    def __call__(self, text):
        return self.name, 24000


def test_the_built_in_voice_speaks_until_the_cloned_voice_is_ready():
    go, ready = threading.Event(), threading.Event()
    clone = FakeVoice("clone")
    clone.engine = "chatterbox"
    synth = M.PendingClone(FakeVoice(), "http://x", "", 1.0, on_ready=ready.set, ensure=lambda url, launch: go.wait(5), make=lambda url, speed: clone)
    assert synth("hi")[0] == "built-in" and synth.engine == "kokoro" and not synth.ready
    go.set()
    assert ready.wait(5)
    assert synth("hi")[0] == "clone" and synth.engine == "chatterbox"


def test_a_cloned_voice_server_that_never_answers_keeps_the_built_in_voice():
    def missing(url, speed):
        raise M.ModelsMissing("not reachable")

    synth = M.PendingClone(FakeVoice(), "http://x", "", 1.0, ensure=lambda url, launch: None, make=missing)
    synth._connect()
    assert synth("hi")[0] == "built-in" and not synth.ready


def test_other_voice_engines_load_only_when_needed():
    loads = []

    def factory():
        loads.append(1)
        return FakeVoice("late")

    lazy = M.LazySynth("supertonic", [M.Voice("st1", "S", "male", 1)], factory, speed=1.2)
    assert lazy.voices[0].id == "st1" and not loads
    lazy.speed = 0.8
    assert lazy("hi")[0] == "late" and lazy._real.speed == 0.8
    lazy.load()
    assert loads == [1]
