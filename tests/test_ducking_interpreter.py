from voice_assistant.ducking import Ducker, is_music
from voice_assistant.interpreter import Interpreter, build_interpreter_tools


def sess(name, level=0.8, active=True):
    box = {"v": level}
    return {"name": name, "active": active, "get": lambda: box["v"], "set": lambda v: box.update(v=v), "box": box}


class FakeAudio:
    def __init__(self, sessions):
        self._s = sessions

    def sessions(self):
        return self._s


def test_only_active_music_apps_are_lowered_and_exactly_restored():
    chrome, spotify, quiet, own, game = sess("chrome", 0.8), sess("Spotify", 0.5), sess("msedge", 0.9, active=False), sess("VoiceAssistant", 1.0), sess("VRChat", 1.0)
    d = Ducker(FakeAudio([chrome, spotify, quiet, own, game]), level=0.25)
    d.duck()
    assert chrome["box"]["v"] == 0.2 and spotify["box"]["v"] == 0.125
    assert quiet["box"]["v"] == 0.9 and own["box"]["v"] == 1.0 and game["box"]["v"] == 1.0  # silent apps, the assistant itself and the game are never touched
    d.duck()  # a second duck must not lower it again (that would lose the real level)
    assert chrome["box"]["v"] == 0.2
    d.restore()
    assert chrome["box"]["v"] == 0.8 and spotify["box"]["v"] == 0.5
    d.restore()  # harmless twice
    assert chrome["box"]["v"] == 0.8


def test_ducking_does_nothing_when_switched_off_and_survives_a_closed_app():
    c = sess("chrome", 0.8)
    off = Ducker(FakeAudio([c]), enabled=lambda: False)
    off.duck()
    assert c["box"]["v"] == 0.8
    on = Ducker(FakeAudio([c]))
    on.duck()

    def boom(v):
        raise OSError("app closed")

    on._saved[0][0]["set"] = boom
    on.restore()  # must not raise
    assert is_music("chrome.exe") and not is_music("VoiceAssistant.exe") and not is_music("notepad")


def test_interpreter_starts_after_the_turn_that_asked_for_it_and_sends_translations_to_the_chatbox():
    said, changes = [], []
    it = Interpreter(say=said.append, on_change=lambda: changes.append(1))
    assert "will start after this reply" in it.start("Japanese", chatbox=True) and not it.active
    it.feed("Okay, starting.")  # the confirmation of the request itself
    it.finish()
    assert it.active and it.lang == "Japanese" and said == [] and changes == [1] and "Japanese" in it.prompt()
    it.feed("こんにちは")
    it.feed("、元気ですか")
    assert it.finish() == "こんにちは、元気ですか" and said == ["こんにちは、元気ですか"]
    assert it.stop() == "Interpreter mode is off." and not it.active and it.prompt() == ""
    it.feed("ignored")
    assert it.finish() == "" and said == ["こんにちは、元気ですか"]


def test_interpreter_without_chatbox_only_translates_and_refuses_the_chatbox_when_it_is_off():
    said = []
    it = Interpreter(say=said.append, chatbox_ok=lambda: False)
    assert it.start("Spanish", chatbox=True).startswith("Error")
    it.start("Spanish")
    it.finish()
    it.feed("hola")
    assert it.finish() == "" and said == [] and it.active
    tools = {t.name: t.handler for t in build_interpreter_tools(it)}
    assert tools["start_interpreter"]("French").startswith("Interpreter mode will start") and tools["stop_interpreter"]() == "Interpreter mode is off."
    assert it.start("").startswith("Error")
