import json

import httpx

from voice_assistant.config import Config
from voice_assistant.extras import Notes, build_extra_tools, weather


class FakeAudio:
    def __init__(self, sessions=()):
        self.level, self.muted, self._s = 0.5, False, list(sessions)

    def master(self):
        return self.level

    def set_master(self, v):
        self.level = v

    def toggle_mute(self):
        self.muted = not self.muted
        return self.muted

    def sessions(self):
        return self._s


def fake_session(name, active=True, level=1.0):
    box = {"v": level}
    return {"name": name, "active": active, "get": lambda: box["v"], "set": lambda v: box.update(v=v), "box": box}


def tools(cfg=None, keys=None, calls=None, notes=None, session=lambda a: None, audio=None):
    cfg = cfg or Config()
    ts = build_extra_tools(cfg, lambda p: True, (lambda n, a: calls.append((n, a)) or "ok") if calls is not None else (lambda n, a: "ok"),
                           notes, keys or (lambda k, n=1: None), session, audio or FakeAudio())
    return {t.name: t.handler for t in ts}


def test_volume_keys_and_media_fallback_key():
    pressed = []
    t = tools(keys=lambda k, n=1: pressed.append((k, n)), session=lambda a: None)
    out = t["media_control"]("play_pause")
    assert pressed == [("play_pause", 1)]
    assert "No music app" in out and t["media_control"]("explode").startswith("Error")


def test_media_words_and_real_result_reported():
    seen = []
    t = tools(session=lambda a: seen.append(a) or "Spotify is now playing: Song by Band.")
    assert t["media_control"]("Play") == "Spotify is now playing: Song by Band."
    for word in ("pause", "resume", "skip", "back", "next track"):
        t["media_control"](word)
    assert seen == ["play", "pause", "play", "next", "previous", "next"]


def test_media_session_parses_powershell_output():
    from voice_assistant.extras import media_session

    class P:
        stdout, returncode = "True|Paused|Playing|Spotify.exe|Song|Band\n", 0

    assert media_session("play", run=lambda *a, **k: P()) == "Spotify is now playing: Song by Band."
    P.stdout = "NONE\n"
    assert media_session("play", run=lambda *a, **k: P()) is None
    P.stdout = "False|Playing|Playing|Chrome|T|\n"
    assert "did not accept" in media_session("next", run=lambda *a, **k: P())


def test_notes_roundtrip(tmp_path):
    t = tools(notes=Notes(tmp_path / "n.json"))
    assert "#1" in t["remember_note"]("wifi code is blue")
    assert "wifi code" in t["list_notes"]("wifi")
    assert t["list_notes"]("zzz") == "No matching notes."
    assert t["delete_note"](1).startswith("Deleted") and t["delete_note"](1) == "No such note."


def test_routine_runs_steps_and_skips_nesting():
    calls = []
    cfg = Config(routines={"good night": [{"tool": "set_lights", "args": {"on": False}}, {"tool": "run_routine", "args": {}}]}, confirm_routines=False)
    t = tools(cfg, calls=calls)
    t["run_routine"]("Good Night")
    assert calls == [("set_lights", json.dumps({"on": False}))]
    assert "run_routine" not in tools()


def test_weather_parses_open_meteo():
    def handler(req):
        if "geocoding" in str(req.url):
            return httpx.Response(200, json={"results": [{"name": "Austin", "latitude": 30.2, "longitude": -97.7}]})
        return httpx.Response(200, json={"current": {"temperature_2m": 71.6, "weather_code": 2, "wind_speed_10m": 6.2},
                                         "daily": {"temperature_2m_max": [80.1], "temperature_2m_min": [62.4], "precipitation_probability_max": [10]}})

    out = weather("Austin", httpx.Client(transport=httpx.MockTransport(handler)))
    assert out.startswith("Austin: 72") and "partly cloudy" in out and "62 to 80" in out


def test_volume_sets_exact_level_once_and_accepts_missing_action():
    a = FakeAudio()
    t = tools(audio=a)
    assert t["set_volume"](percent=50) == "Volume set to 50%." and a.level == 0.5  # the call that failed in real use (no action)
    t["set_volume"]("up")
    assert round(a.level, 2) == 0.6
    t["set_volume"]("lower")
    assert round(a.level, 2) == 0.5
    assert t["set_volume"]("mute") == "Muted." and a.muted
    assert t["set_volume"]().startswith("Error")


def test_per_app_volume_targets_the_apps_that_make_sound():
    chrome, other, spotify = fake_session("chrome"), fake_session("msedge", active=False), fake_session("Spotify")
    a = FakeAudio([chrome, other, spotify])
    t = tools(audio=a)
    out = t["set_volume"](percent=30, app="YouTube Music")
    assert chrome["box"]["v"] == 0.3 and other["box"]["v"] == 1.0 and spotify["box"]["v"] == 1.0 and "30%" in out and a.level == 0.5
    t["set_volume"](percent=20, app="spotify")
    assert spotify["box"]["v"] == 0.2
    assert "No app matching 'discord'" in t["set_volume"](percent=10, app="discord") and "chrome" in t["set_volume"](percent=10, app="discord")


def test_volume_falls_back_to_keys_when_audio_api_fails():
    class Broken(FakeAudio):
        def set_master(self, v):
            raise OSError("no device")

    pressed = []
    t = tools(keys=lambda k, n=1: pressed.append((k, n)), audio=Broken())
    assert "about 40%" in t["set_volume"](percent=40) and pressed == [("volume_down", 50), ("volume_up", 20)]
